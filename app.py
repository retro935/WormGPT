# app.py
import os
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Optional, List

import aiosqlite
from openai import OpenAI

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ---------------- CONFIG ----------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MODEL_API_KEY = os.getenv("MODEL_API_KEY")
OWNER_ID = int(os.getenv("OWNER_ID", "6699273462"))

DB_FILE = os.getenv("DB_FILE", "vip.sqlite3")
# Sticker shown while IA is "thinking" (you provided this)
THINKING_STICKER = "CAACAgEAAxkBAAE90AJpFtQXZ4J90fBT2-R3oBJqi6IUewACrwIAAphXIUS8lNoZG4P3rDYE"

# Default model (you chose option B)
AVAILABLE_MODELS = ["deepseek-ai/deepseek-r1"]
CURRENT_MODEL = AVAILABLE_MODELS[0]

# How long VIP default (days) when owner adds without specifying (fallback)
DEFAULT_VIP_DAYS = 30

# ---------------- LOGGING ----------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("vip-bot")

# ---------------- OPENAI (NVIDIA integrate) CLIENT ----------------
client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=MODEL_API_KEY)

# ---------------- IN-MEMORY USER SETTINGS ----------------
# stored while process runs; persists VIPs in DB only
USER_SETTINGS: Dict[int, Dict[str, bool]] = {}  # e.g. {123: {"reasoning": False}}

# ---------------- DB (aiosqlite) ----------------
CREATE_VIPS_SQL = """
CREATE TABLE IF NOT EXISTS vips (
    user_id INTEGER PRIMARY KEY,
    expires TEXT NOT NULL
);
"""

async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(CREATE_VIPS_SQL)
        await db.commit()

async def add_vip_db(user_id: int, days: int):
    expires = (datetime.utcnow() + timedelta(days=days)).isoformat()
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("INSERT OR REPLACE INTO vips (user_id, expires) VALUES (?, ?);", (user_id, expires))
        await db.commit()
    return datetime.fromisoformat(expires)

