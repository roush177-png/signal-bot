# signal_bot.py
import os
import json
import time
import logging
import signal
import sys
from datetime import datetime, timedelta, timezone

import pytz
import ccxt
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)

# ==================== КОНФИГУРАЦИЯ ====================
BOT_TOKEN = os.environ.get('BOT_TOKEN', '8451464428:AAHgmnQGnw7i13XKU4SZd6KClZ33vcJtdhU')
CHAT_ID = os.environ.get('CHAT_ID', '430720211')
ALLOWED_CHAT_IDS = {int(CHAT_ID)}

TZ = pytz.timezone("Asia/Almaty")
PULSE_MINUTE = 1

STATE_FILE = "state.json"
RUN_LOG = "bot.log"

# Настройка логирования для Railway
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)  # Важно для Railway!
    ]
)
logger = logging.getLogger(__name__)

def log(line: str):
    logger.info(line)
    print(f"[{datetime.now()}] {line}", flush=True)  # flush=True для Railway

# ==================== СЛУЖЕБНЫЕ ФУНКЦИИ ====================
def load_json(path: str, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        log(f"ERR load_json({path}): {e}")
    return default

def save_json(path: str, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"ERR save_json({path}): {e}")

def now_tz():
    return datetime.now(TZ)

# ==================== СОСТОЯНИЕ ====================
def load_state():
    return load_json(STATE_FILE, {"enabled": True, "monitored_symbols": ["SOLUSDT", "ADAUSDT"]})

def save_state(state: dict):
    save_json(STATE_FILE, state)

# ==================== BYBIT API ====================
CCXT_SYMBOLS = {
    "SOLUSDT": "SOL/USDT:USDT",
    "ADAUSDT": "ADA/USDT:USDT", 
    "BTCUSDT": "BTC/USDT:USDT",
}

exchange = ccxt.bybit({
    "enableRateLimit": True,
    "options": {"defaultType": "future"}
})

# ==================== ТОРГОВАЯ ЛОГИКА ====================
def fetch_last_closed_h1_candle(symbol_bot: str):
    market = CCXT_SYMBOLS.get(symbol_bot)
    if not market:
        raise ValueError(f"Неизвестный символ {symbol_bot}")
    ohlcv = exchange.fetch_ohlcv(market, timeframe="1h", limit=2)
    if not ohlcv or len(ohlcv) < 2:
        raise RuntimeError("Недостаточно данных OHLCV")
    ts, op, hi, lo, cl, vol = ohlcv[-2]
    t_open = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).astimezone(TZ)
    t_close = t_open + timedelta(hours=1)
    return {
        "t_open": t_open, "t_close": t_close,
        "open": float(op), "high": float(hi), "low": float(lo),
        "close": float(cl), "volume": float(vol),
    }

def get_prev_levels(symbol: str, level_tf: str = "1d"):
    if level_tf == "1d":
        d = fetch_last_closed_d1(symbol)
    elif level_tf == "4h":
        d = fetch_last_closed_h4(symbol)
    else:
        raise ValueError(f"Неизвестный level_tf: {level_tf}")
    return d["open"], d["high"], d["low"], d["close"]

def fetch_last_closed_d1(symbol_bot: str):
    market = CCXT_SYMBOLS.get(symbol_bot)
    if not market:
        raise ValueError(f"Неизвестный символ {symbol_bot}")
    ohlcv = exchange.fetch_ohlcv(market, timeframe="1d", limit=3)
    if not ohlcv or len(ohlcv) < 2:
        raise RuntimeError("Недостаточно данных D1")
    ts, op, hi, lo, cl, vol = ohlcv[-2]
    t_open = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).astimezone(TZ)
    t_close = t_open + timedelta(days=1)
    return {
        "t_open": t_open, "t_close": t_close,
        "open": float(op), "high": float(hi), "low": float(lo),
        "close": float(cl), "volume": float(vol)
    }

def fetch_last_closed_h4(symbol_bot: str):
    market = CCXT_SYMBOLS.get(symbol_bot)
    if not market:
        raise ValueError(f"Неизвестный символ {symbol_bot}")
    ohlcv = exchange.fetch_ohlcv(market, timeframe="4h", limit=3)
    if not ohlcv or len(ohlcv) < 2:
        raise RuntimeError("Недостаточно данных H4")
    ts, op, hi, lo, cl, vol = ohlcv[-2]
    t_open = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).astimezone(TZ)
    t_close = t_open + timedelta(hours=4)
    return {
        "t_open": t_open, "t_close": t_close,
        "open": float(op), "high": float(hi), "low": float(lo),
        "close": float(cl), "volume": float(vol)
    }

