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
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# ================== تنظیمات ==================
API_TOKEN = os.environ.get("API_TOKEN")
if not API_TOKEN:
    raise SystemExit("❌ API_TOKEN در متغیرهای محیطی تنظیم نشده")

bot = telebot.TeleBot(API_TOKEN)

# برای هر کاربر یک لیست ولت ذخیره می‌کنیم
user_wallets = {}
previous_positions = {}   # کلید: (chat_id, wallet)
user_intervals = {}

def _safe_float(x, default=0.0):
    try:
        return float(x)
    except:
        return default

def _sign_fmt(x):
    v = _safe_float(x, 0.0)
    if v >= 0:
        return f"✅ +{v:,.2f}"
    else:
        return f"🔴 {v:,.2f}"
        
# ---------- نرمال‌سازی داده‌های HyperDash ----------
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

# ---------- نرمال‌سازی داده‌های Hyperliquid ----------
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
    
# ---------- دریافت پوزیشن‌ها ----------
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

# ---------- فرمت پیام ----------
def format_position_line(p):
    lines = [
        f"🪙 *{p.get('pair','?')}* | {('🟢 LONG' if p.get('side')=='LONG' else '🔴 SHORT')}",
        f"🔢 Size: {p.get('size','?')}",
        f"🎯 Entry: {p.get('entryPrice','?')}",
    ]
    if p.get("markPrice") is not None:
        lines.append(f"📍 Mark: {p.get('markPrice')}")
    lines.append(f"💵 PNL: {_sign_fmt(p.get('unrealizedPnl'))}")
    return "\n".join(lines)

def send_message(chat_id, text):
    try:
        bot.send_message(chat_id, text, parse_mode="Markdown")
    except Exception as e:
        print(f"[SendMessage Error] {e}")
        
        
# ================== مانیتورینگ لحظه‌ای + گزارش دوره‌ای ==================
def check_positions():
    for chat_id, wallets in user_wallets.items():
        for wallet in wallets:
            current_positions = get_positions(wallet)
            prev_positions = previous_positions.get((chat_id, wallet), [])

            current_map = {p["uid"]: p for p in current_positions}
            prev_map    = {p["uid"]: p for p in prev_positions}

            # پوزیشن جدید
            for uid, pos in current_map.items():
                if uid not in prev_map:
                    msg = (
                        "🚀 *Position Opened*\n"
                        f"💼 (`{wallet}`)\n"
                        "━━━━━━━━━━\n"
                        f"{format_position_line(pos)}"
                    )
                    send_message(chat_id, msg)

            # پوزیشن بسته
            for uid, pos in prev_map.items():
                if uid not in current_map:
                    msg = (
                        "✅ *Position Closed*\n"
                        f"💼 (`{wallet}`)\n"
                        "━━━━━━━━━━\n"
                        f"🪙 *{pos.get('pair','?')}* | "
                        f"{('🟢 LONG' if pos.get('side')=='LONG' else '🔴 SHORT')}\n"
                        f"🔢 Size: {pos.get('size')}\n"
                        f"🎯 Entry: {pos.get('entryPrice')}\n"
                        f"💵 Final PNL: {_sign_fmt(pos.get('unrealizedPnl',0))}\n"
                        "🔚 پوزیشن بسته شد."
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
            header = f"🕒 *Periodic Report ({interval} min)*\n💼 (`{wallet}`)\n━━━━━━━━━━"
            if current_positions:
                body = "\n\n".join([format_position_line(p) for p in current_positions])
                send_message(chat_id, f"{header}\n{body}")
            else:
                send_message(chat_id, f"{header}\n⏳ هیچ پوزیشنی باز نیست.")
                
# ================== گزارش ۱۰ ارز برتر ==================
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
                f"🪙 *{symbol}*\n"
                f"💵 ${price:,.2f} ({change:+.2f}%)\n"
                f"📊 Binance: 🟢 {bin_long} | 🔴 {bin_short}\n"
                "━━━━━━━━━━"
            )

        return "📊 *Top 10 Coins by Market Cap*\n\n" + "\n".join(lines)

    except Exception as e:
        return f"⚠️ خطا در دریافت گزارش: {e}"
        
