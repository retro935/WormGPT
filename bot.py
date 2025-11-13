# bot.py — versión final sincronizada (Render + bienvenida hacker + respuesta corta/larga)
import os
import time
import random
import logging
import requests
import threading
import http.server
import socketserver
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# ---------------- CONFIG ----------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MODEL_API_KEY = os.getenv("MODEL_API_KEY")
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID")

# ---------------- LOGGING ----------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("wormgpt")

# ---------------- ESTADO ----------------
LAST_MESSAGE_TIME = {}
FLOOD_DELAY = 15
USER_HISTORY = {}
HISTORY_LIMIT = 6

# ---------------- PROMPT DEL SISTEMA ----------------
def load_system_prompt():
    try:
        with open("system-prompt.txt", "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return (
            "Eres un asistente técnico breve, directo y profesional. "
            "Da respuestas cortas por defecto. Si el usuario pide mucha información o dice algo como "
            "'explícame a fondo', 'detallado', 'paso a paso' o 'versión larga', entonces da una respuesta extensa."
        )

SYSTEM_PROMPT = load_system_prompt()

# ---------------- HELPERS ----------------
def send_log_to_channel(text: str):
    if not LOG_CHANNEL_ID or not TELEGRAM_TOKEN:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": LOG_CHANNEL_ID, "text": text, "parse_mode": "Markdown"},
            timeout=5,
        )
    except Exception as e:
        logger.debug(f"No se pudo enviar log: {e}")

def call_text_model(messages):
    if not MODEL_API_KEY:
        return f"Echo: {messages[-1].get('content', '')}"
    try:
        url = "https://integrate.api.nvidia.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {MODEL_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": "deepseek-ai/deepseek-v3.1-terminus",
            "messages": messages,
        }
        r = requests.post(url, headers=headers, json=payload, timeout=120)
        if r.status_code == 200:
            data = r.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "Sin respuesta.")
        else:
            logger.error(f"Error modelo ({r.status_code}): {r.text}")
            return "⚠️ El modelo no respondió correctamente."
    except Exception as e:
        logger.exception("Error conectando con el modelo:")
        return "❌ Error conectando con el modelo."

# ---------------- HANDLERS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.first_name or user.username or "compa"

    # 🎭 Stickers hacker (puedes añadir más)
    hacker_stickers = [
        "CAACAgEAAxkBAAE9zB9pFkOMb_-sMrVy658qUeF9Papp_AACEgUAAkpOqEYkPgc0t0i53jYE",  # tu sticker hacker
    ]
    sticker_id = random.choice(hacker_stickers)

    # Enviar sticker
    sticker_msg = await update.message.reply_sticker(sticker=sticker_id)

    # ⏳ Efecto progresivo visual (3..2..1)
    countdown = await update.message.reply_text("3️⃣")
    for step in ["2️⃣", "1️⃣"]:
        await asyncio.sleep(0.7)
        await countdown.edit_text(step)
    await asyncio.sleep(0.6)

    # 💬 Bienvenida moderna (mientras borra los anteriores)
    welcome_text = (
        f"👋 ¡Hola {name}!\n\n"
        "💻 Bienvenido al bot — totalmente operativo y listo para asistirte. "
        "Pide código, ideas o información técnica y te respondo en segundos ⚡"
    )

    # Borrar al mismo tiempo
    await asyncio.gather(
        sticker_msg.delete(),
        countdown.delete(),
        update.message.reply_text(welcome_text, parse_mode="Markdown"),
    )

    send_log_to_channel(f"🟢 /start por {name} ({user.id})")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    user_msg = (update.message.text or "").strip()
    if not user_msg:
        return

    now = time.time()
    if now - LAST_MESSAGE_TIME.get(uid, 0) < FLOOD_DELAY:
        await update.message.reply_text("⏳ Espera un momento, estoy pensando...")
        return
    LAST_MESSAGE_TIME[uid] = now

    USER_HISTORY.setdefault(uid, []).append({"role": "user", "content": user_msg})
    if len(USER_HISTORY[uid]) > HISTORY_LIMIT * 2:
        USER_HISTORY[uid] = USER_HISTORY[uid][-HISTORY_LIMIT * 2 :]

    messages = (
        [{"role": "system", "content": SYSTEM_PROMPT}]
        + USER_HISTORY[uid][-HISTORY_LIMIT:]
        + [{"role": "user", "content": user_msg}]
    )

    thinking = await update.message.reply_text("💭 Pensando...")
    reply = call_text_model(messages)

    if "```" in reply:
        await thinking.edit_text(reply, parse_mode="Markdown")
    else:
        await thinking.edit_text(reply)

    USER_HISTORY[uid].append({"role": "assistant", "content": reply})
    send_log_to_channel(f"👤 {user.first_name or user.username}:\n❓ {user_msg}\n💬 {reply[:500]}")

# ---------------- RUN ----------------
def run_bot():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("❌ Falta TELEGRAM_TOKEN en variables de entorno.")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("🚀 Bot arrancando (polling)...")
    app.run_polling()

# ---------------- FAKE SERVER PARA RENDER ----------------
def fake_server():
    PORT = int(os.environ.get("PORT", 8080))
    Handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        logger.info(f"🌀 Fake server listening on port {PORT}")
        httpd.serve_forever()

if __name__ == "__main__":
    threading.Thread(target=fake_server, daemon=True).start()
    run_bot()
