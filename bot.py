import os
import time
import logging
import aiohttp
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# ------------ CONFIG ------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MODEL_API_KEY = os.getenv("MODEL_API_KEY")
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID")

LAST_MESSAGE_TIME = {}
FLOOD_DELAY = 5
USER_HISTORY = {}
HISTORY_LIMIT = 6

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("wormgpt")

# ------------ SYSTEM PROMPT ------------
def load_system_prompt():
    try:
        with open("system-prompt.txt", "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return "Responde corto por defecto. Si el usuario pide detalle, da respuesta larga."

SYSTEM_PROMPT = load_system_prompt()

# ------------ MODELO (VERSIÓN ASYNC, ULTRA RÁPIDA) ------------
async def call_text_model(messages):
    if not MODEL_API_KEY:
        return messages[-1]["content"]

    url = "https://integrate.api.nvidia.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {MODEL_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "deepseek-ai/deepseek-v3.1-terminus",
        "messages": messages,
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload, headers=headers, timeout=120) as r:
                data = await r.json()
                return data.get("choices", [{}])[0].get("message", {}).get("content", "Sin respuesta.")
        except asyncio.TimeoutError:
            return "⏳ El modelo tardó demasiado."
        except Exception as e:
            return "❌ Error con el modelo."

# ------------ HANDLERS ------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loading = await update.message.reply_text("👋")
    await loading.edit_text("🔥 Bienvenido! Estoy activo y respondiendo rápido.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()

    # Anti flood (pero suave)
    now = time.time()
    if now - LAST_MESSAGE_TIME.get(uid, 0) < FLOOD_DELAY:
        await update.message.reply_text("⏳ Un segundito…")
        return
    LAST_MESSAGE_TIME[uid] = now

    # Historial
    USER_HISTORY.setdefault(uid, []).append({"role": "user", "content": text})
    USER_HISTORY[uid] = USER_HISTORY[uid][-HISTORY_LIMIT:]

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += USER_HISTORY[uid]

    # Indicador
    thinking = await update.message.reply_text("⚡ Procesando…")

    # --- AQUÍ VIENE LA MAGIA ASYNC (MUY RÁPIDO) ---
    reply = await call_text_model(messages)

    await thinking.edit_text(reply)

    USER_HISTORY[uid].append({"role": "assistant", "content": reply})

# ------------ RUN ------------
def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("❌ Falta TELEGRAM_TOKEN")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🚀 Bot corriendo rápido y listo para miles de usuarios.")
    app.run_polling()

if __name__ == "__main__":
    main()