# ================== منوها ==================
def send_interval_menu(chat_id):
    """
    ✅ فقط تنظیم بازه گزارش دوره‌ای
    (دکمه‌های 'پیش‌بینی' و 'تاپ۱۰' از این منو حذف شدند)
    """
    markup = InlineKeyboardMarkup()
    options = [
        ("1 دقیقه", 1),
        ("15 دقیقه", 15),
        ("30 دقیقه", 30),
        ("4 ساعت", 240),
        ("24 ساعت", 1440),
    ]
    for text, val in options:
        markup.add(InlineKeyboardButton(text, callback_data=f"interval_{val}"))
    bot.send_message(chat_id, "⏱ بازه گزارش رو انتخاب کن:", reply_markup=markup)

def send_predict_menu(chat_id):
    """
    ✅ منوی انتخاب بازه پیش‌بینی BTC
    از طریق /predict باز می‌شود.
    """
    markup = InlineKeyboardMarkup()
    hour_opts = [1, 2, 4, 8, 12, 24]
    row = []
    for h in hour_opts:
        row.append(InlineKeyboardButton(f"{h} ساعت", callback_data=f"predict_h_{h}"))
        if len(row) == 3:
            markup.row(*row)
            row = []
    if row:
        markup.row(*row)
    bot.send_message(chat_id, "🔮 بازه پیش‌بینی BTC رو انتخاب کن:", reply_markup=markup)
    
# ================== پیش‌بینی BTC (بهبود دقت + استراتژی‌های بیشتر) ==================
import matplotlib.pyplot as plt
import io

# -------- اندیکاتورهای پایه --------
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

# -------- دریافت داده‌های OHLCV (برای استراتژی‌ها) --------
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

# -------- استراتژی‌های جدید --------
def _stoch_rsi(values, period=14, k=3, d=3):
    if len(values) < period + 1:
        return 50.0, 50.0
    # محاسبه RSI رولینگ
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
    # %K و %D ساده
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
        # حداقل برای محاسبه ابر
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

