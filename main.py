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
        
        
# ================== پیش‌بینی BTC (قابل تنظیم) ==================
import matplotlib.pyplot as plt
import io

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

def _fetch_binance_closes(symbol="BTCUSDT", interval="5m", limit=500):
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(url, params=params, timeout=10, headers=HEADERS)
    r.raise_for_status()
    data = r.json()
    closes = [float(k[4]) for k in data]
    times  = [int(k[0]) for k in data]
    return times, closes

def _fetch_kraken_closes(pair="XBTUSDT", interval=60):
    url = "https://api.kraken.com/0/public/OHLC"
    params = {"pair": pair, "interval": interval}
    r = requests.get(url, params=params, timeout=10, headers=HEADERS)
    r.raise_for_status()
    data = r.json()
    key = [k for k in data["result"].keys() if k != "last"][0]
    ohlc = data["result"][key]
    closes = [float(c[4]) for c in ohlc]
    times = [int(c[0]) for c in ohlc]
    return times, closes
    
def predict_btc_price(hours_ahead=4):
    # Binance → Kraken fallback
    use_step = 5
    try:
        _, closes = _fetch_binance_closes("BTCUSDT", "5m", 500)
        source = "Binance (5m)"
        use_step = 5
    except Exception as e:
        print(f"[Binance Error] {e} → fallback به Kraken")
        _, closes = _fetch_kraken_closes("XBTUSDT", interval=60)
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

    # اندیکاتورها
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

    return {
        "last": last_price, "point": point,
        "ci68": ci68, "ci95": ci95,
        "mu": mu, "sigma": sigma,
        "mu_adj": mu_adj, "sigma_adj": sigma_adj,
        "trend": trend, "rsi": rsi_val,
        "macd": macd, "macd_sig": macd_sig, "macd_hist": macd_hist,
        "bb_width": bb_w, "bb_up": bb_up, "bb_mid": bb_mid, "bb_low": bb_low,
        "n": n, "step": use_step, "source": source, "closes": closes
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

    return (
        f"🔮 *BTC {hours}h Forecast (Enhanced)*\n"
        f"📊 منبع داده: {source}\n"
        f"💵 قیمت فعلی: ${last:,.2f}\n"
        f"🎯 پیش‌بینی نقطه‌ای: ${point:,.2f}\n"
        f"📏 بازه ۶۸٪: ${l68:,.2f} — ${u68:,.2f}\n"
        f"📐 بازه ۹۵٪: ${l95:,.2f} — ${u95:,.2f}\n"
        f"📈 EMA12-26: {trend_pc:.2f}% | 🔄 RSI(14): {rsi_val:.1f}\n"
        + table +
        "⚠️ *سناریوی آماری است؛ توصیه معاملاتی محسوب نمی‌شود.*"
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
    
    
# ================== منو ==================
def send_interval_menu(chat_id):
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
    # دکمه‌ی پیش‌بینی ۴ساعته (قدیمی) + دکمه‌ی جدید برای انتخاب بازه
    markup.add(InlineKeyboardButton("🔮 پیش‌بینی ۴ساعته BTC (Enhanced)", callback_data="predict_btc_4h"))
    markup.add(InlineKeyboardButton("🕰 انتخاب بازه پیش‌بینی BTC", callback_data="predict_btc_menu"))
    markup.add(InlineKeyboardButton("📊 گزارش 10 ارز برتر", callback_data="top10"))
    bot.send_message(chat_id, "⏱ بازه گزارش رو انتخاب کن:", reply_markup=markup)
    
@bot.callback_query_handler(func=lambda call: call.data.startswith("interval_"))
def callback_interval(call):
    chat_id = call.message.chat.id
    val = int(call.data.split("_")[1])
    user_intervals[chat_id] = val
    bot.answer_callback_query(call.id, f"بازه {val} دقیقه‌ای انتخاب شد ✅")
    send_message(chat_id, f"⏱ گزارش دوره‌ای هر *{val} دقیقه* برای شما ارسال میشه.")

@bot.callback_query_handler(func=lambda call: call.data == "top10")
def callback_top10(call):
    chat_id = call.message.chat.id
    report = get_top10_report()
    bot.answer_callback_query(call.id, "📊 گزارش ارسال شد")
    send_message(chat_id, report)

@bot.callback_query_handler(func=lambda call: call.data == "predict_btc_4h")
def callback_predict_btc_4h(call):
    chat_id = call.message.chat.id
    bot.answer_callback_query(call.id, "در حال محاسبه پیش‌بینی…")
    text = build_btc_forecast_text(hours=4)
    ci_note = (
        "\nℹ️ *توضیح بازه ۹۵٪*: اگر همین شرایط بازار ادامه پیدا کنه، "
        "با تقریباً ۹۵٪ احتمال قیمتِ بازه‌ی انتخابی بین حد پایین و بالای «CI 95%» قرار می‌گیره. "
        "این یک برآورد آماریه، نه قطعیت."
    )
    send_message(chat_id, text + ci_note)
    img_buf, err = build_btc_forecast_chart(hours=4)
    if img_buf:
        try:
            bot.send_photo(chat_id, img_buf)
        except Exception as e:
            print(f"[SendPhoto Error] {e}")
    elif err:
        send_message(chat_id, f"⚠️ {err}")

# ---------- منوی انتخاب بازه پیش‌بینی ----------
@bot.callback_query_handler(func=lambda call: call.data == "predict_btc_menu")
def callback_predict_btc_menu(call):
    chat_id = call.message.chat.id
    bot.answer_callback_query(call.id, "بازه پیش‌بینی رو انتخاب کن")
    mk = InlineKeyboardMarkup(row_width=3)
    # گزینه‌ها: 1h, 2h, 4h, 6h, 12h, 24h
    buttons = [
        InlineKeyboardButton("1h", callback_data="predict_btc_hours_1"),
        InlineKeyboardButton("2h", callback_data="predict_btc_hours_2"),
        InlineKeyboardButton("4h", callback_data="predict_btc_hours_4"),
        InlineKeyboardButton("6h", callback_data="predict_btc_hours_6"),
        InlineKeyboardButton("12h", callback_data="predict_btc_hours_12"),
        InlineKeyboardButton("24h", callback_data="predict_btc_hours_24"),
    ]
    # سه‌تایی در هر ردیف
    mk.add(*buttons[:3])
    mk.add(*buttons[3:])
    bot.send_message(chat_id, "⏳ یکی از بازه‌ها رو انتخاب کن:", reply_markup=mk)

@bot.callback_query_handler(func=lambda call: call.data.startswith("predict_btc_hours_")))
def callback_predict_btc_hours(call):
    chat_id = call.message.chat.id
    try:
        hours = int(call.data.split("_")[-1])
    except Exception:
        hours = 4
    bot.answer_callback_query(call.id, f"پیش‌بینی {hours} ساعته در حال محاسبه…")
    text = build_btc_forecast_text(hours=hours)
    ci_note = (
        "\nℹ️ *توضیح بازه ۹۵٪*: اگر همین شرایط بازار ادامه پیدا کنه، "
        "با تقریباً ۹۵٪ احتمال قیمتِ بازه‌ی انتخابی بین حد پایین و بالای «CI 95%» قرار می‌گیره. "
        "این یک برآورد آماریه، نه قطعیت."
    )
    send_message(chat_id, text + ci_note)
    img_buf, err = build_btc_forecast_chart(hours=hours)
    if img_buf:
        try:
            bot.send_photo(chat_id, img_buf)
        except Exception as e:
            print(f"[SendPhoto Error] {e}")
    elif err:
        send_message(chat_id, f"⚠️ {err}")
        
        
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
        "📍 /predict → پیش‌بینی ۴ ساعته BTC\n"
        "📍 /predictmenu → انتخاب بازه‌ی پیش‌بینی (1/2/4/6/12/24h)"
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
    # رفتار پیش‌فرض: 4h
    text = build_btc_forecast_text(hours=4)
    ci_note = (
        "\nℹ️ *توضیح بازه ۹۵٪*: اگر همین شرایط بازار ادامه پیدا کنه، "
        "با تقریباً ۹۵٪ احتمال قیمتِ بازه‌ی انتخابی بین حد پایین و بالای «CI 95%» قرار می‌گیره. "
        "این یک برآورد آماریه، نه قطعیت."
    )
    send_message(chat_id, text + ci_note)
    img_buf, err = build_btc_forecast_chart(hours=4)
    if img_buf:
        try:
            bot.send_photo(chat_id, img_buf)
        except Exception as e:
            print(f"[SendPhoto Error] {e}")
    elif err:
        send_message(chat_id, f"⚠️ {err}")

@bot.message_handler(commands=['predictmenu'])
def predictmenu(message):
    chat_id = message.chat.id
    # همان منوی Inline انتخاب بازه
    callback_predict_btc_menu(type("obj", (), {"message": type("m", (), {"chat": type("c", (), {"id": chat_id})})(), "id": "fake"}))
   
   
# ================== اجرای زمان‌بندی ==================
def run_scheduler():
    schedule.every(1).minutes.do(check_positions)
    schedule.every(1).minutes.do(periodic_report)
    while True:
        schedule.run_pending()
        time.sleep(1)

# اجرای Scheduler در یک Thread جدا
threading.Thread(target=run_scheduler, daemon=True).start()

print("🤖 Bot started...")
bot.infinity_polling()
