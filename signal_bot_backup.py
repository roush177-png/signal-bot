# signal_bot.py — минимальный рабочий шаблон для Telegram-бота
import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Бот запущен и работает 24/7 на Render!")

async def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не найден. Установите его в настройках Render.")
        return
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    logger.info("Bot started. Listening...")
    await app.run_polling()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
