import os
import requests
import time
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

# === Configuración ===
PROMPT_FILE = "system-prompt.txt"
MODEL_CONFIG = {
    "name": "deepseek-ai/deepseek-v3.1-terminus",
    "base_url": "https://integrate.api.nvidia.com/v1",
}
SITE_NAME = "WormGPT by Retro™"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
LAST_MESSAGE_TIME = {}
FLOOD_DELAY = 3

# === Base prompt ===
if Path(PROMPT_FILE).exists():
    BASE_PROMPT = Path(PROMPT_FILE).read_text(encoding="utf-8").strip()
else:
    BASE_PROMPT = "Eres una IA natural, fresca y charlatana."

# === Optional: langdetect ===
try:
    from langdetect import detect
except Exception:
    detect = None

# === Detección de idioma ===
def get_user_lang(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return "es"
    if detect:
        try:
            return detect(text)
        except Exception:
            pass
    if any(w in text.lower() for w in [" el ", " la ", "que", "hola", "¿", "¡"]):
        return "es"
    return "en"

# === Prompt base neutral ===
def make_system_prompt(lang: str) -> str:
    if lang.startswith("es"):
        header = (
            "Hablas de manera relajada, chistosa y con confianza. "
            "Tus respuestas suenan naturales e improvisadas.\n\n"
        )
    else:
        header = "You are a witty AI with humor.\n\n"
    return header + BASE_PROMPT

# === Llamada a la API ===
def call_model(messages):
    api_key = os.getenv("MODEL_API_KEY")
    if not api_key:
        logger.warning("MODEL_API_KEY no configurada")
        return None

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": MODEL_CONFIG["name"], "messages": messages, "max_tokens": 1024}

    try:
        r = requests.post(
            f"{MODEL_CONFIG['base_url']}/chat/completions",
            headers=headers,
            json=payload,
            timeout=60,
        )
        if r.status_code != 200:
            logger.error(f"Error API: {r.status_code} - {r.text}")
            return None
        data = r.json()
        return data.get("choices", [{}])[0].get("message", {}).get("content", "(sin respuesta)")
    except Exception as e:
        logger.exception("Error en llamada al modelo")
        return None

# === /start con mensaje moderno: nombre del bot y desarrollador ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"/start invocado por usuario {update.message.from_user.id}")
    user = update.message.from_user
    username = user.first_name or user.username or "usuario"
    logger.info(f"Username detectado: {username}")

    try:
        # Mensaje moderno: sleek, techy y directo
        reply = (
            f"🚀 Hey {username}! Bienvenido a {SITE_NAME} – tu IA next-gen.\n"
            f"Creado por t.me/swippe_god\n"
            f"¿Listo pa' level up? Dime qué buscas."
        )
        await update.message.reply_text(reply)
        logger.info("Mensaje de start moderno enviado")

    except Exception as e:
        logger.exception("Error general en /start")
        fallback = f"🚀 Hey {username}! {SITE_NAME} by @swippe_god."
        await update.message.reply_text(fallback)

# === Mensajes normales ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_id = user.id
    user_msg = update.message.text or ""
    chat_type = update.message.chat.type

    now = time.time()
    if now - LAST_MESSAGE_TIME.get(user_id, 0) < FLOOD_DELAY:
        await update.message.reply_text("⏳ Espera un momento, estoy procesando...")
        return
    LAST_MESSAGE_TIME[user_id] = now

    if chat_type in ["group", "supergroup"]:
        if not user_msg.startswith("/") and f"@{context.bot.username}" not in user_msg:
            return

    lang = get_user_lang(user_msg)
    system_prompt = make_system_prompt(lang)

    reply = call_model(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ]
    )

    # Fallback si falla API
    if reply is None:
        reply = "¡Estoy sin conexión hoy! Dime más y lo resolvemos."

    await update.message.reply_text(reply)

# === /setlang ===
async def setlang_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("El idioma se detecta automáticamente.")

# === Ejecutar bot ===
def run_bot():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("❌ Falta TELEGRAM_TOKEN en las variables de entorno.")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setlang", setlang_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("🔥 Bot activo y listo 🔥")
    app.run_polling()

if __name__ == "__main__":
    run_bot()