# ==================== ФОРМАТ ОТЧЕТА ====================
def fmt_candle_report(symbol: str, c: dict, tf: str = "1h") -> str:
    return (
        f"📊 Отчёт по закрытой {tf.upper()}:\n\n"
        f"Актив: {symbol}\n"
        f"Время: {c['t_open'].strftime('%d.%m.%Y г. %H:00')}-{c['t_close'].strftime('%H:00')}\n"
        f"Свеча: {tf.upper()}\n"
        f"Цена открытия: {c['open']:.6f}\n"
        f"Цена закрытия: {c['close']:.6f}\n"
        f"Характер свечи: {'🟢 Зелёная' if c['close']>=c['open'] else '🔴 Красная'}\n"
        f"📈 Максимум: {c['high']:.6f}\n"
        f"📉 Минимум: {c['low']:.6f}\n"
        f"📊 Объём: {c['volume']:.2f}"
    )

# ==================== TELEGRAM КОМАНДЫ ====================
def ensure_chat_allowed(update: Update) -> bool:
    return (update.effective_chat and update.effective_chat.id in ALLOWED_CHAT_IDS)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ensure_chat_allowed(update): return
    state = load_state(); state["enabled"] = True; save_state(state)
    await update.message.reply_text("✅ Бот запущен. Команды: /status, /stop")

async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ensure_chat_allowed(update): return
    state = load_state(); state["enabled"] = False; save_state(state)
    await update.message.reply_text("⏸ Пауза. /start — продолжить")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ensure_chat_allowed(update): return
    state = load_state()
    txt = f"🤖 Статус: {'✅ ВКЛ' if state.get('enabled', True) else '⏸ ВЫКЛ'}\n"
    txt += f"📊 Мониторинг: {', '.join(state.get('monitored_symbols', []))}\n"
    txt += f"⏰ Время сервера: {now_tz().strftime('%d.%m.%Y %H:%M:%S %Z')}"
    await update.message.reply_text(txt)

async def echo_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    if update.message.text.strip().lower() == "ping":
        await update.message.reply_text("🏓 pong")

# ==================== ОСНОВНАЯ ЛОГИКА ====================
def compute_next_pulse(now_dt: datetime) -> datetime:
    base = now_dt.replace(second=0, microsecond=0)
    candidate = base.replace(minute=PULSE_MINUTE)
    if candidate <= now_dt:
        candidate += timedelta(hours=1)
    return candidate

async def hourly_pulse_job(context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    if not state.get("enabled", True):
        return

    symbols = state.get("monitored_symbols", ["SOLUSDT", "ADAUSDT"])
    log(f"🔔 Выполнение пульса для: {symbols}")

    for sym in symbols:
        try:
            candle = fetch_last_closed_h1_candle(sym)
            report = fmt_candle_report(sym, candle)
            
            for cid in ALLOWED_CHAT_IDS:
                try:
                    await context.bot.send_message(chat_id=cid, text=report)
                    log(f"✅ Отправлен отчет для {sym}")
                except Exception as e:
                    log(f"❌ Ошибка отправки {sym}: {e}")
                    
        except Exception as e:
            log(f"❌ Ошибка пульса {sym}: {e}")

# ==================== ЗАВЕРШЕНИЕ РАБОТЫ ====================
def graceful_shutdown(signum, frame):
    log("🔄 Получен сигнал завершения, сохраняем состояние...")
    try:
        state = load_state()
        save_state(state)
        log("✅ Состояние сохранено")
    except Exception as e:
        log(f"❌ Ошибка сохранения: {e}")
    sys.exit(0)

signal.signal(signal.SIGINT, graceful_shutdown)
signal.signal(signal.SIGTERM, graceful_shutdown)

# ==================== ЗАПУСК ПРИЛОЖЕНИЯ ====================
def build_app():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Регистрация команд
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo_ping))

    # Планировщик заданий
    next_pulse = compute_next_pulse(now_tz())
    first_delay = max(0.0, (next_pulse - now_tz()).total_seconds())
    
    app.job_queue.run_repeating(
        hourly_pulse_job,
        interval=3600,
        first=first_delay,
        name="hourly_pulse"
    )
    
    log(f"⏰ Первый пульс в: {next_pulse.strftime('%H:%M:%S')}")
    return app

def main():
    log("🚀 Запуск бота на Railway...")
    log(f"👤 Разрешенные чаты: {ALLOWED_CHAT_IDS}")
    log(f"⏰ Временная зона: {TZ}")
    
    try:
        # Проверка подключения к бирже
        markets = exchange.load_markets()
        log("✅ Подключение к Bybit успешно")
    except Exception as e:
        log(f"❌ Ошибка подключения к Bybit: {e}")
        return

    try:
        app = build_app()
        log("✅ Бот готов к работе")
        app.run_polling()
    except Exception as e:
        log(f"❌ Критическая ошибка: {e}")
        log("🔄 Перезапуск через 10 секунд...")
        time.sleep(10)
        main()  # Рекурсивный перезапуск

if __name__ == "__main__":
    main()
