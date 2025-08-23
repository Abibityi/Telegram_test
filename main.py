
HEADERS = {"User-Agent": "Mozilla/5.0"}
import time
import schedule
import telebot
import threading
import requests
import os
import math
import matplotlib.pyplot as plt
import io
import websocket
import json
import threading
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import re



def validate_wallet_inputs(items):
    import re
    valid = []
    errors = []

    eth_pattern = re.compile(r"^0x[a-fA-F0-9]{40}$")

    for item in items:
        item = item.strip()

        if not eth_pattern.fullmatch(item):
            errors.append({
                "input": item,
                "reason": "Ø³Ø§Ø®ØªØ§Ø± Address ÙˆÙ„Øª Ø¨Ø§ÛŒØ¯ Ø¯Ù‚ÛŒÙ‚Ø§ 0x + 40 Ú©Ø§Ø±Ø§Ú©ØªØ± Ù‡Ú¯Ø² Ø¨Ø§Ø´Ø¯"
            })
            continue

        # ÙÙ‚Ø· regex Ú†Ú© Ù…ÛŒâ€ŒØ´ÙˆØ¯
        valid.append(item)

    return valid, errors
    

# ================== Settings ==================
API_TOKEN = os.environ.get("API_TOKEN")
if not API_TOKEN:
    raise SystemExit("âŒ API_TOKEN Ø¯Ø± Ù…ØªØºÛŒØ±Ù‡Ø§ÛŒ Ù…Ø­ÛŒØ·ÛŒ ØªÙ†Ø¸ÛŒÙ… Ù†Ø´Ø¯Ù‡")

bot = telebot.TeleBot(API_TOKEN)

# Ø¨Ø±Ø§ÛŒ Ù‡Ø± User ÛŒÚ© Ù„ÛŒØ³Øª ÙˆÙ„Øª Ø°Ø®ÛŒØ±Ù‡ Ù…ÛŒâ€ŒÚ©Ù†ÛŒÙ…
user_wallets = {}
previous_positions = {}   # Ú©Ù„ÛŒØ¯: (chat_id, wallet)
user_intervals = {}

def _safe_float(x, default=0.0):
    try:
        return float(x)
    except:
        return default

def _sign_fmt(x):
    v = _safe_float(x, 0.0)
    if v >= 0:
        return f"âœ… +{v:,.2f}"
    else:
        return f"ðŸ”´ {v:,.2f}"
        
# ---------- Ù†Ø±Ù…Ø§Ù„â€ŒØ³Ø§Ø²ÛŒ Dataâ€ŒÙ‡Ø§ÛŒ HyperDash ----------
def _normalize_from_hyperdash(raw):
    out = []
    items = raw if isinstance(raw, list) else []
    if isinstance(raw, dict):
        for key in ("positions", "openPositions", "data"):
            if key in raw and isinstance(raw[key], list):
                items = raw[key]
                break
    for p in items:
        pair = p.get("pair") or p.get("symbol") or p.get("coin") or p.get("name")
        side = (p.get("side") or p.get("positionSide") or "").upper()
        size = _safe_float(p.get("size") or p.get("amount") or p.get("qty") or 0)
        entry = _safe_float(p.get("entryPrice") or p.get("entry") or p.get("avgEntryPrice") or 0)
        mark = _safe_float(p.get("markPrice") or p.get("mark") or p.get("price") or 0)
        pnl  = _safe_float(p.get("unrealizedPnl") or p.get("uPnl") or p.get("pnl") or 0)
        base_id = p.get("id") or p.get("positionId") or f"HD:{pair}:{side}"
        if abs(size) > 0:
            out.append({
                "uid": str(base_id),
                "pair": pair or "UNKNOWN",
                "side": side or ("LONG" if size > 0 else "SHORT"),
                "size": abs(size),
                "entryPrice": entry,
                "markPrice": mark if mark else None,
                "unrealizedPnl": pnl
            })
    return out

# ---------- Ù†Ø±Ù…Ø§Ù„â€ŒØ³Ø§Ø²ÛŒ Dataâ€ŒÙ‡Ø§ÛŒ Hyperliquid ----------
def _normalize_from_hyperliquid(raw):
    out = []
    items = raw.get("assetPositions", []) if isinstance(raw, dict) else raw if isinstance(raw, list) else []
    for p in items:
        try:
            pos = p.get("position", {})
            szi = _safe_float(pos.get("szi"), 0)
            if szi == 0:
                continue
            coin = pos.get("coin") or "UNKNOWN"
            entry = _safe_float(pos.get("entryPx"), 0)
            pnl = _safe_float(pos.get("unrealizedPnl"), 0)
            side = "LONG" if szi > 0 else "SHORT"
            uid = f"HL:{coin}:{side}"
            out.append({
                "uid": uid,
                "pair": coin,
                "side": side,
                "size": abs(szi),
                "entryPrice": entry,
                "markPrice": None,
                "unrealizedPnl": pnl
            })
        except Exception:
            continue
    return out
    
# ---------- Ø¯Ø±ÛŒØ§ÙØª Positionâ€ŒÙ‡Ø§ ----------
def get_positions(wallet):
    headers = {"User-Agent": "Mozilla/5.0"}  

    try:
        url = f"https://hyperdash.info/api/v1/trader/{wallet}/positions"
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            norm = _normalize_from_hyperdash(r.json())
            if norm:
                return norm
    except Exception as e:
        print(f"[HyperDash] error for {wallet}: {e}")

    try:
        url = "https://api.hyperliquid.xyz/info"
        payload = {"type": "clearinghouseState", "user": wallet}
        r = requests.post(url, json=payload, headers=headers, timeout=12)
        r.raise_for_status()
        return _normalize_from_hyperliquid(r.json())
    except Exception as e:
        print(f"[Hyperliquid] error for {wallet}: {e}")

    return []

# ---------- ÙØ±Ù…Øª Ù¾ÛŒØ§Ù… ----------
def format_position_line(p):
    lines = [
        f"ðŸª™ *{p.get('pair','?')}* | {('ðŸŸ¢ LONG' if p.get('side')=='LONG' else 'ðŸ”´ SHORT')}",
        f"ðŸ”¢ Size: {p.get('size','?')}",
        f"ðŸŽ¯ Entry: {p.get('entryPrice','?')}",
    ]
    if p.get("markPrice") is not None:
        lines.append(f"ðŸ“ Mark: {p.get('markPrice')}")
    lines.append(f"ðŸ’µ PNL: {_sign_fmt(p.get('unrealizedPnl'))}")
    return "\n".join(lines)

def send_message(chat_id, text):
    try:
        bot.send_message(chat_id, text, parse_mode="Markdown")
    except Exception as e:
        print(f"[SendMessage Error] {e}")
        
        
