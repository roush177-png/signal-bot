# signal_bot.py
# -*- coding: utf-8 -*-
"""
Oblivion Signal Bot — автоматический мониторинг и сигналы на основе структуры.

Требуется:
    pip install python-telegram-bot==21.6 ccxt==4.4.66 pytz==2024.1
"""

import json
import os
import time
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

# -------------------- КОНФИГ --------------------

BOT_TOKEN = "8451464428:AAHgmnQGnw7i13XKU4SZd6KClZ33vcJtdhU"
ALLOWED_CHAT_IDS = {430720211}  # строгий доступ

TZ = pytz.timezone("Asia/Almaty")
PULSE_MINUTE = 1  # :01 каждого часа

STATE_FILE = "state.json"
RUN_LOG = "run.log"

# Мониторируемые символы (можно добавить через команду /add_symbol)
MONITORED_SYMBOLS = ["SOLUSDT", "ADAUSDT"]

# Bybit perpetual tickers for ccxt
CCXT_SYMBOLS = {
    "SOLUSDT": "SOL/USDT:USDT",
    "ADAUSDT": "ADA/USDT:USDT",
    "BTCUSDT": "BTC/USDT:USDT",  # для фильтра BTC
    "LINKUSDT": "LINK/USDT:USDT",  # Chainlink perpetual
    "DOTUSDT": "DOT/USDT:USDT",    # Polkadot perpetual
    "XRPUSDT": "XRP/USDT:USDT",    # Ripple perpetual
}

# Параметры логики
RVOL_SWEEP_MIN = 1.2       # мин. относительный объём для фиксации sweep
EPS_LEVEL = 0.0005         # допуск при сравнении уровней (PDH/PDL)
ATR_PERIOD = 14            # ATR(H1) для правил
EMA_TREND_PERIOD = 20      # EMA(H1) для UP/DOWN режима по структуре

# Автоматические сигналы
BUFFER_ATR_K = 0.5         # буфер для стопов в ATR (чтобы цена "дышала")
RR_TP = [1.0, 2.0, 3.0]    # R:R для TP1/TP2/TP3
PARTIAL_SCHEME = "40/40/20"
FVG_REQUIRED = False
RISK_FILTERS = True
BTC_WINDOW = 5
VOL_WINDOW = 20
VOL_ANOMALY = 1.5

# Реал-тайм: M15 вместо M5
REALTIME_TF = "15m"  
REALTIME_CHECK_INTERVAL = 900  # секунды, проверка каждые 15 мин

exchange = ccxt.bybit({"enableRateLimit": True})
NEXT_PULSE_AT = None  # для /pulse

# -------------------- УТИЛЫ --------------------

def log(line: str):
    ts = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    msg = f"[{ts}] {line}"
    print(msg, flush=True)
    try:
        with open(RUN_LOG, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass

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
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        log(f"ERR save_json({path}): {e}")

def now_tz():
    return datetime.now(TZ)

def as_float_or_none(x):
    try:
        if x is None: return None
        return float(x)
    except Exception:
        return None

def split_floats_csv(x):
    if not x:
        return []
    out = []
    for p in str(x).replace(";", ",").split(","):
        p = p.strip()
        if not p:
            continue
        v = as_float_or_none(p)
        if v is not None:
            out.append(v)
    return out

# -------------------- СОСТОЯНИЕ --------------------

def load_state():
    return load_json(STATE_FILE, {"enabled": True, "monitored_symbols": MONITORED_SYMBOLS})

def save_state(state: dict):
    save_json(STATE_FILE, state)

# -------------------- РЫНОК --------------------

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

def fetch_last_n_h1_candles(symbol_bot: str, n: int):
    market = CCXT_SYMBOLS.get(symbol_bot)
    if not market:
        raise ValueError(f"Неизвестный символ {symbol_bot}")
    limit = max(3, n + 1)
    ohlcv = exchange.fetch_ohlcv(market, timeframe="1h", limit=limit)
    if not ohlcv or len(ohlcv) < 3:
        raise RuntimeError("Недостаточно данных OHLCV")
    ohlcv = ohlcv[:-1]  # убираем текущую незакрытую
    out = []
    for ts, op, hi, lo, cl, vol in ohlcv[-n:]:
        t_open = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).astimezone(TZ)
        t_close = t_open + timedelta(hours=1)
        out.append({
            "t_open": t_open, "t_close": t_close,
            "open": float(op), "high": float(hi), "low": float(lo),
            "close": float(cl), "volume": float(vol)
        })
    return out

