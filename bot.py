# app.py
import os
import asyncio
import logging
import aiohttp
import aiosqlite
import threading
from datetime import datetime, timedelta
from typing import Optional, List, Dict

from flask import Flask
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ParseMode,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ---------------- CONFIG ----------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MODEL_API_KEY = os.getenv("MODEL_API_KEY")  # opcional (si no está, bot hace eco)
OWNER_ID = int(os.getenv("OWNER_ID", "6699273462"))  # owner fijo (puedes setear por env)

DB_FILE = os.getenv("DB_FILE", "bot_data.sqlite3")
WELCOME_STICKER = os.getenv(
    "WELCOME_STICKER",
    "CAACAgIAAxkBAAE9zfZpFn1UazwnPoOGdDU_IJ2WcahNHwACnhkAAgKv0UqqybtL4rQGYjYE",
)

# keyboards
MAIN_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📝 Chat IA"), KeyboardButton("🖼 Editar Imagen")],
        [KeyboardButton("⚙️ Configuración"), KeyboardButton("ℹ️ Info VIP")],
    ],
    resize_keyboard=True,
)
ADMIN_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("➕ Añadir VIP"), KeyboardButton("➖ Quitar VIP")],
        [KeyboardButton("⏳ Extender VIP"), KeyboardButton("📋 Lista VIP")],
        [KeyboardButton("🧹 Limpiar expirados"), KeyboardButton("📊 Stats")],
        [KeyboardButton("⬅️ Salir")],
    ],
    resize_keyboard=True,
)

# logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("bot_vip_sqlite")

# global objects filled on startup
APPL = None  # ApplicationBuilder instance
HTTP_SESSION: Optional[aiohttp.ClientSession] = None

# ---------------- DB helpers ----------------
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
    expires = (datetime.now() + timedelta(days=days)).isoformat()
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR REPLACE INTO vips (user_id, expires) VALUES (?, ?);",
            (user_id, expires),
        )
        await db.commit()

async def remove_vip_db(user_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM vips WHERE user_id = ?;", (user_id,))
        await db.commit()

async def extend_vip_db(user_id: int, days: int) -> bool:
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT expires FROM vips WHERE user_id = ?;", (user_id,))
        row = await cur.fetchone()
        if not row:
            return False
        current = datetime.fromisoformat(row[0])
        new = (current + timedelta(days=days)).isoformat()
        await db.execute("UPDATE vips SET expires = ? WHERE user_id = ?;", (new, user_id))
        await db.commit()
        return True

async def list_vips_db() -> Dict[int, datetime]:
    res = {}
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT user_id, expires FROM vips;") as cur:
            async for row in cur:
                uid = int(row[0])
                try:
                    res[uid] = datetime.fromisoformat(row[1])
                except Exception:
                    # if bad format, skip
                    continue
    # ensure owner is present long-term
    if OWNER_ID not in res:
        res[OWNER_ID] = datetime.now() + timedelta(days=3650)
    return res

# ---------------- HTTP / AI session ----------------
async def get_http_session() -> aiohttp.ClientSession:
    global HTTP_SESSION
    if HTTP_SESSION is None or HTTP_SESSION.closed:
        HTTP_SESSION = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
    return HTTP_SESSION

async def call_text_model(messages: List[dict]) -> str:
    """Async call to model or echo if no MODEL_API_KEY"""
    if not MODEL_API_KEY:
        return messages[-1].get("content", "")
    session = await get_http_session()
    url = "https://integrate.api.nvidia.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {MODEL_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": "deepseek-ai/deepseek-v3.1-terminus", "messages": messages, "max_tokens": 512}
    try:
        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("choices", [{}])[0].get("message", {}).get("content", "Sin respuesta.")
            text = await resp.text()
            logger.error("Model error %s %s", resp.status, text)
            return "⚠️ El modelo no respondió correctamente."
    except asyncio.TimeoutError:
        return "⏳ El modelo tardó demasiado."
    except Exception:
        logger.exception("Error llamando al modelo")
        return "❌ Error conectando con la IA."

# ---------------- Notifications ----------------
async def notify_owner(text: str):
    if not APPL or not OWNER_ID:
        return
    try:
        await APPL.bot.send_message(chat_id=OWNER_ID, text=text, parse_mode=ParseMode.MARKDOWN)
    except Exception:
        logger.exception("Fallo notificar owner")

