import os
import time
import logging
import os
import time
import json
import logging
import asyncio
import aiohttp

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ------------- CONFIG -------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MODEL_API_KEY = os.getenv("MODEL_API_KEY")  # tu API key del modelo (opcional si usas eco)
ADMIN_ID = int(os.getenv("ADMIN_ID", "6699273462"))  # pon aquí tu id si no usas env

VIP_FILE = "vip.json"
WELCOME_STICKER = "CAACAgIAAxkBAAE9zC1pFkPkMxt4V0MQ1pry-erxbbKRswACPQADDbbSGa8UnEXVDgHzNgQ"

# ------------- LOGGING -------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("vip-bot")

# ------------- SESSION GLOBAL -------------
SESSION: aiohttp.ClientSession | None = None


async def get_session():
    global SESSION
    if SESSION is None or SESSION.closed:
        SESSION = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
    return SESSION


# ------------- VIP STORAGE -------------
def load_vip():
    try:
        if not os.path.exists(VIP_FILE):
            save_vip([])
            return []
        with open(VIP_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("vips", [])
    except Exception as e:
        logger.exception("Error cargando vip.json:")
        return []


def save_vip(vips):
    with open(VIP_FILE, "w", encoding="utf-8") as f:
        json.dump({"vips": vips}, f, indent=2, ensure_ascii=False)


def is_vip(user_id: int) -> bool:
    return user_id in load_vip()


# ------------- Llamada a la IA (async) -------------
async def call_text_model(messages):
    """
    Llamada async al modelo. Si no hay API key hace eco.
    Ajusta URL/payload a tu proveedor si es necesario.
    """
    if not MODEL_API_KEY:
        # modo eco para pruebas
        return messages[-1].get("content", "")

    session = await get_session()
    url = "https://integrate.api.nvidia.com/v1/chat/completions"  # cambiar si usas otro proveedor
    headers = {"Authorization": f"Bearer {MODEL_API_KEY}", "Content-Type": "application/json"}

    payload = {
        "model": "deepseek-ai/deepseek-v3.1-terminus",
        "messages": messages,
        "max_tokens": 512,
        "temperature": 0.2,
    }

    try:
        async with session.post(url, json=payload, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("choices", [{}])[0].get("message", {}).get("content", "Sin respuesta.")
            else:
                text = await resp.text()
                logger.error("Error modelo %s %s", resp.status, text)
                return "⚠️ El modelo no respondió correctamente."
    except asyncio.TimeoutError:
        return "⏳ El modelo tardó demasiado."
    except Exception as e:
        logger.exception("Error llamando al modelo:")
        return "❌ Error conectando con la IA."


# ------------- HELPERS ADMIN -------------
async def send_log(text: str):
    # envía logs al admin por Telegram si TELEGRAM_TOKEN y ADMIN_ID están configurados
    if not TELEGRAM_TOKEN or not ADMIN_ID:
        return
    session = await get_session()
    try:
        await session.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": ADMIN_ID, "text": text},
            timeout=5,
        )
    except Exception:
        pass


# ------------- HANDLERS -------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    name = update.effective_user.first_name or update.effective_user.username or "usuario"

    if not is_vip(uid):
        # Usuario NO VIP: rechazar, indicar cómo solicitar acceso
        await update.message.reply_text(
            "🚫 Este bot es SOLO para usuarios VIP.\n\n"
            "Si deseas acceso, contacta al administrador o solicita que te agreguen.\n"
            "Comando de admin para agregar: /vip add <id>"
        )
        return

    # Usuario VIP: saludo + sticker
    try:
        # enviar sticker animado de bienvenida
        await context.bot.send_sticker(update.effective_chat.id, WELCOME_STICKER)
    except Exception:
        # si falla el sticker, ignorar
        logger.debug("No se pudo enviar sticker de bienvenida.")

    welcome = f"👋 Hola {name}! ✅ Eres VIP. Estoy listo para asistirte — envía tu pregunta."
    await update.message.reply_text(welcome)


async def vip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Comando admin para manejar VIPs:
    /vip add <id>
    /vip remove <id>
    /vip list
    """
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        return await update.message.reply_text("No tienes permiso para usar este comando.")

    args = context.args
    if not args:
        return await update.message.reply_text("Uso: /vip add <id> | remove <id> | list")

    cmd = args[0].lower()
    vips = load_vip()

    if cmd == "add" and len(args) == 2:
        try:
            target = int(args[1])
        except ValueError:
            return await update.message.reply_text("ID inválido.")
        if target in vips:
            return await update.message.reply_text("Ese usuario ya es VIP.")
        vips.append(target)
        save_vip(vips)
        await update.message.reply_text(f"Usuario {target} agregado como VIP ✅")
        await send_log(f"VIP agregado: {target} por admin {uid}")
        return

    if cmd == "remove" and len(args) == 2:
        try:
            target = int(args[1])
        except ValueError:
            return await update.message.reply_text("ID inválido.")
        if target not in vips:
            return await update.message.reply_text("Ese usuario no es VIP.")
        vips.remove(target)
        save_vip(vips)
        await update.message.reply_text(f"Usuario {target} eliminado de VIP ❌")
        await send_log(f"VIP eliminado: {target} por admin {uid}")
        return

    if cmd == "list":
        if not vips:
            return await update.message.reply_text("No hay usuarios VIP.")
        text = "⭐ Usuarios VIP:\n" + "\n".join([f"- `{x}`" for x in vips])
        return await update.message.reply_text(text, parse_mode="Markdown")

    await update.message.reply_text("Comando inválido. Uso: /vip add <id> | remove <id> | list")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Solo VIP puede usar el bot. Si no es VIP, responde con instrucción.
    """
    uid = update.effective_user.id
    if not is_vip(uid):
        return await update.message.reply_text(
            "🚫 Este bot es exclusivo para VIPs. Contacta al administrador para acceso."
        )

    text = (update.message.text or "").strip()
    if not text:
        return

    # indicar typing
    await context.bot.send_chat_action(update.effective_chat.id, "typing")

    # construir mensajes (breve por defecto; VIP -> sin limitaciones)
    system_prompt = (
        "Eres un asistente técnico directo y profesional. Responde claro y directo. "
        "Como el usuario es VIP, puedes dar respuestas detalladas si es necesario."
    )

    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": text}]

    # llamar al modelo async
    reply = await call_text_model(messages)

    # enviar respuesta
    # si la respuesta incluye triple backticks, mandarla tal cual (telegram lo mostrará)
    try:
        await update.message.reply_text(reply)
    except Exception:
        # fallback simple
        await update.message.reply_text(reply[:400])


# ------------- RUN -------------
def run_bot():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("❌ Falta TELEGRAM_TOKEN en variables de entorno.")

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .concurrent_updates(True)
        .build()
    )

    # handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("vip", vip_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🚀 VIP-only bot corriendo...")
    app.run_polling()


if __name__ == "__main__":
    # ensure vip file exists
    if not os.path.exists(VIP_FILE):
        save_vip([])
    run_bot()