# ================== Ù…Ø§Ù†ÛŒØªÙˆØ±ÛŒÙ†Ú¯ Ù„Ø­Ø¸Ù‡â€ŒØ§ÛŒ + Report Intervalâ€ŒØ§ÛŒ ==================
def check_positions():
    for chat_id, wallets in user_wallets.items():
        for wallet in wallets:
            current_positions = get_positions(wallet)
            prev_positions = previous_positions.get((chat_id, wallet), [])

            current_map = {p["uid"]: p for p in current_positions}
            prev_map    = {p["uid"]: p for p in prev_positions}

            # Position Ø¬Ø¯ÛŒØ¯
            for uid, pos in current_map.items():
                if uid not in prev_map:
                    msg = (
                        "ðŸš€ *Position Opened*\n"
                        f"ðŸ’¼ (`{wallet}`)\n"
                        "â”â”â”â”â”â”â”â”â”â”\n"
                        f"{format_position_line(pos)}"
                    )
                    send_message(chat_id, msg)

            # Position Ø¨Ø³ØªÙ‡
            for uid, pos in prev_map.items():
                if uid not in current_map:
                    msg = (
                        "âœ… *Position Closed*\n"
                        f"ðŸ’¼ (`{wallet}`)\n"
                        "â”â”â”â”â”â”â”â”â”â”\n"
                        f"ðŸª™ *{pos.get('pair','?')}* | "
                        f"{('ðŸŸ¢ LONG' if pos.get('side')=='LONG' else 'ðŸ”´ SHORT')}\n"
                        f"ðŸ”¢ Size: {pos.get('size')}\n"
                        f"ðŸŽ¯ Entry: {pos.get('entryPrice')}\n"
                        f"ðŸ’µ Final PNL: {_sign_fmt(pos.get('unrealizedPnl',0))}\n"
                        "ðŸ”š Position Position closed."
                    )
                    send_message(chat_id, msg)

            previous_positions[(chat_id, wallet)] = current_positions

def periodic_report():
    for chat_id, wallets in user_wallets.items():
        interval = user_intervals.get(chat_id, 1)
        now_minute = int(time.time() / 60)
        if now_minute % interval != 0:
            continue

        for wallet in wallets:
            current_positions = get_positions(wallet)
            header = f"ðŸ•’ *Periodic Report ({interval} min)*\nðŸ’¼ (`{wallet}`)\nâ”â”â”â”â”â”â”â”â”â”"
            if current_positions:
                body = "\n\n".join([format_position_line(p) for p in current_positions])
                send_message(chat_id, f"{header}\n{body}")
            else:
                send_message(chat_id, f"{header}\nâ³ Ù‡ÛŒÚ† PositionÛŒ No open positions.")
                
# ================== Report Û±Û° Ø§Ø±Ø² Ø¨Ø±ØªØ± ==================
def get_top10_report():
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 10, "page": 1}
        r = requests.get(url, params=params, timeout=10, headers=HEADERS)
        r.raise_for_status()
        coins = r.json()

        lines = []
        for c in coins:
            symbol = c.get("symbol", "").upper()
            price = c.get("current_price", 0)
            change = c.get("price_change_percentage_24h", 0)

            bin_long, bin_short = "-", "-"
            try:
                b_url = f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={symbol.upper()}USDT&period=5m&limit=1"
                b_res = requests.get(b_url, timeout=8, headers=HEADERS)
                if b_res.status_code == 200:
                    data = b_res.json()
                    if data:
                        bin_long = f"{float(data[0]['longAccount'])*100:.1f}%"
                        bin_short = f"{float(data[0]['shortAccount'])*100:.1f}%"
            except Exception as e:
                print(f"[Binance] error for {symbol}: {e}")

            lines.append(
                f"ðŸª™ *{symbol}*\n"
                f"ðŸ’µ ${price:,.2f} ({change:+.2f}%)\n"
                f"ðŸ“Š Binance: ðŸŸ¢ {bin_long} | ðŸ”´ {bin_short}\n"
                "â”â”â”â”â”â”â”â”â”â”"
            )

        return "ðŸ“Š *Top 10 Coins by Market Cap*\n\n" + "\n".join(lines)

    except Exception as e:
        return f"âš ï¸ Error Ø¯Ø± Ø¯Ø±ÛŒØ§ÙØª Report: {e}"
        
# ================== Ù…Ù†ÙˆÙ‡Ø§ ==================
def send_interval_menu(chat_id):
    """
    âœ… ÙÙ‚Ø· ØªÙ†Ø¸ÛŒÙ… Ø¨Ø§Ø²Ù‡ Report Intervalâ€ŒØ§ÛŒ
    (Ø¯Ú©Ù…Ù‡â€ŒÙ‡Ø§ÛŒ 'Prediction' Ùˆ 'ØªØ§Ù¾Û±Û°' Ø§Ø² Ø§ÛŒÙ† Ù…Ù†Ùˆ Ø­Ø°Ù Ø´Ø¯Ù†Ø¯)
    """
    markup = InlineKeyboardMarkup()
    options = [
        ("1 Ø¯Ù‚ÛŒÙ‚Ù‡", 1),
        ("15 Ø¯Ù‚ÛŒÙ‚Ù‡", 15),
        ("30 Ø¯Ù‚ÛŒÙ‚Ù‡", 30),
        ("4 Ø³Ø§Ø¹Øª", 240),
        ("24 Ø³Ø§Ø¹Øª", 1440),
    ]
    for text, val in options:
        markup.add(InlineKeyboardButton(text, callback_data=f"interval_{val}"))
    bot.send_message(chat_id, "â± Ø¨Ø§Ø²Ù‡ Report Ø±Ùˆ Select Ú©Ù†:", reply_markup=markup)

def send_predict_menu(chat_id):
    """
    âœ… Ù…Ù†ÙˆÛŒ Select Ø¨Ø§Ø²Ù‡ Prediction BTC
    Ø§Ø² Ø·Ø±ÛŒÙ‚ /predict Ø¨Ø§Ø² Ù…ÛŒâ€ŒØ´ÙˆØ¯.
    """
    markup = InlineKeyboardMarkup()
    hour_opts = [1, 2, 4, 8, 12, 24]
    row = []
    for h in hour_opts:
        row.append(InlineKeyboardButton(f"{h} Ø³Ø§Ø¹Øª", callback_data=f"predict_h_{h}"))
        if len(row) == 3:
            markup.row(*row)
            row = []
    if row:
        markup.row(*row)
    bot.send_message(chat_id, "ðŸ”® Ø¨Ø§Ø²Ù‡ Prediction BTC Ø±Ùˆ Select Ú©Ù†:", reply_markup=markup)
    
# ================== Prediction BTC (Ø¨Ù‡Ø¨ÙˆØ¯ Ø¯Ù‚Øª + Strategyâ€ŒÙ‡Ø§ÛŒ Ø¨ÛŒØ´ØªØ±) ==================
import matplotlib.pyplot as plt
import io

# -------- IndicatorÙ‡Ø§ÛŒ Ù¾Ø§ÛŒÙ‡ --------
def _ema(values, span):
    if not values:
        return 0.0
    alpha = 2 / (span + 1.0)
    s = values[0]
    for v in values[1:]:
        s = alpha * v + (1 - alpha) * s
    return s

def _sma(values, window):
    if len(values) < window or window <= 0:
        return sum(values) / max(1, len(values))
    return sum(values[-window:]) / window

def _std(values, window):
    if len(values) < window or window <= 1:
        return 0.0
    sub = values[-window:]
    m = sum(sub) / window
    var = sum((x - m) ** 2 for x in sub) / (window - 1)
    return math.sqrt(var)

