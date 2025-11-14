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

# FIX: ParseMode ahora se importa desde telegram.constants
from telegram.constants import ParseMode

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
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
OWNER_ID = int(os.getenv("OWNER_ID", "6699273462"))

DB_FILE = os.getenv("DB_FILE", "bot_data.sqlite3")
WELCOME_STICKER = os.getenv(
    "WELCOME_STICKER",
    "CAACAgIAAxkBAAE9zfZpFn1UazwnPoOGdDU_IJ2WcahNHwACnhkAAgKv0UqqybtL4rQGYjYE",
)

# Keyboards
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

# global
APPL = None
HTTP_SESSION: Optional[aiohttp.ClientSession] = None

# ---------------- DB ----------------
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
            "INSERT OR REPLACE INTO vips (user_id, expires) VALUES (?, ?)",
            (user_id, expires),
        )
        await db.commit()

async def remove_vip_db(user_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM vips WHERE user_id = ?", (user_id,))
        await db.commit()

async def extend_vip_db(user_id: int, days: int) -> bool:
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT expires FROM vips WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        if not row:
            return False
        current = datetime.fromisoformat(row[0])
        new = current + timedelta(days=days)
        await db.execute("UPDATE vips SET expires = ? WHERE user_id = ?", (new.isoformat(), user_id))
        await db.commit()
        return True

async def list_vips_db() -> Dict[int, datetime]:
    out = {}
    async with aiosqlite.connect(DB_FILE) as db:
        async with db.execute("SELECT user_id, expires FROM vips") as cur:
            async for uid, exp in cur:
                try:
                    out[int(uid)] = datetime.fromisoformat(exp)
                except:
                    continue
    if OWNER_ID not in out:
        out[OWNER_ID] = datetime.now() + timedelta(days=3650)
    return out

# ---------------- IA ----------------
async def get_http_session():
    global HTTP_SESSION
    if HTTP_SESSION is None or HTTP_SESSION.closed:
        HTTP_SESSION = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
    return HTTP_SESSION

async def call_text_model(messages: List[dict]) -> str:
    if not MODEL_API_KEY:
        return messages[-1].get("content", "")

    session = await get_http_session()
    url = "https://integrate.api.nvidia.com/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {MODEL_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "deepseek-ai/deepseek-v3.1-terminus",
        "messages": messages,
        "max_tokens": 512,
    }

    try:
        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data["choices"][0]["message"]["content"]
            return "⚠️ Error del modelo."
    except:
        return "❌ Error conectando con la IA."

# ---------------- Notificaciones ----------------
async def notify_owner(text: str):
    if not APPL:
        return
    try:
        await APPL.bot.send_message(chat_id=OWNER_ID, text=text, parse_mode=ParseMode.MARKDOWN)
    except:
        pass

async def notify_new_vip(vip_id: int, days: int):
    await notify_owner(f"🔥 Nuevo VIP: `{vip_id}` por {days} días.")
    try:
        await APPL.bot.send_message(
            chat_id=vip_id,
            text=f"🎉 Ahora eres VIP por {days} días.\nDisfruta!",
            parse_mode=ParseMode.MARKDOWN,
        )
    except:
        pass

# ---------------- Limpieza automática ----------------
async def cleanup_expired():
    vips = await list_vips_db()
    now = datetime.now()
    removed = []
    for uid, exp in vips.items():
        if uid == OWNER_ID:
            continue
        if exp <= now:
            removed.append(uid)
            await remove_vip_db(uid)

    if removed:
        await notify_owner(f"♻️ VIPs expirados: {removed}")

async def periodic_cleanup_task():
    while True:
        try:
            await cleanup_expired()
        except:
            pass
        await asyncio.sleep(60)

