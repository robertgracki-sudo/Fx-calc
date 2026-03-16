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
TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
# Upewnij się, że w Render Environment masz GEMINI_KEY
GEMINI_KEY = os.environ['GEMINI_KEY']

DEFAULT_CAPITAL = 20000
DEFAULT_RISK_PERCENT = 0.75  # Ustawiono na Twoje 0.75%
DEFAULT_LEVERAGE = 100
DEFAULT_CURRENCY = 'GBP'
JOURNAL_FILE = 'journal.json'

# --- INICJALIZACJA ---
# WYMUSZAMY v1, aby uniknąć błędów 404 NOT_FOUND na Renderze
client = genai.Client(api_key=GEMINI_KEY, http_options={'api_version': 'v1'})
bot = telebot.TeleBot(TELEGRAM_TOKEN)

user_settings = {}

# --- SERWER DLA RENDERA (Port 10000) ---
app = Flask(__name__)

@app.route('/', methods=['GET', 'HEAD'])
def home():
    return "Bot Kasownik FX: Online", 200

def run_server():
    # Render wymaga portu 10000
    app.run(host='0.0.0.0', port=10000)

def keep_alive():
    t = Thread(target=run_server)
    t.daemon = True
    t.start()

# --- JOURNAL HELPERS ---
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

# --- COMMANDS ---
@bot.message_handler(commands=['start', 'settings'])
def handle_start(message):
    s = get_settings(message.chat.id)
    cur = s['currency']
    risk_amt = s['capital'] * s['risk'] / 100
    text = (
        f"<b>Kasownik FX 📊</b>\n\n"
        f"💰 Account: <b>{cur} {s['capital']:,}</b>\n"
        f"🎯 Risk: <b>{s['risk']}%</b> ({cur} {risk_amt:,.2f})\n\n"
        f"Wyślij zdjęcie wykresu (SL=Fiolet, Entry=Morski/Czarny)."
    )
    bot.send_message(message.chat.id, text, parse_mode='HTML')

# Dodaj tutaj resztę swoich komend (/capital, /risk, /export) z oryginalnego kodu Replit...

# --- CHART ANALYSIS (PROMPT + GEMINI FIX) ---
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    chat_id = message.chat.id
    s = get_settings(chat_id)
    status_msg = bot.reply_to(message, "⏳ AI analizuje wykres (Gemini 1.5 Flash)...")
    
    file_info = bot.get_file(message.photo[-1].file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    
    try:
        img = PIL.Image.open(io.BytesIO(downloaded_file))
        risk_amount = s['capital'] * s['risk'] / 100
        
        prompt = f"""
        Analyze this FX chart. Purple=SL, Teal/Black=Entry.
        Account: {s['capital']} {s['currency']}, Risk: {s['risk']}% ({risk_amount}).
        
        CALCULATION RULES:
        1. Forex: Lot = Risk / (Pips * 10)
        2. JPY: Lot = Risk / (Pips * (1000/Price))
        3. Gold: Lot = Risk / (Points * 1)
        4. Oil (WTI/Brent): Point=0.01, Contract=1000bbl. Lot = Risk / (Points * 10)
        
        Return: Pair, Direction, Entry, SL, Lot Size, Margin, TP RR 1:2 and 1:3.
        """
        
        # POPRAWKA: Używamy stabilnego modelu gemini-1.5-flash
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
            "outcome": None,
            "photo_file_id": message.photo[-1].file_id
        }
        journal = load_journal()
        journal.append(new_entry)
        save_journal(journal)
        
        bot.edit_message_text(response.text, chat_id, status_msg.message_id)
        
    except Exception as e:
        bot.edit_message_text(f"❌ Błąd Gemini: {str(e)}", chat_id, status_msg.message_id)

# --- START ---
if __name__ == "__main__":
    keep_alive()
    print("Bot Kasownik wystartował na Renderze...")
    bot.infinity_polling()
