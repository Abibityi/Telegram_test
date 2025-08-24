
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

import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

def get_top10_report():
    try:
        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 10, "page": 1}
        r = requests.get(url, params=params, timeout=10, headers=HEADERS)
        r.raise_for_status()
        coins = r.json()

        # Get Fear & Greed Index data
        fear_greed_url = "https://api.alternative.me/fng/"
        fg_res = requests.get(fear_greed_url, timeout=10, headers=HEADERS)
        fg_data = fg_res.json()
        
        # Extract current fear & greed value and classification
        fg_value = "-"
        fg_label = "-"
        if fg_res.status_code == 200 and fg_data.get('data') and len(fg_data['data']) > 0:
            fg_value = fg_data['data'][0].get('value', '-')
            fg_label = fg_data['data'][0].get('value_classification', '-')

        lines = []
        for c in coins:
            symbol = c.get("symbol", "").upper()
            price = c.get("current_price", 0)
            change = c.get("price_change_percentage_24h", 0)

            lines.append(
                f"🪙 *{symbol}*\n"
                f"💵 ${price:,.2f} ({change:+.2f}%)\n"
                f"📊 Market Sentiment: {fg_label} ({fg_value}/100)\n"
                "━━━━━━━━━━"
            )

        return f"📊 *Top 10 Coins by Market Cap*\n\n📈 *Market Sentiment: {fg_label} ({fg_value}/100)*\n\n" + "\n".join(lines)

    except Exception as e:
        return f"⚠️ Error retrieving report: {e}"


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
                "reason": "Wallet address structure must be exactly 0x + 40 hex characters"
            })
            continue

        # Only regex is checked
        valid.append(item)

    return valid, errors
    

# ================== Settings ==================
API_TOKEN = os.environ.get("API_TOKEN")
if not API_TOKEN:
    raise SystemExit("❌ API_TOKEN not set in environment variables")

bot = telebot.TeleBot(API_TOKEN)

# We store a wallet list for each user
user_wallets = {}
previous_positions = {}   # Key: (chat_id, wallet)
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
        
# ---------- Normalizing HyperDash Data ----------
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

# ---------- Normalizing Hyperliquid Data ----------
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
    
# ---------- Getting Positions ----------
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

# ---------- Message Format ----------
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
        
        
# ================== Real-time Monitoring + Periodic Reports ==================
def check_positions():
    for chat_id, wallets in user_wallets.items():
        for wallet in wallets:
            current_positions = get_positions(wallet)
            prev_positions = previous_positions.get((chat_id, wallet), [])

            current_map = {p["uid"]: p for p in current_positions}
            prev_map    = {p["uid"]: p for p in prev_positions}

            # New position
            for uid, pos in current_map.items():
                if uid not in prev_map:
                    msg = (
                        "🚀 *Position Opened*\n"
                        f"💼 (`{wallet}`)\n"
                        "━━━━━━━━━━\n"
                        f"{format_position_line(pos)}"
                    )
                    send_message(chat_id, msg)

            # Closed position
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
                        "🔚 Position closed."
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
                send_message(chat_id, f"{header}\n⏳ No open positions.")
                

def send_interval_menu(chat_id):
    """
    ✅ Only setting the periodic report interval
    ('Prediction' and 'Top10' buttons have been removed from this menu)
    """
    markup = InlineKeyboardMarkup()
    options = [
        ("1 minute", 1),
        ("15 minutes", 15),
        ("30 minutes", 30),
        ("4 hours", 240),
        ("24 hours", 1440),
    ]
    for text, val in options:
        markup.add(InlineKeyboardButton(text, callback_data=f"interval_{val}"))
    bot.send_message(chat_id, "⏱ Select report interval:", reply_markup=markup)

def send_predict_menu(chat_id):
    """
    ✅ BTC prediction timeframe selection menu
    Opened via /predict command.
    """
    markup = InlineKeyboardMarkup()
    hour_opts = [1, 2, 4, 8, 12, 24]
    row = []
    for h in hour_opts:
        row.append(InlineKeyboardButton(f"{h} hours", callback_data=f"predict_h_{h}"))
        if len(row) == 3:
            markup.row(*row)
            row = []
    if row:
        markup.row(*row)
    bot.send_message(chat_id, "🔮 Select BTC prediction timeframe:", reply_markup=markup)
# Auto report every 4 hours
    



# ================== Liquidation Settings ==================
LIQ_THRESHOLD = 10   # 🔹 Test: above 10 dollars (later change to 1_000_000)
MAX_LIQS = 10        # Keep maximum 10 records
liq_list = []

# ================== Binance WebSocket ==================
BINANCE_WS = (
    "wss://fstream.binance.com/stream?"
    "streams=btcusdt@forceOrder/ethusdt@forceOrder/bnbusdt@forceOrder"
)