# ---------------- Handlers ----------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cleanup_expired()
    user = update.effective_user
    uid = user.id

    vips = await list_vips_db()
    if uid not in vips or vips[uid] <= datetime.now():
        return await update.message.reply_text("🚫 Solo VIP. Contacta al admin.")

    # sticker
    try:
        st = await update.message.reply_sticker(WELCOME_STICKER)
        await asyncio.sleep(2)
        await context.bot.delete_message(chat_id=uid, message_id=st.message_id)
    except:
        pass

    await update.message.reply_text(
        f"👋 Bienvenido {user.first_name}, eres VIP.\nUsa el menú.",
        reply_markup=MAIN_MENU,
        parse_mode=ParseMode.MARKDOWN,
    )
    await notify_owner(f"🔔 /start de `{uid}`")

async def addvip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text("⛔ No autorizado.")
    if len(context.args) < 2:
        return await update.message.reply_text("Uso: /addvip ID DIAS")

    uid = int(context.args[0])
    dias = int(context.args[1])
    await add_vip_db(uid, dias)
    await update.message.reply_text("✔ VIP agregado.")
    await notify_new_vip(uid, dias)

async def delvip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text("⛔ No autorizado.")
    if len(context.args) < 1:
        return await update.message.reply_text("Uso: /delvip ID")

    uid = int(context.args[0])
    await remove_vip_db(uid)
    await update.message.reply_text("🗑 VIP eliminado.")

async def extendvip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    if len(context.args) < 2:
        return await update.message.reply_text("Uso: /extendvip ID DIAS")

    uid = int(context.args[0])
    dias = int(context.args[1])
    ok = await extend_vip_db(uid, dias)
    if not ok:
        return await update.message.reply_text("No era VIP.")
    await update.message.reply_text("⏳ VIP extendido.")

async def listvip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return
    vip = await list_vips_db()
    txt = "⭐ VIPs:\n\n"
    for uid, exp in vip.items():
        left = (exp - datetime.now()).days
        txt += f"`{uid}` — {left} días\n"
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "📝 Chat IA":
        return await update.message.reply_text("Escribe tu mensaje para la IA.")

    if text == "🖼 Editar Imagen":
        return await update.message.reply_text("Edición de imágenes pronto disponible.")

    if text == "⚙️ Configuración":
        uid = update.effective_user.id
        vip = await list_vips_db()
        left = max(0, (vip[uid] - datetime.now()).days)
        return await update.message.reply_text(
            f"⚙️ Configuración\nID: `{uid}`\nDías VIP restantes: {left}",
            parse_mode=ParseMode.MARKDOWN,
        )

    if text == "ℹ️ Info VIP":
        vip = await list_vips_db()
        count = len(vip) - 1
        return await update.message.reply_text(f"VIP activos: {count}")

    asyncio.create_task(process_ai(update, context))

async def process_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    vip = await list_vips_db()

    if uid not in vip or vip[uid] <= datetime.now():
        return await update.message.reply_text("🚫 Solo VIP.")

    prompt = update.message.text

    await context.bot.send_chat_action(chat_id=uid, action="typing")

    messages = [
        {"role": "system", "content": "Eres un asistente VIP técnico, preciso y directo."},
        {"role": "user", "content": prompt},
    ]

    r = await call_text_model(messages)
    await update.message.reply_text(r)

# ---------------- Webserver ----------------
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot VIP activo."

def start_web():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)

# ---------------- Run ----------------
def run():
    global APPL
    asyncio.get_event_loop().run_until_complete(init_db())

    APPL = ApplicationBuilder().token(TELEGRAM_TOKEN).concurrent_updates(True).build()

    APPL.add_handler(CommandHandler("start", start_handler))
    APPL.add_handler(CommandHandler("addvip", addvip))
    APPL.add_handler(CommandHandler("delvip", delvip))
    APPL.add_handler(CommandHandler("extendvip", extendvip))
    APPL.add_handler(CommandHandler("listvip", listvip))

    APPL.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_router))

    async def on_start(app):
        app.create_task(periodic_cleanup_task())

    APPL.post_init = on_start

    threading.Thread(target=start_web, daemon=True).start()

    APPL.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    run()
