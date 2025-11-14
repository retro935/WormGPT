# bot_vip_full.py
import os
import sqlite3
import json
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional

import requests
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
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
MODEL_API_KEY = os.getenv("MODEL_API_KEY")  # NVIDIA / DeepSeek API key
OWNER_ID = int(os.getenv("OWNER_ID", "6699273462"))

DB_PATH = os.getenv("DB_PATH", "bot_vip.sqlite3")
SYSTEM_PROMPTS_FILE = "system-prompts.txt"

# Endpoint (ajusta si tu proveedor lo pide diferente)
MODEL_API_URL = os.getenv("MODEL_API_URL", "https://integrate.api.nvidia.com/v1")

# Sticker to show WHILE the AI is thinking (you provided this)
THINKING_STICKER = os.getenv(
    "THINKING_STICKER",
    "CAACAgEAAxkBAAE90AJpFtQXZ4J90fBT2-R3oBJqi6IUewACrwIAAphXIUS8lNoZG4P3rDYE",
)

# Models available (solo deepseek + editor, como pediste)
AVAILABLE_MODELS = {
    "deepseek": "deepseek-ai/deepseek-v3.1",   # chat model
    "editor": "deepseek-ai/deepseek-editor"    # ejemplo de modelo de edición (ajusta nombre si lo tienes)
}

DEFAULT_MODEL_KEY = "deepseek"  # key in AVAILABLE_MODELS

# Keyboards
MAIN_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📝 Chat IA"), KeyboardButton("🖼 Editar Imagen")],
        [KeyboardButton("⚙️ Configuración"), KeyboardButton("ℹ️ Info VIP")],
    ],
    resize_keyboard=True,
)

# Admin keyboard (inline uses callback)
ADMIN_MENU = InlineKeyboardMarkup(
    [
        [InlineKeyboardButton("➕ Añadir VIP", callback_data="admin_addvip_prompt"),
         InlineKeyboardButton("🗑 Limpiar expirados", callback_data="admin_clean")],
        [InlineKeyboardButton("📋 Lista VIP", callback_data="admin_list")],
    ]
)

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("bot_vip_full")

# ---------------- DB ----------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # VIPs table: user_id, expires_iso
    cur.execute("""
    CREATE TABLE IF NOT EXISTS vips (
        user_id INTEGER PRIMARY KEY,
        expires TEXT NOT NULL
    )
    """)
    # user prefs: model_key
    cur.execute("""
    CREATE TABLE IF NOT EXISTS user_prefs (
        user_id INTEGER PRIMARY KEY,
        model_key TEXT NOT NULL
    )
    """)
    conn.commit()
    conn.close()

def add_vip_db(user_id: int, days: int):
    expires = (datetime.utcnow() + timedelta(days=days)).isoformat()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO vips (user_id, expires) VALUES (?, ?)", (user_id, expires))
    conn.commit()
    conn.close()
    return expires