def _rsi(values, period=14):
    if len(values) < period + 1:
        return 50.0
    deltas = [values[i] - values[i-1] for i in range(1, len(values))]
    up = sum(x for x in deltas[:period] if x > 0) / period
    down = -sum(x for x in deltas[:period] if x < 0) / period
    up_avg, down_avg = up, down
    for d in deltas[period:]:
        upval = max(d, 0.0)
        downval = max(-d, 0.0)
        up_avg = (up_avg * (period - 1) + upval) / period
        down_avg = (down_avg * (period - 1) + downval) / period
    if down_avg == 0:
        return 100.0
    rs = up_avg / down_avg
    return 100 - (100 / (1 + rs))

def _macd(values, fast=12, slow=26, signal=9):
    if len(values) < slow + signal:
        return 0.0, 0.0, 0.0
    ema_fast_vals = []
    ema_slow_vals = []
    ef, es = values[0], values[0]
    af = 2 / (fast + 1.0)
    aslow = 2 / (slow + 1.0)
    for v in values:
        ef = af * v + (1 - af) * ef
        es = aslow * v + (1 - aslow) * es
        ema_fast_vals.append(ef)
        ema_slow_vals.append(es)
    macd_series = [a - b for a, b in zip(ema_fast_vals, ema_slow_vals)]
    s = macd_series[0]
    a_sig = 2 / (signal + 1.0)
    sig_series = []
    for v in macd_series:
        s = a_sig * v + (1 - a_sig) * s
        sig_series.append(s)
    macd = macd_series[-1]
    signal_line = sig_series[-1]
    hist = macd - signal_line
    return macd, signal_line, hist

def _bb_width(values, window=20, k=2.0):
    if len(values) < window:
        return 0.0, 0.0, 0.0, 0.0
    m = _sma(values, window)
    sd = _std(values, window)
    upper = m + k * sd
    lower = m - k * sd
    width = (upper - lower) / m if m else 0.0
    return width, upper, m, lower

# -------- Ø¯Ø±ÛŒØ§ÙØª Dataâ€ŒÙ‡Ø§ÛŒ OHLCV (Ø¨Ø±Ø§ÛŒ Strategyâ€ŒÙ‡Ø§) --------
def _fetch_binance_ohlcv(symbol="BTCUSDT", interval="5m", limit=500):
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(url, params=params, timeout=10, headers=HEADERS)
    r.raise_for_status()
    data = r.json()
    opens  = [float(k[1]) for k in data]
    highs  = [float(k[2]) for k in data]
    lows   = [float(k[3]) for k in data]
    closes = [float(k[4]) for k in data]
    vols   = [float(k[5]) for k in data]
    times  = [int(k[0]) for k in data]
    return times, opens, highs, lows, closes, vols

def _fetch_kraken_ohlcv(pair="XBTUSDT", interval=60):
    url = "https://api.kraken.com/0/public/OHLC"
    params = {"pair": pair, "interval": interval}
    r = requests.get(url, params=params, timeout=10, headers=HEADERS)
    r.raise_for_status()
    data = r.json()
    key = [k for k in data["result"].keys() if k != "last"][0]
    ohlc = data["result"][key]
    times  = [int(c[0]) for c in ohlc]
    opens  = [float(c[1]) for c in ohlc]
    highs  = [float(c[2]) for c in ohlc]
    lows   = [float(c[3]) for c in ohlc]
    closes = [float(c[4]) for c in ohlc]
    vols   = [float(c[6]) for c in ohlc]
    return times, opens, highs, lows, closes, vols

# -------- Strategyâ€ŒÙ‡Ø§ÛŒ Ø¬Ø¯ÛŒØ¯ --------
def _stoch_rsi(values, period=14, k=3, d=3):
    if len(values) < period + 1:
        return 50.0, 50.0
    # Ù…Ø­Ø§Ø³Ø¨Ù‡ RSI Ø±ÙˆÙ„ÛŒÙ†Ú¯
    rsi_list = []
    for i in range(period, len(values)):
        rsi_list.append(_rsi(values[i-period:i], period))
    window_vals = rsi_list[-period:] if len(rsi_list) >= period else rsi_list
    if not window_vals:
        return 50.0, 50.0
    mn, mx = min(window_vals), max(window_vals)
    if mx - mn == 0:
        return 50.0, 50.0
    stoch = (rsi_list[-1] - mn) / (mx - mn) * 100.0
    # %K Ùˆ %D Ø³Ø§Ø¯Ù‡
    k_val = sum(rsi_list[-k:]) / max(1, min(k, len(rsi_list)))
    d_val = sum(rsi_list[-d:]) / max(1, min(d, len(rsi_list)))
    return stoch, d_val

def _atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return 0.0
    trs = []
    prev_close = closes[0]
    for h, l, c in zip(highs, lows, closes):
        tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
        prev_close = c
    if len(trs) < period:
        return sum(trs) / max(1, len(trs))
    return sum(trs[-period:]) / period

def _ichimoku(highs, lows, closes):
    # Tenkan (9), Kijun (26), Senkou A/B (26-shifted)
    if len(closes) < 52:
        # Ø­Ø¯Ø§Ù‚Ù„ Ø¨Ø±Ø§ÛŒ Ù…Ø­Ø§Ø³Ø¨Ù‡ Ø§Ø¨Ø±
        return None
    def hl_mid(arr_h, arr_l, p):
        hh = max(arr_h[-p:])
        ll = min(arr_l[-p:])
        return (hh + ll) / 2.0
    tenkan = hl_mid(highs, lows, 9)
    kijun  = hl_mid(highs, lows, 26)
    spanA  = (tenkan + kijun) / 2.0
    spanB  = hl_mid(highs, lows, 52)
    close  = closes[-1]
    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "spanA": spanA,
        "spanB": spanB,
        "close": close
    }

def _vwap(highs, lows, closes, vols):
    if not closes or not vols:
        return None
    typical = [(h + l + c) / 3.0 for h, l, c in zip(highs, lows, closes)]
    cum_pv = 0.0
    cum_v  = 0.0
    for tp, v in zip(typical, vols):
        cum_pv += tp * v
        cum_v  += v
    return (cum_pv / cum_v) if cum_v else None