def fetch_last_closed_d1(symbol_bot: str):
    market = CCXT_SYMBOLS.get(symbol_bot)
    if not market:
        raise ValueError(f"Неизвестный символ {symbol_bot}")
    ohlcv = exchange.fetch_ohlcv(market, timeframe="1d", limit=3)
    if not ohlcv or len(ohlcv) < 2:
        raise RuntimeError("Недостаточно данных D1")
    ts, op, hi, lo, cl, vol = ohlcv[-2]  # предыдущий закрытый день
    t_open = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).astimezone(TZ)
    t_close = t_open + timedelta(days=1)
    return {
        "t_open": t_open, "t_close": t_close,
        "open": float(op), "high": float(hi), "low": float(lo),
        "close": float(cl), "volume": float(vol)
    }

def fetch_last_closed_lower_tf_candle(symbol_bot: str, tf: str = REALTIME_TF):
    market = CCXT_SYMBOLS.get(symbol_bot)
    if not market:
        raise ValueError(f"Неизвестный символ {symbol_bot}")
    ohlcv = exchange.fetch_ohlcv(market, timeframe=tf, limit=2)
    if not ohlcv or len(ohlcv) < 2:
        raise RuntimeError(f"Недостаточно данных OHLCV на {tf}")
    ts, op, hi, lo, cl, vol = ohlcv[-2]  # последняя закрытая
    t_open = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).astimezone(TZ)
    delta_min = 5 if tf == "5m" else 15
    t_close = t_open + timedelta(minutes=delta_min)
    return {
        "t_open": t_open, "t_close": t_close,
        "open": float(op), "high": float(hi), "low": float(lo),
        "close": float(cl), "volume": float(vol),
    }

def fmt_candle_report(symbol: str, c: dict) -> str:
    return (
        f"Актив: {symbol}\n"
        f"Время: {c['t_open'].strftime('%d.%m.%Y г. %H:00')}-{c['t_close'].strftime('%H:00')}\n"
        f"Свеча: H1\n"
        f"Цена открытия: {c['open']:.6f}\n"
        f"Цена закрытия: {c['close']:.6f}\n"
        f"Характер свечи: {'Зелёная' if c['close']>=c['open'] else 'Красная'}\n"
        f"Прокол вверх: {c['high']:.6f}\n"
        f"Прокол вниз: {c['low']:.6f}\n"
        f"Объём: {c['volume']:.2f}"
    )

# -------------------- ТЕХНИКА: EMA/ATR --------------------

def ema(values, n):
    if not values:
        return None
    k = 2.0 / (n + 1.0)
    ema_val = values[0]
    for v in values[1:]:
        ema_val = (v - ema_val) * k + ema_val
    return ema_val

def atr_from_candles(candles, n=ATR_PERIOD):
    if len(candles) < n + 1:
        return None
    trs = []
    for i in range(-n, 0):
        h = candles[i]["high"]
        l = candles[i]["low"]
        c_prev = candles[i-1]["close"]
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else None

# -------------------- (2) FVG-ФИЛЬТР --------------------

def detect_fvg_h1_zone_last3(symbol_bot: str, direction: str):
    try:
        cs = fetch_last_n_h1_candles(symbol_bot, 3)
    except Exception as e:
        log(f"FVG fetch ERR {symbol_bot}: {e}")
        return None
    if len(cs) < 3:
        return None
    c1, c2, c3 = cs[-3], cs[-2], cs[-1]
    if direction == "long":
        if c2["low"] > c1["high"]:
            top, bot = c1["high"], c2["low"]
            return max(top, bot), min(top, bot)
    else:
        if c2["high"] < c1["low"]:
            top, bot = c2["high"], c1["low"]
            return max(top, bot), min(top, bot)
    return None

