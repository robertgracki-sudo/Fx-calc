import os
import re
import json
import time
import uuid
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from google import genai
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
import PIL.Image
import io
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

# Pobieranie zmiennych środowiskowych
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GEMINI_KEY = os.environ.get('GEMINI_KEY')

DEFAULT_CAPITAL = 20000
DEFAULT_RISK_PERCENT = 1
DEFAULT_LEVERAGE = 100
DEFAULT_CURRENCY = 'GBP'

JOURNAL_FILE = 'journal.json'

# Inicjalizacja klientów
client = genai.Client(api_key=GEMINI_KEY)
bot = telebot.TeleBot(TELEGRAM_TOKEN)

user_settings = {}
pending_trades = {}
trade_counter = 0
update_states = {}

# ── Journal helpers ──────────────────────────────────────────────────────────

def load_journal():
    if os.path.exists(JOURNAL_FILE):
        try:
            with open(JOURNAL_FILE, 'r') as f:
                return json.load(f)
        except:
            return []
    return []

def save_journal(entries):
    with open(JOURNAL_FILE, 'w') as f:
        json.dump(entries, f, indent=2)

def find_entry_by_uid(entries, uid):
    for i, e in enumerate(entries):
        if e.get('uid') == uid:
            return i, e
    return None, None

def parse_analysis(text):
    fields = {}
    patterns = {
        'pair':     r'Pair:\s*(.+)',
        'entry':    r'Entry:\s*(.+)',
        'sl':       r'SL:\s*(.+)',
        'distance': r'Distance:\s*(.+)',
        'lot':      r'LOT:\s*(.+)',
        'tp2':      r'RR 1:2.*?TP:\s*(.+)',
        'tp3':      r'RR 1:3.*?TP:\s*(.+)',
        'margin':   r'Margin:\s*(.+)',
    }
    # Usuwamy tagi HTML do regexa
    clean = re.sub(r'<[^>]+>', '', text)
    for key, pattern in patterns.items():
        m = re.search(pattern, clean)
        fields[key] = m.group(1).strip() if m else ''
    return fields

# ── Settings helpers ─────────────────────────────────────────────────────────

def get_settings(chat_id):
    if chat_id not in user_settings:
        user_settings[chat_id] = {
            'capital': DEFAULT_CAPITAL,
            'risk': DEFAULT_RISK_PERCENT,
            'leverage': DEFAULT_LEVERAGE,
            'currency': DEFAULT_CURRENCY,
        }
    return user_settings[chat_id]

# ── Commands ─────────────────────────────────────────────────────────────────

@bot.message_handler(commands=['start'])
def handle_start(message):
    chat_id = message.chat.id
    s = get_settings(chat_id)
    cur = s.get('currency', DEFAULT_CURRENCY)
    risk_amt = s['capital'] * s['risk'] / 100
    text = (
        f"<b>Welcome to FX Position Size Calculator 📊</b>\n\n"
        f"<b>Your current settings:</b>\n"
        f"💰 Account size: <b>{cur} {s['capital']:,}</b>\n"
        f"⚡ Leverage: <b>1:{s['leverage']}</b>\n"
        f"🎯 Risk per trade: <b>{s['risk']}%</b> ({cur} {risk_amt:,.2f})\n"
        f"💱 Account currency: <b>{cur}</b>\n\n"
        f"<b>Commands:</b>\n"
        f"/capital [amount] — set account size\n"
        f"/risk [percent] — set risk per trade\n"
        f"/leverage [value] — set leverage\n"
        f"/currency [code] — set account currency (e.g. GBP, USD, EUR)\n"
        f"/settings — show current settings\n"
        f"/journal — view your trading journal\n"
        f"/export — export today's trades\n\n"
        f"Send a chart photo to get your position size!"
    )
    bot.send_message(chat_id, text, parse_mode='HTML')

@bot.message_handler(commands=['settings'])
def handle_settings(message):
    chat_id = message.chat.id
    s = get_settings(chat_id)
    cur = s.get('currency', DEFAULT_CURRENCY)
    risk_amt = s['capital'] * s['risk'] / 100
    text = (
        f"<b>Your current settings:</b>\n"
        f"💰 Account size: <b>{cur} {s['capital']:,}</b>\n"
        f"⚡ Leverage: <b>1:{s['leverage']}</b>\n"
        f"🎯 Risk per trade: <b>{s['risk']}%</b> ({cur} {risk_amt:,.2f})\n"
        f"💱 Account currency: <b>{cur}</b>"
    )
    bot.send_message(chat_id, text, parse_mode='HTML')