# -------- دریافت داده و مدل آماری پایه + استراتژی‌ها --------
def predict_btc_price(hours_ahead=4):
    # سعی می‌کنیم از بایننس با تایم‌فریم 5m بخوانیم؛ در صورت خطا → کرَکن با 1h
    use_step = 5
    try:
        times, opens, highs, lows, closes, vols = _fetch_binance_ohlcv("BTCUSDT", "5m", 500)
        source = "Binance (5m)"
        use_step = 5
    except Exception as e:
        print(f"[Binance Error] {e} → fallback به Kraken")
        times, opens, highs, lows, closes, vols = _fetch_kraken_ohlcv("XBTUSDT", interval=60)
        source = "Kraken (1h)"
        use_step = 60

    if len(closes) < 60:
        return {"error": "داده‌های کافی برای پیش‌بینی وجود ندارد."}

    last_price = closes[-1]

    # بازده لگاریتمی
    rets = []
    for i in range(1, len(closes)):
        c0, c1 = closes[i-1], closes[i]
        if c0 <= 0:
            continue
        rets.append(math.log(c1 / c0))
    if not rets:
        return {"error": "عدم امکان محاسبه بازده‌ها."}

    # mu و sigma
    window = min(200, len(rets))
    r_win = rets[-window:]
    mu = sum(r_win) / len(r_win)
    var = sum((x - mu)**2 for x in r_win) / max(1, len(r_win) - 1)
    sigma = math.sqrt(var)

    # اندیکاتورهای پایه
    ema_fast = _ema(closes, 12)
    ema_slow = _ema(closes, 26)
    trend = (ema_fast - ema_slow) / ema_slow if ema_slow else 0.0
    rsi_val = _rsi(closes, 14)
    macd, macd_sig, macd_hist = _macd(closes)
    bb_w, bb_up, bb_mid, bb_low = _bb_width(closes, 20)

    # نوسان کوتاه
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

    # -------- محاسبه استراتژی‌ها --------
    # Ichimoku
    ich = _ichimoku(highs, lows, closes)
    ich_signal, ich_text = "⚪ خنثی", "دادهٔ کافی برای ابر یا وضعیت بین ابرها خنثی است."
    ich_low, ich_high = None, None
    if ich:
        above_cloud = ich["close"] > max(ich["spanA"], ich["spanB"])
        below_cloud = ich["close"] < min(ich["spanA"], ich["spanB"])
        tenkan_above = ich["tenkan"] > ich["kijun"]
        if above_cloud and tenkan_above:
            ich_signal = "🟢 صعودی"
            ich_text = "قیمت بالای ابر ایچیموکو و تنکان‌سن بالای کیجون‌سن → برتری خریداران."
            ich_low, ich_high = ich["kijun"], max(ich["spanA"], ich["spanB"]) * 1.01
        elif below_cloud and not tenkan_above:
            ich_signal = "🔴 نزولی"
            ich_text = "قیمت زیر ابر ایچیموکو و تنکان‌سن زیر کیجون‌سن → فشار فروش بیشتر."
            ich_low, ich_high = min(ich["spanA"], ich["spanB"]) * 0.99, ich["kijun"]
        else:
            ich_signal = "⚪ خنثی"
            ich_text = "قیمت داخل/نزدیک ابر است یا سیگنال‌ها متناقض‌اند."
            ich_low, ich_high = min(ich["spanA"], ich["spanB"]), max(ich["spanA"], ich["spanB"])

    # Stoch RSI
    stoch, dval = _stoch_rsi(closes)
    if stoch >= 80:
        stoch_signal = "🔴 نزولی"
        stoch_text = "اشباع خرید → احتمال افزایش فشار فروش."
        stoch_low, stoch_high = ci68[0], max(point, ci68[1] * 0.99)
    elif stoch <= 20:
        stoch_signal = "🟢 صعودی"
        stoch_text = "اشباع فروش → احتمال ورود خریداران."
        stoch_low, stoch_high = min(point, ci68[0] * 1.01), ci68[1]
    else:
        stoch_signal = "⚪ خنثی"
        stoch_text = "در محدودهٔ میانی است؛ سیگنال قوی ندارد."
        stoch_low, stoch_high = ci68

    # ATR (Volatility)
    atr_val = _atr(highs, lows, closes, 14)
    # نسبت ATR به قیمت برای تخمین شدت نوسان
    atr_ratio = (atr_val / last_price) if last_price else 0.0
    if atr_ratio >= 0.02:
        atr_signal = "🔴 نوسان بالا"
        atr_text = "نوسان کوتاه‌مدت بالاست → ریسک حرکات تند."
        # بازه بازتر
        atr_low, atr_high = ci95
    elif atr_ratio <= 0.008:
        atr_signal = "🟢 نوسان کم"
        atr_text = "نوسان پایین‌تر از معمول → حرکت آرام‌تر محتمل."
        # بازه فشرده‌تر
        mid = point
        w = (ci68[1] - ci68[0]) * 0.5
        atr_low, atr_high = mid - w * 0.6, mid + w * 0.6
    else:
        atr_signal = "⚪ نرمال"
        atr_text = "نوسان در محدودهٔ معمول بازار است."
        atr_low, atr_high = ci68

    # Golden/Death Cross (SMA50/200)
    sma50 = _sma(closes, 50)
    sma200 = _sma(closes, 200)
    if sma50 > sma200:
        gd_signal = "🟢 صعودی"
        gd_text = "میانگین ۵۰ بالای ۲۰۰ → روند میان‌مدت مثبت."
        gd_low, gd_high = max(ci68[0], sma200 * 0.995), ci68[1] * 1.01
    elif sma50 < sma200:
        gd_signal = "🔴 نزولی"
        gd_text = "میانگین ۵۰ زیر ۲۰۰ → روند میان‌مدت منفی."
        gd_low, gd_high = ci68[0] * 0.99, min(ci68[1], sma200 * 1.005)
    else:
        gd_signal = "⚪ خنثی"
        gd_text = "تفاوت معنادار بین MA50 و MA200 دیده نمی‌شود."
        gd_low, gd_high = ci68

    # VWAP
    vwap = _vwap(highs, lows, closes, vols)
    if vwap is not None:
        if last_price > vwap * 1.002:
            vwap_signal = "🟢 حمایتی"
            vwap_text = "قیمت بالای VWAP → دست بالا با خریداران."
            vwap_low, vwap_high = max(ci68[0], vwap), ci68[1] * 1.005
        elif last_price < vwap * 0.998:
            vwap_signal = "🔴 مقاومتی"
            vwap_text = "قیمت زیر VWAP → فروشندگان فعال‌ترند."
            vwap_low, vwap_high = ci68[0] * 0.995, min(ci68[1], vwap)
        else:
            vwap_signal = "⚪ خنثی"
            vwap_text = "قیمت نزدیک VWAP → تعادل نسبی."
            vwap_low, vwap_high = ci68
    else:
        vwap_signal = "⚪ خنثی"
        vwap_text = "دادهٔ کافی برای VWAP موجود نیست."
        vwap_low, vwap_high = ci68

    strategies = [
        ("ایچیموکو", ich_signal, ich_text, ich_low, ich_high),
        ("استوک RSI", stoch_signal, stoch_text, stoch_low, stoch_high),
        ("ATR", atr_signal, atr_text, atr_low, atr_high),
        ("کراس طلایی/مرگ", gd_signal, gd_text, gd_low, gd_high),
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

def build_btc_forecast_text(hours=4):
    res = predict_btc_price(hours)
    if "error" in res:
        return f"⚠️ {res['error']}"

    last  = res["last"]
    point = res["point"]
    l68, u68 = res["ci68"]
    l95, u95 = res["ci95"]
    rsi_val = res["rsi"]
    trend_pc = res["trend"] * 100
    source = res["source"]

    # جدول خلاصه
    table = (
        "```\n"
        f"{'Metric':<18}{'Value':>18}\n"
        f"{'-'*36}\n"
        f"{'Source':<18}{source:>18}\n"
        f"{'Price (now)':<18}${last:>17,.2f}\n"
        f"{'Forecast':<18}${point:>17,.2f}\n"
        f"{'CI 68% Low':<18}${l68:>17,.2f}\n"
        f"{'CI 68% High':<18}${u68:>17,.2f}\n"
        f"{'CI 95% Low':<18}${l95:>17,.2f}\n"
        f"{'CI 95% High':<18}${u95:>17,.2f}\n"
        f"{'EMA Momentum':<18}{trend_pc:>17.2f}%\n"
        f"{'RSI(14)':<18}{rsi_val:>18.1f}\n"
        f"{'MACD Hist':<18}{res['macd_hist']:>18.6f}\n"
        f"{'BB Width':<18}{res['bb_width']:>18.4f}\n"
        "```\n"
    )

    # لیست استراتژی‌ها با توضیح و بازهٔ قیمتی
    strategies_text_lines = ["📊 *سیگنال استراتژی‌ها*:"]
    for name, sig, desc, lo, hi in res["strategies"]:
        rng = ""
        if lo and hi:
            rng = f"\n  🎯 *بازه تخمینی ({hours}ساعت آینده)*: ${lo:,.0f} — ${hi:,.0f}"
        strategies_text_lines.append(f"- {name}: {sig}\n  {desc}{rng}")
    strategies_text = "\n".join(strategies_text_lines)

    return (
        f"🔮 *پیش‌بینی BTC ({hours} ساعت آینده)*\n"
        f"📊 منبع داده: {source}\n"
        f"💵 قیمت فعلی: ${last:,.2f}\n"
        f"🎯 پیش‌بینی نقطه‌ای: ${point:,.2f}\n"
        f"📏 بازه ۶۸٪: ${l68:,.2f} — ${u68:,.2f}\n"
        f"📐 بازه ۹۵٪: ${l95:,.2f} — ${u95:,.2f}\n"
        f"📈 EMA12-26: {trend_pc:.2f}% | 🔄 RSI(14): {rsi_val:.1f}\n"
        + table +
        strategies_text +
        "\n\n⚠️ *این یک سناریوی آماری و آموزشی است؛ توصیهٔ معاملاتی محسوب نمی‌شود.*"
    )

def build_btc_forecast_chart(hours=4):
    res = predict_btc_price(hours)
    if "error" in res:
        return None, res["error"]

    closes = res["closes"]
    forecast = res["point"]
    l95, u95 = res["ci95"]

    plt.figure(figsize=(8,4))
    plt.plot(closes[-100:], label="Price", color="blue")
    plt.axhline(forecast, color="green", linestyle="--", label="Forecast")
    plt.axhline(l95, color="red", linestyle=":", label="CI95 Low")
    plt.axhline(u95, color="red", linestyle=":", label="CI95 High")
    plt.title(f"BTC Forecast (next {hours}h)")
    plt.legend()
    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)
    plt.close()
    return buf, None
    
