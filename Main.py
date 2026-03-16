import os
import telebot
from flask import Flask
from threading import Thread
from google import genai
import PIL.Image
import io

# --- KONFIGURACJA (Pobiera dane z ustawień serwera, nie z kodu) ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GEMINI_KEY = os.environ.get('GEMINI_KEY')

bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = genai.Client(api_key=GEMINI_KEY)

# --- SERWER WWW (Dla UptimeRobota / Telefonu) ---
app = Flask('')

@app.route('/')
def home():
    return "Bot Kasownik działa!"

def run_server():
    # Render wymaga portu 10000
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_server)
    t.daemon = True
    t.start()

# --- LOGIKA BOTA ---
@bot.message_handler(commands=['start'])
def welcome(message):
    bot.reply_to(message, "FX Kalkulator gotowy. Wyślij screena!")

# Tutaj dodaj swoją resztę logiki (obliczanie lota itd.)

# --- START ---
if __name__ == "__main__":
    keep_alive() # Odpala serwer WWW
    print("Serwer HTTP ruszył...")
    bot.infinity_polling() # Odpala bota Telegram