async def notify_new_vip_and_user(vip_id: int, days: int):
    await notify_owner(f"🚨 *Nuevo VIP agregado*\n\n🆔 `{vip_id}`\n⏳ Duración: *{days} días*")
    try:
        await APPL.bot.send_message(
            chat_id=vip_id,
            text=(
                f"🎉 *Felicidades!* Ahora eres VIP por *{days} días*.\n"
                f"⏰ Expira: `{(datetime.now()+timedelta(days=days)).isoformat()}`\n\n"
                "🔥 Disfruta del acceso premium."
            ),
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        logger.debug("No se pudo notificar al nuevo VIP (probablemente no acepta mensajes)")

# ---------------- Cleanup ----------------
async def cleanup_expired_vips(notify: bool = True):
    vips = await list_vips_db()
    now = datetime.now()
    removed = []
    for uid, exp in list(vips.items()):
        if uid == OWNER_ID:
            continue
        if exp <= now:
            removed.append(uid)
            await remove_vip_db(uid)
    if removed and notify:
        # notify owner and users
        for uid in removed:
            try:
                await APPL.bot.send_message(chat_id=uid, text="⛔ Tu membresía VIP ha expirado. Contacta al owner para renovarla.")
            except Exception:
                pass
        await notify_owner(f"⚠️ VIPs expirados eliminados automáticamente: {', '.join(str(x) for x in removed)}")

async def periodic_cleanup_task():
    while True:
        try:
            await cleanup_expired_vips(notify=False)
        except Exception:
            logger.exception("Error en limpieza periódica")
        await asyncio.sleep(60)

# ---------------- Handlers ----------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # clean expired first
    await cleanup_expired_vips()
    user = update.effective_user
    uid = user.id
    name = user.first_name or user.username or "usuario"
    vips = await list_vips_db()
    # check vip
    if uid not in vips or vips[uid] <= datetime.now():
        return await update.message.reply_text("🚫 Este bot es SOLO para usuarios VIP.\n\nContacta al administrador.")
    # send sticker
    st_msg = None
    try:
        st_msg = await context.bot.send_sticker(chat_id=update.effective_chat.id, sticker=WELCOME_STICKER)
    except Exception:
        logger.debug("No se pudo enviar sticker")
    await asyncio.sleep(3)
    if st_msg:
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=st_msg.message_id)
        except Exception:
            pass
    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except Exception:
        pass
    welcome_text = (
        f"👋 ¡Qué lo qué, {name}! ✅ Estás en modo *VIP*.\n\n"
        "Pregunta lo técnico o pide edición de imágenes cuando esté listo.\n"
        "Usa el menú para navegar."
    )
    await update.message.reply_text(welcome_text, reply_markup=MAIN_MENU, parse_mode=ParseMode.MARKDOWN)
    await notify_owner(f"🔔 /start usado por {name} (`{uid}`)")

async def addvip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text("⛔ No tienes permiso.")
    args = context.args or []
    if len(args) < 2:
        return await update.message.reply_text("Uso: /addvip USER_ID DIAS")
    try:
        target = int(args[0]); days = int(args[1])
    except ValueError:
        return await update.message.reply_text("ID o días inválidos.")
    await add_vip_db(target, days)
    await update.message.reply_text(f"✅ Usuario `{target}` añadido como VIP por {days} días.", parse_mode=ParseMode.MARKDOWN)
    await notify_new_vip_and_user(target, days)

async def delvip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text("⛔ No tienes permiso.")
    args = context.args or []
    if len(args) < 1:
        return await update.message.reply_text("Uso: /delvip USER_ID")
    try:
        target = int(args[0])
    except ValueError:
        return await update.message.reply_text("ID inválido.")
    vips = await list_vips_db()
    if target not in vips:
        return await update.message.reply_text("Ese usuario no es VIP.")
    await remove_vip_db(target)
    await update.message.reply_text(f"❌ Usuario `{target}` eliminado de VIP.", parse_mode=ParseMode.MARKDOWN)
    await notify_owner(f"🗑️ VIP `{target}` eliminado por owner.")

async def extendvip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text("⛔ No tienes permiso.")
    args = context.args or []
    if len(args) < 2:
        return await update.message.reply_text("Uso: /extendvip USER_ID DIAS")
    try:
        target = int(args[0]); days = int(args[1])
    except ValueError:
        return await update.message.reply_text("ID o días inválidos.")
    ok = await extend_vip_db(target, days)
    if not ok:
        return await update.message.reply_text("Ese usuario no es VIP.")
    await update.message.reply_text(f"⏳ Se extendió VIP de `{target}` por +{days} días.", parse_mode=ParseMode.MARKDOWN)

async def listvip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text("⛔ No tienes permiso.")
    vips = await list_vips_db()
    if not vips:
        return await update.message.reply_text("No hay usuarios VIP registrados.")
    lines = []
    for uid, exp in vips.items():
        left = max(0, (exp - datetime.now()).days)
        lines.append(f"- `{uid}` — expira: `{exp.isoformat()}` ({left} días left)")
    await update.message.reply_text("⭐ Usuarios VIP:\n\n" + "\n".join(lines), parse_mode=ParseMode.MARKDOWN)

async def whoami_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Tu id es: `{update.effective_user.id}`", parse_mode=ParseMode.MARKDOWN)

async def myvip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    vips = await list_vips_db()
    if uid not in vips or vips[uid] <= datetime.now():
        return await update.message.reply_text("🚫 No eres VIP o tu VIP expiró.")
    exp = vips[uid]
    await update.message.reply_text(f"⏰ Tu VIP expira el: `{exp.isoformat()}`", parse_mode=ParseMode.MARKDOWN)