# ================== منوها ==================
def send_interval_menu(chat_id):
    """
    ✅ فقط تنظیم بازه گزارش دوره‌ای
    (دکمه‌های 'پیش‌بینی' و 'تاپ۱۰' از این منو حذف شدند)
    """
    markup = InlineKeyboardMarkup()
    options = [
        ("1 دقیقه", 1),
        ("15 دقیقه", 15),
        ("30 دقیقه", 30),
        ("4 ساعت", 240),
        ("24 ساعت", 1440),
    ]
    for text, val in options:
        markup.add(InlineKeyboardButton(text, callback_data=f"interval_{val}"))
    bot.send_message(chat_id, "⏱ بازه گزارش رو انتخاب کن:", reply_markup=markup)

def send_predict_menu(chat_id):
    """
    ✅ منوی انتخاب بازه پیش‌بینی BTC
    از طریق /predict باز می‌شود.
    """
    markup = InlineKeyboardMarkup()
    hour_opts = [1, 2, 4, 8, 12, 24]
    row = []
    for h in hour_opts:
        row.append(InlineKeyboardButton(f"{h} ساعت", callback_data=f"predict_h_{h}"))
        if len(row) == 3:
            markup.row(*row)
            row = []
    if row:
        markup.row(*row)
    bot.send_message(chat_id, "🔮 بازه پیش‌بینی BTC رو انتخاب کن:", reply_markup=markup)
    
