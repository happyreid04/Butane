import telebot
import requests
from bs4 import BeautifulSoup

# Initialize bot with your token
BOT_TOKEN = 'YOUR_TOKEN_HERE'
bot = telebot.TeleBot(BOT_TOKEN)

# SECTION 1: The Scraper Function
def scrape_data(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Example: Grabbing the page title
        title = soup.title.string
        return f"Page Title: {title}"
    except Exception as e:
        return f"Error: {e}"

# SECTION 2: Telegram Handlers
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Send me a link, and I will scrape the title for you!")

@bot.message_handler(func=lambda message: True)
def handle_all_messages(message):
    if message.text.startswith('http'):
        bot.send_message(message.chat.id, "Scraping... please wait.")
        result = scrape_data(message.text)
        bot.send_message(message.chat.id, result)
    else:
        bot.reply_to(message, "Please send a valid URL.")

# SECTION 3: The "Polling" (Keep the bot alive)
bot.infinity_polling()
