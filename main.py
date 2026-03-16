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
DEFAULT_RISK_PERCENT = 0.75
DEFAULT_LEVERAGE = 100
DEFAULT_CURRENCY = 'GBP'
JOURNAL_FILE = 'journal.json'

# KLUCZOWA POPRAWKA: Wymuszamy wersję v1 (stabilną)
client = genai.Client(api_key=GEMINI_KEY, http_options={'api_version': 'v1'})
bot = telebot.TeleBot(TELEGRAM_TOKEN)

user_settings = {}

# --- SERWER DLA RENDERA ---
app = Flask(__name__)
@app.route('/', methods=['GET', 'HEAD'])
def home(): return "Bot Kasownik Online", 200

def run_server():
    app.run(host='0.0.0.0', port=10000)

def keep_alive():
    t = Thread(target=run_server)
    t.daemon = True
    t.start()

# --- DANE I POMOCNIKI ---
def load_journal():
    if os.path.exists(JOURNAL_FILE):
        try:
            with open(JOURNAL_FILE, 'r') as f: return json.load(f)
        except: return []
    return []

def save_journal(entries):
    with open(JOURNAL_FILE, 'w') as f: json.dump(entries, f, indent=2)

def get_settings(chat_id):
    if chat_id not in user_settings:
        user_settings[chat_id] = {'capital': DEFAULT_CAPITAL, 'risk': DEFAULT_RISK_PERCENT, 'leverage': DEFAULT_LEVERAGE, 'currency': DEFAULT_CURRENCY}
    return user_settings[chat_id]

# --- KOMENDY ---
@bot.message_handler(commands=['start'])
def handle_start(message):
    s = get_settings(message.chat.id)
    bot.reply_to(message, f"📊 Kasownik gotowy!\nKapitał: {s['capital']} {s['currency']}\nRyzyko: {s['risk']}%\n\nWyślij screenshot wykresu.")

# --- ANALIZA (POPRAWIONA) ---
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    chat_id = message.chat.id
    s = get_settings(chat_id)
    status = bot.reply_to(message, "⏳ AI czyta wykres...")
    
    file_info = bot.get_file(message.photo[-1].file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    
    try:
        img = PIL.Image.open(io.BytesIO(downloaded_file))
        risk_amt = s['capital'] * s['risk'] / 100
        
        prompt = f"""
        Analyze this FX chart. Purple=SL, Teal/Black=Entry.
        Account: {s['capital']} {s['currency']}, Risk: {s['risk']}% ({risk_amt}).
        
        Return in list:
        - Pair
        - Direction
        - Entry & SL
        - Lot Size (Forex: 10$/pip, Gold: 1$/point, Oil: 10$/point)
        - TP 1:2 & 1:3
        """
        
        # Używamy modelu 1.5-flash, bo 2.0-flash na serwerach zewnętrznych często rzuca 404
        response = client.models.generate_content(
            model="gemini-1.5-flash", 
            contents=[prompt, img]
        )
        
        # Zapis do dziennika
        new_entry = {"uid": str(uuid.uuid4())[:8], "chat_id": chat_id, "date": time.strftime('%Y-%m-%d %H:%M'), "analysis": response.text}
        journal = load_journal()
        journal.append(new_entry)
        save_journal(journal)
        
        bot.edit_message_text(response.text, chat_id, status.message_id)
        
    except Exception as e:
        bot.edit_message_text(f"❌ Błąd: {str(e)}\nUpewnij się, że masz włączone API Gemini.", chat_id, status.message_id)

if __name__ == "__main__":
    keep_alive()
    bot.infinity_polling()