@bot.message_handler(commands=['currency'])
def handle_currency(message):
    chat_id = message.chat.id
    try:
        value = message.text.split()[1].upper()
        if len(value) != 3: raise ValueError
        get_settings(chat_id)['currency'] = value
        bot.send_message(chat_id, f"✅ Account currency set to <b>{value}</b>", parse_mode='HTML')
    except:
        bot.send_message(chat_id, "❌ Usage: /currency [code] — e.g. /currency USD")

@bot.message_handler(commands=['capital'])
def handle_capital(message):
    chat_id = message.chat.id
    try:
        value = float(message.text.split()[1])
        get_settings(chat_id)['capital'] = value
        bot.send_message(chat_id, f"✅ Account size set to <b>{value:,.2f}</b>", parse_mode='HTML')
    except:
        bot.send_message(chat_id, "❌ Usage: /capital [amount]")

@bot.message_handler(commands=['risk'])
def handle_risk(message):
    chat_id = message.chat.id
    try:
        value = float(message.text.split()[1])
        get_settings(chat_id)['risk'] = value
        bot.send_message(chat_id, f"✅ Risk per trade set to <b>{value}%</b>", parse_mode='HTML')
    except:
        bot.send_message(chat_id, "❌ Usage: /risk [percent]")

@bot.message_handler(commands=['leverage'])
def handle_leverage(message):
    chat_id = message.chat.id
    try:
        value = int(message.text.split()[1])
        get_settings(chat_id)['leverage'] = value
        bot.send_message(chat_id, f"✅ Leverage set to <b>1:{value}</b>", parse_mode='HTML')
    except:
        bot.send_message(chat_id, "❌ Usage: /leverage [value]")

@bot.message_handler(commands=['journal'])
def handle_journal(message):
    chat_id = message.chat.id
    entries = load_journal()
    user_entries = [e for e in entries if e['chat_id'] == chat_id]

    if not user_entries:
        bot.send_message(chat_id, "📒 Your trading journal is empty.")
        return

    recent = user_entries[-5:][::-1]
    bot.send_message(chat_id, f"<b>📒 Recent trades:</b>", parse_mode='HTML')

    for entry in recent:
        outcome = entry.get('outcome')
        notes = entry.get('notes')
        uid = entry.get('uid', '')
        icon = {'Win': '🟢', 'Loss': '🔴', 'Breakeven': '🟡'}.get(outcome, '⏳')

        caption = (
            f"{icon} <b>{entry['date']}</b>"
            + (f" — <b>{outcome}</b>" if outcome else " — <i>Pending</i>")
            + f"\n\n{entry['analysis']}"
            + (f"\n\n💰 Profit: <b>{notes}</b>" if notes else "")
        )

        markup = None
        if not outcome:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("✏️ Set outcome", callback_data=f"update:{uid}"))

        photo_id = entry.get('photo_file_id')
        if photo_id:
            bot.send_photo(chat_id, photo_id, caption=caption, parse_mode='HTML', reply_markup=markup)
        else:
            bot.send_message(chat_id, caption, parse_mode='HTML', reply_markup=markup)

@bot.message_handler(commands=['export'])
def handle_export(message):
    chat_id = message.chat.id
    parts = message.text.split()
    target_date = parts[1] if len(parts) > 1 else time.strftime('%Y-%m-%d')
    entries = load_journal()
    day_entries = [e for e in entries if e['chat_id'] == chat_id and e.get('date', '').startswith(target_date)]

    if not day_entries:
        bot.send_message(chat_id, f"📭 No trades for {target_date}")
        return

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = target_date
    headers = ['Date', 'Pair', 'Entry', 'SL', 'Distance', 'LOT', 'Margin', 'Outcome', 'Profit']
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = Font(bold=True)

    for row, entry in enumerate(day_entries, 2):
        f = parse_analysis(entry.get('analysis', ''))
        row_data = [entry.get('date'), f.get('pair'), f.get('entry'), f.get('sl'), f.get('distance'), f.get('lot'), f.get('margin'), entry.get('outcome'), entry.get('notes')]
        for col, val in enumerate(row_data, 1):
            ws.cell(row=row, column=col, value=val)

    filename = f'journal_{target_date}.xlsx'
    wb.save(filename)
    with open(filename, 'rb') as f:
        bot.send_document(chat_id, f)
    os.remove(filename)

# ── Chart analysis ────────────────────────────────────────────────────────────

