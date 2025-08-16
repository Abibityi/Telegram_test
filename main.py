import time
import schedule
import telebot
import threading
import requests
import os
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

# ---------- ابزارهای کمکی ----------
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

def get_positions(wallet):
    try:
        url = f"https://hyperdash.info/api/v1/trader/{wallet}/positions"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            norm = _normalize_from_hyperdash(r.json())
            if norm:
                return norm
    except Exception as e:
        print(f"[HyperDash] error for {wallet}: {e}")
    try:
        url = "https://api.hyperliquid.xyz/info"
        payload = {"type": "clearinghouseState", "user": wallet}
        r = requests.post(url, json=payload, timeout=12)
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
    bot.send_message(chat_id, text, parse_mode="Markdown")

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
                        f"🪙 *{pos.get('pair','?')}* | {('🟢 LONG' if pos.get('side')=='LONG' else '🔴 SHORT')}\n"
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
        # 1) قیمت و تغییر ۲۴ساعت از CoinGecko
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 10, "page": 1}
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        coins = r.json()

        lines = []
        for c in coins:
            symbol = c.get("symbol", "").upper()
            price = c.get("current_price", 0)
            change = c.get("price_change_percentage_24h", 0)

            # 2) پوزیشن‌ها از CoinGlass
            cg_long, cg_short = "-", "-"
            try:
                cg_url = f"https://open-api.coinglass.com/api/pro/v1/futures/liquidation_chart?symbol={symbol.upper()}"
                cg_res = requests.get(cg_url, timeout=8)
                if cg_res.status_code == 200 and "data" in cg_res.json():
                    # ساده‌سازی: اینجا میشه دیتا رو parse کرد، ولی به خاطر محدودیت API کلید لازم داره
                    pass
            except: pass

            # 3) پوزیشن‌ها از Binance Futures
            bin_long, bin_short = "-", "-"
            try:
                b_url = f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={symbol.upper()}USDT&period=5m&limit=1"
                b_res = requests.get(b_url, timeout=8)
                if b_res.status_code == 200:
                    data = b_res.json()
                    if data:
                        bin_long = f"{float(data[0]['longAccount'])*100:.1f}%"
                        bin_short = f"{float(data[0]['shortAccount'])*100:.1f}%"
            except: pass

            # خروجی نهایی برای هر کوین
            lines.append(
                f"🪙 *{symbol}*\n"
                f"💵 ${price:,.2f} ({change:+.2f}%)\n"
                f"📊 Binance: 🟢 {bin_long} | 🔴 {bin_short}\n"
                f"📊 CoinGlass: 🟢 {cg_long} | 🔴 {cg_short}\n"
                "━━━━━━━━━━"
            )

        return "📊 *Top 10 Coins by Market Cap*\n\n" + "\n".join(lines)

    except Exception as e:
        return f"⚠️ خطا در دریافت گزارش: {e}"

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
        "📍 /top10 → گزارش ۱۰ ارز برتر"
    )
    send_interval_menu(chat_id)

@bot.message_handler(commands=['interval'])
def interval(message):
    send_interval_menu(message.chat.id)

@bot.message_handler(commands=['stop'])
def stop(message):
    chat_id = message.chat.id
    if chat_id in user_wallets:
        user_wallets.pop(chat_id, None)
        keys_to_remove = [k for k in previous_positions if k[0] == chat_id]
        for k in keys_to_remove:
            previous_positions.pop(k, None)
        send_message(chat_id, "🛑 مانیتورینگ متوقف شد.")
    else:
        send_message(chat_id, "⚠️ مانیتورینگی فعال نبود.")

@bot.message_handler(commands=['top10'])
def cmd_top10(message):
    send_message(message.chat.id, get_top10_report())

@bot.message_handler(func=lambda m: True)
def add_wallet(message):
    chat_id = message.chat.id
    wallet = message.text.strip()
    if not wallet:
        return
    user_wallets.setdefault(chat_id, [])
    if wallet in user_wallets[chat_id]:
        send_message(chat_id, f"⚠️ ولت `{wallet}` از قبل اضافه شده.")
        return
    user_wallets[chat_id].append(wallet)
    previous_positions[(chat_id, wallet)] = get_positions(wallet)
    send_message(chat_id, f"✅ ولت `{wallet}` اضافه شد و مانیتور میشه.")

# ================== اجرا ==================
schedule.every(1).minutes.do(periodic_report)

def run_scheduler():
    while True:
        check_positions()
        schedule.run_pending()
        time.sleep(2)

threading.Thread(target=run_scheduler, daemon=True).start()
bot.polling()                          
