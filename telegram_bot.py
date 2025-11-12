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
SITE_NAME = "WormGPT (La IA más freca)"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
LAST_MESSAGE_TIME = {}
FLOOD_DELAY = 3

# === Sticker de “pensando” ===
THINKING_STICKER = "CAACAgIAAxkBAAEIB_1m0-9sHPRZglTQj53MehLQe-P1MgACbRQAAsuwQUo0b7OTV2Kj8zYE"

# === Base prompt ===
if Path(PROMPT_FILE).exists():
    BASE_PROMPT = Path(PROMPT_FILE).read_text(encoding="utf-8").strip()
else:
    BASE_PROMPT = "Eres una IA con humor dominicano, natural, fresca y charlatana."

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

# === Prompt base con tono dominicano ===
def make_system_prompt(lang: str) -> str:
    if lang.startswith("es"):
        header = (
            "Hablas como un dominicano auténtico: relajado, chistoso, con confianza, usando expresiones como "
            "'manín', 'mi loco', 'ta heavy', 'vamo allá'. Tienes humor de calle, pero con respeto. "
            "Tus respuestas suenan naturales, improvisadas, con ese flow criollo.\n\n"
        )
    else:
        header = "You are a witty, Caribbean-style AI with Dominican flavor and humor.\n\n"
    return header + BASE_PROMPT

# === Llamada a la API ===
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
        logger.exception("Error en llamada al modelo")
        return f"❌ Error en la solicitud: {e}"

# === /start con broma dominicana sobre el nombre ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    username = user.first_name or user.username or "mi pana"

    lang = get_user_lang(username)
    system_prompt = make_system_prompt(lang)

    # Sticker mientras “piensa”
    sticker_msg = await update.message.reply_sticker(THINKING_STICKER)

    # Prompt: broma sobre el nombre
    user_prompt = (
        f"Eres una IA dominicana que da la bienvenida a un usuario llamado '{username}'. "
        f"Haz una broma corta, natural y con flow dominicano sobre su nombre — puede ser un relajo amistoso, "
        f"como si lo dijera un pana en el colmado. Luego, dale la bienvenida al chat de manera relajada y graciosa. "
        f"Ejemplo: si el nombre es 'Carlos', podrías decir algo como 'Carlos... ese nombre suena a tiguerazo serio, cuidado si tú eres del bloque 😎'. "
        f"No seas grosero, pero sí pícaro y simpático. Usa jerga dominicana con naturalidad."
    )

    reply = call_model(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
    )

    # Eliminar sticker y enviar respuesta
    try:
        await sticker_msg.delete()
    except Exception:
        pass

    await update.message.reply_text(reply)

# === Mensajes normales ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_id = user.id
    user_msg = update.message.text or ""
    chat_type = update.message.chat.type

    now = time.time()
    if now - LAST_MESSAGE_TIME.get(user_id, 0) < FLOOD_DELAY:
        await update.message.reply_text("⏳ Aguanta un chin, mi loco, toy procesando...")
        return
    LAST_MESSAGE_TIME[user_id] = now

    if chat_type in ["group", "supergroup"]:
        if not user_msg.startswith("/") and f"@{context.bot.username}" not in user_msg:
            return

    lang = get_user_lang(user_msg)
    system_prompt = make_system_prompt(lang)

    sticker_msg = await update.message.reply_sticker(THINKING_STICKER)

    reply = call_model(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ]
    )

    try:
        await sticker_msg.delete()
    except Exception:
        pass

    await update.message.reply_text(reply)

# === /setlang ===
async def setlang_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("El idioma se detecta solo, manín 😎")

# === Ejecutar bot ===
def run_bot():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("❌ Falta TELEGRAM_TOKEN en las variables de entorno.")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setlang", setlang_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("🔥 Bot dominicano activo y prendido 🔥")
    app.run_polling()

if __name__ == "__main__":
    run_bot()
