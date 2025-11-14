# bot_vip_final.py
import os
import json
import logging
import asyncio
import aiohttp
from typing import Optional, List
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ---------------- CONFIG ----------------
# Variables de entorno
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MODEL_API_KEY = os.getenv("MODEL_API_KEY")  # opcional: si no está, el bot hace eco
# Owner / Admin fijo
OWNER_ID = 6699273462  # <-- tu ID como OWNER

# VIP storage file
VIP_FILE = "vip.json"

# Sticker inicial (el que enviaste)
WELCOME_STICKER = "CAACAgIAAxkBAAE9zfZpFn1UazwnPoOGdDU_IJ2WcahNHwACnhkAAgKv0UqqybtL4rQGYjYE"

# Menú principal (teclado)
MAIN_MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📝 Chat IA"), KeyboardButton("🖼 Editar Imagen (pronto)")],
        [KeyboardButton("⚙️ Configuración (pronto)"), KeyboardButton("ℹ️ Info VIP")],
    ],
    resize_keyboard=True,
)

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("bot_vip_final")

# HTTP session global
SESSION: Optional[aiohttp.ClientSession] = None


async def get_session() -> aiohttp.ClientSession:
    global SESSION
    if SESSION is None or SESSION.closed:
        SESSION = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
    return SESSION


# ---------------- VIP storage helpers ----------------
def ensure_vip_file():
    if not os.path.exists(VIP_FILE):
        save_vip([])


def load_vip() -> List[int]:
    try:
        ensure_vip_file()
        with open(VIP_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("vips", [])
    except Exception as e:
        logger.exception("Error cargando vip.json:")
        return []


def save_vip(vips: List[int]):
    try:
        with open(VIP_FILE, "w", encoding="utf-8") as f:
            json.dump({"vips": vips}, f, indent=2, ensure_ascii=False)
    except Exception:
        logger.exception("Error guardando vip.json")


def is_vip(user_id: int) -> bool:
    # Owner es siempre VIP
    if user_id == OWNER_ID:
        return True
    return user_id in load_vip()


# ---------------- AI call (async) ----------------
async def call_text_model(messages: List[dict]) -> str:
    """
    Llamada async al modelo. Si no hay MODEL_API_KEY devuelve eco.
    Ajusta URL/payload según tu proveedor si hace falta.
    """
    if not MODEL_API_KEY:
        # Modo eco para pruebas
        return messages[-1].get("content", "")

    session = await get_session()
    url = "https://integrate.api.nvidia.com/v1/chat/completions"  # cambiar si usas otro
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
                txt = await resp.text()
                logger.error("Error modelo %s %s", resp.status, txt)
                return "⚠️ El modelo no respondió correctamente."
    except asyncio.TimeoutError:
        return "⏳ El modelo tardó demasiado."
    except Exception:
        logger.exception("Error llamando al modelo")
        return "❌ Error conectando con la IA."


# ---------------- Admin logs helper ----------------
async def send_log_to_owner(text: str):
    if not TELEGRAM_TOKEN or not OWNER_ID:
        return
    try:
        session = await get_session()
        await session.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": OWNER_ID, "text": text},
            timeout=5,
        )
    except Exception:
        pass