# -------- Ø¯Ø±ÛŒØ§ÙØª Data Ùˆ Ù…Ø¯Ù„ Ø¢Ù…Ø§Ø±ÛŒ Ù¾Ø§ÛŒÙ‡ + Strategyâ€ŒÙ‡Ø§ --------
def predict_btc_price(hours_ahead=4):
    # Ø³Ø¹ÛŒ Ù…ÛŒâ€ŒÚ©Ù†ÛŒÙ… Ø§Ø² Ø¨Ø§ÛŒÙ†Ù†Ø³ Ø¨Ø§ ØªØ§ÛŒÙ…â€ŒÙØ±ÛŒÙ… 5m Ø¨Ø®ÙˆØ§Ù†ÛŒÙ…Ø› Ø¯Ø± ØµÙˆØ±Øª Error â†’ Ú©Ø±ÙŽÚ©Ù† Ø¨Ø§ 1h
    use_step = 5
    try:
        times, opens, highs, lows, closes, vols = _fetch_binance_ohlcv("BTCUSDT", "5m", 500)
        source = "Binance (5m)"
        use_step = 5
    except Exception as e:
        print(f"[Binance Error] {e} â†’ fallback Ø¨Ù‡ Kraken")
        times, opens, highs, lows, closes, vols = _fetch_kraken_ohlcv("XBTUSDT", interval=60)
        source = "Kraken (1h)"
        use_step = 60

    if len(closes) < 60:
        return {"error": "Dataâ€ŒÙ‡Ø§ÛŒ Ú©Ø§ÙÛŒ Ø¨Ø±Ø§ÛŒ Prediction ÙˆØ¬ÙˆØ¯ Ù†Ø¯Ø§Ø±Ø¯."}

    last_price = closes[-1]

    # Ø¨Ø§Ø²Ø¯Ù‡ Ù„Ú¯Ø§Ø±ÛŒØªÙ…ÛŒ
    rets = []
    for i in range(1, len(closes)):
        c0, c1 = closes[i-1], closes[i]
        if c0 <= 0:
            continue
        rets.append(math.log(c1 / c0))
    if not rets:
        return {"error": "Ø¹Ø¯Ù… Ø§Ù…Ú©Ø§Ù† Ù…Ø­Ø§Ø³Ø¨Ù‡ Ø¨Ø§Ø²Ø¯Ù‡â€ŒÙ‡Ø§."}

    # mu Ùˆ sigma
    window = min(200, len(rets))
    r_win = rets[-window:]
    mu = sum(r_win) / len(r_win)
    var = sum((x - mu)**2 for x in r_win) / max(1, len(r_win) - 1)
    sigma = math.sqrt(var)

    # IndicatorÙ‡Ø§ÛŒ Ù¾Ø§ÛŒÙ‡
    ema_fast = _ema(closes, 12)
    ema_slow = _ema(closes, 26)
    trend = (ema_fast - ema_slow) / ema_slow if ema_slow else 0.0
    rsi_val = _rsi(closes, 14)
    macd, macd_sig, macd_hist = _macd(closes)
    bb_w, bb_up, bb_mid, bb_low = _bb_width(closes, 20)

    # Ù†ÙˆØ³Ø§Ù† Ú©ÙˆØªØ§Ù‡
    short_win = min(30, len(r_win))
    short_sigma = _std(r_win, short_win) if short_win >= 2 else sigma
    if short_sigma == 0:
        short_sigma = sigma

    mu_adj = mu + 0.15 * trend
    if macd_hist > 0:
        mu_adj += 0.10 * abs(mu)
    elif macd_hist < 0:
        mu_adj -= 0.10 * abs(mu)
    if rsi_val > 70:
        mu_adj -= 0.20 * abs(mu)
    elif rsi_val < 30:
        mu_adj += 0.20 * abs(mu)

    sigma_adj = 0.5 * sigma + 0.5 * short_sigma
    median_bb_w = 0.04
    bb_scale = max(0.5, min(1.5, bb_w / median_bb_w if median_bb_w else 1.0))
    sigma_adj *= bb_scale

    n = max(1, int((hours_ahead * 60) / use_step))

    log_S0 = math.log(last_price)
    log_mean = log_S0 + n * mu_adj
    log_std = math.sqrt(n) * sigma_adj

    point = math.exp(log_mean)
    ci68 = (math.exp(log_mean - log_std), math.exp(log_mean + log_std))
    ci95 = (math.exp(log_mean - 1.96 * log_std), math.exp(log_mean + 1.96 * log_std))

    # -------- Ù…Ø­Ø§Ø³Ø¨Ù‡ Strategyâ€ŒÙ‡Ø§ --------
    # Ichimoku
    ich = _ichimoku(highs, lows, closes)
    ich_signal, ich_text = "âšª Ø®Ù†Ø«ÛŒ", "DataÙ” Ú©Ø§ÙÛŒ Ø¨Ø±Ø§ÛŒ Ø§Ø¨Ø± ÛŒØ§ ÙˆØ¶Ø¹ÛŒØª Ø¨ÛŒÙ† Ø§Ø¨Ø±Ù‡Ø§ Ø®Ù†Ø«ÛŒ Ø§Ø³Øª."
    ich_low, ich_high = None, None
    if ich:
        above_cloud = ich["close"] > max(ich["spanA"], ich["spanB"])
        below_cloud = ich["close"] < min(ich["spanA"], ich["spanB"])
        tenkan_above = ich["tenkan"] > ich["kijun"]
        if above_cloud and tenkan_above:
            ich_signal = "ðŸŸ¢ ØµØ¹ÙˆØ¯ÛŒ"
            ich_text = "Price UpÛŒ Ø§Ø¨Ø± Ø§ÛŒÚ†ÛŒÙ…ÙˆÚ©Ùˆ Ùˆ ØªÙ†Ú©Ø§Ù†â€ŒØ³Ù† UpÛŒ Ú©ÛŒØ¬ÙˆÙ†â€ŒØ³Ù† â†’ Ø¨Ø±ØªØ±ÛŒ Ø®Ø±ÛŒØ¯Ø§Ø±Ø§Ù†."
            ich_low, ich_high = ich["kijun"], max(ich["spanA"], ich["spanB"]) * 1.01
        elif below_cloud and not tenkan_above:
            ich_signal = "ðŸ”´ Ù†Ø²ÙˆÙ„ÛŒ"
            ich_text = "Price Ø²ÛŒØ± Ø§Ø¨Ø± Ø§ÛŒÚ†ÛŒÙ…ÙˆÚ©Ùˆ Ùˆ ØªÙ†Ú©Ø§Ù†â€ŒØ³Ù† Ø²ÛŒØ± Ú©ÛŒØ¬ÙˆÙ†â€ŒØ³Ù† â†’ ÙØ´Ø§Ø± ÙØ±ÙˆØ´ Ø¨ÛŒØ´ØªØ±."
            ich_low, ich_high = min(ich["spanA"], ich["spanB"]) * 0.99, ich["kijun"]
        else:
            ich_signal = "âšª Ø®Ù†Ø«ÛŒ"
            ich_text = "Price Ø¯Ø§Ø®Ù„/Ù†Ø²Ø¯ÛŒÚ© Ø§Ø¨Ø± Ø§Ø³Øª ÛŒØ§ Ø³ÛŒÚ¯Ù†Ø§Ù„â€ŒÙ‡Ø§ Ù…ØªÙ†Ø§Ù‚Ø¶â€ŒØ§Ù†Ø¯."
            ich_low, ich_high = min(ich["spanA"], ich["spanB"]), max(ich["spanA"], ich["spanB"])

    # Stoch RSI
    stoch, dval = _stoch_rsi(closes)
    if stoch >= 80:
        stoch_signal = "ðŸ”´ Ù†Ø²ÙˆÙ„ÛŒ"
        stoch_text = "Ø§Ø´Ø¨Ø§Ø¹ Ø®Ø±ÛŒØ¯ â†’ Ø§Ø­ØªÙ…Ø§Ù„ Ø§ÙØ²Ø§ÛŒØ´ ÙØ´Ø§Ø± ÙØ±ÙˆØ´."
        stoch_low, stoch_high = ci68[0], max(point, ci68[1] * 0.99)
    elif stoch <= 20:
        stoch_signal = "ðŸŸ¢ ØµØ¹ÙˆØ¯ÛŒ"
        stoch_text = "Ø§Ø´Ø¨Ø§Ø¹ ÙØ±ÙˆØ´ â†’ Ø§Ø­ØªÙ…Ø§Ù„ ÙˆØ±ÙˆØ¯ Ø®Ø±ÛŒØ¯Ø§Ø±Ø§Ù†."
        stoch_low, stoch_high = min(point, ci68[0] * 1.01), ci68[1]
    else:
        stoch_signal = "âšª Ø®Ù†Ø«ÛŒ"
        stoch_text = "Ø¯Ø± Ù…Ø­Ø¯ÙˆØ¯Ù‡Ù” Ù…ÛŒØ§Ù†ÛŒ Ø§Ø³ØªØ› Ø³ÛŒÚ¯Ù†Ø§Ù„ Ù‚ÙˆÛŒ Ù†Ø¯Ø§Ø±Ø¯."
        stoch_low, stoch_high = ci68

    # ATR (Volatility)
    atr_val = _atr(highs, lows, closes, 14)
    # Ù†Ø³Ø¨Øª ATR Ø¨Ù‡ Price Ø¨Ø±Ø§ÛŒ ØªØ®Ù…ÛŒÙ† Ø´Ø¯Øª Ù†ÙˆØ³Ø§Ù†
    atr_ratio = (atr_val / last_price) if last_price else 0.0
    if atr_ratio >= 0.02:
        atr_signal = "ðŸ”´ Ù†ÙˆØ³Ø§Ù† Up"
        atr_text = "Ù†ÙˆØ³Ø§Ù† Ú©ÙˆØªØ§Ù‡â€ŒÙ…Ø¯Øª UpØ³Øª â†’ Ø±ÛŒØ³Ú© Ø­Ø±Ú©Ø§Øª ØªÙ†Ø¯."
        # Ø¨Ø§Ø²Ù‡ Ø¨Ø§Ø²ØªØ±
        atr_low, atr_high = ci95
    elif atr_ratio <= 0.008:
        atr_signal = "ðŸŸ¢ Ù†ÙˆØ³Ø§Ù† Ú©Ù…"
        atr_text = "Ù†ÙˆØ³Ø§Ù† Downâ€ŒØªØ± Ø§Ø² Ù…Ø¹Ù…ÙˆÙ„ â†’ Ø­Ø±Ú©Øª Ø¢Ø±Ø§Ù…â€ŒØªØ± Ù…Ø­ØªÙ…Ù„."
        # Ø¨Ø§Ø²Ù‡ ÙØ´Ø±Ø¯Ù‡â€ŒØªØ±
        mid = point
        w = (ci68[1] - ci68[0]) * 0.5
        atr_low, atr_high = mid - w * 0.6, mid + w * 0.6
    else:
        atr_signal = "âšª Ù†Ø±Ù…Ø§Ù„"
        atr_text = "Ù†ÙˆØ³Ø§Ù† Ø¯Ø± Ù…Ø­Ø¯ÙˆØ¯Ù‡Ù” Ù…Ø¹Ù…ÙˆÙ„ Market Ø§Ø³Øª."
        atr_low, atr_high = ci68

    # Golden/Death Cross (SMA50/200)
    sma50 = _sma(closes, 50)
    sma200 = _sma(closes, 200)
    if sma50 > sma200:
        gd_signal = "ðŸŸ¢ ØµØ¹ÙˆØ¯ÛŒ"
        gd_text = "Ù…ÛŒØ§Ù†Ú¯ÛŒÙ† ÛµÛ° UpÛŒ Û²Û°Û° â†’ Ø±ÙˆÙ†Ø¯ Ù…ÛŒØ§Ù†â€ŒÙ…Ø¯Øª Ù…Ø«Ø¨Øª."
        gd_low, gd_high = max(ci68[0], sma200 * 0.995), ci68[1] * 1.01
    elif sma50 < sma200:
        gd_signal = "ðŸ”´ Ù†Ø²ÙˆÙ„ÛŒ"
        gd_text = "Ù…ÛŒØ§Ù†Ú¯ÛŒÙ† ÛµÛ° Ø²ÛŒØ± Û²Û°Û° â†’ Ø±ÙˆÙ†Ø¯ Ù…ÛŒØ§Ù†â€ŒÙ…Ø¯Øª Ù…Ù†ÙÛŒ."
        gd_low, gd_high = ci68[0] * 0.99, min(ci68[1], sma200 * 1.005)
    else:
        gd_signal = "âšª Ø®Ù†Ø«ÛŒ"
        gd_text = "ØªÙØ§ÙˆØª Ù…Ø¹Ù†Ø§Ø¯Ø§Ø± Ø¨ÛŒÙ† MA50 Ùˆ MA200 Ø¯ÛŒØ¯Ù‡ Ù†Ù…ÛŒâ€ŒØ´ÙˆØ¯."
        gd_low, gd_high = ci68

    # VWAP
    vwap = _vwap(highs, lows, closes, vols)
    if vwap is not None:
        if last_price > vwap * 1.002:
            vwap_signal = "ðŸŸ¢ Ø­Ù…Ø§ÛŒØªÛŒ"
            vwap_text = "Price UpÛŒ VWAP â†’ Ø¯Ø³Øª Up Ø¨Ø§ Ø®Ø±ÛŒØ¯Ø§Ø±Ø§Ù†."
            vwap_low, vwap_high = max(ci68[0], vwap), ci68[1] * 1.005
        elif last_price < vwap * 0.998:
            vwap_signal = "ðŸ”´ Ù…Ù‚Ø§ÙˆÙ…ØªÛŒ"
            vwap_text = "Price Ø²ÛŒØ± VWAP â†’ ÙØ±ÙˆØ´Ù†Ø¯Ú¯Ø§Ù† ÙØ¹Ø§Ù„â€ŒØªØ±Ù†Ø¯."
            vwap_low, vwap_high = ci68[0] * 0.995, min(ci68[1], vwap)
        else:
            vwap_signal = "âšª Ø®Ù†Ø«ÛŒ"
            vwap_text = "Price Ù†Ø²Ø¯ÛŒÚ© VWAP â†’ ØªØ¹Ø§Ø¯Ù„ Ù†Ø³Ø¨ÛŒ."
            vwap_low, vwap_high = ci68
    else:
        vwap_signal = "âšª Ø®Ù†Ø«ÛŒ"
        vwap_text = "DataÙ” Ú©Ø§ÙÛŒ Ø¨Ø±Ø§ÛŒ VWAP Ù…ÙˆØ¬ÙˆØ¯ Ù†ÛŒØ³Øª."
        vwap_low, vwap_high = ci68

    strategies = [
        ("Ø§ÛŒÚ†ÛŒÙ…ÙˆÚ©Ùˆ", ich_signal, ich_text, ich_low, ich_high),
        ("Ø§Ø³ØªÙˆÚ© RSI", stoch_signal, stoch_text, stoch_low, stoch_high),
        ("ATR", atr_signal, atr_text, atr_low, atr_high),
        ("Ú©Ø±Ø§Ø³ Ø·Ù„Ø§ÛŒÛŒ/Ù…Ø±Ú¯", gd_signal, gd_text, gd_low, gd_high),
        ("VWAP", vwap_signal, vwap_text, vwap_low, vwap_high),
    ]

    return {
        "last": last_price, "point": point,
        "ci68": ci68, "ci95": ci95,
        "mu": mu, "sigma": sigma,
        "mu_adj": mu_adj, "sigma_adj": sigma_adj,
        "trend": trend, "rsi": rsi_val,
        "macd": macd, "macd_sig": macd_sig, "macd_hist": macd_hist,
        "bb_width": bb_w, "bb_up": bb_up, "bb_mid": bb_mid, "bb_low": bb_low,
        "n": n, "step": use_step, "source": source,
        "closes": closes, "highs": highs, "lows": lows, "vols": vols,
        "strategies": strategies
    }

