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

# === Config / Env ===
PROMPT_FILE = "system-prompt.txt"

MODEL_CONFIG = {
    "name": "deepseek-ai/deepseek-v3.1-terminus",
    "base_url": "https://integrate.api.nvidia.com/v1",
}

SITE_NAME = "WormGPT (Research Assistant)"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# === Anti-Flood ===
LAST_MESSAGE_TIME = {}
FLOOD_DELAY = 3  # seconds

# === Load base system prompt ===
if Path(PROMPT_FILE).exists():
    with open(PROMPT_FILE, "r", encoding="utf-8") as f:
        BASE_PROMPT = f.read().strip()
else:
    BASE_PROMPT = "You are a helpful, honest and safety-minded assistant."

# === Optional: langdetect ===
try:
    from langdetect import detect
except Exception:
    detect = None

# === Build system prompt ===
def make_system_prompt(lang_code: str) -> str:
    if lang_code and lang_code.startswith("es"):
        header = "Eres un asistente útil, directo y honesto. Responde en Español.\n\n"
    elif lang_code and lang_code.startswith("id"):
        header = "Anda asisten yang berguna, jujur, dan aman. Jawab dalam Bahasa Indonesia.\n\n"
    else:
        header = "You are a helpful and concise assistant. Answer in English.\n\n"
    return header + BASE_PROMPT

# === Detect language from text ===
def get_user_lang_from_text(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return "en"
    if detect:
        try:
            return detect(text)
        except Exception:
            pass
    lowered = text.lower()
    if any(w in lowered for w in [" el ", " la ", " que ", "para", "por", "hola", "¿"]):
        return "es"
    if any(w in lowered for w in [" dan ", " yang ", " itu ", " saya ", "ter"]):
        return "id"
    if any(w in lowered for w in [" the ", " and ", " you ", " is ", "hello "]):
        return "en"
    return "en"

# === Call model helper ===
def call_model(messages):
    api_key = os.getenv("MODEL_API_KEY")
    if not api_key:
        return "❌ MODEL_API_KEY not configured in environment."

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": MODEL_CONFIG["name"],
        "messages": messages,
        "max_tokens": 512,
    }

    try:
        res = requests.post(
            f"{MODEL_CONFIG['base_url']}/chat/completions",
            headers=headers,
            json=payload,
            timeout=30,
        )
        if res.status_code != 200:
            try:
                err_text = res.json()
            except Exception:
                err_text = res.text
            return f"⚠️ API ERROR {res.status_code}\n{err_text}"
        data = res.json()
        return data.get("choices", [{}])[0].get("message", {}).get("content", "(no content)")
    except Exception as e:
        logger.exception("Request failed")
        return f"❌ Request failed: {e}"

# === /start handler with AI greeting ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    username = user.first_name or user.username or "usuario"

    lang = get_user_lang_from_text(username)
    system_prompt = make_system_prompt(lang)

    # Mensaje temporal con lupa
    thinking_msg = await update.message.reply_text("🔍 Generando tu bienvenida...")

    user_prompt = (
        f"Da una bienvenida amistosa y profesional a {username}. "
        "Usa un tono natural, cercano, y menciona que este es un asistente basado en IA. "
        "Incluye un emoji relevante al final."
    )

    reply = call_model([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ])

    # Edita el mensaje con la respuesta
    try:
        await thinking_msg.edit_text(f"🍳 {reply}")
    except Exception:
        await update.message.reply_text(f"🍳 {reply}")

# === Message handler ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_username = context.bot_data.get("username", "")
    user = update.message.from_user
    user_id = user.id
    user_msg = update.message.text or ""
    chat_type = update.message.chat.type

    # Anti-Flood
    now = time.time()
    last = LAST_MESSAGE_TIME.get(user_id, 0)
    if now - last < FLOOD_DELAY:
        await update.message.reply_text("⏳ Slowmode activo (3 seg). Espera un momento...")
        return
    LAST_MESSAGE_TIME[user_id] = now

    # Ignora mensajes no mencionando el bot en grupos
    if chat_type in ["group", "supergroup"]:
        if not user_msg.startswith("/") and f"@{bot_username}" not in user_msg:
            return

    lang = get_user_lang_from_text(user_msg)
    system_prompt = make_system_prompt(lang)

    thinking_msg = await update.message.reply_text("🔍")

    reply = call_model([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ])

    try:
        await thinking_msg.edit_text(f"👹 {reply}")
    except Exception:
        await update.message.reply_text(f"👹 {reply}")

# === /setlang placeholder ===
async def setlang_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("El idioma se detecta automáticamente. No es necesario configurarlo.")

# === Run Bot ===
def run_bot():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("❌ TELEGRAM_TOKEN environment variable not set.")
    logger.info("Starting bot...")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setlang", setlang_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()

if __name__ == "__main__":
    run_bot()