def analyze_chart(image_data, capital, risk, leverage, currency):
    img = PIL.Image.open(io.BytesIO(image_data))
    risk_amount = capital * risk / 100
    
    # Wykorzystujemy Gemini 2.0 Flash Exp (najszybszy i dostępny w USA)
    prompt = f"""
Analyze this forex chart. 
Prices: Purple line = SL, Teal/Black = Entry.
Account: {capital} {currency}, Risk: {risk}% ({risk_amount} {currency}), Leverage 1:{leverage}.

Determine: Pair, Direction (Long if Entry > SL), SL distance, Lot size, Margin, and TP for RR 1:2 and 1:3.
Follow standard lot formulas for Forex, Gold (100oz), JPY (0.01 pip), Crypto, Oil.

Return ONLY this HTML format:
Pair: [Name]
Entry: [Price]
SL: [Price]
Distance: [Value] [pips/points]
——————————————
<b>📊 LOT: [Value]</b>
——————————————
RR 1:2 → TP: [Price]
RR 1:3 → TP: [Price]
Margin: [Value] {currency}
"""
    response = client.models.generate_content(
        model='gemini-2.0-flash-exp', # Zmienione na poprawną nazwę modelu
        contents=[prompt, img]
    )
    return response.text

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    global trade_counter
    chat_id = message.chat.id
    try:
        s = get_settings(chat_id)
        photo = message.photo[-1]
        file_info = bot.get_file(photo.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        bot.send_chat_action(chat_id, 'typing')
        result = analyze_chart(downloaded_file, s['capital'], s['risk'], s['leverage'], s['currency'])

        trade_counter += 1
        trade_id = trade_counter
        pending_trades[trade_id] = {
            'chat_id': chat_id,
            'analysis': result,
            'photo_file_id': photo.file_id,
        }

        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("✅ Save to Journal", callback_data=f"save:{trade_id}"))
        bot.reply_to(message, result, parse_mode='HTML', reply_markup=markup)
    except Exception as e:
        bot.reply_to(message, f"Error: {str(e)}")

# ── Callbacks & Updating ──────────────────────────────────────────────────────

@bot.callback_query_handler(func=lambda call: call.data.startswith('save:'))
def handle_save_trade(call):
    trade_id = int(call.data.split(':')[1])
    if trade_id in pending_trades:
        trade = pending_trades.pop(trade_id)
        entries = load_journal()
        entries.append({
            'uid': str(uuid.uuid4())[:8],
            'chat_id': call.message.chat.id,
            'date': time.strftime('%Y-%m-%d %H:%M'),
            'analysis': trade['analysis'],
            'photo_file_id': trade['photo_file_id'],
            'outcome': None,
            'notes': None
        })
        save_journal(entries)
        bot.answer_callback_query(call.id, "Saved!")
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)

@bot.callback_query_handler(func=lambda call: call.data.startswith('update:'))
def handle_update_trade(call):
    uid = call.data.split(':')[1]
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("🟢 Win", callback_data=f"outcome:{uid}:Win"),
        InlineKeyboardButton("🔴 Loss", callback_data=f"outcome:{uid}:Loss"),
        InlineKeyboardButton("🟡 BE", callback_data=f"outcome:{uid}:Breakeven")
    )
    bot.send_message(call.message.chat.id, "Outcome?", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('outcome:'))
def handle_outcome(call):
    _, uid, outcome = call.data.split(':')
    update_states[call.message.chat.id] = {'uid': uid, 'outcome': outcome, 'step': 'notes'}
    bot.send_message(call.message.chat.id, f"Set to {outcome}. Enter profit/loss (text) or send /skip:")

@bot.message_handler(func=lambda m: m.chat.id in update_states)
def handle_notes_input(message):
    state = update_states.pop(message.chat.id)
    notes = message.text if message.text != '/skip' else "N/A"
    entries = load_journal()
    idx, entry = find_entry_by_uid(entries, state['uid'])
    if entry:
        entries[idx]['outcome'] = state['outcome']
        entries[idx]['notes'] = notes
        save_journal(entries)
        bot.reply_to(message, "✅ Journal updated!")

# ── Keep-alive server for Render ──────────────────────────────────────────────

class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is online")
    def log_message(self, *args): pass

def _start_health_server():
    port = int(os.environ.get('PORT', 8080))
    server = HTTPServer(('0.0.0.0', port), _HealthHandler)
    Thread(target=server.serve_forever, daemon=True).start()

if __name__ == '__main__':
    print("Bot starting...")
    _start_health_server()
    bot.infinity_polling()
