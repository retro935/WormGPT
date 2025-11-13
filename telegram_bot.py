import os
import time
import base64
import requests
import logging
from io import BytesIO
from PIL import Image
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
HF_TOKEN = os.getenv("HF_TOKEN")

BASE_PROMPT = "Eres una IA directa, con sentido del humor y sin filtros innecesarios. Responde con claridad y estilo natural."
MODEL_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
HF_MODEL = "runwayml/stable-diffusion-inpainting"

# === LOGGING ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === CONTROL DE MENSAJES ===
LAST_MESSAGE_TIME = {}
FLOOD_DELAY = 2
USER_HISTORY = {}
HISTORY_LIMIT = 5


# === Llamada al modelo de texto ===
def call_text_model(messages):
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


# === Edición de imágenes con Hugging Face ===
def edit_image_hf(image_url: str, prompt: str):
    if not HF_TOKEN:
        return "❌ Falta HF_TOKEN. Agrega tu token de Hugging Face a las variables de entorno."

    try:
        # Descargar imagen original
        img_resp = requests.get(image_url)
        img_resp.raise_for_status()
        image = Image.open(BytesIO(img_resp.content)).convert("RGB")

        # Convertir imagen a base64 (HF espera files, pero Render no permite multipart grandes)
        buffered = BytesIO()
        image.save(buffered, format="PNG")
        img_b64 = base64.b64encode(buffered.getvalue()).decode("utf-8")

        # Payload a Hugging Face
        headers = {"Authorization": f"Bearer {HF_TOKEN}"}
        payload = {
            "inputs": prompt,
            "parameters": {"num_inference_steps": 30, "guidance_scale": 8.0},
            "image": img_b64
        }

        resp = requests.post(f"https://api-inference.huggingface.co/models/{HF_MODEL}",
                             headers=headers, json=payload, timeout=120)

        if resp.status_code == 200:
            edited_image = Image.open(BytesIO(resp.content))
            buffer_out = BytesIO()
            edited_image.save(buffer_out, format="PNG")
            buffer_out.seek(0)
            return buffer_out
        else:
            return f"⚠️ Error HF API ({resp.status_code}): {resp.text}"

    except Exception as e:
        logger.exception("Error en edición de imagen:")
        return f"❌ Error editando imagen: {e}"


# === COMANDOS ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    username = user.first_name or user.username or "compa"
    await update.message.reply_text(
        f"🔥 ¡Qué lo qué, {username}! Bienvenido a *{SITE_NAME}* 🚀\n"
        f"Tu IA freca: responde texto y también edita imágenes.\n\n"
        f"Ejemplo:\n"
        f"`edita esta imagen cambiando el fondo a playa`\n seguido de una URL de imagen 🌴",
        parse_mode="Markdown",
    )


# === MENSAJES ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    user_id = user.id
    user_msg = update.message.text or ""

    now = time.time()
    if now - LAST_MESSAGE_TIME.get(user_id, 0) < FLOOD_DELAY:
        await update.message.reply_text("⏳ Espera un momento, toy pensando...")
        return
    LAST_MESSAGE_TIME[user_id] = now

    if user_id not in USER_HISTORY:
        USER_HISTORY[user_id] = []
    USER_HISTORY[user_id].append({"role": "user", "content": user_msg})

    # Detecta si hay URL e intención de editar
    if any(k in user_msg.lower() for k in ["edita", "editar", "imagen", "foto", "image"]) and "http" in user_msg:
        parts = user_msg.split()
        image_url = next((p for p in parts if p.startswith("http")), None)
        edit_prompt = user_msg.replace(image_url, "").strip()
        await update.message.reply_text("🎨 Editando imagen, dame un momento...")
        edited = edit_image_hf(image_url, edit_prompt)
        if isinstance(edited, BytesIO):
            await update.message.reply_photo(photo=edited, caption="🖼️ Imagen editada por IA")
        else:
            await update.message.reply_text(str(edited))
        return

    # Texto normal
    thinking = await update.message.reply_text("🔍 Pensando...")
    history = USER_HISTORY[user_id][-HISTORY_LIMIT * 2 :]
    messages = [{"role": "system", "content": BASE_PROMPT}] + history
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
    logger.info("🚀 Bot Telegram corriendo y listo para texto + edición de imágenes.")
    app.run_polling()