def entry_in_zone(entry_price: float, zone):
    if not zone: return False
    top, bot = zone
    return bot <= entry_price <= top

# -------------------- (3) PARTIAL TP/SL --------------------

def parse_partial_scheme(s: str = PARTIAL_SCHEME):
    if not s:
        return [40, 40, 20]
    parts = []
    for p in str(s).split("/"):
        p = p.strip()
        if not p: continue
        try:
            parts.append(int(p))
        except Exception:
            pass
    if not parts:
        parts = [40, 40, 20]
    tot = sum(parts)
    if tot > 100:
        parts = [round(100 * x / tot) for x in parts]
    return parts

def check_tp_hits_and_messages(symbol: str, direction: str, signal: dict, candle: dict):
    msgs, changed = [], False
    tp_list = signal.get("TP", [])
    if not tp_list:
        return msgs, changed

    tp_list = sorted(tp_list)
    prog = signal.get("tp_progress", {"TP1": False, "TP2": False, "TP3": False, "BE": False})
    parts = parse_partial_scheme()

    hi = candle["high"]; lo = candle["low"]
    entry = signal.get("entry")
    sl = signal.get("SL")
    be_level = entry  # по умолчанию entry
    be_level_fmt = f"{be_level:.6f}"

    def hit(tp):
        return (hi >= tp) if direction == "long" else (lo <= tp)

    if len(tp_list) >= 1 and not prog.get("TP1", False) and hit(tp_list[0]):
        msgs.append(f"{symbol} [{direction.upper()}] TP1 hit ({tp_list[0]:.6f}) → close {parts[0]}% | move SL → {be_level_fmt} (BE)")
        prog["TP1"] = True
        if not prog.get("BE", False): prog["BE"] = True
        changed = True

    if len(tp_list) >= 2 and not prog.get("TP2", False) and hit(tp_list[1]):
        close_pct = parts[1] if len(parts) > 1 else 0
        msgs.append(f"{symbol} [{direction.upper()}] TP2 hit ({tp_list[1]:.6f}) → close {close_pct}% | keep tail")
        prog["TP2"] = True
        changed = True

    if len(tp_list) >= 3 and not prog.get("TP3", False) and hit(tp_list[2]):
        close_pct = parts[2] if len(parts) > 2 else 0
        msgs.append(f"{symbol} [{direction.upper()}] TP3 hit ({tp_list[2]:.6f}) → close {close_pct}% | trade complete ✅")
        prog["TP3"] = True
        changed = True

    signal["tp_progress"] = prog
    return msgs, changed

# -------------------- (4) BTC + VOLUME ФИЛЬТРЫ --------------------

def btc_headwind_status(window=BTC_WINDOW):
    try:
        cs = fetch_last_n_h1_candles("BTCUSDT", window)
    except Exception as e:
        log(f"BTC fetch ERR: {e}")
        return "flat", 0.0
    delta = sum(c["close"] - c["open"] for c in cs)
    if delta > 0: return "up", delta
    if delta < 0: return "down", delta
    return "flat", 0.0

def volume_anomaly(symbol_bot: str, window=VOL_WINDOW, last_candle: dict | None = None):
    try:
        cs = fetch_last_n_h1_candles(symbol_bot, max(2, window))
    except Exception as e:
        log(f"VOL fetch ERR {symbol_bot}: {e}")
        return 1.0, "normal"
    vols = [c["volume"] for c in cs[:-1]]
    if not vols:
        return 1.0, "normal"
    avg = sum(vols) / len(vols)
    v = (last_candle or cs[-1])["volume"]
    ratio = (v / avg) if avg > 0 else 1.0
    if ratio >= VOL_ANOMALY: tag = "spike ⚡"
    elif ratio <= 0.5: tag = "weak"
    else: tag = "normal"
    return ratio, tag