async def remove_vip_db(user_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM vips WHERE user_id = ?;", (user_id,))
        await db.commit()

async def list_vips_db() -> Dict[int, datetime]:
    out: Dict[int, datetime] = {}
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT user_id, expires FROM vips;") as cur:
            async for row in cur:
                try:
                    uid = int(row[0])
                    exp = datetime.fromisoformat(row[1])
                    out[uid] = exp
                except Exception:
                    continue
    # ensure owner is present long-term
    if OWNER_ID not in out:
        out[OWNER_ID] = datetime.utcnow() + timedelta(days=3650)
    return out

async def remove_expired_vips_and_notify(app):
    vips = await list_vips_db()
    now = datetime.utcnow()
    removed = []
    for uid, exp in list(vips.items()):
        if uid == OWNER_ID:
            continue
        if exp <= now:
            removed.append(uid)
            await remove_vip_db(uid)
    if removed:
        # notify owner
        try:
            await app.bot.send_message(chat_id=OWNER_ID, text=f"⚠️ VIP expirados eliminados automáticamente: {', '.join(str(x) for x in removed)}")
        except Exception:
            logger.exception("No pudo notificar owner sobre expirados")
        # notify users (best-effort)
        for uid in removed:
            try:
                await app.bot.send_message(chat_id=uid, text="⛔ Tu membresía VIP ha expirado.")
            except Exception:
                pass

# ---------------- HELPERS ----------------
def get_user_setting(uid: int) -> Dict[str, bool]:
    s = USER_SETTINGS.get(uid)
    if s is None:
        s = {"reasoning": False}  # default off (you asked reasoning should be internal; default off)
        USER_SETTINGS[uid] = s
    return s

async def notify_owner_text(app, text: str):
    try:
        await app.bot.send_message(chat_id=OWNER_ID, text=text)
    except Exception:
        logger.exception("Fallo notificar owner")

async def notify_new_vip(app, vip_id: int, days: int):
    # notify owner
    await notify_owner_text(app, f"🔔 Nuevo VIP agregado: `{vip_id}` por {days} días.")
    # notify the new vip (best-effort)
    try:
        await app.bot.send_message(chat_id=vip_id, text=f"🎉 Ahora eres VIP por {days} días. Disfruta!", parse_mode="Markdown")
    except Exception:
        logger.debug("No se pudo notificar al nuevo VIP (probablemente no acepta mensajes)")

# ---------------- AI CALL ----------------
async def ask_ai(system_prompt: str, user_prompt: str, model: str, reasoning_enabled: bool) -> str:
    """
    Llamada al modelo NVIDIA. NO devuelve el razonamiento visible.
    Si reasoning_enabled == True, la usamos internamente (se solicita al modelo),
    pero no lo enviamos al chat; solamente lo guardamos en logs si aparece.
    """
    try:
        payload_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        completion = client.chat.completions.create(
            model=model,
            messages=payload_messages,
            temperature=0.2,
            top_p=0.7,
            max_tokens=1500,
            extra_body={"chat_template_kwargs": {"thinking": True}},
            stream=False,
        )

        # access message safely
        choice = completion.choices[0]
        msg = getattr(choice, "message", None) or getattr(choice, "delta", None)

        # final text content
        final_text = ""
        if msg is None:
            # fallback to json path if SDK returns plain dict-like
            try:
                final_text = completion["choices"][0]["message"]["content"]
            except Exception:
                final_text = str(completion)
        else:
            # safe attribute access: many SDKs expose .content
            final_text = getattr(msg, "content", None) or getattr(msg, "text", "") or ""

        # check reasoning if present (DO NOT send to user)
        reasoning = getattr(msg, "reasoning_content", None) or ""
        if reasoning:
            # only log it for owner (internal)
            logger.info("RAZONAMIENTO (internal): %s", reasoning[:1000])
        return final_text or "⚠️ El modelo devolvió respuesta vacía."

    except Exception as e:
        logger.exception("Error llamando a la IA")
        return f"❌ Error conectando con la IA: {e}"

# ---------------- UI / MENU ----------------
def build_main_keyboard(uid: int) -> InlineKeyboardMarkup:
    # show reasoning on/off and current model label
    user_setting = get_user_setting(uid)
    reasoning_label = "🧠 Razonamiento: ON" if user_setting.get("reasoning") else "🧠 Razonamiento: OFF"
    model_label = f"🔁 Modelo: {CURRENT_MODEL.split('/')[-1]}"
    keyboard = [
        [InlineKeyboardButton("🤖 Usar IA", callback_data="use_ai")],
        [InlineKeyboardButton(reasoning_label, callback_data="toggle_reasoning"), InlineKeyboardButton(model_label, callback_data="show_model")],
        [InlineKeyboardButton("⭐ Estado VIP", callback_data="vip_status"), InlineKeyboardButton("🛠 Admin", callback_data="admin_panel")],
    ]
    return InlineKeyboardMarkup(keyboard)

# ---------------- HANDLERS ----------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # cleanup expired before proceeding
    await remove_expired_vips_and_notify(context.application)
    user = update.effective_user
    uid = user.id

    vips = await list_vips_db()
    if uid not in vips or vips[uid] <= datetime.utcnow():
        return await update.message.reply_text("🚫 Este bot es solo para usuarios VIP. Contacta al administrador.")

    await update.message.reply_text(f"👋 ¡Hola {user.first_name}! Bienvenido — eres VIP ✅", reply_markup=build_main_keyboard(uid))

    # notify owner
    await notify_owner_text(context.application, f"🔔 /start usado por {user.first_name} (`{uid}`)")

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # for /menu command
    user = update.effective_user
    uid = user.id
    await update.message.reply_text("📍 Menú principal:", reply_markup=build_main_keyboard(uid))

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    data = query.data

    if data == "use_ai":
        # ask user to send a message (we simply reply prompting)
        return await query.edit_message_text("✍️ Escribe tu pregunta en el chat (envía texto).")

    if data == "toggle_reasoning":
        s = get_user_setting(uid)
        s["reasoning"] = not s.get("reasoning", False)
        # update keyboard label
        await query.edit_message_text("✅ Ajuste cambiado.", reply_markup=build_main_keyboard(uid))
        return

    if data == "show_model":
        await query.edit_message_text(f"Modelo actual: `{CURRENT_MODEL}`\n(Disponible: {', '.join(AVAILABLE_MODELS)})", parse_mode="Markdown")
        return

    if data == "vip_status":
        vips = await list_vips_db()
        if uid in vips:
            exp = vips[uid]
            days_left = max(0, (exp - datetime.utcnow()).days)
            await query.edit_message_text(f"⭐ Eres VIP. Expira en {days_left} días (hasta {exp.isoformat()}).")
        else:
            await query.edit_message_text("❌ No eres VIP.")
        return

    if data == "admin_panel":
        if uid != OWNER_ID:
            return await query.edit_message_text("❌ No eres el owner.")
        # simple admin options
        keyboard = [
            [InlineKeyboardButton("➕ Añadir VIP (usa /addvip)", callback_data="noop")],
            [InlineKeyboardButton("🧹 Limpiar expirados", callback_data="admin_clean")],
            [InlineKeyboardButton("⬅️ Volver", callback_data="back_main")],
        ]
        return await query.edit_message_text("🛠 Panel admin", reply_markup=InlineKeyboardMarkup(keyboard))

    if data == "admin_clean":
        await remove_expired_vips_and_notify(context.application)
        return await query.edit_message_text("🧹 Limpieza ejecutada.")

    if data == "back_main":
        return await query.edit_message_text("Volviendo...", reply_markup=build_main_keyboard(uid))

    # default
    await query.edit_message_text("Acción no reconocida.")

async def addvip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # usage: /addvip <user_id> <days>
    uid = update.effective_user.id
    if uid != OWNER_ID:
        return await update.message.reply_text("❌ No tienes permisos.")
    args = context.args or []
    if len(args) < 2:
        return await update.message.reply_text("Uso: /addvip <user_id> <days>")
    try:
        target = int(args[0])
        days = int(args[1])
    except ValueError:
        return await update.message.reply_text("ID o días inválidos.")
    exp = await add_vip_db(target, days)
    await update.message.reply_text(f"✅ Usuario {target} agregado como VIP hasta {exp.isoformat()}")
    await notify_new_vip(context.application, target, days)

async def delvip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != OWNER_ID:
        return await update.message.reply_text("❌ No tienes permisos.")
    args = context.args or []
    if len(args) < 1:
        return await update.message.reply_text("Uso: /delvip <user_id>")
    try:
        target = int(args[0])
    except ValueError:
        return await update.message.reply_text("ID inválido.")
    await remove_vip_db(target)
    await update.message.reply_text(f"❌ Usuario {target} eliminado de VIP.")
    await notify_owner_text(context.application, f"🗑️ VIP {target} eliminado por owner.")

async def listvip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != OWNER_ID:
        return await update.message.reply_text("❌ No tienes permisos.")
    vips = await list_vips_db()
    if not vips:
        return await update.message.reply_text("No hay VIPs.")
    lines = []
    for u, exp in vips.items():
        left = max(0, (exp - datetime.utcnow()).days)
        lines.append(f"- `{u}` — expira: `{exp.isoformat()}` ({left} días left)")
    await update.message.reply_text("⭐ VIPs:\n" + "\n".join(lines), parse_mode="Markdown")

# ---------------- PROCESS AI (background to avoid blocking) ----------------
async def process_ai_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Send thinking sticker, call the model, delete sticker and send the result.
    """
    msg = update.message
    uid = update.effective_user.id
    text = (msg.text or "").strip()
    if not text:
        return

    # check VIP
    vips = await list_vips_db()
    if uid not in vips or vips[uid] <= datetime.utcnow():
        return await msg.reply_text("🚫 Solo VIPs pueden usar esta función.")

    # load system prompt (file)
    system_prompt = ""
    if os.path.exists("system-prompts.txt"):
        try:
            with open("system-prompts.txt", "r", encoding="utf-8") as f:
                system_prompt = f.read().strip()
        except Exception:
            system_prompt = ""

    # send thinking sticker (keep id)
    st_msg = None
    try:
        st_msg = await context.bot.send_sticker(chat_id=msg.chat_id, sticker=THINKING_STICKER)
    except Exception:
        st_msg = None

    # call IA (non-blocking but we are in an async function)
    reasoning_enabled = get_user_setting(uid).get("reasoning", False)
    resp_text = await ask_ai(system_prompt, text, CURRENT_MODEL, reasoning_enabled)

    # delete thinking sticker when model done
    if st_msg:
        try:
            await context.bot.delete_message(chat_id=msg.chat_id, message_id=st_msg.message_id)
        except Exception:
            pass

    # send response (respect Telegram limits; break into chunks if long)
    MAX_LEN = 4000
    if len(resp_text) <= MAX_LEN:
        await msg.reply_text(resp_text)
    else:
        # split by paragraphs approximately
        parts: List[str] = []
        cur = ""
        for line in resp_text.splitlines(keepends=True):
            if len(cur) + len(line) > MAX_LEN:
                parts.append(cur)
                cur = line
            else:
                cur += line
        if cur:
            parts.append(cur)
        for p in parts:
            try:
                await msg.reply_text(p)
            except Exception:
                logger.exception("No se pudo enviar parte de la respuesta")

# ---------------- STARTUP / PERIODIC ----------------
async def periodic_cleanup_task(app):
    while True:
        try:
            await remove_expired_vips_and_notify(app)
        except Exception:
            logger.exception("Error en limpieza periódica")
        await asyncio.sleep(60)  # cada 60s

# ---------------- MAIN ----------------
def main():
    # ensure DB
    asyncio.get_event_loop().run_until_complete(init_db())

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).concurrent_updates(True).build()

    # Commands
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("menu", menu_handler))
    app.add_handler(CommandHandler("addvip", addvip_command))
    app.add_handler(CommandHandler("delvip", delvip_command))
    app.add_handler(CommandHandler("listvip", listvip_command))

    # Callbacks and messages
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    # when user sends normal text we process AI in background (so UI isn't blocked)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u,c: asyncio.create_task(process_ai_text(u,c))))

    # register periodic cleanup on startup
    async def _on_startup(a):
        # start periodic cleanup task
        a.create_task(periodic_cleanup_task(a))
        logger.info("Tarea periódica de limpieza iniciada.")

    app.post_init = _on_startup

    logger.info("🚀 Bot VIP corriendo...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
