HEADERS = {"User-Agent": "Mozilla/5.0"}
import time
import schedule
import telebot
import threading
import requests
import os
import math
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
        
def _ema(values, span):
    if not values:
        return 0.0
    alpha = 2 / (span + 1.0)
    s = values[0]
    for v in values[1:]:
        s = alpha * v + (1 - alpha) * s
    return s

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
    key = list(data["result"].keys())[0]
    ohlc = data["result"][key]
    closes = [float(c[4]) for c in ohlc]
    times = [int(c[0]) for c in ohlc]
    return times, closes
    
def predict_btc_price(hours_ahead=4):
    try:
        _, closes = _fetch_binance_closes("BTCUSDT", "5m", 500)
        source = "Binance (5m)"
    except Exception as e:
        print(f"[Binance Error] {e} → fallback به Kraken")
        _, closes = _fetch_kraken_closes("XBTUSDT", interval=60)
        source = "Kraken (1h)"

    if len(closes) < 60:
        return {"error": "داده‌های کافی برای پیش‌بینی وجود ندارد."}

    last_price = closes[-1]

    rets = []
    for i in range(1, len(closes)):
        c0, c1 = closes[i-1], closes[i]
        if c0 <= 0:
            continue
        rets.append(math.log(c1 / c0))

    if not rets:
        return {"error": "عدم امکان محاسبه بازده‌ها."}

    window = min(200, len(rets))
    r_win = rets[-window:]
    mu = sum(r_win) / len(r_win)
    var = sum((x - mu)**2 for x in r_win) / max(1, len(r_win) - 1)
    sigma = math.sqrt(var)

    lookback_prices = closes[-150:] if len(closes) >= 150 else closes
    ema_fast = _ema(lookback_prices, 12)
    ema_slow = _ema(lookback_prices, 26)
    trend = (ema_fast - ema_slow) / ema_slow if ema_slow else 0.0
    rsi_val = _rsi(closes, 14)

    mu_adj = mu + 0.20 * trend
    if rsi_val > 70:
        mu_adj -= 0.25 * abs(mu)
    elif rsi_val < 30:
        mu_adj += 0.25 * abs(mu)

    step_minutes = 5
    n = int((hours_ahead * 60) / step_minutes)

    log_S0 = math.log(last_price)
    log_mean = log_S0 + n * mu_adj
    log_std = math.sqrt(n) * sigma

    point = math.exp(log_mean)
    ci68 = (math.exp(log_mean - log_std), math.exp(log_mean + log_std))
    ci95 = (math.exp(log_mean - 1.96 * log_std), math.exp(log_mean + 1.96 * log_std))

    return {
        "last": last_price,
        "point": point,
        "ci68": ci68,
        "ci95": ci95,
        "mu": mu,
        "sigma": sigma,
        "mu_adj": mu_adj,
        "trend": trend,
        "rsi": rsi_val,
        "n": n,
        "source": source
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
    trend = res["trend"] * 100
    source = res["source"]

    return (
        "🔮 *BTC 4h Forecast*\n"
        f"⏱ افق: {hours} ساعت ({res['n']} کندل)\n"
        f"📊 منبع داده: {source}\n"
        f"💵 قیمت فعلی: ${last:,.2f}\n"
        f"🎯 پیش‌بینی نقطه‌ای: ${point:,.2f}\n"
        f"📏 بازه ۶۸٪: ${l68:,.2f} — ${u68:,.2f}\n"
        f"📐 بازه ۹۵٪: ${l95:,.2f} — ${u95:,.2f}\n"
        f"📈 مومنتوم EMA12-26: {trend:.2f}%\n"
        f"🔄 RSI(14): {rsi_val:.1f}\n"
        "⚙️ روش: بازده لگاریتمی + واریانس (GBM) با تعدیل مومنتوم/RSI\n"
        "⚠️ *این صرفاً یک پیش‌بینی آماری است و به هیچ وجه پیشنهاد خرید یا فروش نیست.*"
    )
    
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
    markup.add(InlineKeyboardButton("📊 گزارش 10 ارز برتر", callback_data="top10"))
    markup.add(InlineKeyboardButton("🔮 پیش‌بینی ۴ساعته BTC", callback_data="predict_btc_4h"))
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
    send_message(chat_id, text)
    
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
        "📍 /predict → پیش‌بینی ۴ ساعته BTC"
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
    text = build_btc_forecast_text(hours=4)
    send_message(chat_id, text)


@bot.message_handler(func=lambda m: True, content_types=['text'])
def add_wallet(message):
    chat_id = message.chat.id
    wallet = message.text.strip()
    if not wallet or len(wallet) < 5:
        send_message(chat_id, "❌ ولت نامعتبره.")
        return
    user_wallets.setdefault(chat_id, []).append(wallet)
    send_message(chat_id, f"✅ ولت `{wallet}` اضافه شد و مانیتورینگ شروع شد.")
    
def run_scheduler():
    schedule.every(1).minutes.do(check_positions)
    schedule.every(1).minutes.do(periodic_report)
    while True:
        schedule.run_pending()
        time.sleep(1)


threading.Thread(target=run_scheduler, daemon=True).start()

print("🤖 Bot started...")
bot.infinity_polling()