def risk_line_for(direction: str, btc_dir: str):
    if btc_dir == "flat":
        return "BTC: neutral → STATUS: OK"
    if direction == "long":
        return "BTC: " + ("with trend → STATUS: OK" if btc_dir == "up" else "against trend → STATUS: CAUTION")
    else:
        return "BTC: " + ("with trend → STATUS: OK" if btc_dir == "down" else "against trend → STATUS: CAUTION")

# -------------------- PDH/PDL + РЕЖИМ --------------------

def get_prev_day_levels(symbol: str):
    """Возвращает (PDO, PDH, PDL, PDC) по предыдущему дню (D1)."""
    d = fetch_last_closed_d1(symbol)
    return d["open"], d["high"], d["low"], d["close"]

def detect_regime(symbol: str, last_c: dict, pdl: float, pdh: float, rvol: float):
    lo = last_c["low"]; hi = last_c["high"]; cl = last_c["close"]
    if (lo < pdl - EPS_LEVEL) and (cl > pdl) and (rvol >= RVOL_SWEEP_MIN):
        return "SWEEP_LONG"
    if (hi > pdh + EPS_LEVEL) and (cl < pdh) and (rvol >= RVOL_SWEEP_MIN):
        return "SWEEP_SHORT"
    # тренд по EMA20(H1)
    try:
        cs = fetch_last_n_h1_candles(symbol, max(EMA_TREND_PERIOD + 5, 30))
        closes = [c["close"] for c in cs]
        e = ema(closes, EMA_TREND_PERIOD)
        return "UP" if cl >= e else "DOWN"
    except Exception:
        return "FLAT"

def btc_bias_sign():
    d, _ = btc_headwind_status()
    return 1 if d == "up" else (-1 if d == "down" else 0), d

# -------------------- СЕССИИ --------------------

ASIA_START, ASIA_END   = 6, 15
EURO_START, EURO_END   = 15, 19
US_START,   US_END     = 19, 26

def session_ranges_kzt(date_dt: datetime):
    d0 = TZ.localize(datetime(date_dt.year, date_dt.month, date_dt.day, 0, 0, 0))
    def rng(h1, h2):
        start = d0 + timedelta(hours=h1)
        end   = d0 + timedelta(hours=h2 if h2 <= 24 else 24)
        if h2 > 24:
            end = d0 + timedelta(days=1, hours=(h2-24))
        return start, end
    return {
        "Asia": rng(ASIA_START, ASIA_END),
        "Europe": rng(EURO_START, EURO_END),
        "US": rng(US_START, US_END),
    }

def session_hilo(symbol: str, date_dt: datetime, sess_name: str):
    rngs = session_ranges_kzt(date_dt)
    if sess_name not in rngs:
        return None
    start, end = rngs[sess_name]
    cs = fetch_last_n_h1_candles(symbol, 60)
    his = []; los = []
    for c in cs:
        if start <= c["t_open"] < end:
            his.append(c["high"]); los.append(c["low"])
    if not his or not los:
        return None
    return max(his), min(los)

# -------------------- АВТОМАТИЧЕСКИЕ СИГНАЛЫ --------------------