# ------------------ ØªØºÛŒÛŒØ± Ø¸Ø§Ù‡Ø± ÙÙ‚Ø· Ø¯Ø± Ø®Ø±ÙˆØ¬ÛŒ Prediction ------------------
def build_btc_forecast_text(hours=4):
    res = predict_btc_price(hours)
    if "error" in res:
        return f"âš ï¸ {res['error']}"

    last  = res["last"]
    point = res["point"]
    l68, u68 = res["ci68"]
    l95, u95 = res["ci95"]
    rsi_val = res["rsi"]
    trend_pc = res["trend"] * 100
    source = res["source"]

    # Ø®Ù„Ø§ØµÙ‡ ÙØ´Ø±Ø¯Ù‡ Ùˆ Ø®ÙˆØ§Ù†Ø§ØªØ±
    summary = (
        f"ðŸ”® *Prediction BTC ({hours}h)*\n"
        f"ðŸ“Š Data: *{source}*\n"
        f"ðŸ’µ Price ÙØ¹Ù„ÛŒ: *${last:,.2f}*\n"
        f"ðŸŽ¯ Prediction: *${point:,.2f}*\n"
        f"ðŸ“ Ø¨Ø§Ø²Ù‡ Û¶Û¸Ùª: `${l68:,.0f} â€” {u68:,.0f}`\n"
        f"ðŸ“ Ø¨Ø§Ø²Ù‡ Û¹ÛµÙª: `${l95:,.0f} â€” {u95:,.0f}`\n"
        f"ðŸ“ˆ EMA12-26: {trend_pc:.2f}% | ðŸ”„ RSI(14): {rsi_val:.1f}\n"
    )

    # Ø³ÛŒÚ¯Ù†Ø§Ù„ Strategyâ€ŒÙ‡Ø§ Ø¨Ù‡â€ŒØµÙˆØ±Øª Ù„ÛŒØ³Øª ØªÙ…ÛŒØ²
    strategies_text = "\nðŸ“Š *Ø³ÛŒÚ¯Ù†Ø§Ù„ Strategyâ€ŒÙ‡Ø§:*\n"
    for name, sig, desc, lo, hi in res["strategies"]:
        rng = f"\n    ðŸŽ¯ ${lo:,.0f} â€” ${hi:,.0f}" if lo and hi else ""
        strategies_text += f"â€¢ *{name}*: {sig}\n    {desc}{rng}\n"

    return summary + strategies_text + "\nâš ï¸ Ø§ÛŒÙ† ÙÙ‚Ø· ØªØ­Ù„ÛŒÙ„ Ø¢Ù…Ø§Ø±ÛŒ Ùˆ Ø¢Ù…ÙˆØ²Ø´ÛŒ Ø§Ø³Øª."

