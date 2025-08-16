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
previous_positions = {}
user_intervals = {}  # بازه گزارش‌دهی هر کاربر

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

# نرمال‌سازی داده‌ها از hyperdash
def _normalize_from_hyperdash(raw):
    out = []
    items = []
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        for key in ("positions", "openPositions", "data"):
            if key in raw and isinstance(raw[key], list):
                items = raw[key]
                break
    for p in items:
        pair = p.get("pair") or p.get("symbol") or p.get("coin")
        side = (p.get("side") or p.get("positionSide") or "").upper()
        size = _safe_float(p.get("size") or p.get("amount") or p.get("qty"))
        entry = _safe_float(p.get("entryPrice") or p.get("avgEntryPrice"))
        mark = _safe_float(p.get("markPrice") or p.get("price"))
        pnl  = _safe_float(p.get("unrealizedPnl") or p.get("pnl"))
        if abs(size) > 0:
            uid = f"HD:{pair}:{side}"
            out.append({
                "uid": uid,
                "pair": pair or "UNKNOWN",
                "side": side or ("LONG" if size > 0 else "SHORT"),
                "size": abs(size),
                "entryPrice": entry,
                "markPrice": mark,
                "unrealizedPnl": pnl
            })
    return out

# نرمال‌سازی از hyperliquid
def _normalize_from_hyperliquid(raw):
    out = []
    items = raw.get("assetPositions", []) if isinstance(raw, dict) else raw
    for p in items:
        try:
            pos = p.get("position", {})
            szi = _safe_float(pos.get("szi"))
            if szi == 0:
                continue
            coin = pos.get("coin") or "UNKNOWN"
            entry = _safe_float(pos.get("entryPx"))
            pnl = _safe_float(pos.get("unrealizedPnl"))
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
        except:
            continue
    return out

# گرفتن پوزیشن‌ها
def get_positions(wallet):
    try:
        r = requests.get(f"https://hyperdash.info/api/v1/trader/{wallet}/positions", timeout=10)
        if r.status_code == 200:
            norm = _normalize_from_hyperdash(r.json())
            if norm:
                return norm
    except:
        pass
    try:
        url = "https://api.hyperliquid.xyz/info"
        payload = {"type": "clearinghouseState", "user": wallet}
        r = requests.post(url, json=payload, timeout=12)
        norm = _normalize_from_hyperliquid(r.json())
        return norm
    except:
        return []

def send_message(chat_id, text):
    bot.send_message(chat_id, text, parse_mode="Markdown")

def format_position_line(p):
    lines = [
        f"🪙 *{p.get('pair','?')}* | {('🟢 LONG' if p['side']=='LONG' else '🔴 SHORT')}",
        f"🔢 Size: {p['size']}",
        f"🎯 Entry: {p['entryPrice']}",
    ]
    if p.get("markPrice") is not None:
        lines.append(f"📍 Mark: {p['markPrice']}")
    lines.append(f"💵 PNL: {_sign_fmt(p.get('unrealizedPnl'))}")
    return "\n".join(lines)

