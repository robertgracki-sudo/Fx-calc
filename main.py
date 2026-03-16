import os
import telebot
import google.generativeai as genai
from flask import Flask
from threading import Thread
import PIL.Image
import io

# --- KONFIGURACJA ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GEMINI_KEY = os.environ.get('GEMINI_KEY')

# Inicjalizacja Gemini w starym, sprawdzonym stylu
genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

bot = telebot.TeleBot(TELEGRAM_TOKEN)

# --- SERWER DLA RENDERA ---
app = Flask(__name__)
@app.route('/', methods=['GET', 'HEAD'])
def home(): return "OK", 200

def run_server():
    app.run(host='0.0.0.0', port=10000)

# --- ANALIZA ZDJĘĆ ---
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    status = bot.reply_to(message, "⏳ Łączę z Google AI...")
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        img = PIL.Image.open(io.BytesIO(downloaded_file))
        
        # Bardzo prosty prompt na test
        prompt = "Przeanalizuj ten wykres Forex. Podaj parę, kierunek i sugerowany lot dla ryzyka 0.75% z 20k GBP."
        
        # To wywołanie omija błędy wersji API
        response = model.generate_content([prompt, img])
        bot.edit_message_text(response.text, message.chat.id, status.message_id)
        
    except Exception as e:
        bot.edit_message_text(f"❌ Błąd krytyczny: {str(e)}", message.chat.id, status.message_id)

if __name__ == "__main__":
    Thread(target=run_server).start()
    bot.infinity_polling()

