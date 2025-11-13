from keep_alive import keep_alive
from telegram_bot import run_bot

if __name__ == "__main__":
    # Inicia el servidor mínimo (mantiene el contenedor vivo si usas Render)
    keep_alive()
    # Inicia el bot (blocking)
    run_bot()
