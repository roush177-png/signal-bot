import os
import logging
import requests
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# Настройка логов
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Получаем токен и чат ID из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

if not BOT_TOKEN or not CHAT_ID:
    logger.error("❌ BOT_TOKEN или CHAT_ID не заданы в переменных окружения. Выход.")
    exit(1)

# Проверка API (пример: Binance)
def get_price(symbol="ADAUSDT"):
    try:
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
        response = requests.get(url, timeout=5)
        data = response.json()
        return float(data["price"])
    except Exception as e:
        logger.error(f"Ошибка при получении цены {symbol}: {e}")
        return None

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Бот запущен и готов работать!")

# Команда /price
async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbols = ["ADAUSDT", "XRPUSDT", "SOLUSDT"]
    reply = "📊 Актуальные цены:\n"
    for s in symbols:
        p = get_price(s)
        reply += f"{s}: {p}\n" if p else f"{s}: ошибка\n"
    await update.message.reply_text(reply)

def main():
    logger.info("🚀 Запуск бота...")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Регистрируем команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("price", price))

    # Запуск бота
    app.run_polling()

if __name__ == "__main__":
    main()
