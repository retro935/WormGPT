import os
import sys
import time
import base64
import requests
import logging
import subprocess
from io import BytesIO
from pathlib import Path

# ====== INSTALACIÓN DINÁMICA DE PILLOW ======
try:
    from PIL import Image
except ImportError:
    print("🧩 Pillow no encontrado. Instalando dinámicamente...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow==9.5.0"])
    from PIL import Image
# ============================================

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ---------- CONFIG ----------
SITE_NAME = "WormGPT"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MODEL_API_KEY = os.getenv("MODEL_API_KEY")  # API del modelo
HF_TOKEN = os.getenv("HF_TOKEN")            # Token de Hugging Face (opcional)
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID")  # Canal de logs opcional

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
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ---------- ESTADO ----------
LAST_MESSAGE_TIME = {}
FLOOD_DELAY = 2
USER_HISTORY = {}
HISTORY_LIMIT = 6

# ---------- FUNCIONES ----------
def send_log_to_channel(text: str):
    """Envía logs al canal si está configurado."""
    if not LOG_CHANNEL_ID or not TELEGRAM_TOKEN:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": LOG_CHANNEL_ID, "text": text, "parse_mode": "Markdown"},
            timeout=5
        )
    except Exception as e:
        logger.debug(f"No se pudo enviar log: {e}")

def log_interaction(user, question, answer):
    short = f"👤 *{user}*\n❓ {question}\n💬 {answer[:800]}"
    logger.info(short)
    send_log_to_channel(short)

def call_text_model(messages):
    """Llama al modelo de texto remoto."""
    if not MODEL_API_KEY:
        return "⚠️ Falta la API KEY del modelo."
    headers = {"Authorization": f"Bearer {MODEL_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": "deepseek-ai/deepseek-v3.1-terminus", "messages": messages, "max_tokens": 512}
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
    """Usa Hugging Face para edición de imágenes."""
    if not HF_TOKEN:
        return "❌ Falta HF_TOKEN."
    try:
        resp = requests.get(image_url, timeout=10)
        image = Image.open(BytesIO(resp.content)).convert("RGB")
        buf = BytesIO()
        image.save(buf, format="PNG")
        img_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        headers = {"Authorization": f"Bearer {HF_TOKEN}"}
        payload = {"inputs": prompt, "image": img_b64}
        r = requests.post(f"https://api-inference.huggingface.co/models/{HF_MODEL}", headers=headers, json=payload, timeout=120)
        if r.status_code == 200:
            edited = Image.open(BytesIO(r.content))
            out = BytesIO()
            edited.save(out, format="PNG")
            out.seek(0)
            return out
        return f"⚠️ Error HF API ({r.status_code}): {r.text}"
    except Exception as e:
        return f"❌ Error editando imagen: {e}"

# ---------- HANDLERS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.first_name or user.username or "compa"
    greeting = f"🔥 ¡Qué lo qué, {name}! Bienvenido a *{SITE_NAME}* 🚀\nSoy Fraudix, pero tranquilo, aquí no hacemos líos. 😎"
    await update.message.reply_text(greeting, parse_mode="Markdown")
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

    # Si detecta imagen
    if any(k in text.lower() for k in ["edita", "editar", "imagen", "foto", "image"]) and "http" in text:
        parts = text.split()
        image_url = next((p for p in parts if p.startswith("http")), None)
        prompt = text.replace(image_url or "", "").strip()
        await update.message.reply_text("🎨 Editando imagen...")
        result = edit_image_hf(image_url, prompt)
        if isinstance(result, BytesIO):
            await update.message.reply_photo(photo=result, caption="🖼️ Imagen editada")
            send_log_to_channel(f"🖼️ Edit request by {user.first_name}: {text}")
        else:
            await update.message.reply_text(str(result))
        return

    # Construye el contexto para el modelo
    history = USER_HISTORY[uid][-HISTORY_LIMIT * 10:]
    messages = [{"role": "system", "content": BASE_PROMPT}] + history + [{"role": "user", "content": text}]

    await update.message.reply_text("🔍")
    reply = call_text_model(messages)
    await update.message.reply_text(reply)
    USER_HISTORY[uid].append({"role": "assistant", "content": reply})
    log_interaction(user.first_name or user.username or str(uid), text, reply)

# ---------- MAIN ----------
def run_bot():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("❌ Falta TELEGRAM_TOKEN en las variables de entorno.")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("🚀 Bot Telegram corriendo y listo para responder.")
    app.run_polling()

if __name__ == "__main__":
    run_bot()