def build_btc_forecast_chart(hours=4):
    res = predict_btc_price(hours)
    if "error" in res:
        return None, res["error"]

    closes = res["closes"]
    forecast = res["point"]
    l95, u95 = res["ci95"]

    # Ø§Ø³ØªØ§ÛŒÙ„ ÙÙ‚Ø· Ø¸Ø§Ù‡Ø±ÛŒ
    plt.style.use("dark_background")
    plt.figure(figsize=(9,5))
    plt.plot(closes[-100:], label="Price", color="#00BFFF", linewidth=2)
    plt.axhline(forecast, color="#32CD32", linestyle="--", linewidth=2, label="Forecast")
    plt.axhline(l95, color="#FF4500", linestyle=":", linewidth=1.5, label="CI95 Low")
    plt.axhline(u95, color="#FF4500", linestyle=":", linewidth=1.5, label="CI95 High")

    last_price = closes[-1]
    # Ù†Ù…Ø§ÛŒØ´ Ø¢Ø®Ø±ÛŒÙ† Price Ø±ÙˆÛŒ Ù†Ù…ÙˆØ¯Ø§Ø±
    plt.text(len(closes[-100:]) - 1, last_price, f"${last_price:,.0f}", color="white")

    plt.title(f"BTC Forecast (next {hours}h)", fontsize=14, color="white")
    plt.legend()
    plt.grid(alpha=0.2)

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=200, bbox_inches="tight")
    buf.seek(0)
    plt.close()
    return buf, None
    
# ================== Ù…Ù†ÙˆÙ‡Ø§ ==================
def send_interval_menu(chat_id):
    """
    âœ… ÙÙ‚Ø· ØªÙ†Ø¸ÛŒÙ… Ø¨Ø§Ø²Ù‡ Report Intervalâ€ŒØ§ÛŒ
    (Ø¯Ú©Ù…Ù‡â€ŒÙ‡Ø§ÛŒ 'Prediction' Ùˆ 'ØªØ§Ù¾Û±Û°' Ø§Ø² Ø§ÛŒÙ† Ù…Ù†Ùˆ Ø­Ø°Ù Ø´Ø¯Ù†Ø¯)
    """
    markup = InlineKeyboardMarkup()
    options = [
        ("1 Ø¯Ù‚ÛŒÙ‚Ù‡", 1),
        ("15 Ø¯Ù‚ÛŒÙ‚Ù‡", 15),
        ("30 Ø¯Ù‚ÛŒÙ‚Ù‡", 30),
        ("4 Ø³Ø§Ø¹Øª", 240),
        ("24 Ø³Ø§Ø¹Øª", 1440),
    ]
    for text, val in options:
        markup.add(InlineKeyboardButton(text, callback_data=f"interval_{val}"))
    bot.send_message(chat_id, "â± Ø¨Ø§Ø²Ù‡ Report Ø±Ùˆ Select Ú©Ù†:", reply_markup=markup)

