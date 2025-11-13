# bot.py — Versión simple (sin edición de imágenes)
import os
import time
import logging
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# ---------------- CONFIG ----------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MODEL_API_KEY = os.getenv("MODEL_API_KEY")  # opcional: si lo pones, se llamará al modelo remoto
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID")  # opcional: canal para logs (chat_id)

# ---------------- LOGGING ----------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("wormgpt")

# ---------------- ESTADO ----------------
LAST_MESSAGE_TIME = {}
FLOOD_DELAY = 2  # segundos entre mensajes por usuario
USER_HISTORY = {}
HISTORY_LIMIT = 6

# ---------------- HELPERS ----------------
def send_log_to_channel(text: str):
    """Envía texto al canal de logs (si está configurado)."""
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
    """Llamada simple al endpoint de texto si MODEL_API_KEY está configurada.
       Si no, devuelve un eco (útil para pruebas)."""
    if not MODEL_API_KEY:
        # Respuesta de fallback (eco corto)
        user_text = messages[-1].get("content", "")
        return f"Echo: {user_text}"
    try:
        url = "https://integrate.api.nvidia.com/v1/chat/completions"  # o cambia por tu endpoint
        headers = {"Authorization": f"Bearer {MODEL_API_KEY}", "Content-Type": "application/json"}
        payload = {"model": "deepseek-ai/deepseek-v3.1-terminus", "messages": messages, "max_tokens": 512}
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        if r.status_code == 200:
            data = r.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "Sin respuesta.")
        else:
            logger.error(f"Error modelo ({r.status_code}): {r.text}")
            return "⚠️ El modelo no respondió correctamente."
    except Exception as e:
        logger.exception("Error llamando al modelo:")
        return "❌ Error conectando con el modelo."

# ---------------- HANDLERS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.first_name or user.username or "compa"
    text = (
        f"🔥 ¡Qué lo qué, {name}! Bienvenido.\n\n"
        "Soy tu bot minimal — responde texto y hago eco si no hay modelo.\n"
        "Escribe cualquier cosa para probar."
    )
    await update.message.reply_text(text)
    send_log_to_channel(f"🟢 /start por {name} ({user.id})")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    user_msg = (update.message.text or "").strip()
    if not user_msg:
        return

    # anti-flood
    now = time.time()
    if now - LAST_MESSAGE_TIME.get(uid, 0) < FLOOD_DELAY:
        await update.message.reply_text("⏳ Espera un momento, estoy procesando...")
        return
    LAST_MESSAGE_TIME[uid] = now

    # historial simple
    USER_HISTORY.setdefault(uid, []).append({"role": "user", "content": user_msg})
    if len(USER_HISTORY[uid]) > HISTORY_LIMIT * 2:
        USER_HISTORY[uid] = USER_HISTORY[uid][-HISTORY_LIMIT * 2 :]

    # construye mensajes para el modelo (si aplica)
    messages = [{"role": "system", "content": "Eres un asistente conciso y claro."}] + USER_HISTORY[uid][-HISTORY_LIMIT:] + [{"role": "user", "content": user_msg}]

    # feedback al usuario
    thinking = await update.message.reply_text("🔍")

    # llama al modelo o eco
    reply = call_text_model(messages)

    # responde y limpia
    await thinking.edit_text(reply)
    USER_HISTORY[uid].append({"role": "assistant", "content": reply})

    # log ligero
    send_log_to_channel(f"👤 {user.first_name or user.username}:\n❓ {user_msg}\n💬 {reply[:500]}")

# ---------------- RUN ----------------
def run_bot():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("❌ Configura TELEGRAM_TOKEN en las variables de entorno.")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("🚀 Bot arrancando (polling)...")
    app.run_polling()

if __name__ == "__main__":
    run_bot()