def generate_signal(symbol: str, candle: dict, tf: str = "1h"):
    try:
        pdo, pdh, pdl, pdc = get_prev_day_levels(symbol)
        cs = fetch_last_n_h1_candles(symbol, max(ATR_PERIOD+2, 30))
        a = atr_from_candles(cs, n=ATR_PERIOD) or 0.0
        rvol_ratio, rvol_tag = volume_anomaly(symbol, window=VOL_WINDOW, last_candle=candle)
        regime = detect_regime(symbol, candle, pdl, pdh, rvol_ratio)
        close = candle["close"]
    except Exception as e:
        log(f"generate_signal ERR {symbol}: {e}")
        return None

    direction = None
    entry = None
    sl = None
    tp = []
    confirm_thr = None

    if regime == "UP" or regime == "SWEEP_LONG":
        direction = "long"
        entry = pdl + (a * BUFFER_ATR_K / 2)  # entry на retest PDL с буфером
        swing_low = min([c["low"] for c in cs[-5:]])
        sl = swing_low - (a * BUFFER_ATR_K)  # за недавний swing low с буфером
        # Проверка: SL должен быть ниже entry для лонга
        if sl >= entry:
            sl = entry - (a * BUFFER_ATR_K)  # Используем entry - буфер как запасной вариант
        confirm_thr = entry  # confirm above entry
        risk = abs(entry - sl)
        tp = [entry + (risk * r) for r in RR_TP]
        # Фильтр запоздалости для лонга
        if close > entry * 1.005:  # Цена > entry на 0.5%
            log(f"Signal {symbol} skipped: close={close} too far from entry={entry} (long)")
            return None
    elif regime == "DOWN" or regime == "SWEEP_SHORT":
        direction = "short"
        entry = pdh - (a * BUFFER_ATR_K / 2)  # entry на retest PDH с буфером
        swing_high = max([c["high"] for c in cs[-5:]])
        sl = swing_high + (a * BUFFER_ATR_K)  # за недавний swing high с буфером
        # Проверка: SL должен быть выше entry для шорта
        if sl <= entry:
            sl = entry + (a * BUFFER_ATR_K)  # Используем entry + буфер как запасной вариант
        confirm_thr = entry  # confirm below entry
        risk = abs(sl - entry)
        tp = [entry - (risk * r) for r in RR_TP]
        # Фильтр запоздалости для шорта
        if close < entry * 0.995:  # Цена < entry на 0.5%
            log(f"Signal {symbol} skipped: close={close} too far from entry={entry} (short)")
            return None

    # Логирование для отладки
    if direction:
        log(f"Signal {symbol}: regime={regime}, entry={entry}, sl={sl}, tp={tp}, pdl={pdl}, pdh={pdh}, swing_low={min([c['low'] for c in cs[-5:]])}")

    if direction is None:
        return None
    return {
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "confirm_thr": confirm_thr,
        "regime": regime,
        "atr": a,
        "rvol_ratio": rvol_ratio,
        "rvol_tag": rvol_tag,
        "tf": tf,
    }
    if not direction:
        return None

    # FVG check
    if FVG_REQUIRED:
        zone = detect_fvg_h1_zone_last3(symbol, direction)
        if not zone or not entry_in_zone(entry, zone):
            return None

    # Sweep block
    sweep_block = (regime == "SWEEP_LONG" and direction == "short") or (regime == "SWEEP_SHORT" and direction == "long")
    if sweep_block:
        return None

    return {
        "direction": direction,
        "entry": entry,
        "SL": sl,
        "TP": tp,
        "confirm_thr": confirm_thr,
        "regime": regime,
        "atr": a,
        "rvol_ratio": rvol_ratio,
        "rvol_tag": rvol_tag,
        "tp_progress": {"TP1": False, "TP2": False, "TP3": False, "BE": False},
        "early_confirmed": False if tf != "1h" else True
    }

def check_confirmation(direction: str, signal: dict, close_price: float):
    thr = signal.get("confirm_thr")
    if direction == "short":
        if thr is not None and close_price <= thr:
            return True, "Confirm short (C)", f"close {close_price:.4f} ≤ {thr}"
    else:
        if thr is not None and close_price >= thr:
            return True, "Confirm long (C)", f"close {close_price:.4f} ≥ {thr}"
    return False, None, None

# -------------------- ПУЛЬС JOBQUEUE --------------------

def compute_next_pulse(now_dt: datetime) -> datetime:
    base = now_dt.replace(second=0, microsecond=0)
    candidate = base.replace(minute=PULSE_MINUTE)
    if candidate <= now_dt:
        candidate += timedelta(hours=1)
    return candidate