def send_predict_menu(chat_id):
    """
    âœ… Ù…Ù†ÙˆÛŒ Select Ø¨Ø§Ø²Ù‡ Prediction BTC
    Ø§Ø² Ø·Ø±ÛŒÙ‚ /predict Ø¨Ø§Ø² Ù…ÛŒâ€ŒØ´ÙˆØ¯.
    """
    markup = InlineKeyboardMarkup()
    hour_opts = [1, 2, 4, 8, 12, 24]
    row = []
    for h in hour_opts:
        row.append(InlineKeyboardButton(f"{h} Ø³Ø§Ø¹Øª", callback_data=f"predict_h_{h}"))
        if len(row) == 3:
            markup.row(*row)
            row = []
    if row:
        markup.row(*row)
    bot.send_message(chat_id, "ðŸ”® Ø¨Ø§Ø²Ù‡ Prediction BTC Ø±Ùˆ Select Ú©Ù†:", reply_markup=markup)
# Ù‡Ø± Û´ Ø³Ø§Ø¹Øª Report Ø®ÙˆØ¯Ú©Ø§Ø±
    


# ================== Settings Ù„ÛŒÚ©ÙˆÛŒÛŒØ¯ÛŒØ´Ù† ==================
LIQ_THRESHOLD = 10   # ðŸ”¹ ØªØ³ØªÛŒ: UpÛŒ Û±Û° Ø¯Ù„Ø§Ø± (Ø¨Ø¹Ø¯Ø§Ù‹ Ø¨Ø²Ù† 1_000_000)
MAX_LIQS = 10        # Ø­Ø¯Ø§Ú©Ø«Ø± Û±Û° Ø±Ú©ÙˆØ±Ø¯ Ù†Ú¯Ù‡Ø¯Ø§Ø±ÛŒ Ø¨Ø´Ù‡
liq_list = []

# ================== Binance WebSocket ==================
BINANCE_WS = (
    "wss://fstream.binance.com/stream?"
    "streams=btcusdt@forceOrder/ethusdt@forceOrder/bnbusdt@forceOrder"
)

def start_binance_ws():
    """Start ÙˆØ¨â€ŒØ³ÙˆÚ©Øª Ø¨Ø§ÛŒÙ†Ù†Ø³ Ø¨Ø±Ø§ÛŒ Ú¯Ø±ÙØªÙ† Ù„ÛŒÚ©ÙˆÛŒÛŒØ¯ÛŒØ´Ù†â€ŒÙ‡Ø§"""
    def on_message(ws, message):
        try:
            data = json.loads(message)
            order = data["data"]["o"]

            symbol = order["s"]
            side = order["S"]
            price = float(order["ap"])
            qty = float(order["q"])
            notional = price * qty

            if notional >= LIQ_THRESHOLD:
                event = (
                    f"ðŸ”´ Liquidation\n"
                    f"ðŸ“Œ Symbol: {symbol}\n"
                    f"ðŸ“ˆ Side: {side}\n"
                    f"ðŸ’° Notional: {notional:.2f} USD\n"
                    f"ðŸ’² Price: {price}\n"
                    f"ðŸ“¦ Quantity: {qty}"
                )

                # Ø°Ø®ÛŒØ±Ù‡ Ø¯Ø± Ù„ÛŒØ³Øª
                liq_list.append(event)
                if len(liq_list) > MAX_LIQS:
                    liq_list.pop(0)

                print(event)
                print("-" * 30)

        except Exception as e:
            print("âŒ Error parsing message:", e)
            print("Raw:", message)

    def on_error(ws, error):
        print("âŒ WebSocket Error:", error)

    def on_close(ws, close_status_code, close_msg):
        print("ðŸ”Œ WebSocket Connection closed")

    def on_open(ws):
        print("âœ… Connected to Binance WebSocket (BTC/ETH/BNB)")

    ws = websocket.WebSocketApp(
        BINANCE_WS,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
        on_open=on_open
    )
    ws.run_forever()

def run_ws_thread():
    """Ø§Ø¬Ø±Ø§ÛŒ ÙˆØ¨â€ŒØ³ÙˆÚ©Øª Ø¯Ø± ØªØ±Ø¯ Ø¬Ø¯Ø§Ú¯Ø§Ù†Ù‡"""
    ws_thread = threading.Thread(target=start_binance_ws, daemon=True)
    ws_thread.start()

def get_liq_report():
    """Ù…ØªÙ† Report Ù„ÛŒÚ©ÙˆÛŒÛŒØ¯ÛŒØ´Ù†â€ŒÙ‡Ø§"""
    if not liq_list:
        return "âš ï¸ Ù‡Ù†ÙˆØ² Ù„ÛŒÚ©ÙˆÛŒÛŒØ¯ÛŒØ´Ù†ÛŒ Ø«Ø¨Øª Ù†Ø´Ø¯Ù‡."
    return "\n\n".join(liq_list)

# ================== Ø¯Ø³ØªÙˆØ±Ø§Øª ØªÙ„Ú¯Ø±Ø§Ù… ==================
@bot.message_handler(commands=["liqs"])
def send_liqs(message):
    bot.reply_to(message, get_liq_report())

# Ù‡Ø± Û´ Ø³Ø§Ø¹Øª Report Ø¨Ø±Ø§ÛŒ Ù‡Ù…Ù‡ Ù…Ø´ØªØ±Ú©â€ŒÙ‡Ø§
def auto_send_liqs():
    report = get_liq_report()
    for user in subscribers:
        try:
            bot.send_message(user, report)
        except:
            pass

schedule.every(4).hours.do(auto_send_liqs)


# ================== Timeâ€ŒØ¨Ù†Ø¯ÛŒ ==================
 # Ù‡Ø± Û± Ø¯Ù‚ÛŒÙ‚Ù‡ Ø¯ÛŒØªØ§ÛŒ Ø¬Ø¯ÛŒØ¯
schedule.every(4).hours.do(auto_send_liqs)      # Ù‡Ø± Û´ Ø³Ø§Ø¹Øª Report Ø®ÙˆØ¯Ú©Ø§Ø±    
    
# ================== Ø¯Ø³ØªÙˆØ±Ø§Øª ==================
@bot.message_handler(commands=['start'])
def start(message):
    chat_id = message.chat.id
    user_wallets.setdefault(chat_id, [])
    user_intervals[chat_id] = 1
    send_message(chat_id,
        "Ø³Ù„Ø§Ù… ðŸ‘‹\n"
        "Address ÙˆÙ„Øªâ€ŒÙ‡Ø§Øª Ø±Ùˆ Ø¨ÙØ±Ø³Øª ØªØ§ Ø¨Ø±Ø§Øª Ù…Ø§Ù†ÛŒØªÙˆØ± Ú©Ù†Ù….\n\n"
        "ðŸ“ /stop â†’ Stop Ù…Ø§Ù†ÛŒØªÙˆØ±ÛŒÙ†Ú¯\n"
        "ðŸ“ /interval â†’ ØªØºÛŒÛŒØ± Ø¨Ø§Ø²Ù‡ Report\n"
        "ðŸ“ /top10 â†’ Report Û±Û° Ø§Ø±Ø² Ø¨Ø±ØªØ±\n"
        "ðŸ“ /predict â†’ Prediction Ø¨ÛŒØªâ€ŒÚ©ÙˆÛŒÙ† (Select Ø¨Ø§Ø²Ù‡)"
    )

@bot.message_handler(commands=['stop'])
def stop(message):
    chat_id = message.chat.id
    user_wallets.pop(chat_id, None)
    user_intervals.pop(chat_id, None)
    send_message(chat_id, "â¹ Ù…Ø§Ù†ÛŒØªÙˆØ±ÛŒÙ†Ú¯ Ù…Stop Ø´Ø¯.")

