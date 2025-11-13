from flask import Flask
import threading
import telegram_bot

app = Flask(__name__)

@app.route('/')
def index():
    return "✅ Bot activo y escuchando en Telegram."

def start_bot():
    telegram_bot.run_bot()

if __name__ == "__main__":
    # Inicia el bot en un hilo separado
    threading.Thread(target=start_bot, daemon=True).start()
    # Flask sirve keepalive
    app.run(host="0.0.0.0", port=8080)