# ================== منطق مانیتورینگ ==================
def check_positions():
    for chat_id, wallets in user_wallets.items():
        for wallet in wallets:
            current_positions = get_positions(wallet)
            prev_positions = previous_positions.get((chat_id, wallet), [])
            current_map = {p["uid"]: p for p in current_positions}
            prev_map    = {p["uid"]: p for p in prev_positions}
            
            # باز شدن پوزیشن
            for uid, pos in current_map.items():
                if uid not in prev_map:
                    msg = (
                        "🚀 *Position Opened*\n"
                        f"💼 (`{wallet}`)\n━━━━━━━━━━\n{format_position_line(pos)}"
                    )
                    send_message(chat_id, msg)
            
            # بسته شدن پوزیشن
            for uid, pos in prev_map.items():
                if uid not in current_map:
                    msg = (
                        "✅ *Position Closed*\n"
                        f"💼 (`{wallet}`)\n━━━━━━━━━━\n"
                        f"🪙 *{pos['pair']}* | {('🟢 LONG' if pos['side']=='LONG' else '🔴 SHORT')}\n"
                        f"🔢 Size: {pos['size']}\n"
                        f"🎯 Entry: {pos['entryPrice']}\n"
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
def get_top10():
    try:
        r = requests.get("https://api.coingecko.com/api/v3/coins/markets",
                         params={"vs_currency": "usd", "order": "market_cap_desc", "per_page": 10, "page": 1},
                         timeout=10)
        data = r.json()
    except Exception as e:
        return f"⚠️ خطا در گرفتن اطلاعات بازار: {e}"

    lines = ["📊 *ده ارز برتر بازار و لانگ/شورت ریشیو*:"]
    for c in data:
        symbol = (c.get("symbol") or "").upper()
        name   = c.get("name")
        price  = c.get("current_price")
        change = c.get("price_change_percentage_24h")

        long_pct = short_pct = "?"

        try:
            url = "https://fapi.binance.com/futures/data/globalLongShortAccountRatio"
            resp = requests.get(url, params={"symbol": f"{symbol}USDT", "period": "5m", "limit": 1}, timeout=8)
            if resp.status_code == 200:
                ratios = resp.json()
                if ratios:
                    long_ratio = float(ratios[0]["longAccount"])
                    short_ratio = float(ratios[0]["shortAccount"])
                    total = long_ratio + short_ratio
                    if total > 0:
                        long_pct = round((long_ratio / total) * 100, 1)
                        short_pct = round((short_ratio / total) * 100, 1)
        except:
            pass

        lines.append(
            f"🪙 {name} (${price:,.2f}, {change:+.2f}%)\n"
            f"   🟢 Long: {long_pct}% | 🔴 Short: {short_pct}%"
        )

    return "\n\n".join(lines)

# ================== منوها ==================
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
    markup.add(InlineKeyboardButton("📊 گزارش ۱۰ ارز برتر", callback_data="top10"))
    bot.send_message(chat_id, "⏱ بازه گزارش رو انتخاب کن یا دکمه زیر:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    chat_id = call.message.chat.id
    if call.data.startswith("interval_"):
        val = int(call.data.split("_")[1])
        user_intervals[chat_id] = val
        bot.answer_callback_query(call.id, f"بازه {val} دقیقه‌ای انتخاب شد ✅")
        send_message(chat_id, f"⏱ گزارش دوره‌ای هر *{val} دقیقه* برای شما ارسال خواهد شد.")
    elif call.data == "top10":
        bot.answer_callback_query(call.id)
        report = get_top10()
        send_message(chat_id, report)

# ================== دستورات ==================
@bot.message_handler(commands=['start'])
def start(message):
    chat_id = message.chat.id
    user_wallets.setdefault(chat_id, [])
    user_intervals[chat_id] = 1
    send_message(chat_id, "سلام 👋\nولت‌هات رو یکی یکی بفرست.\n\n"
                          "دستور /stop برای توقف.\n"
                          "دستور /interval برای تغییر زمان گزارش.")
    send_interval_menu(chat_id)

@bot.message_handler(commands=['interval'])
def interval(message):
    send_interval_menu(message.chat.id)

@bot.message_handler(commands=['stop'])
def stop(message):
    chat_id = message.chat.id
    user_wallets.pop(chat_id, None)
    keys_to_remove = [k for k in previous_positions if k[0] == chat_id]
    for k in keys_to_remove:
        previous_positions.pop(k, None)
    send_message(chat_id, "🛑 مانیتورینگ متوقف شد. برای شروع دوباره ولت جدید بفرست.")

@bot.message_handler(func=lambda m: True)
def add_wallet(message):
    chat_id = message.chat.id
    wallet = message.text.strip()
    if not wallet:
        return
    if wallet in user_wallets.get(chat_id, []):
        send_message(chat_id, f"⚠️ ولت `{wallet}` از قبل اضافه شده.")
        return
    user_wallets.setdefault(chat_id, []).append(wallet)
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
