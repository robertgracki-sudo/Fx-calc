import os
import re
import json
import time
import uuid
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from google import genai
from flask import Flask
from threading import Thread
import PIL.Image
import io
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

# --- KONFIGURACJA ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GEMINI_KEY = os.environ.get('GEMINI_KEY')

DEFAULT_CAPITAL = 20000
DEFAULT_RISK_PERCENT = 1
DEFAULT_LEVERAGE = 100
DEFAULT_CURRENCY = 'GBP'
JOURNAL_FILE = 'journal.json'

client = genai.Client(api_key=GEMINI_KEY)
bot = telebot.TeleBot(TELEGRAM_TOKEN)

user_settings = {}
pending_trades = {}
trade_counter = 0
update_states = {}

# --- SERWER FLASK (Dla Render.com na Port 10000) ---
app = Flask(__name__)

@app.route('/', methods=['GET', 'HEAD'])
def home():
    return "Bot Kasownik FX: Online", 200

def run_server():
    # Render wymaga portu 10000. Wpisujemy go na sztywno.
    print("Uruchamiam serwer na porcie 10000...")
    app.run(host='0.0.0.0', port=10000, debug=False, use_reloader=False)

def keep_alive():
    t = Thread(target=run_server)
    t.daemon = True
    t.start()

# --- POMOCNIKI DZIENNIKA ---
def load_journal():
    if os.path.exists(JOURNAL_FILE):
        try:
            with open(JOURNAL_FILE, 'r') as f:
                return json.load(f)
        except: return []
    return []

def save_journal(entries):
    with open(JOURNAL_FILE, 'w') as f:
        json.dump(entries, f, indent=2)

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

def get_settings(chat_id):
    if chat_id not in user_settings:
        user_settings[chat_id] = {
            'capital': DEFAULT_CAPITAL,
            'risk': DEFAULT_RISK_PERCENT,
            'leverage': DEFAULT_LEVERAGE,
            'currency': DEFAULT_CURRENCY,
        }
    return user_settings[chat_id]

# --- KOMENDY ---
@bot.message_handler(commands=['start'])
def handle_start(message):
    chat_id = message.chat.id
    s = get_settings(chat_id)
    cur = s['currency']
    risk_amt = s['capital'] * s['risk'] / 100
    text = (
        f"<b>Welcome to FX Position Size Calculator 📊</b>\n\n"
        f"💰 Account size: <b>{cur} {s['capital']:,}</b>\n"
        f"🎯 Risk: <b>{s['risk']}%</b> ({cur} {risk_amt:,.2f})\n\n"
        f"Send a chart photo to get started!"
    )
    bot.send_message(chat_id, text, parse_mode='HTML')

@bot.message_handler(commands=['capital'])
def handle_capital(message):
    try:
        value = float(message.text.split()[1])
        get_settings(message.chat.id)['capital'] = value
        bot.send_message(message.chat.id, f"✅ Account size set to <b>{value:,.2f}</b>", parse_mode='HTML')
    except: bot.send_message(message.chat.id, "❌ Usage: /capital 30000")

@bot.message_handler(commands=['risk'])
def handle_risk(message):
    try:
        value = float(message.text.split()[1])
        get_settings(message.chat.id)['risk'] = value
        bot.send_message(message.chat.id, f"✅ Risk set to <b>{value}%</b>", parse_mode='HTML')
    except: bot.send_message(message.chat.id, "❌ Usage: /risk 1")

@bot.message_handler(commands=['journal'])
def handle_journal(message):
    chat_id = message.chat.id
    entries = load_journal()
    user_entries = [e for e in entries if e['chat_id'] == chat_id]
    if not user_entries:
        bot.send_message(chat_id, "📒 Journal is empty.")
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
        bot.send_message(chat_id, caption, parse_mode='HTML', reply_markup=markup)

@bot.message_handler(commands=['export'])
def handle_export(message):
    chat_id = message.chat.id
    entries = load_journal()
    user_entries = [e for e in entries if e['chat_id'] == chat_id]
    if not user_entries:
        bot.send_message(chat_id, "📭 No trades to export.")
        return

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['Date', 'Pair', 'Entry', 'SL', 'LOT', 'Outcome'])
    for e in user_entries:
        f = parse_analysis(e['analysis'])
        ws.append([e['date'], f.get('pair'), f.get('entry'), f.get('sl'), f.get('lot'), e.get('outcome', 'Pending')])
    
    filename = f'journal_{chat_id}.xlsx'
    wb.save(filename)
    with open(filename, 'rb') as f:
        bot.send_document(chat_id, f)
    os.remove(filename)

# --- ANALIZA ZDJĘĆ ---
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    chat_id = message.chat.id
    s = get_settings(chat_id)
    status = bot.reply_to(message, "⏳ AI Analyzing chart...")
    
    file_info = bot.get_file(message.photo[-1].file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    
    try:
        risk_amount = s['capital'] * s['risk'] / 100
        prompt = f"""
        Analyze this trading chart. SL is Purple line, Entry is Teal/Black.
        Account: {s['capital']} {s['currency']}, Risk: {s['risk']}% ({risk_amount}).
        Return Pair, Entry, SL, SL distance, LOT SIZE (Standard FX: 0.0001/10$, JPY: 0.01, Gold: 0.01/1$, Oil: 0.01/10$).
        """
        
        img = PIL.Image.open(io.BytesIO(downloaded_file))
        response = client.models.generate_content(model="gemini-1.5-flash", contents=[prompt, img])
        
        # Save to Journal
        new_entry = {
            "uid": str(uuid.uuid4())[:8],
            "chat_id": chat_id,
            "date": time.strftime('%Y-%m-%d %H:%M'),
            "analysis": response.text,
            "photo_file_id": message.photo[-1].file_id
        }
        journal = load_journal()
        journal.append(new_entry)
        save_journal(journal)
        
        bot.edit_message_text(response.text, chat_id, status.message_id)
        
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {str(e)}", chat_id, status.message_id)

# --- START ---
if __name__ == "__main__":
    keep_alive()
    print("Bot Kasownik is starting...")
    bot.infinity_polling(timeout=20, long_polling_timeout=10)
