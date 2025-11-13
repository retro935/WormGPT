import os
import logging
import threading
import asyncio
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

import httpx  # Usamos HF API directamente sin Pillow ni extras.

# ───────────────────────────────────────────────────────────────
# CONFIGURACIÓN DE LOGS
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ───────────────────────────────────────────────────────────────
# VARIABLES DE ENTORNO
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
HF_TOKEN = os.getenv("HF_TOKEN")
PORT = int(os.getenv("PORT", "10000"))
RENDER_URL = os.getenv("RENDER_EXTERNAL_URL", "https://wormgpt-n0jr.onrender.com")

# ───────────────────────────────────────────────────────────────
# FLASK APP (para mantener el contenedor activo en Render)
app = Flask(__name__)

@app.route("/", methods=["GET", "HEAD"])
def home():
    return "✅ Bot activo y escuchando", 200

@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.update_queue.put_nowait(update)
    return "OK", 200

# ───────────────────────────────────────────────────────────────
# FUNCIONES DEL BOT

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"👋 ¡Qué lo qué, {user.first_name or 'tigre'}!\n"
        f"Soy tu IA dominicana en la nube 😎.\n\n"
        f"Escríbeme algo y te lo respondo con estilo 🔥"
    )

async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.strip()
    chat_id = update.effective_chat.id

    # Mensaje animado de "pensando..."
    thinking_msg = await update.message.reply_text("🤔 Déjame pensarlo un chin...")

    try:
        # Llamada al modelo de Hugging Face
        headers = {"Authorization": f"Bearer {HF_TOKEN}"}
        payload = {"inputs": user_text, "parameters": {"max_new_tokens": 250}}
        async with httpx.AsyncClient(timeout=40) as client:
            resp = await client.post("https://api-inference.huggingface.co/models/gpt2", headers=headers, json=payload)
            data = resp.json()

        # Procesar la respuesta
        if isinstance(data, list) and len(data) > 0 and "generated_text" in data[0]:
            reply_text = data[0]["generated_text"].strip()
        else:
            reply_text = "😅 No entendí eso, repíteme de otra forma."

        await thinking_msg.edit_text(reply_text)

    except Exception as e:
        logging.error(f"❌ Error al procesar mensaje: {e}")
        await thinking_msg.edit_text("⚠️ Ocurrió un problema procesando tu mensaje.")

# ───────────────────────────────────────────────────────────────
# INICIALIZACIÓN DE TELEGRAM APP
application = Application.builder().token(TELEGRAM_TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_message))

# ───────────────────────────────────────────────────────────────
# FUNCIÓN PARA MANTENER EL LOOP ACTIVO EN HILO SEPARADO
def run_telegram():
    asyncio.run(application.run_polling(stop_signals=None))

# ───────────────────────────────────────────────────────────────
# MAIN
if __name__ == "__main__":
    logging.info(f"🌐 Configurando webhook en {RENDER_URL}/{TELEGRAM_TOKEN}")

    # Aseguramos el webhook correcto
    async def set_hook():
        async with application.bot:
            await application.bot.set_webhook(url=f"{RENDER_URL}/{TELEGRAM_TOKEN}")
    asyncio.run(set_hook())

    # Hilo paralelo para procesar updates
    threading.Thread(target=lambda: asyncio.run(application.start()), daemon=True).start()

    # Mantener el servidor Flask activo (Render necesita esto)
    app.run(host="0.0.0.0", port=PORT)