def remove_vip_db(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM vips WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

def list_vips_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id, expires FROM vips")
    rows = cur.fetchall()
    conn.close()
    out = {}
    for uid, expires in rows:
        try:
            out[int(uid)] = datetime.fromisoformat(expires)
        except:
            continue
    # ensure owner present
    if OWNER_ID not in out:
        out[OWNER_ID] = datetime.utcnow() + timedelta(days=3650)
    return out

def is_vip_db(user_id: int) -> bool:
    vips = list_vips_db()
    if user_id in vips and vips[user_id] > datetime.utcnow():
        return True
    return False

def set_user_model(user_id: int, model_key: str):
    if model_key not in AVAILABLE_MODELS:
        raise ValueError("Modelo no disponible.")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO user_prefs (user_id, model_key) VALUES (?, ?)", (user_id, model_key))
    conn.commit()
    conn.close()

def get_user_model(user_id: int) -> str:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT model_key FROM user_prefs WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if row and row[0] in AVAILABLE_MODELS:
        return row[0]
    return DEFAULT_MODEL_KEY

# ---------------- Helpers ----------------
def load_system_prompt() -> str:
    if os.path.exists(SYSTEM_PROMPTS_FILE):
        try:
            with open(SYSTEM_PROMPTS_FILE, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            return ""
    return ""

async def notify_owner(text: str):
    try:
        app = APPLICATION  # set later
        if app:
            await app.bot.send_message(chat_id=OWNER_ID, text=text)
    except Exception:
        logger.debug("No se pudo notificar al owner", exc_info=True)

# ---------------- AI call (blocking -> run in thread) ----------------
def _call_model_blocking(model_name: str, messages: list) -> dict:
    """
    Hace la petición HTTP a la API del modelo (NVIDIA/DeepSeek).
    Devuelve el JSON sin exponer razonamiento al usuario.
    """
    headers = {
        "Authorization": f"Bearer {MODEL_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_name,
        "messages": messages,
        # no pedimos campos de razonamiento para que no se vuelvan visibles;
        # si la API devuelve razonamiento internamente, lo almacenamos localmente y NO lo enviamos.
        "max_tokens": 1500,
        "temperature": 0.2,
        "top_p": 0.7,
        "stream": False,
    }
    r = requests.post(MODEL_API_URL, json=payload, headers=headers, timeout=120)
    r.raise_for_status()
    return r.json()

async def ask_model_for_user(user_id: int, user_prompt: str) -> str:
    """
    Construye messages con system prompt y llama al modelo en un executor.
    Oculta el razonamiento (no se envía).
    """
    system_prompt = load_system_prompt()
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})

    model_key = get_user_model(user_id)
    model_name = AVAILABLE_MODELS.get(model_key, list(AVAILABLE_MODELS.values())[0])

    loop = asyncio.get_running_loop()
    try:
        resp_json = await loop.run_in_executor(None, _call_model_blocking, model_name, messages)
    except Exception as e:
        logger.exception("Error llamando al modelo")
        return "❌ Error conectando con la IA."

    # Parse response robustamente (depende de proveedor)
    try:
        # Standard OpenAI-like response parsing
        choice = resp_json.get("choices", [{}])[0]
        # some SDKs return 'message' dict, others 'text'
        message = choice.get("message") or {}
        content = message.get("content") or choice.get("text") or ""
        # razonamiento interno en otras claves -> lo ignoramos completamente
        return content or "Sin respuesta del modelo."
    except Exception:
        logger.exception("Error parseando respuesta del modelo")
        return "❌ Error procesando la respuesta del modelo."

# ---------------- Periodic cleanup ----------------
async def cleanup_task():
    while True:
        try:
            vips = list_vips_db()
            now = datetime.utcnow()
            removed = []
            for uid, exp in list(vips.items()):
                if uid == OWNER_ID:
                    continue
                if exp <= now:
                    remove_vip_db(uid)
                    removed.append(uid)
            if removed:
                await notify_owner(f"⚠️ VIPs expirados eliminados: {removed}")
        except Exception:
            logger.exception("Error en limpieza periódica")
        await asyncio.sleep(60 * 5)  # cada 5 minutos

# ---------------- Handlers ----------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    # Mostrar bienvenida si VIP, si no mensaje con instrucciones
    if not is_vip_db(uid):
        await update.message.reply_text(
            "🚫 Este bot es solo para VIPs.\nContacta al owner para que te active con /addvip <id> <días>."
        )
        return

    await update.message.reply_text(
        f"👋 Hola {user.first_name or user.username} — bienvenido al bot VIP.\n"
        "Usa el menú o escribe tu pregunta."
    )
    await update.message.reply_text("Menú:", reply_markup=MAIN_MENU)

async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Menú principal:", reply_markup=MAIN_MENU)

# Admin commands
async def addvip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text("⛔ No autorizado.")
    args = context.args or []
    if len(args) < 2:
        return await update.message.reply_text("Uso: /addvip <user_id> <dias>")
    try:
        target = int(args[0]); days = int(args[1])
    except:
        return await update.message.reply_text("ID o días inválidos.")
    expires = add_vip_db(target, days)
    await update.message.reply_text(f"✅ Usuario {target} VIP por {days} días (hasta {expires}).")
    # notify target (intentar)
    try:
        await context.bot.send_message(chat_id=target, text=f"🎉 Has sido activado como VIP por {days} días. Disfruta.")
    except Exception:
        logger.debug("No se pudo notificar al usuario (probablemente no abrió chat).")
    await notify_owner(f"🔔 Añadido VIP {target} por {days} días por owner.")

async def delvip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text("⛔ No autorizado.")
    args = context.args or []
    if len(args) < 1:
        return await update.message.reply_text("Uso: /delvip <user_id>")
    try:
        target = int(args[0])
    except:
        return await update.message.reply_text("ID inválido.")
    remove_vip_db(target)
    await update.message.reply_text(f"❌ VIP {target} eliminado.")
    await notify_owner(f"🗑 VIP {target} eliminado por owner.")

async def listvip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text("⛔ No autorizado.")
    vips = list_vips_db()
    if not vips:
        return await update.message.reply_text("No hay VIPs.")
    lines = []
    for uid, exp in vips.items():
        left = max(0, (exp - datetime.utcnow()).days)
        lines.append(f"- `{uid}` — expira {exp.isoformat()} ({left} días left)")
    await update.message.reply_text("⭐ VIPs:\n" + "\n".join(lines))