@bot.message_handler(commands=['interval'])
def interval(message):
    chat_id = message.chat.id
    send_interval_menu(chat_id)

@bot.message_handler(commands=['top10'])
def top10(message):
    chat_id = message.chat.id
    report = get_top10_report()
    send_message(chat_id, report)

@bot.message_handler(commands=['predict'])
def predict(message):
    chat_id = message.chat.id
    send_predict_menu(chat_id)


@bot.message_handler(func=lambda m: True, content_types=['text'])
def add_wallet(message):
    chat_id = message.chat.id
    text = (message.text or "").strip()

    # Ú†Ù†Ø¯ ÙˆØ±ÙˆØ¯ÛŒ Ø±Ø§ Ø¨Ø§ ÙØ§ØµÙ„Ù‡/Ú©Ø§Ù…Ø§/Ø®Ø·â€ŒØ¬Ø¯ÛŒØ¯ Ø¬Ø¯Ø§ Ù…ÛŒâ€ŒÚ©Ù†ÛŒÙ…
    parts = re.split(r'[\s,]+', text)
    parts = [p.strip() for p in parts if p.strip()]

    if not parts:
        send_message(chat_id, "âŒ Ù„Ø·ÙØ§Ù‹ Ø­Ø¯Ø§Ù‚Ù„ ÛŒÚ© Address ÙˆÙ„Øª Ø¨ÙØ±Ø³Øª.")
        return

    # Ø§Ø³ØªÙØ§Ø¯Ù‡ Ø§Ø² Ø§Ø¹ØªØ¨Ø§Ø±Ø³Ù†Ø¬ÛŒ Ø³ÙØªâ€ŒÙˆØ³Ø®Øª
    valid, errors = validate_wallet_inputs(parts)

    # Ø§ÙØ²ÙˆØ¯Ù† Ù…ÙˆØ§Ø±Ø¯ Ù…Ø¹ØªØ¨Ø± (Ø¨Ø¯ÙˆÙ† ØªÚ©Ø±Ø§Ø±)
    added = []
    if valid:
        user_wallets.setdefault(chat_id, [])
        for w in valid:
            if w not in user_wallets[chat_id]:
                user_wallets[chat_id].append(w)
                added.append(w)

    # Ø³Ø§Ø®Øª Ù¾ÛŒØ§Ù… Ø®Ø±ÙˆØ¬ÛŒ
    msg_lines = []
    if added:
        msg_lines.append(f"âœ… {len(added)} ÙˆÙ„Øª Ù…Ø¹ØªØ¨Ø± Ø§Ø¶Ø§ÙÙ‡ Ø´Ø¯:")
        msg_lines.extend([f"- `{w}`" for w in added])
    if errors:
        msg_lines.append("âŒ Ù…ÙˆØ§Ø±Ø¯ Ù†Ø§Ù…Ø¹ØªØ¨Ø±:")
        for err in errors:
            reason = err.get('reason', 'Ù†Ø§Ù…Ø¹ØªØ¨Ø±')
            msg_lines.append(f"- `{err['input']}` â†’ {reason}")

    if not msg_lines:
        msg_lines = ["âš ï¸ Ù‡ÛŒÚ† ÙˆÙ„Øª Ø¬Ø¯ÛŒØ¯ÛŒ Ø§Ø¶Ø§ÙÙ‡ Ù†Ø´Ø¯."]

    send_message(chat_id, "\n".join(msg_lines))

# ================== Ø§Ø¬Ø±Ø§ÛŒ Timeâ€ŒØ¨Ù†Ø¯ÛŒ ==================
def run_scheduler():
    schedule.every(1).minutes.do(check_positions)
    schedule.every(1).minutes.do(periodic_report)
    while True:
        schedule.run_pending()
        time.sleep(1)

threading.Thread(target=run_scheduler, daemon=True).start()
# ================== Ù‡Ù†Ø¯Ù„Ø± Ø¯Ú©Ù…Ù‡â€ŒÙ‡Ø§ÛŒ Ø´ÛŒØ´Ù‡â€ŒØ§ÛŒ ==================
@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    chat_id = call.message.chat.id
    data = call.data

    # --- Ù‡Ù†Ø¯Ù„Ø± ØªØºÛŒÛŒØ± Ø¨Ø§Ø²Ù‡ Report Intervalâ€ŒØ§ÛŒ ---
    if data.startswith("interval_"):
        try:
            val = int(data.split("_")[1])
            user_intervals[chat_id] = val
            bot.answer_callback_query(call.id, f"Ø¨Ø§Ø²Ù‡ Report {val} Ø¯Ù‚ÛŒÙ‚Ù‡ ØªÙ†Ø¸ÛŒÙ… Ø´Ø¯ âœ…")
            send_message(chat_id, f"â± Ø¨Ø§Ø²Ù‡ Report Ø¨Ù‡ {val} Ø¯Ù‚ÛŒÙ‚Ù‡ ØªØºÛŒÛŒØ± Ú©Ø±Ø¯.")
        except:
            bot.answer_callback_query(call.id, "Error Ø¯Ø± Ù¾Ø±Ø¯Ø§Ø²Ø´ Ø¨Ø§Ø²Ù‡ âŒ")

    # --- Ù‡Ù†Ø¯Ù„Ø± Prediction Ø¨ÛŒØªâ€ŒÚ©ÙˆÛŒÙ† ---
    elif data.startswith("predict_h_"):
        try:
            hours = int(data.split("_")[2])
            bot.answer_callback_query(call.id, f"Prediction Ø¨Ø±Ø§ÛŒ {hours} Ø³Ø§Ø¹Øª Ø¢ÛŒÙ†Ø¯Ù‡ â³")

            text = build_btc_forecast_text(hours)
            chart, err = build_btc_forecast_chart(hours)

            if chart:
                # Ú©Ù¾Ø´Ù† Ú©ÙˆØªØ§Ù‡ Ø¨Ø±Ø§ÛŒ ØªØµÙˆÛŒØ±
                bot.send_photo(chat_id, chart, caption="ðŸ“Š Ù†Ù…ÙˆØ¯Ø§Ø± Prediction BTC")
                # Ù…ØªÙ† Ú©Ø§Ù…Ù„ ØªØ­Ù„ÛŒÙ„ Ø¬Ø¯Ø§ Ø§Ø±Ø³Ø§Ù„ Ù…ÛŒØ´Ù‡
                send_message(chat_id, text)
            else:
                if err:
                    send_message(chat_id, err)
                else:
                    send_message(chat_id, text)

        except Exception as e:
            send_message(chat_id, f"âš ï¸ Error Ø¯Ø± Prediction: {e}")
 
 
if __name__ == "__main__":
    run_ws_thread()         # ðŸ”¹ ÙˆØ¨â€ŒØ³ÙˆÚ©Øª Ø¨Ø§ÛŒÙ†Ù†Ø³ Ø±Ø§Ù‡ Ù…ÛŒÙØªÙ‡
    print("ðŸš€ Bot started...")
    bot.infinity_polling()