# ================== دستورات ==================
@bot.message_handler(commands=['start'])
def start(message):
    chat_id = message.chat.id
    user_wallets.setdefault(chat_id, [])
    user_intervals[chat_id] = 1
    send_message(chat_id,
        "سلام 👋\n"
        "آدرس ولت‌هات رو بفرست تا برات مانیتور کنم.\n\n"
        "📍 /stop → توقف مانیتورینگ\n"
        "📍 /interval → تغییر بازه گزارش\n"
        "📍 /top10 → گزارش ۱۰ ارز برتر\n"
        "📍 /predict → پیش‌بینی بیت‌کوین (انتخاب بازه)"
    )

@bot.message_handler(commands=['stop'])
def stop(message):
    chat_id = message.chat.id
    user_wallets.pop(chat_id, None)
    user_intervals.pop(chat_id, None)
    send_message(chat_id, "⏹ مانیتورینگ متوقف شد.")

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
    wallet = message.text.strip()
    if not wallet or len(wallet) < 5:
        send_message(chat_id, "❌ ولت نامعتبره.")
        return
    user_wallets.setdefault(chat_id, []).append(wallet)
    send_message(chat_id, f"✅ ولت `{wallet}` اضافه شد و مانیتورینگ شروع شد.")
    
# ================== اجرای زمان‌بندی ==================
def run_scheduler():
    schedule.every(1).minutes.do(check_positions)
    schedule.every(1).minutes.do(periodic_report)
    while True:
        schedule.run_pending()
        time.sleep(1)

threading.Thread(target=run_scheduler, daemon=True).start()
# ================== هندلر دکمه‌های شیشه‌ای ==================
@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    chat_id = call.message.chat.id
    data = call.data

    # --- هندلر تغییر بازه گزارش دوره‌ای ---
    if data.startswith("interval_"):
        try:
            val = int(data.split("_")[1])
            user_intervals[chat_id] = val
            bot.answer_callback_query(call.id, f"بازه گزارش {val} دقیقه تنظیم شد ✅")
            send_message(chat_id, f"⏱ بازه گزارش به {val} دقیقه تغییر کرد.")
        except:
            bot.answer_callback_query(call.id, "خطا در پردازش بازه ❌")

    # --- هندلر پیش‌بینی بیت‌کوین ---
    elif data.startswith("predict_h_"):
        try:
            hours = int(data.split("_")[2])
            bot.answer_callback_query(call.id, f"پیش‌بینی برای {hours} ساعت آینده ⏳")

            text = build_btc_forecast_text(hours)
            chart, err = build_btc_forecast_chart(hours)

            if chart:
                bot.send_photo(chat_id, chart, caption=text, parse_mode="Markdown")
            else:
                if err:
                    send_message(chat_id, err)
                else:
                    send_message(chat_id, text)

        except Exception as e:
            send_message(chat_id, f"⚠️ خطا در پیش‌بینی: {e}")
print("🤖 Bot started...")
bot.infinity_polling()