# ---------------- Handlers ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id
    name = user.first_name or user.username or "usuario"

    # Si no es VIP, rechazar
    if not is_vip(uid):
        await update.message.reply_text(
            "🚫 Este bot es SOLO para usuarios VIP.\n\n"
            "Si quieres acceso, contacta al administrador."
        )
        return

    # Enviar sticker de bienvenida
    try:
        st_msg = await context.bot.send_sticker(chat_id=update.effective_chat.id, sticker=WELCOME_STICKER)
    except Exception as e:
        logger.debug("No se pudo enviar sticker: %s", e)
        st_msg = None

    # Esperar 3 segundos (temporizador)
    await asyncio.sleep(3)

    # Borrar sticker (si se envió)
    if st_msg:
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=st_msg.message_id)
        except Exception:
            pass

    # Simular typing breve antes del mensaje final
    try:
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    except Exception:
        pass

    # Mensaje de bienvenida con broma dominicana y menú
    welcome_text = (
        f"👋 ¡Qué lo qué, {name}! ✅ Estás en modo *VIP*.\n\n"
        "Aquí no se juega: pregúntame algo técnico o pide edición de imágenes cuando esté listo.\n"
        "Si quieres ver opciones, usa el menú abajo.\n\n"
        "PD: Si me pides que sea suave, te devuelvo una respuesta suave. Si me pides que sea brutal, te devuelvo brutal. 😎"
    )
    await update.message.reply_text(welcome_text, reply_markup=MAIN_MENU, parse_mode="Markdown")

    # Log al owner
    await send_log_to_owner(f"🔔 /start usado por {name} ({uid})")


async def vip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Comando admin para manejar VIPs:
    /vip add <id>
    /vip remove <id>
    /vip list
    """
    uid = update.effective_user.id
    if uid != OWNER_ID:
        return await update.message.reply_text("No tienes permiso para usar este comando.")

    args = context.args or []
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
        await send_log_to_owner(f"VIP agregado: {target} por {uid}")
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
        await send_log_to_owner(f"VIP eliminado: {target} por {uid}")
        return

    if cmd == "list":
        if not vips:
            return await update.message.reply_text("No hay usuarios VIP.")
        text = "⭐ Usuarios VIP:\n" + "\n".join([f"- `{x}`" for x in vips])
        return await update.message.reply_text(text, parse_mode="Markdown")

    await update.message.reply_text("Comando inválido. Uso: /vip add <id> | remove <id> | list")


async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    # Opciones del menú
    if text == "📝 Chat IA":
        await update.message.reply_text("Escribe tu mensaje y te respondo.")
        return
    if text == "🖼 Editar Imagen (pronto)":
        await update.message.reply_text("🖼 Módulo de edición de imágenes: *próximamente* (placeholder).")
        return
    if text == "⚙️ Configuración (pronto)":
        await update.message.reply_text("⚙️ configuración: pronto.")
        return
    if text == "ℹ️ Info VIP":
        vips = load_vip()
        msg = f"⭐ Usuarios VIP: {len(vips)} (Owner incluido)\nContacta al OWNER para acceso."
        await update.message.reply_text(msg)
        return

    # Si no es una opción de menú, lo tratamos como consulta IA
    # No bloqueamos: lanzamos task para procesar en background
    asyncio.create_task(process_ai_request(update, context))


async def process_ai_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Procesa la petición a la IA en background para no bloquear handlers.
    """
    uid = update.effective_user.id
    if not is_vip(uid):
        await update.message.reply_text("🚫 Solo VIP puede usar este bot.")
        return

    text = (update.message.text or "").strip()
    if not text:
        return

    chat_id = update.effective_chat.id

    # Mostrar typing
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    except Exception:
        pass

    # Construir prompt (VIP => respuestas completas)
    system_prompt = (
        "Eres un asistente técnico directo y profesional. Responde claro y detallado porque el usuario es VIP."
    )
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": text}]

    # Llamada a la IA (async)
    reply = await call_text_model(messages)

    # Enviar respuesta (manejo básico de errores)
    try:
        await context.bot.send_message(chat_id=chat_id, text=reply)
    except Exception:
        try:
            await context.bot.send_message(chat_id=chat_id, text=reply[:400])
        except Exception:
            logger.exception("No se pudo enviar la respuesta al usuario.")


# ---------------- Run ----------------
def run_bot():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("Falta TELEGRAM_TOKEN en variables de entorno.")

    app = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .concurrent_updates(True)
        .build()
    )

    # handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("vip", vip_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_handler))

    logger.info("🚀 Ejecutando bot_vip_final (polling)...")
    app.run_polling(drop_pending_updates=True)


# Entrypoint
if __name__ == "__main__":
    ensure_vip_file()
    run_bot()
