import os
import logging
import asyncio
from datetime import datetime, timedelta

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputFile,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import sqlite3
import requests

# -------------------------------- CONFIG --------------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MODEL_API_KEY = os.getenv("MODEL_API_KEY")

OWNER_ID = 6699273462  # <-- CAMBIA A TU ID

DB_PATH = "vip.db"

# ------------------------------ LOGGER ------------------------------
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# --------------------------- DB INIT ---------------------------
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

# --------------------------- VIP FUNCTIONS ---------------------------
def is_vip(user_id: int) -> bool:
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


# --------------------------- IA CALL ---------------------------
async def ask_ai(prompt: str):
    url = "https://api.openai.com/v1/chat/completions"

    data = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
    }

    headers = {
        "Authorization": f"Bearer {MODEL_API_KEY}",
        "Content-Type": "application/json",
    }

    r = requests.post(url, json=data, headers=headers)
    res = r.json()

    try:
        return res["choices"][0]["message"]["content"]
    except:
        return "❌ Error en la IA."


# --------------------------- START ---------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # sticker de bienvenida (1.5s)
    await update.message.reply_sticker("CAACAgEAAxkBAAIDGWb_QJVdEo-TU3_N0JVpaz6RtZpmAAJdAANWnb8l1BxPTsCkXys2BA")
    await asyncio.sleep(1.5)

    await update.message.reply_text(
        f"👋 Que lo que `{user.first_name}`.\n"
        f"😈 Bienvenido al bot VIP.\n\n"
        f"Usa /menu para ver opciones."
    )


# --------------------------- MENU ---------------------------
async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🤖 Usar IA", callback_data="use_ai")],
        [InlineKeyboardButton("🖼 Editar Imagen", callback_data="edit_img")],
        [InlineKeyboardButton("⭐ Estado VIP", callback_data="vip_status")],
        [InlineKeyboardButton("🛠 Admin", callback_data="admin")],
    ]
    await update.message.reply_text("📍 MENÚ", reply_markup=InlineKeyboardMarkup(keyboard))


# --------------------------- CALLBACKS ---------------------------
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if query.data == "vip_status":
        status = "✅ Activo" if is_vip(user_id) else "❌ NO VIP"
        await query.edit_message_text(f"⭐ Tu estado VIP: **{status}**")
        return

    if query.data == "admin":
        if user_id != OWNER_ID:
            await query.edit_message_text("❌ No eres owner.")
            return

        keyboard = [
            [InlineKeyboardButton("➕ Agregar VIP", callback_data="admin_addvip")],
            [InlineKeyboardButton("🗑 Limpiar expirados", callback_data="admin_clean")],
        ]

        await query.edit_message_text(
            "🛠 Panel administrativo",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

        return

    if query.data == "admin_clean":
        remove_expired_vips()
        await query.edit_message_text("🧹 VIP expirados eliminados.")
        return

    if query.data == "admin_addvip":
        await query.edit_message_text("Envia:  `/addvip user_id días`", parse_mode="Markdown")
        return

    if query.data == "use_ai":
        await query.edit_message_text("✍️ Escribe tu prompt para la IA.")
        return

    if query.data == "edit_img":
        await query.edit_message_text("📤 Envia una imagen para editar.")
        return


# --------------------------- ADD VIP COMMAND ---------------------------
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

    # notificar al VIP
    try:
        await context.bot.send_message(
            user_id,
            f"🎉 Fuiste activado como VIP por {days} días.\nDisfruta!"
        )
    except:
        pass

    # notificar al owner
    await context.bot.send_message(
        OWNER_ID,
        f"🔔 Nuevo VIP agregado:\nID: {user_id}\nDías: {days}\nHasta: {expires}"
    )


# --------------------------- IA MENSAJES ---------------------------
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not is_vip(user_id):
        return await update.message.reply_text("❌ Solo usuarios VIP pueden usar la IA.")

    text = update.message.text

    response = await ask_ai(text)
    await update.message.reply_text(response)


# --------------------------- IMAGE HANDLER ---------------------------
async def image_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_vip(user_id):
        return await update.message.reply_text("❌ Necesitas VIP.")

    await update.message.reply_text("🖼 Procesando imagen... (demo)")

    # Aquí integrarías tu API de edición (si quieres lo agrego luego)


# --------------------------- MAIN ---------------------------
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CommandHandler("addvip", addvip_cmd))

    app.add_handler(CallbackQueryHandler(callback_handler))

    app.add_handler(MessageHandler(filters.PHOTO, image_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    print("BOT CORRIENDO...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
