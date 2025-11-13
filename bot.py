import os
import time
import base64
import requests
import logging
from io import BytesIO
from pathlib import Path
from flask import Flask, request
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ---------- CONFIG ----------
SITE_NAME = "WormGPT"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MODEL_API_KEY = os.getenv("MODEL_API_KEY")
HF_TOKEN = os.getenv("HF_TOKEN")
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID")

MODEL_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
HF_MODEL = "runwayml/stable-diffusion-inpainting"

# ---------- PROMPT BASE ----------
PROMPT_FILE = "system-prompt.txt"
if Path(PROMPT_FILE).exists():
    BASE_PROMPT = Path(PROMPT_FILE).read_text(encoding="utf-8").strip()
else:
    BASE_PROMPT = "Eres un asistente servicial y profesional. Responde en español."

# ---------- LOGGING ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ---------- ESTADO ----------
LAST_MESSAGE_TIME = {}
FLOOD_DELAY = 2
USER_HISTORY = {}
HISTORY_LIMIT = 6

# ---------- FUNCIONES ----------
def send_log_to_channel(text: str):
    if not LOG_CHANNEL_ID or not TELEGRAM_TOKEN:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": LOG_CHANNEL_ID, "text": text, "parse_mode": "Markdown"},
            timeout=5,
        )
    except Exception:
        pass

def call_text_model(messages):
    if not MODEL_API_KEY:
        return "⚠️ Falta la API KEY del modelo."
    headers = {"Authorization": f"Bearer {MODEL_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "deepseek-ai/deepseek-v3.1-terminus",
        "messages": messages,
        "max_tokens": 512,
    }
    try:
        r = requests.post(MODEL_URL, headers=headers, json=payload, timeout=60)
        if r.status_code == 200:
            data = r.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "Sin respuesta.")
        return f"⚠️ Error del modelo ({r.status_code}): {r.text}"
    except Exception as e:
        logger.error(f"Error conectando con el modelo: {e}")
        return "❌ Error al conectar con el modelo."

def edit_image_hf(image_url: str, prompt: str):
    if not HF_TOKEN:
        return "❌ Falta HF_TOKEN."
    if not image_url.startswith("http"):
        return "⚠️ URL no válida."
    try:
        resp = requests.get(image_url, timeout=10)
        if resp.status_code != 200:
            return f"⚠️ Error al descargar la imagen ({resp.status_code})."

        img_b64 = base64.b64encode(resp.content).decode("utf-8")
        headers = {"Authorization": f"Bearer {HF_TOKEN}"}
        payload = {"inputs": prompt, "image": img_b64}

        r = requests.post(
            f"https://api-inference.huggingface.co/models/{HF_MODEL}",
            headers=headers,
            json=payload,
            timeout=120,
        )
        if r.status_code == 200:
            return BytesIO(r.content)
        return f"⚠️ Error HF API ({r.status_code}): {r.text}"
    except Exception as e:
        return f"❌ Error editando imagen: {e}"

# ---------- HANDLERS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.first_name or user.username or "compa"
    msg = f"🔥 ¡Qué lo qué, {name}! Bienvenido a *{SITE_NAME}* 🚀\nSoy Fraudix, pero tranquilo, aquí no hacemos líos. 😎"
    await update.message.reply_text(msg, parse_mode="Markdown")
    send_log_to_channel(f"🟢 /start por {name} ({user.id})")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    text = (update.message.text or "").strip()
    if not text:
        return

    now = time.time()
    if now - LAST_MESSAGE_TIME.get(uid, 0) < FLOOD_DELAY:
        await update.message.reply_text("⏳ Espera un momento, toy procesando...")
        return
    LAST_MESSAGE_TIME[uid] = now

    USER_HISTORY.setdefault(uid, []).append({"role": "user", "content": text})
    if len(USER_HISTORY[uid]) > HISTORY_LIMIT * 2:
        USER_HISTORY[uid] = USER_HISTORY[uid][-HISTORY_LIMIT * 2 :]

    if any(k in text.lower() for k in ["edita", "imagen", "foto"]) and "http" in text:
        parts = text.split()
        image_url = next((p for p in parts if p.startswith("http")), None)
        prompt = text.replace(image_url or "", "").strip()
        await update.message.reply_text("🎨 Procesando imagen con Hugging Face...")
        result = edit_image_hf(image_url, prompt)
        if isinstance(result, BytesIO):
            await update.message.reply_photo(photo=result, caption="🖼️ Imagen editada 🔥")
        else:
            await update.message.reply_text(str(result))
        return

    messages = [{"role": "system", "content": BASE_PROMPT}] + USER_HISTORY[uid][-HISTORY_LIMIT:] + [{"role": "user", "content": text}]
    await update.message.reply_text("🔍")
    reply = call_text_model(messages)
    await update.message.reply_text(reply)
    USER_HISTORY[uid].append({"role": "assistant", "content": reply})

# ---------- FLASK + WEBHOOK ----------
app = Flask(__name__)

@app.route('/')
def index():
    return "🤖 Bot WormGPT activo.", 200

@app.route(f'/{TELEGRAM_TOKEN}', methods=["POST"])
def telegram_webhook():
    """Recibe las actualizaciones del bot vía webhook."""
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.update_queue.put_nowait(update)
    return "OK", 200

# ---------- BOT ----------
application = Application.builder().token(TELEGRAM_TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

def run_bot():
    render_url = os.getenv("RENDER_EXTERNAL_URL", "").strip()
    if not render_url:
        render_url = "https://wormgpt-n0jr.onrender.com"

    webhook_url = f"{render_url}/{TELEGRAM_TOKEN}"
    logger.info(f"🌐 Configurando webhook en {webhook_url}")

    # Configura el webhook automáticamente
    resp = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook?url={webhook_url}")
    logger.info(f"Webhook respuesta: {resp.text}")

    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))

if __name__ == "__main__":
    import threading
    threading.Thread(target=run_bot).start()
