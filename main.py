import os
import re
import json
import time
import uuid
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import google.generativeai as genai  # POPRAWKA 1: Stabilna biblioteka
from flask import Flask             # POPRAWKA 2: Flask dla Rendera
from threading import Thread
import PIL.Image
import io
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

# --- KONFIGURACJA ---
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
GEMINI_KEY = os.environ['GEMINI_KEY']

DEFAULT_CAPITAL = 20000
DEFAULT_RISK_PERCENT = 1
DEFAULT_LEVERAGE = 100
DEFAULT_CURRENCY = 'GBP'
JOURNAL_FILE = 'journal.json'

# POPRAWKA 1: Inicjalizacja Gemini (stary, pewny styl)
genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel('gemini-1.5-flash') # Najbardziej stabilny pod Render

bot = telebot.TeleBot(TELEGRAM_TOKEN)

user_settings = {}
pending_trades = {}
trade_counter = 0
update_states = {}

# --- POPRAWKA 2: Serwer Flask (zamiast HTTPServer) ---
app = Flask(__name__)
@app.route('/')
def health(): return "Bot Active", 200

def run_health_server():
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)

# ── Journal helpers ──────────────────────────────────────────────────────────

def load_journal():
    if os.path.exists(JOURNAL_FILE):
        try:
            with open(JOURNAL_FILE, 'r') as f: return json.load(f)
        except: return []
    return []

def save_journal(entries):
    with open(JOURNAL_FILE, 'w') as f:
        json.dump(entries, f, indent=2)

def find_entry_by_uid(entries, uid):
    for i, e in enumerate(entries):
        if e.get('uid') == uid: return i, e
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
    cur = s['currency']
    risk_amt = s['capital'] * s['risk'] / 100
    text = (
        f"<b>Welcome to FX Position Size Calculator 📊</b>\n\n"
        f"💰 Account size: <b>{cur} {s['capital']:,}</b>\n"
        f"🎯 Risk: <b>{s['risk']}%</b> ({cur} {risk_amt:,.2f})\n"
        f"Send a chart photo to get your position size!"
    )
    bot.send_message(chat_id, text, parse_mode='HTML')

@bot.message_handler(commands=['journal'])
def handle_journal(message):
    chat_id = message.chat.id
    entries = load_journal()
    user_entries = [e for e in entries if e['chat_id'] == chat_id]
    if not user_entries:
        bot.send_message(chat_id, "📒 Your journal is empty.")
        return
    recent = user_entries[-5:][::-1]
    for entry in recent:
        outcome = entry.get('outcome')
        uid = entry.get('uid', '')
        outcome_icon = {'Win': '🟢', 'Loss': '🔴', 'Breakeven': '🟡'}.get(outcome, '⏳')
        caption = f"{outcome_icon} <b>{entry['date']}</b>\n\n{entry['analysis']}"
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
        bot.send_message(chat_id, f"📭 No trades for {target_date}.")
        return
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['Date', 'Pair', 'Entry', 'SL', 'Distance', 'LOT', 'Margin', 'Outcome', 'Profit'])
    for entry in day_entries:
        f = parse_analysis(entry.get('analysis', ''))
        ws.append([entry['date'], f['pair'], f['entry'], f['sl'], f['distance'], f['lot'], f['margin'], entry['outcome'], entry['notes']])
    filename = f'journal_{target_date}.xlsx'
    wb.save(filename)
    with open(filename, 'rb') as f:
        bot.send_document(chat_id, f, caption=f"📊 Journal {target_date}")
    os.remove(filename)

# ── Chart analysis ────────────────────────────────────────────────────────────

def analyze_chart(image_data, capital, risk, leverage, currency):
    img = PIL.Image.open(io.BytesIO(image_data))
    risk_amount = capital * risk / 100
    prompt = f"""
    Analyze FX chart. Purple=SL, Teal/Black=Entry.
    Capital: {capital} {currency}, Risk: {risk}% ({risk_amount}).
    Return HTML:
    Pair: [Name]
    Entry: [Price]
    SL: [Price]
    Distance: [Value]
    ——————————————
    <b>📊 LOT: [Value]</b>
    ——————————————
    RR 1:2 → TP: [Price]
    RR 1:3 → TP: [Price]
    Margin: [Value] USD
    """
    # POPRAWKA 1: Nowy sposób wywołania dla stabilnej biblioteki
    response = model.generate_content([prompt, img])
    return response.text

# ── Photo handler ─────────────────────────────────────────────────────────────

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
        result = analyze_chart(downloaded_file, s['capital'], s['risk'], s['leverage'], s.get('currency', 'GBP'))
        
        trade_counter += 1
        pending_trades[trade_counter] = {'chat_id': chat_id, 'analysis': result, 'photo_file_id': photo.file_id}
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("✅ Save to journal", callback_data=f"save:{trade_counter}"))
        bot.reply_to(message, result, parse_mode='HTML', reply_markup=markup)
    except Exception as e:
        bot.reply_to(message, f"Error: {str(e)}")

# ── Callbacks (Save/Outcome) ──────────────────────────────────────────────────

@bot.callback_query_handler(func=lambda call: call.data.startswith('save:'))
def handle_save_trade(call):
    trade_id = int(call.data.split(':')[1])
    if trade_id in pending_trades:
        trade = pending_trades.pop(trade_id)
        entries = load_journal()
        entries.append({
            'uid': str(uuid.uuid4())[:8], 'chat_id': call.message.chat.id,
            'date': time.strftime('%Y-%m-%d %H:%M'), 'analysis': trade['analysis'],
            'photo_file_id': trade['photo_file_id'], 'outcome': None, 'notes': None
        })
        save_journal(entries)
        bot.answer_callback_query(call.id, "✅ Saved!")
        bot.send_message(call.message.chat.id, "📒 Trade saved!")

@bot.callback_query_handler(func=lambda call: call.data.startswith('update:'))
def handle_update_trade(call):
    uid = call.data.split(':', 1)[1]
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🟢 Win", callback_data=f"outcome:{uid}:Win"),
               InlineKeyboardButton("🔴 Loss", callback_data=f"outcome:{uid}:Loss"))
    bot.send_message(call.message.chat.id, "Outcome?", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('outcome:'))
def handle_outcome(call):
    parts = call.data.split(':')
    uid, outcome = parts[1], parts[2]
    _save_outcome(uid, outcome, "0") # Uproszczone zapisywanie wyniku
    bot.answer_callback_query(call.id, f"Locked: {outcome}")
    bot.send_message(call.message.chat.id, f"🔒 Trade {outcome}")

def _save_outcome(uid, outcome, notes):
    entries = load_journal()
    idx, entry = find_entry_by_uid(entries, uid)
    if entry:
        entries[idx]['outcome'], entries[idx]['notes'] = outcome, notes
        save_journal(entries)

# ── Start ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    Thread(target=run_health_server).start()
    print("Bot is LIVE...")
    bot.infinity_polling(skip_pending=True)
