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

# --- KONFIGURACJA ŚRODOWISKA ---
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
GEMINI_KEY = os.environ['GEMINI_KEY']

DEFAULT_CAPITAL = 20000
DEFAULT_RISK_PERCENT = 0.75  # Ustawione zgodnie z Twoją preferencją
DEFAULT_LEVERAGE = 100
DEFAULT_CURRENCY = 'GBP'
JOURNAL_FILE = 'journal.json'

# --- POPRAWKA 1: Wymuszenie stabilnego API v1 ---
client = genai.Client(api_key=GEMINI_KEY, http_options={'api_version': 'v1'})
bot = telebot.TeleBot(TELEGRAM_TOKEN)

user_settings = {}

# --- POPRAWKA 2: Serwer Flask pod port Rendera (10000) ---
app = Flask(__name__)

@app.route('/', methods=['GET', 'HEAD'])
def home():
    return "Bot Kasownik FX Online", 200

def run_server():
    # Render zawsze szuka portu 10000
    app.run(host='0.0.0.0', port=10000)

def keep_alive():
    t = Thread(target=run_server)
    t.daemon = True
    t.start()

# --- TWOJE FUNKCJE Z REPLIT ---
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
@bot.message_handler(commands=['start', 'settings'])
def handle_start(message):
    s = get_settings(message.chat.id)
    cur = s['currency']
    risk_amt = s['capital'] * s['risk'] / 100
    text = (
        f"<b>Kasownik FX 📊</b>\n\n"
        f"💰 Account size: <b>{cur} {s['capital']:,}</b>\n"
        f"🎯 Risk: <b>{s['risk']}%</b> ({cur} {risk_amt:,.2f})\n"
        f"💱 Currency: <b>{cur}</b>\n\n"
        f"Wyślij zdjęcie wykresu, aby obliczyć pozycję."
    )
    bot.send_message(message.chat.id, text, parse_mode='HTML')

# --- ANALIZA OBRAZU (Z POPRAWKĄ MODELU) ---
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    chat_id = message.chat.id
    s = get_settings(chat_id)
    status_msg = bot.reply_to(message, "⏳ Analizuję wykres...")
    
    file_info = bot.get_file(message.photo[-1].file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    
    try:
        img = PIL.Image.open(io.BytesIO(downloaded_file))
        risk_amount = s['capital'] * s['risk'] / 100
        
        prompt = f"""
        Jesteś ekspertem Forex. Analizuj wykres: Fiolet=SL, Morski/Czarny=Entry.
        Konto: {s['capital']} {s['currency']}, Ryzyko: {s['risk']}% ({risk_amount}).
        
        OBLICZ:
        1. Forex: Lot = Ryzyko / (Pips * 10)
        2. Gold: Lot = Ryzyko / (Punkty * 1)
        3. Oil (WTI/Brent): Lot = Ryzyko / (Punkty * 10)
        
        PODAJ: Instrument, Kierunek, Entry, SL, LOT, TP (RR 1:2 i 1:3).
        """
        
        # POPRAWKA 3: Model 1.5-flash (stabilny na v1)
        response = client.models.generate_content(
            model="gemini-1.5-flash", 
            contents=[prompt, img]
        )
        
        # Zapis do dziennika
        new_entry = {
            "uid": str(uuid.uuid4())[:8],
            "chat_id": chat_id,
            "date": time.strftime('%Y-%m-%d %H:%M'),
            "analysis": response.text,
            "outcome": None
        }
        journal = load_journal()
        journal.append(new_entry)
        save_journal(journal)
        
        bot.edit_message_text(response.text, chat_id, status_msg.message_id)
        
    except Exception as e:
        bot.edit_message_text(f"❌ Błąd: {str(e)}", chat_id, status_msg.message_id)

# --- START ---
if __name__ == "__main__":
    keep_alive()
    print("Bot Kasownik startuje...")
    bot.infinity_polling()
