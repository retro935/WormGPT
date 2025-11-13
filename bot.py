# bot.py — Usa system-prompt.txt como prompt base
import os
import time
import logging
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# ---------------- CONFIG ----------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MODEL_API_KEY = os.getenv("MODEL_API_KEY")
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID")  # opcional

if not MODEL_API_KEY:
    raise RuntimeError("❌ Falta MODEL_API_KEY. Configúrala en las variables de entorno.")

# ---------------- LOGGING ----------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("wormgpt")

# ---------------- ESTADO ----------------
LAST_MESSAGE_TIME = {}
FLOOD_DELAY = 2  # segundos entre mensajes por usuario
USER_HISTORY = {}
HISTORY_LIMIT = 6

# ---------------- PROMPT SYSTEM ----------------
def load_system_prompt():
    """Carga prompt base desde system-prompt.txt (si existe)."""
    path = "system-prompt.txt"
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            base = f.read().strip()
            logger.info("📜 system-prompt.txt cargado correctamente.")
            return base
    logger.warning("⚠️ No se encontró system-prompt.txt, usando prompt por defecto.")
    return "Eres un asistente técnico, directo y conciso."

SYSTEM_PROMPT = load_system_prompt()

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
    """Llama siempre al modelo remoto."""
    try:
        url = "https://integrate.api.nvidia.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {MODEL_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": "deepseek-ai/deepseek-v3.1-terminus",
            "messages": messages,
            "max_tokens": 512
        }
        r = requests.post(url, headers=headers, json=payload, timeout=45)
        if r.status_code == 200:
            data = r.json()
            return data.get("choices", [{}])[0].get("message", {}).get("content", "Sin respuesta del modelo.")
        else:
            logger.error(f"Error modelo ({r.status_code}): {r.text}")
            return f"⚠️ Error del modelo ({r.status_code})."
    except requests.exceptions.ReadTimeout:
        return "⏳ El modelo tardó demasiado en responder. Intenta de nuevo."
    except Exception as e:
        logger.exception("Error llamando al modelo:")
        return f"❌ Error conectando con el modelo: {e}"

# ---------------- HANDLERS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    name = user.first_name or user.username or "compa"
    text = (
        f"🔥 ¡Qué lo qué, {name}! Bienvenido.\n\n"
        "Este bot usa *system-prompt.txt* como base y se conecta directamente al modelo remoto.\n"
        "Escribe lo que quieras para probar."
    )
    await update.message.reply_text(text, parse_mode="Markdown")
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
        await update.message.reply_text("⏳ Espera un momento, procesando...")
        return
    LAST_MESSAGE_TIME[uid] = now

    # historial
    USER_HISTORY.setdefault(uid, []).append({"role": "user", "content": user_msg})
    if len(USER_HISTORY[uid]) > HISTORY_LIMIT * 2:
        USER_HISTORY[uid] = USER_HISTORY[uid][-HISTORY_LIMIT * 2:]

    # armar conversación
    messages = (
        [{"role": "system", "content": SYSTEM_PROMPT}]
        + USER_HISTORY[uid][-HISTORY_LIMIT:]
        + [{"role": "user", "content": user_msg}]
    )

    thinking = await update.message.reply_text("💭")

    # llamada al modelo
    reply = call_text_model(messages)

    await thinking.edit_text(reply)
    USER_HISTORY[uid].append({"role": "assistant", "content": reply})

    # log
    send_log_to_channel(f"👤 {user.first_name or user.username}:\n❓ {user_msg}\n💬 {reply[:500]}")

# ---------------- RUN ----------------
def run_bot():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("❌ Falta TELEGRAM_TOKEN.")
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("🚀 Bot conectado al modelo remoto con system-prompt.txt (polling)...")
    app.run_polling()

if __name__ == "__main__":
    run_bot()
