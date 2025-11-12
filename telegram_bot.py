import os
import requests
import time
import asyncio
from pathlib import Path
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
import logging

# === Logging ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === Config / Env ===
PROMPT_FILE = "system-prompt.txt"
MODEL_CONFIG = {
    "name": "deepseek-ai/deepseek-v3.1-terminus",
    "base_url": "https://integrate.api.nvidia.com/v1",
}
SITE_NAME = "WormGPT (Research Assistant)"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
LAST_MESSAGE_TIME = {}
FLOOD_DELAY = 3  # seconds

# === Load base prompt ===
if Path(PROMPT_FILE).exists():
    BASE_PROMPT = Path(PROMPT_FILE).read_text(encoding="utf-8").strip()
else:
    BASE_PROMPT = "You are WormGPT running on Telegram."

# === Optional: langdetect ===
try:
    from langdetect import detect
except Exception:
    detect = None


# === Utility: persistent typing indicator ===
async def show_typing(bot, chat_id, stop_event):
    """Mantiene visible la animación 'escribiendo...' hasta que stop_event se activa."""
    try:
        while not stop_event.is_set():
            await bot.send_chat_action(chat_id=chat_id, action="typing")
            await asyncio.sleep(4)  # Telegram expira cada ~5 segundos, así que la reenvíamos periódicamente
    except Exception:
        pass


# === Language detection ===
def get_user_lang(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return "en"
    if detect:
        try:
            return detect(text)
        except Exception:
            pass
    lowered = text.lower()
    if any(w in lowered for w in [" el ", " la ", " que ", "hola", "¿", "¡"]):
        return "es"
    if any(w in lowered for w in [" dan ", " yang ", " itu ", " saya "]):
        return "id"
    return "en"


# === System prompt by language ===
def make_system_prompt(lang: str) -> str:
    if lang.startswith("es"):
        header = "Eres Wormgpt asistente útil, racional y directo. Responde en español.\n\n"
    else:
        header = "You are a wormgpt, direct and intelligent assistant.\n\n"
    return header + BASE_PROMPT


# === Call model ===
def call_model(messages):
    api_key = os.getenv("MODEL_API_KEY")
    if not api_key:
        return "❌ Falta la variable de entorno MODEL_API_KEY."

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": MODEL_CONFIG["name"], "messages": messages, "max_tokens": 512}

    try:
        r = requests.post(
            f"{MODEL_CONFIG['base_url']}/chat/completions",
            headers=headers,
            json=payload,
            timeout=45,
        )
        if r.status_code != 200:
            return f"⚠️ Error API ({r.status_code}): {r.text}"
        data = r.json()
        return data.get("choices", [{}])[0].get("message", {}).get("content", "(sin respuesta)")
    except Exception as e:
        logger.exception("Error en llamada a modelo")
        return f"❌ Error en la solicitud: {e}"


# === /start handler ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    username = user.first_name or user.username or "usuario"

    lang = get_user_lang(username)
    system_prompt = make_system_prompt(lang)
    chat_id = update.effective_chat.id

    stop_event = asyncio.Event()
    typing_task = asyncio.create_task(show_typing(context.bot, chat_id, stop_event))

    user_prompt = (
        f"Da una bienvenida natural, cálida pero profesional a {username}, "
        f"mencionando que este es un asistente basado en IA llamado {SITE_NAME}."
    )

    reply = call_model(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
    )

    stop_event.set()
    await typing_task
    await update.message.reply_text(reply)


# === Handle normal messages ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_id = user.id
    user_msg = update.message.text or ""
    chat_id = update.effective_chat.id
    chat_type = update.message.chat.type

    now = time.time()
    if now - LAST_MESSAGE_TIME.get(user_id, 0) < FLOOD_DELAY:
        await update.message.reply_text("⏳ Espera un momento antes de enviar otro mensaje...")
        return
    LAST_MESSAGE_TIME[user_id] = now

    if chat_type in ["group", "supergroup"]:
        if not user_msg.startswith("/") and f"@{context.bot.username}" not in user_msg:
            return

    lang = get_user_lang(user_msg)
    system_prompt = make_system_prompt(lang)

    stop_event = asyncio.Event()
    typing_task = asyncio.create_task(show_typing(context.bot, chat_id, stop_event))

    reply = call_model(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ]
    )

    stop_event.set()
    await typing_task
    await update.message.reply_text(reply)


# === /setlang (placeholder) ===
async def setlang_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("El idioma se detecta automáticamente, no hace falta configurarlo.")


# === Main Runner ===
def run_bot():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("❌ Falta TELEGRAM_TOKEN en las variables de entorno.")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setlang", setlang_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Bot en marcha...")
    app.run_polling()


if __name__ == "__main__":
    run_bot()