async def hourly_pulse_job(context: ContextTypes.DEFAULT_TYPE):
    global NEXT_PULSE_AT
    state = load_state()
    if not state.get("enabled", True):
        return

    symbols = state.get("monitored_symbols", MONITORED_SYMBOLS)

    candles = {}
    signals = {}
    for sym in symbols:
        try:
            candles[sym] = fetch_last_closed_h1_candle(sym)
            signals[sym] = generate_signal(sym, candles[sym])
        except Exception as e:
            log(f"pulse ERR {sym}: {e}")

    btc_bias, btc_dir_str = btc_bias_sign()

    reports_done = set()
    parts = []
    changed_any = False

    for sym in symbols:
        c = candles.get(sym)
        if not c: continue

        if sym not in reports_done:
            parts.append("📊 Отчёт по закрытой H1:\n\n" + fmt_candle_report(sym, c))
            reports_done.add(sym)

        signal = signals.get(sym)
        if not signal: continue

        direction = signal["direction"]
        ok, label, reason = check_confirmation(direction, signal, c["close"])

        if ok:
            line = f"\n⚡️ {sym} [{direction.upper()}]: {label} — {reason}\n"
            line += f"entry={signal['entry']:.6f} SL={signal['SL']:.6f} TP={','.join(f'{t:.6f}' for t in signal['TP'])}\n"
            line += risk_line_for(direction, btc_dir_str) + "\n"
            line += f"VOL: {signal['rvol_ratio']:.2f}×avg → {signal['rvol_tag']}\n"
            line += f"context: regime={signal['regime']} ATR={signal['atr']:.4f}\n"
            parts.append(line)

        msgs, changed = check_tp_hits_and_messages(sym, direction, signal, c)
        if msgs: parts.extend(msgs)
        if changed: changed_any = True

    if parts:
        text = "\n".join(parts)
        for cid in ALLOWED_CHAT_IDS:
            try:
                await context.bot.send_message(chat_id=cid, text=text)
            except Exception as e:
                log(f"send ERR chat {cid}: {e}")

    NEXT_PULSE_AT = compute_next_pulse(now_tz())

# Реал-тайм на M15
async def realtime_monitor_job(context: ContextTypes.DEFAULT_TYPE):
    state = load_state()
    if not state.get("enabled", True):
        return

    symbols = state.get("monitored_symbols", MONITORED_SYMBOLS)

    now = now_tz()
    next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    time_to_hour_end = (next_hour - now).total_seconds()
    if time_to_hour_end <= 0:
        log("realtime: уже конец часа, пропускаем")
        return

    lower_candles = {}
    signals = {}
    for sym in symbols:
        try:
            lower_candles[sym] = fetch_last_closed_lower_tf_candle(sym, REALTIME_TF)
            signals[sym] = generate_signal(sym, lower_candles[sym], tf=REALTIME_TF)
        except Exception as e:
            log(f"realtime ERR {sym} {REALTIME_TF}: {e}")

    btc_bias, btc_dir_str = btc_bias_sign()

    parts = []
    for sym in symbols:
        c = lower_candles.get(sym)
        if not c: continue

        signal = signals.get(sym)
        if not signal or signal.get("early_confirmed", False): continue

        direction = signal["direction"]
        ok, label, reason = check_confirmation(direction, signal, c["close"])

        if ok:
            line = f"\n🚨 EARLY {sym} [{direction.upper()}]: {label} — {reason} (на {REALTIME_TF.upper()})\n"
            line += f"entry={signal['entry']:.6f} SL={signal['SL']:.6f} TP={','.join(f'{t:.6f}' for t in signal['TP'])}\n"
            line += risk_line_for(direction, btc_dir_str) + "\n"
            line += f"VOL: {signal['rvol_ratio']:.2f}×avg → {signal['rvol_tag']}\n"
            line += f"context: regime={signal['regime']} ATR={signal['atr']:.4f} TF={REALTIME_TF.upper()}\n"
            parts.append(line)
            signal["early_confirmed"] = True  # mark as sent

    if parts:
        text = "\n".join(parts)
        for cid in ALLOWED_CHAT_IDS:
            try:
                await context.bot.send_message(chat_id=cid, text=text)
            except Exception as e:
                log(f"realtime send ERR chat {cid}: {e}")

# -------------------- ХЕНДЛЕРЫ --------------------