def start_binance_ws():
    """Start Binance websocket to get liquidations"""
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
                    f"🔴 Liquidation\n"
                    f"📌 Symbol: {symbol}\n"
                    f"📈 Side: {side}\n"
                    f"💰 Notional: {notional:.2f} USD\n"
                    f"💲 Price: {price}\n"
                    f"📦 Quantity: {qty}"
                )

                # Save to list
                liq_list.append(event)
                if len(liq_list) > MAX_LIQS:
                    liq_list.pop(0)

                print(event)
                print("-" * 30)

        except Exception as e:
            print("❌ Error parsing message:", e)
            print("Raw:", message)

    def on_error(ws, error):
        print("❌ WebSocket Error:", error)

    def on_close(ws, close_status_code, close_msg):
        print("🔌 WebSocket Connection closed")

    def on_open(ws):
        print("✅ Connected to Binance WebSocket (BTC/ETH/BNB)")

    ws = websocket.WebSocketApp(
        BINANCE_WS,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
        on_open=on_open
    )
    ws.run_forever()

def run_ws_thread():
    """Run websocket in a separate thread"""
    ws_thread = threading.Thread(target=start_binance_ws, daemon=True)
    ws_thread.start()

def get_liq_report():
    """Liquidation report text"""
    if not liq_list:
        return "⚠️ No liquidations recorded yet."
    return "\n\n".join(liq_list)

# ================== Telegram Commands ==================
@bot.message_handler(commands=["liqs"])
def send_liqs(message):
    bot.reply_to(message, get_liq_report())

# Send report to all subscribers every 4 hours
def auto_send_liqs():
    report = get_liq_report()
    for user in subscribers:
        try:
            bot.send_message(user, report)
        except:
            pass

schedule.every(4).hours.do(auto_send_liqs)


# ================== Scheduling ==================
 # New data every 1 minute
schedule.every(4).hours.do(auto_send_liqs)      # Auto report every 4 hours    
    
# ================== Commands ==================
@bot.message_handler(commands=['start'])
def start(message):
    chat_id = message.chat.id
    user_wallets.setdefault(chat_id, [])
    user_intervals[chat_id] = 1
    send_message(chat_id,
        "Hello 👋\n"
        "Send your wallet addresses for me to monitor.\n\n"
        "📍 /stop → Stop monitoring\n"
        "📍 /interval → Change report interval\n"
        "📍 /top10 → Top 10 coins report\n"
        "📍 /predict → Bitcoin prediction (select timeframe)"
    )

@bot.message_handler(commands=['stop'])
def stop(message):
    chat_id = message.chat.id
    user_wallets.pop(chat_id, None)
    user_intervals.pop(chat_id, None)
    send_message(chat_id, "⏹ Monitoring stopped.")

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

    # Split multiple inputs by space/comma/newline
    parts = re.split(r'[\s,]+', text)
    parts = [p.strip() for p in parts if p.strip()]

    if not parts:
        send_message(chat_id, "❌ Please send at least one wallet address.")
        return

    # Use strict validation
    valid, errors = validate_wallet_inputs(parts)

    # Add valid items (without duplicates)
    added = []
    if valid:
        user_wallets.setdefault(chat_id, [])
        for w in valid:
            if w not in user_wallets[chat_id]:
                user_wallets[chat_id].append(w)
                added.append(w)

    # Build output message
    msg_lines = []
    if added:
        msg_lines.append(f"✅ {len(added)} valid wallets added:")
        msg_lines.extend([f"- `{w}`" for w in added])
    if errors:
        msg_lines.append("❌ Invalid items:")
        for err in errors:
            reason = err.get('reason', 'Invalid')
            msg_lines.append(f"- `{err['input']}` → {reason}")

    if not msg_lines:
        msg_lines = ["⚠️ No new wallets added."]

    send_message(chat_id, "\n".join(msg_lines))

# ================== Run Scheduler ==================
def run_scheduler():
    schedule.every(1).minutes.do(check_positions)
    schedule.every(1).minutes.do(periodic_report)
    while True:
        schedule.run_pending()
        time.sleep(1)

threading.Thread(target=run_scheduler, daemon=True).start()
# ================== Inline Button Handlers ==================
@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    chat_id = call.message.chat.id
    data = call.data

    # --- Report interval change handler ---
    if data.startswith("interval_"):
        try:
            val = int(data.split("_")[1])
            user_intervals[chat_id] = val
            bot.answer_callback_query(call.id, f"Report interval set to {val} minutes ✅")
            send_message(chat_id, f"⏱ Report interval changed to {val} minutes.")
        except:
            bot.answer_callback_query(call.id, "Error processing interval ❌")

    # --- Bitcoin prediction handler ---
    elif data.startswith("predict_h_"):
        try:
            hours = int(data.split("_")[2])
            bot.answer_callback_query(call.id, f"Prediction for next {hours} hours ⏳")

            text = build_btc_forecast_text(hours)
            chart, err = build_btc_forecast_chart(hours)

            if chart:
                # Short caption for image
                bot.send_photo(chat_id, chart, caption="📊 BTC Prediction Chart")
                # Full analysis text sent separately
                send_message(chat_id, text)
            else:
                if err:
                    send_message(chat_id, err)
                else:
                    send_message(chat_id, text)

        except Exception as e:
            send_message(chat_id, f"⚠️ Error in prediction: {e}")
 
 
if __name__ == "__main__":
    run_ws_thread()         # 🔹 Binance websocket starts
    print("🚀 Bot started...")
    bot.infinity_polling()
