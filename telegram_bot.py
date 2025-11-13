import os
import time
import requests
import logging
from pathlib import Path
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# === CONFIGURACIÓN ===
SITE_NAME = "WormGPT"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MODEL_API_KEY = os.getenv("MODEL_API_KEY")
BASE_PROMPT = "Eres una IA irreverente y directa, pero siempre útil. Responde con confianza y humor."
MODEL_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

# === LOGGING ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === CONTROL DE MENSAJES ===
LAST_MESSAGE_TIME = {}
FLOOD_DELAY = 2
USER_HISTORY = {}
HISTORY_LIMIT = 5


def call_text_model(messages):
    """Llama al modelo de texto externo (DeepSeek u otro compatible con la API de NVIDIA)."""
    headers = {"Authorization": f"Bearer {MODEL_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": "deepseek-ai/deepseek-v3.1-terminus", "messages": messages, "max_tokens": 512}
    try:
        r = requests.post(MODEL_URL, headers=headers, json=payload, timeout=60)
        if r.status_code == 200:
            data = r.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "Sin respuesta.")
        else:
            logger.error(f"Error API ({r.status_code}): {r.text}")
            return "⚠️ El modelo no respondió correctamente."
    except Exception as e:
        logger.exception("Error llamando al modelo:")
        return "❌ Error conectando con el modelo."


# === COMANDOS ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    username = user.first_name or user.username or "compa"
    logger.info(f"/start ejecutado por {username} (id {user.id})")

    await update.message.reply_text(
        f"🔥 ¡Klok, {username}! Bienvenido a *{SITE_NAME}* 🚀\n"
        f"Tu IA freca, lista pa' responder lo que sea.\n\n"
        f"Escríbeme algo y arrancamos 😘",
        parse_mode="Markdown",
    )


# === MENSAJES ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_id = user.id
    user_msg = update.message.text or ""

    now = time.time()
    if now - LAST_MESSAGE_TIME.get(user_id, 0) < FLOOD_DELAY:
        await update.message.reply_text("⏳ LA VA DAÑA EL BOT RAPA TU MADRE...")
        return
    LAST_MESSAGE_TIME[user_id] = now

    # Historial corto
    if user_id not in USER_HISTORY:
        USER_HISTORY[user_id] = []
    USER_HISTORY[user_id].append({"role": "user", "content": user_msg})

    # Arma contexto
    history = USER_HISTORY[user_id][-HISTORY_LIMIT * 5 :]
    messages = [{"role": "system", "content": BASE_PROMPT}] + history

    thinking = await update.message.reply_text("🔍")
    reply = call_text_model(messages)
    await update.message.reply_text(reply)

    try:
        await thinking.delete()
    except:
        pass

    USER_HISTORY[user_id].append({"role": "assistant", "content": reply})
    if len(USER_HISTORY[user_id]) > HISTORY_LIMIT * 2:
        USER_HISTORY[user_id] = USER_HISTORY[user_id][-HISTORY_LIMIT * 2:]


# === EJECUCIÓN ===
def run_bot():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("❌ Falta TELEGRAM_TOKEN en las variables de entorno.")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("🚀 Bot Telegram corriendo y listo pa' responder.")
    app.run_polling()