def ensure_chat_allowed(update: Update) -> bool:
    return (update.effective_chat and update.effective_chat.id in ALLOWED_CHAT_IDS)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ensure_chat_allowed(update): return
    state = load_state(); state["enabled"] = True; save_state(state)
    await update.message.reply_text("▶️ Бот запущен. Команды: /status, /pulse, /add_symbol, /remove_symbol, /stop")

async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ensure_chat_allowed(update): return
    state = load_state(); state["enabled"] = False; save_state(state)
    await update.message.reply_text("⏸ Пауза. /start — продолжить")

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ensure_chat_allowed(update): return
    state = load_state()
    txt = f"🤖 enabled={state.get('enabled', True)}\n"
    txt += f"Мониторируемые символы: {', '.join(state.get('monitored_symbols', []))}\n"
    await update.message.reply_text(txt or "Нет символов для мониторинга.")

async def cmd_pulse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ensure_chat_allowed(update): return
    global NEXT_PULSE_AT
    if NEXT_PULSE_AT is None:
        NEXT_PULSE_AT = compute_next_pulse(now_tz())
    await update.message.reply_text(f"🕐 Следующий пульс: {NEXT_PULSE_AT.strftime('%Y-%m-%d %H:%M:%S (%Z)')}")

async def cmd_add_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ensure_chat_allowed(update): return
    args = (update.message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await update.message.reply_text("Использование: /add_symbol <symbol> (e.g. SOLUSDT)")
        return
    sym = args[1].strip().upper()
    if sym not in CCXT_SYMBOLS:
        await update.message.reply_text(f"🚫 Неизвестный symbol={sym}. Разрешены: {', '.join(CCXT_SYMBOLS.keys())}")
        return
    state = load_state()
    mons = state.get("monitored_symbols", [])
    if sym not in mons:
        mons.append(sym)
        state["monitored_symbols"] = mons
        save_state(state)
        await update.message.reply_text(f"✅ Добавлен {sym}")
    else:
        await update.message.reply_text(f"ℹ️ {sym} уже мониторится")

async def cmd_remove_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ensure_chat_allowed(update): return
    args = (update.message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await update.message.reply_text("Использование: /remove_symbol <symbol>")
        return
    sym = args[1].strip().upper()
    state = load_state()
    mons = state.get("monitored_symbols", [])
    if sym in mons:
        mons.remove(sym)
        state["monitored_symbols"] = mons
        save_state(state)
        await update.message.reply_text(f"✅ Удалён {sym}")
    else:
        await update.message.reply_text(f"ℹ️ {sym} не мониторится")

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat: return
    await update.message.reply_text(f"🆔 chat_id = {update.effective_chat.id}")

async def echo_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text: return
    if update.message.text.strip().lower() == "ping":
        await update.message.reply_text("pong")

# -------------------- APP --------------------

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    log(f"Unhandled error: {context.error}")

def build_app():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("pulse", cmd_pulse))
    app.add_handler(CommandHandler("add_symbol", cmd_add_symbol))
    app.add_handler(CommandHandler("remove_symbol", cmd_remove_symbol))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo_ping))

    app.add_error_handler(error_handler)

    global NEXT_PULSE_AT
    NEXT_PULSE_AT = compute_next_pulse(now_tz())
    first_delay = max(0.0, (NEXT_PULSE_AT - now_tz()).total_seconds())
    app.job_queue.run_repeating(
        hourly_pulse_job,
        interval=3600,
        first=first_delay,
        name="hourly_pulse",
        chat_id=None,
    )
    log(f"Планирование пульса на {NEXT_PULSE_AT.strftime('%Y-%m-%d %H:%M:%S %Z')}")

    app.job_queue.run_repeating(
        realtime_monitor_job,
        interval=REALTIME_CHECK_INTERVAL,
        first=10,
        name="realtime_monitor",
        chat_id=None,
    )
    log(f"Планирование реал-тайм мониторинга каждые {REALTIME_CHECK_INTERVAL/60} мин на {REALTIME_TF}")

    return app

def main():
    log("Bot started.")
    app = build_app()
    app.run_polling(close_loop=False)
    log("Bot stopped.")

if __name__ == "__main__":
    main()
