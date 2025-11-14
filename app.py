import os
import logging
import asyncio
from datetime import datetime, timedelta
import sqlite3
from openai import OpenAI

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# -------------------- CONFIG --------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MODEL_API_KEY = os.getenv("MODEL_API_KEY")

OWNER_ID = 6699273462
DB_PATH = "vip.db"

# -------------------- LOGGER --------------------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# -------------------- NVIDIA CLIENT --------------------
client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=MODEL_API_KEY
)

# -------------------- DB INIT --------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS vip (
            user_id INTEGER PRIMARY KEY,
            expires_at TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# ---------------- VIP FUNCTIONS -----------------
def is_vip(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT expires_at FROM vip WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return False

    expires = datetime.fromisoformat(row[0])
    return expires > datetime.utcnow()


def add_vip(user_id: int, days: int):
    expires = datetime.utcnow() + timedelta(days=days)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        INSERT OR REPLACE INTO vip (user_id, expires_at)
        VALUES (?, ?)
    """, (user_id, expires.isoformat()))

    conn.commit()
    conn.close()
    return expires


def remove_expired_vips():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM vip WHERE expires_at <= ?", (datetime.utcnow().isoformat(),))
    conn.commit()
    conn.close()

# ---------------- AI REQUEST -----------------
async def ask_ai(prompt: str):
    try:
        # Cargar system prompt
        system_prompt = ""
        if os.path.exists("system-prompts.txt"):
            with open("system-prompts.txt", "r", encoding="utf-8") as f:
                system_prompt = f.read().strip()

        completion = client.chat.completions.create(
            model="deepseek-ai/deepseek-v3.1",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2,
            top_p=0.7,
            max_tokens=2000,
            extra_body={"chat_template_kwargs": {"thinking": False}},
            stream=False
        )

        # DEVUELVE SOLO RESPUESTA FINAL
        final_response = completion.choices[0].message.content
        return final_response

    except Exception as e:
        return f"❌ Error en la IA: {e}"

# ---------------- START -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    await update.message.reply_sticker(
        "CAACAgIAAxkBAAE9zfZpFn1UazwnPoOGdDU_IJ2WcahNHwACnhkAAgKv0UqqybtL4rQGYjYE"
    )
    await asyncio.sleep(1)

    await update.message.reply_text(
        f"👋 Kloq `{user.first_name}`.\n"
        f"🍆 Bienvenido a RetroAI.\n\n"
        f"Usa /menu para ver opciones.",
        parse_mode="Markdown"
    )

# ---------------- MENU -----------------
async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🤖 Usar IA", callback_data="use_ai")],
        [InlineKeyboardButton("🖼 Editar Imagen", callback_data="edit_img")],
        [InlineKeyboardButton("⭐ Estado VIP", callback_data="vip_status")],
        [InlineKeyboardButton("🛠 Admin", callback_data="admin")],
    ]
    await update.message.reply_text(
        "📍 MENÚ",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ---------------- CALLBACKS -----------------
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "vip_status":
        status = "✅ Activo" if is_vip(user_id) else "❌ NO VIP"
        await query.edit_message_text(f"⭐ Tu estado VIP: *{status}*", parse_mode="Markdown")
        return

    if query.data == "admin":
        if user_id != OWNER_ID:
            return await query.edit_message_text("❌ No eres owner.")

        keyboard = [
            [InlineKeyboardButton("➕ Agregar VIP", callback_data="admin_addvip")],
            [InlineKeyboardButton("🗑 Limpiar expirados", callback_data="admin_clean")],
        ]
        return await query.edit_message_text(
            "🛠 Panel administrativo",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    if query.data == "admin_clean":
        remove_expired_vips()
        return await query.edit_message_text("🧹 VIP expirados eliminados.")

    if query.data == "admin_addvip":
        return await query.edit_message_text(
            "Envia:  `/addvip user_id días`",
            parse_mode="Markdown"
        )

    if query.data == "use_ai":
        return await query.edit_message_text("✍️ Escribe tu prompt para la IA.")

    if query.data == "edit_img":
        return await query.edit_message_text("📤 Envia una imagen para editar.")

# ---------------- ADD VIP CMD -----------------
async def addvip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        return await update.message.reply_text("❌ No eres owner.")

    try:
        user_id = int(context.args[0])
        days = int(context.args[1])
    except:
        return await update.message.reply_text("Formato: /addvip 123456 30")

    expires = add_vip(user_id, days)

    await update.message.reply_text(f"✅ VIP agregado hasta {expires}")

    try:
        await context.bot.send_message(
            user_id,
            f"🎉 Fuiste activado como VIP por {days} días.\nDisfruta!"
        )
    except:
        pass

# ---------------- IA MENSAJE -----------------
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_vip(user_id):
        return await update.message.reply_text("❌ Solo usuarios VIP pueden usar la IA.")

    text = update.message.text

    # ----- Sticker pensando -----
    thinking_msg = await context.bot.send_sticker(
        chat_id=update.effective_chat.id,
        sticker="CAACAgEAAxkBAAE90AJpFtQXZ4J90fBT2-R3oBJqi6IUewACrwIAAphXIUS8lNoZG4P3rDYE"
    )

    # ----- Llamada a IA -----
    result = await ask_ai(text)

    # ----- Eliminar sticker -----
    try:
        await thinking_msg.delete()
    except:
        pass

    # ----- Respuesta final -----
    await update.message.reply_text(result, parse_mode="Markdown")

# ---------------- IMAGE -----------------
async def image_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_vip(user_id):
        return await update.message.reply_text("❌ Necesitas VIP.")

    await update.message.reply_text("🖼 Procesando imagen... (demo)")

# ---------------- MAIN -----------------
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("addvip", addvip_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.PHOTO, image_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    print("BOT CORRIENDO...")
    app.run_polling()

if __name__ == "__main__":
    main()