async def myvip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    vips = list_vips_db()
    if uid not in vips:
        return await update.message.reply_text("🚫 No eres VIP.")
    exp = vips[uid]
    await update.message.reply_text(f"⏰ Tu VIP expira: {exp.isoformat()}")

# Model menu (inline)
async def model_menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    kb = [
        [InlineKeyboardButton("🔹 DeepSeek (chat)", callback_data=f"setmodel:deepseek")],
        [InlineKeyboardButton("🔹 Editor (edición)", callback_data=f"setmodel:editor")],
        [InlineKeyboardButton("🔸 Mostrar modelo actual", callback_data=f"showmodel")],
    ]
    await update.message.reply_text("Selecciona modelo:", reply_markup=InlineKeyboardMarkup(kb))

async def callback_queries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    if data.startswith("setmodel:"):
        key = data.split(":", 1)[1]
        if key not in AVAILABLE_MODELS:
            return await q.edit_message_text("Modelo no disponible.")
        set_user_model(q.from_user.id, key)
        await q.edit_message_text(f"✅ Modelo cambiado a *{key}*.", parse_mode="Markdown")
        return

    if data == "showmodel":
        key = get_user_model(q.from_user.id)
        await q.edit_message_text(f"Modelo actual: *{key}*  → `{AVAILABLE_MODELS[key]}`", parse_mode="Markdown")
        return

    if data == "admin_clean":
        if q.from_user.id != OWNER_ID:
            return await q.edit_message_text("⛔ No autorizado.")
        # limpiar ahora
        vips = list_vips_db()
        now = datetime.utcnow()
        removed = []
        for uid, exp in list(vips.items()):
            if uid == OWNER_ID:
                continue
            if exp <= now:
                remove_vip_db(uid)
                removed.append(uid)
        await q.edit_message_text(f"🧹 Limpieza ejecutada. Eliminados: {removed}")
        return

    if data == "admin_list":
        if q.from_user.id != OWNER_ID:
            return await q.edit_message_text("⛔ No autorizado.")
        vips = list_vips_db()
        s = "\n".join([f"- `{u}`: {e.isoformat()}" for u,e in vips.items()])
        await q.edit_message_text("📋 VIPs:\n" + (s or "ninguno"))

# AI processing: user types message -> bot sends THINKING_STICKER, calls model, deletes sticker, sends answer.
async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_vip_db(uid):
        return await update.message.reply_text("🚫 Solo VIP pueden usar este bot.")

    prompt = (update.message.text or "").strip()
    if not prompt:
        return

    chat_id = update.effective_chat.id

    # send thinking sticker and keep its message_id to delete later
    thinking_msg = None
    try:
        thinking_msg = await context.bot.send_sticker(chat_id=chat_id, sticker=THINKING_STICKER)
    except Exception:
        thinking_msg = None

    # call model (async)
    reply_text = await ask_model_for_user(uid, prompt)

    # delete sticker if we can
    if thinking_msg:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=thinking_msg.message_id)
        except Exception:
            pass

    # send response (split if very long)
    try:
        await context.bot.send_message(chat_id=chat_id, text=reply_text)
    except Exception:
        # fallback: chunk
        max_len = 4000
        for i in range(0, len(reply_text), max_len):
            try:
                await context.bot.send_message(chat_id=chat_id, text=reply_text[i:i+max_len])
            except Exception:
                break

# Image handler placeholder (calls editor model if wanted)
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_vip_db(uid):
        return await update.message.reply_text("🚫 Solo VIP pueden usar este bot.")
    await update.message.reply_text("🖼 Recibí tu imagen. Módulo de edición (demo) — próximamente integración real.")

# ---------------- Startup & run ----------------
APPLICATION = None

def build_application():
    global APPLICATION
    init_db()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    # comandos
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CommandHandler("addvip", addvip_cmd))
    app.add_handler(CommandHandler("delvip", delvip_cmd))
    app.add_handler(CommandHandler("listvip", listvip_cmd))
    app.add_handler(CommandHandler("myvip", myvip_cmd))
    app.add_handler(CommandHandler("model", model_menu_cmd))
    # callbacks
    app.add_handler(CallbackQueryHandler(callback_queries))
    # messages
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    # inicio tareas
    async def _on_startup(application):
        # start cleanup task
        application.create_task(cleanup_task())
        logger.info("Tarea de limpieza periódica iniciada.")
    app.post_init = _on_startup

    APPLICATION = app
    return app

def main():
    app = build_application()
    # quitar updates pendientes para evitar conflictos si reinicias frecuentemente
    logger.info("Iniciando bot (polling).")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