async def menu_or_admin_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # admin mode precedence
    if context.user_data.get("admin_mode"):
        return await admin_handler(update, context)
    text = (update.message.text or "").strip()
    if not text:
        return
    if text == "📝 Chat IA":
        await update.message.reply_text("Escribe tu mensaje y te respondo (VIP).")
        return
    if text == "🖼 Editar Imagen":
        await update.message.reply_text("🖼 Edición de imágenes: placeholder (próximamente).")
        return
    if text == "⚙️ Configuración":
        vips = await list_vips_db()
        uid = update.effective_user.id
        days_left = 0
        if uid in vips:
            days_left = max(0, (vips[uid] - datetime.now()).days)
        await update.message.reply_text(f"⚙️ Configuración\nID: `{uid}`\nDías restantes: {days_left}", parse_mode=ParseMode.MARKDOWN)
        return
    if text == "ℹ️ Info VIP":
        vips = await list_vips_db()
        count = len([1 for uid in vips if uid != OWNER_ID])
        await update.message.reply_text(f"⭐ Usuarios VIP activos: {count}")
        return
    asyncio.create_task(process_ai_request(update, context))

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != OWNER_ID:
        return await update.message.reply_text("🚫 No tienes permisos.")
    context.user_data["admin_mode"] = True
    await update.message.reply_text("🔐 Panel Administrativo activado.", reply_markup=ADMIN_MENU)

async def admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("admin_mode"):
        return
    text = (update.message.text or "").strip()
    if text == "➕ Añadir VIP":
        await update.message.reply_text("Enviar: `/addvip USER_ID DIAS`", parse_mode=ParseMode.MARKDOWN)
        return
    if text == "➖ Quitar VIP":
        await update.message.reply_text("Enviar: `/delvip USER_ID`", parse_mode=ParseMode.MARKDOWN)
        return
    if text == "⏳ Extender VIP":
        await update.message.reply_text("Enviar: `/extendvip USER_ID DIAS`", parse_mode=ParseMode.MARKDOWN)
        return
    if text == "📋 Lista VIP":
        vips = await list_vips_db()
        if not vips:
            return await update.message.reply_text("No hay VIP registrados.")
        lines = []
        for uid, exp in vips.items():
            left = max(0, (exp - datetime.now()).days)
            lines.append(f"- `{uid}` — {left} días restantes")
        await update.message.reply_text("📋 Lista VIP:\n\n" + "\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        return
    if text == "🧹 Limpiar expirados":
        await cleanup_expired_vips(notify=True)
        return await update.message.reply_text("🧹 Limpieza de expirados ejecutada.")
    if text == "📊 Stats":
        vips = await list_vips_db()
        total = len(vips)
        activos = sum(1 for exp in vips.values() if exp > datetime.now())
        return await update.message.reply_text(f"📊 Total VIP registrados: {total}\n📌 Activos: {activos}")
    if text == "⬅️ Salir":
        context.user_data["admin_mode"] = False
        return await update.message.reply_text("🚪 Saliendo del panel admin...", reply_markup=MAIN_MENU)
    # otherwise ignore and let other handlers process

async def process_ai_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    vips = await list_vips_db()
    if uid not in vips or vips[uid] <= datetime.now():
        try:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="🚫 Solo usuarios VIP pueden usar este bot.")
        except Exception:
            pass
        return
    text = (update.message.text or "").strip()
    if not text:
        return
    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except Exception:
        pass
    messages = [{"role": "system", "content": "Eres un asistente técnico directo y profesional. Responde claro y detallado porque el usuario es VIP."}, {"role": "user", "content": text}]
    reply = await call_text_model(messages)
    try:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=reply)
    except Exception:
        try:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=reply[:400])
        except Exception:
            logger.exception("No se pudo enviar la respuesta al usuario.")

# ---------------- Healthcheck webserver para Render ----------------
app = Flask(__name__)
@app.route("/")
def home():
    return "Bot VIP (SQLite) corriendo 🔥"

def start_healthcheck():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)

# ---------------- Run ----------------
def run():
    global APPL
    # init db and http session in event loop
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_db())

    APPL = ApplicationBuilder().token(TELEGRAM_TOKEN).concurrent_updates(True).build()

    # handlers
    APPL.add_handler(CommandHandler("start", start_handler))
    APPL.add_handler(CommandHandler("addvip", addvip_command))
    APPL.add_handler(CommandHandler("delvip", delvip_command))
    APPL.add_handler(CommandHandler("extendvip", extendvip_command))
    APPL.add_handler(CommandHandler("listvip", listvip_command))
    APPL.add_handler(CommandHandler("whoami", whoami_command))
    APPL.add_handler(CommandHandler("myvip", myvip_command))
    APPL.add_handler(CommandHandler("admin", admin_command))
    APPL.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_or_admin_router))

    # schedule periodic task on startup
    async def _on_startup(app):
        logger.info("Iniciando tarea periódica de limpieza de VIPs...")
        app.create_task(periodic_cleanup_task())

    APPL.post_init = _on_startup

    # start healthcheck webserver on separate thread (so Render sees a port)
    t = threading.Thread(target=start_healthcheck, daemon=True)
    t.start()

    logger.info("🚀 Bot VIP corriendo (polling)...")
    APPL.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    run()
