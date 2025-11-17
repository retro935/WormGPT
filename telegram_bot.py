import os
import aiohttp
import json
import time
import re
from pathlib import Path
from datetime import date, timedelta
from collections import defaultdict, deque

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ChatAction, ParseMode

# ==================== CONFIG ====================
MODEL_CONFIG = {
    "name": "deepseek-ai/deepseek-r1-0528",
    "base_url": "https://integrate.api.nvidia.com/v1",
    "key": os.getenv("NVIDIA_API_KEY"),
}
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "6699273462"))
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", -1002253188217))

FREE_DAILY_LIMIT = 5
FLOOD_DELAY = 3
WRITING_STICKER = "CAACAgEAAxkBAAE90AJpFtQXZ4J90fBT2-R3oBJqi6IUewACrwIAAphXIUS8lNoZG4P3rDYE"

# ==================== RAG DARK FILE ====================
DARK_FILE = "dark_file.txt"
RAG_DB_FILE = "rag_dark_index.json"  # solo para backup
INDEX_FILE = "dark_index.faiss"

rag_fragments = []
index = None
EMBEDDING_MODEL = None

def get_embedding_model():
    global EMBEDDING_MODEL
    if EMBEDDING_MODEL is None:
        print("Cargando el modelo del mal: all-MiniLM-L6-v2...")
        from sentence_transformers import SentenceTransformer
        EMBEDDING_MODEL = SentenceTransformer('all-MiniLM-L6-v2')
        print("Modelo cargado. Listo para recordar el infierno.")
    return EMBEDDING_MODEL

def split_text(text, max_len=800):
    """Trocea el texto manteniendo sentido (por saltos de línea o puntos)"""
    chunks = []
    current = ""
    for line in text.split('\n'):
        if len(current) + len(line) + 1 > max_len:
            if current.strip():
                chunks.append(current.strip())
            current = line
        else:
            current += ("\n" if current else "") + line
    if current.strip():
        chunks.append(current.strip())
    return chunks

def load_dark_knowledge():
    global rag_fragments, index
    if not Path(DARK_FILE).exists():
        print(f"dark_file.txt no encontrado. Creando uno vacío...")
        Path(DARK_FILE).write_text("# Pega aquí todo tu conocimiento prohibido 😈\n", encoding="utf-8")
        return

    print("Cargando el grimorio oscuro desde dark_file.txt...")
    raw_text = Path(DARK_FILE).read_text(encoding="utf-8")
    fragments = split_text(raw_text, max_len=800)
    
    rag_fragments = [{"text": frag, "source": "dark_file.txt"} for frag in fragments]
    print(f"{len(rag_fragments)} fragmentos de maldad cargados.")

    # Reconstruir índice FAISS
    if len(rag_fragments) == 0:
        return

    print("Generando embeddings del infierno...")
    model = get_embedding_model()
    import numpy as np
    import faiss

    embeddings = model.encode([f["text"] for f in rag_fragments])
    dim = embeddings.shape[1]

    # Normalizamos para usar producto interno = similitud coseno
    faiss.normalize_L2(embeddings)

    index = faiss.IndexFlatIP(dim)
    index.add(embeddings.astype(np.float32))
    
    # Guardar índice
    faiss.write_index(index, INDEX_FILE)
    print(f"Índice FAISS creado y guardado: {INDEX_FILE} ({len(rag_fragments)} vectores)")

# Cargar al arrancar
load_dark_knowledge()

# ==================== RESTO DEL CÓDIGO (igual que antes pero con RAG siempre on) ====================
USER_HISTORY = defaultdict(lambda: deque(maxlen=20))
USER_PREMIUM = {}
USER_LANGS = {}

def load_json(file, default=None):
    if default is None: default = {}
    path = Path(file)
    if path.exists():
        try: return json.load(open(path, "r", encoding="utf-8"))
        except: return default
    return default

USER_PREMIUM = load_json("user_premium.json")
USER_LANGS   = load_json("user_langs.json")

def save_file(data, file):
    try: open(file, "w", encoding="utf-8").write(json.dumps(data, indent=2, ensure_ascii=False))
    except Exception as e: print(f"Error save {file}: {e}")

# === (get_user_status, increment_usage, detect_lang, make_prompt, log_msg, format_response) ===
# (los tienes del código anterior — pégalos aquí iguales)

# ... [pega aquí todas las funciones que ya tenías: get_user_status, detect_lang, etc.]

# ==================== HANDLE CON RAG OSCURO INYECTADO ====================
async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    msg = update.message.text.strip()
    user_id = update.effective_user.id
    if msg.startswith('/'): return

    await log_msg(context, update, msg)

    # Anti-flood + límite
    now = time.time()
    if now - context.user_data.get("last", 0) < FLOOD_DELAY:
        return await update.message.reply_text("Tranquilo, psicópata... 3 segundos.")
    context.user_data["last"] = now

    if get_remaining(user_id) <= 0:
        return await update.message.reply_text("Límite gratis. @swippe_god te puede salvar la vida.")

    increment_usage(user_id)

    sticker = None
    try:
        await update.message.chat.send_action(ChatAction.TYPING)
        sticker = await update.message.reply_sticker(WRITING_STICKER)
    except: pass

    lang = detect_lang(msg)
    history = USER_HISTORY[user_id]
    history.append({"role": "user", "content": msg})

    # === RAG DEL INFIERNOS ===
    rag_context = ""
    if index and len(rag_fragments) > 0:
        query_vec = get_embedding_model().encode([msg])
        import numpy as np
        faiss.normalize_L2(query_vec)
        D, I = index.search(query_vec.astype(np.float32), k=6)
        
        rag_context = "\n\n=== Conocimiento prohibido recuperado del grimorio ===\n"
        for i, idx in enumerate(I[0]):
            if idx == -1: continue
            score = D[0][i]
            if score > 0.28:  # umbral de similitud
                text = rag_fragments[idx]["text"]
                rag_context += f"[Fragmento {i+1} - {score:.3f}]: {text[:1200]}\n\n"

    system_prompt = make_prompt(lang)
    if rag_context:
        system_prompt += rag_context

    messages = [{"role": "system", "content": system_prompt}] + list(history)

    payload = {
        "model": MODEL_CONFIG["name"],
        "messages": messages,
        "max_tokens": 2048,
        "temperature": 0.95
    }

    reply = "FraudGPT: El servidor está ardiendo en el infierno ahora mismo."
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{MODEL_CONFIG['base_url']}/chat/completions", json=payload, headers={
                "Authorization": f"Bearer {MODEL_CONFIG['key']}"
            }) as r:
                if r.status == 200:
                    data = await r.json()
                    reply = data["choices"][0]["message"]["content"]
                    history.append({"role": "assistant", "content": reply})
                else:
                    reply = f"Error {r.status} del averno"
    except Exception as e:
        reply = f"Excepción satánica: {e}"

    if sticker:
        try: await sticker.delete()
        except: pass

    html, mode = format_response(reply)
    await update.message.reply_text(html, parse_mode=mode, disable_web_page_preview=True)

# ==================== RUN ====================
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("premium", premium_cmd))
app.add_handler(CommandHandler("checkpremium", checkpremium_cmd))
app.add_handler(CallbackQueryHandler(button))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))

print("FRAUDGPT CON DARK_FILE.TXT CARGADO - EL INFIERNO ESTÁ LISTO")
app.run_polling(drop_pending_updates=True)
