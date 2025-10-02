# signal_bot.py — для python-telegram-bot v21.6
import os
import logging
import requests
from datetime import datetime

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

# Логирование в stdout — Render покажет это в логах
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")  # оставь строкой, не обязательно int

if not BOT_TOKEN or not CHAT_ID:
    logger.error("BOT_TOKEN или CHAT_ID не заданы в переменных окружения. Выход.")
    raise SystemExit(1)

BASE_TG_SEND = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

def get_last_candle(symbol="SOLUSDT", interval="60"):
    url = "https://api.bybit.com/v5/market/kline"
    params = {
        "category": "linear",
        "symbol": symbol,
        "interval": interval,
        "limit": 2
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    if "result" not in data or "list" not in data["result"] or len(data["result"]["list"]) < 2:
        raise ValueError(f"Unexpected kline response for {symbol}: {data}")
    candle = data["result"]["list"][1]
    ts, o, h, l, c, vol, turnover = candle[:7]
    t_open = datetime.fromtimestamp(int(ts) / 1000)
    return {
        "time": t_open.strftime("%d.%m.%Y %H:%M"),
        "open": float(o),
        "high": float(h),
        "low": float(l),
        "close": float(c),
        "volume": float(vol)
    }

def format_candle(symbol):
    c = get_last_candle(symbol)
    color = "Зеленая" if c["close"] > c["open"] else "Красная"
    return (f"Актив: {symbol}\n"
            f"Время: {c['time']}\n"
            f"Свеча: H1\n"
            f"Цена открытия: {c['open']:.6f}\n"
            f"Цена закрытия: {c['close']:.6f}\n"
            f"Характер свечи: {color}\n"
            f"Прокол вверх: {c['high']:.6f}\n"
            f"Прокол вниз: {c['low']:.6f}\n"
            f"Объём: {c['volume']:.2f}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Бот запущен. Я буду слать отчёты по SOL и TRX каждый час.")

# Фоновая задача, вызывается JobQueue
async def hourly_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        logger.info("Job: собираю и отправляю SOL и TRX")
        sol = format_candle("SOLUSDT")
        trx = format_candle("TRXUSDT")

        # Отправка через context.bot
        await context.bot.send_message(chat_id=CHAT_ID, text=sol)
        await context.bot.send_message(chat_id=CHAT_ID, text=trx)
        logger.info("Job: отправлено успешно")
    except Exception as e:
        logger.exception("Ошибка в hourly_job: %s", e)
        # Попытка уведомить в телеграм (если отправка упадёт — лог будет)
        try:
            await context.bot.send_message(chat_id=CHAT_ID, text=f"⚠️ Ошибка в боте: {e}")
        except Exception:
            logger.exception("Не удалось отправить сообщение об ошибке")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Команда /start
    app.add_handler(CommandHandler("start", start))

    # Добавляем периодическое задание: интервал 3600 секунд (1 час)
    # first=0 — сразу выполняет при старте, можно поставить first=3600 для запуска на следующем часе
    app.job_queue.run_repeating(hourly_job, interval=3600, first=0)

    logger.info("Запускаю приложение (polling).")
    app.run_polling(stop_signals=None)  # Render корректно обрабатывает стопы

if __name__ == "__main__":
    main()

