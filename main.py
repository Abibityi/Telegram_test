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

def get_positions(wallet):
    try:
        url = f"https://hyperdash.info/api/v1/trader/{wallet}/positions"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return data.get("positions", [])
    except Exception:
        pass
    return []

def send_message(chat_id, text):
    bot.send_message(chat_id, text, parse_mode="Markdown")

def format_position_line(p):
    return f"🪙 *{p.get('pair','?')}* | {p.get('side','?')} | Size: {p.get('size','?')}"

# ================== منطق پوزیشن ==================
def check_positions():
    for chat_id, wallets in user_wallets.items():
        for wallet in wallets:
            current_positions = get_positions(wallet)
            prev_positions = previous_positions.get((chat_id, wallet), [])
            current_ids = {p.get("id") for p in current_positions}
            prev_ids    = {p.get("id") for p in prev_positions}
            
            # پوزیشن جدید
            for p in current_positions:
                if p.get("id") not in prev_ids:
                    send_message(chat_id, f"🚀 Position Opened\n💼 `{wallet}`\n{format_position_line(p)}")
            
            # پوزیشن بسته
            for p in prev_positions:
                if p.get("id") not in current_ids:
                    send_message(chat_id, f"✅ Position Closed\n💼 `{wallet}`\n{format_position_line(p)}")

            previous_positions[(chat_id, wallet)] = current_positions

# ================== گزارش 10 ارز برتر ==================
def fetch_top10_and_positions():
    try:
        # گرفتن 10 ارز برتر از CoinGecko
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 10, "page": 1}
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        coins = r.json()
    except Exception as e:
        print("Top10 fetch error:", e)
        return

    report_lines = ["📊 *Top 10 Market Coins + Position Sentiment*"]
    for coin in coins:
        symbol = coin.get("symbol", "").upper()
        name   = coin.get("name", "?")
        price  = coin.get("current_price", "?")
        change = coin.get("price_change_percentage_24h", 0)

        # بررسی لانگ/شورت برای همین کوین
        long_count, short_count = 0, 0
        for (chat_id, wallets) in user_wallets.items():
            for wallet in wallets:
                positions = get_positions(wallet)
                for p in positions:
                    if symbol in str(p.get("pair","")).upper():
                        if "LONG" in str(p.get("side","")).upper():
                            long_count += 1
                        elif "SHORT" in str(p.get("side","")).upper():
                            short_count += 1
        sentiment = "➖ No data"
        if long_count > short_count:
            sentiment = f"🟢 Long ({long_count}) vs 🔴 Short ({short_count})"
        elif short_count > long_count:
            sentiment = f"🔴 Short ({short_count}) vs 🟢 Long ({long_count})"

        report_lines.append(f"• *{name}* ({symbol})\n💲 {price} | 24h: {change:.2f}%\n📈 {sentiment}\n")

    text = "\n".join(report_lines)
    for chat_id in user_wallets.keys():
        send_message(chat_id, text)

# ================== منو انتخاب زمان ==================
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
    bot.send_message(chat_id, "⏱ لطفا بازه زمانی گزارش رو انتخاب کن:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("interval_"))
def callback_interval(call):
    chat_id = call.message.chat.id
    val = int(call.data.split("_")[1])
    user_intervals[chat_id] = val
    bot.answer_callback_query(call.id, f"بازه {val} دقیقه‌ای انتخاب شد ✅")
    send_message(chat_id, f"⏱ گزارش دوره‌ای هر *{val} دقیقه* برای شما ارسال خواهد شد.")

# ================== دستورات ربات ==================
@bot.message_handler(commands=['start'])
def start(message):
    chat_id = message.chat.id
    user_wallets.setdefault(chat_id, [])
    user_intervals[chat_id] = 1
    send_message(chat_id, "سلام 👋\nآدرس ولت رو بفرست.\n/stop = توقف\n/interval = تغییر بازه")
    send_interval_menu(chat_id)

@bot.message_handler(commands=['stop'])
def stop(message):
    chat_id = message.chat.id
    if chat_id in user_wallets:
        user_wallets.pop(chat_id, None)
        send_message(chat_id, "🛑 مانیتورینگ متوقف شد.")
    else:
        send_message(chat_id, "⚠️ فعال نبود.")

@bot.message_handler(commands=['interval'])
def interval(message):
    send_interval_menu(message.chat.id)

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
    send_message(chat_id, f"✅ ولت `{wallet}` اضافه شد.")

# ================== اجرا ==================
schedule.every(1).minutes.do(check_positions)
schedule.every(1).minutes.do(fetch_top10_and_positions)  # 🔥 فعلاً هر 1 دقیقه

def run_scheduler():
    while True:
        schedule.run_pending()
        time.sleep(2)

threading.Thread(target=run_scheduler, daemon=True).start()
bot.polling()
