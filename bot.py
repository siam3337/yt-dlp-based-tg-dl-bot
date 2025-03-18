import os
import time
import uuid
import telebot
import dropbox
import requests
import threading
from yt_dlp import YoutubeDL
from urllib.parse import quote
from telebot.types import ReplyKeyboardMarkup, KeyboardButton

# --- Configuration ---
BOT_TOKEN = '7405332523:AAFxaAaOx8iUQjkcQwi6rO-UPDhCer-q72o'  # Replace with your Telegram Bot token
DROPBOX_APP_KEY = 'agpjokrw2yfarvb'
DROPBOX_APP_SECRET = 'd2jvy3ab4cocbk5'
DROPBOX_REFRESH_TOKEN = 'bDwJCXIqjDwAAAAAAAAAAfSn3KYEQdyu24KX2WXS1TL93RLzMD9lRS_HZP9KZiib'
FLASK_APP_URL = 'https://yourserver.com/downloads/'
TINYURL_API_KEY = 'YOUR_TINYURL_API_KEY'

# Max file size for direct Telegram upload (50 MB)
MAX_UPLOAD_SIZE = 50 * 1024 * 1024

bot = telebot.TeleBot(BOT_TOKEN)
user_data = {}

# Initialize Dropbox session
def get_dropbox_access_token():
    """ Refresh Dropbox access token automatically """
    response = requests.post("https://api.dropbox.com/oauth2/token", data={
        "grant_type": "refresh_token",
        "refresh_token": DROPBOX_REFRESH_TOKEN,
        "client_id": DROPBOX_APP_KEY,
        "client_secret": DROPBOX_APP_SECRET
    })
    return response.json().get("access_token")

def upload_to_dropbox(local_path, dropbox_path):
    """ Upload file to Dropbox with chunked upload support """
    dbx = dropbox.Dropbox(get_dropbox_access_token())
    file_size = os.path.getsize(local_path)

    with open(local_path, "rb") as f:
        if file_size <= 150 * 1024 * 1024:  # Simple upload for small files
            dbx.files_upload(f.read(), dropbox_path, mode=dropbox.files.WriteMode.overwrite)
        else:
            CHUNK_SIZE = 10 * 1024 * 1024
            session_start = dbx.files_upload_session_start(f.read(CHUNK_SIZE))
            cursor = dropbox.files.UploadSessionCursor(session_id=session_start.session_id, offset=f.tell())

            while f.tell() < file_size:
                dbx.files_upload_session_append(f.read(CHUNK_SIZE), cursor.session_id, cursor.offset)
                cursor.offset = f.tell()

            dbx.files_upload_session_finish(f.read(), cursor, dropbox.files.CommitInfo(path=dropbox_path))

    return dbx.sharing_create_shared_link_with_settings(dropbox_path).url

def modify_dropbox_link(shared_link):
    """ Modify Dropbox shared link for direct download & streaming """
    return shared_link.replace("?dl=0", "?dl=1").replace("www.dropbox.com", "dl.dropboxusercontent.com")

def shorten_link(long_url):
    """ Shorten URL using TinyURL API """
    headers = {'Authorization': f'Bearer {TINYURL_API_KEY}', 'Content-Type': 'application/json'}
    data = {"url": long_url}

    try:
        response = requests.post("https://api.tinyurl.com/create", json=data, headers=headers)
        return response.json().get('data', {}).get('tiny_url', long_url)
    except Exception as e:
        print(f"Error shortening link: {e}")
        return long_url

def auto_delete_message(chat_id, message_id, delay=3600):
    """ Delete messages after a delay """
    time.sleep(delay)
    try:
        bot.delete_message(chat_id, message_id)
    except:
        pass

@bot.message_handler(commands=['start'])
def send_welcome(message):
    """ Welcome message """
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("⬇️ Download"), KeyboardButton("ℹ️ Supported Sites"))

    bot_msg = bot.reply_to(message, "Welcome! Send a video link to download.", reply_markup=keyboard)
    threading.Thread(target=auto_delete_message, args=(message.chat.id, bot_msg.message_id)).start()

@bot.message_handler(func=lambda message: message.text == "⬇️ Download")
def prompt_download(message):
    bot_msg = bot.reply_to(message, "Send me a video link to fetch available formats.")
    threading.Thread(target=auto_delete_message, args=(message.chat.id, bot_msg.message_id)).start()

@bot.message_handler(func=lambda message: any(protocol in message.text for protocol in ['http://', 'https://']))
def fetch_formats(message):
    """ Fetch available video formats """
    chat_id, url = message.chat.id, message.text
    user_data[chat_id] = {"url": url}

    bot_msg = bot.reply_to(message, "Fetching video formats... Please wait.")

    try:
        ydl_opts = {'listformats': True, 'nocolor': True}
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = info.get('formats', [])

        user_data[chat_id]['formats'] = formats
        markup = telebot.types.InlineKeyboardMarkup()

        for fmt in formats:
            fmt_id = fmt.get('format_id', 'Unknown')
            resolution = fmt.get('resolution', 'Unknown')
            ext = fmt.get('ext', 'Unknown')
            markup.add(telebot.types.InlineKeyboardButton(f"{resolution} | {ext}", callback_data=fmt_id))

        bot.edit_message_text("Choose a format:", chat_id, bot_msg.message_id, reply_markup=markup)

    except Exception as e:
        bot.edit_message_text(f"Error: {str(e)}", chat_id, bot_msg.message_id)

@bot.callback_query_handler(func=lambda call: True)
def download_video(call):
    """ Download video and upload to Dropbox """
    chat_id, format_id = call.message.chat.id, call.data
    url = user_data.get(chat_id, {}).get('url')

    if not url:
        bot.send_message(chat_id, "Error: URL not found. Please send the link again.")
        return

    random_name = uuid.uuid4().hex[:8]
    output_path = f"downloads/{random_name}.mp4"

    bot_msg = bot.send_message(chat_id, "Downloading video...")

    ydl_opts = {
        'format': f'{format_id}+bestaudio/best',
        'outtmpl': output_path,
        'merge_output_format': 'mp4'
    }

    try:
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Upload to Dropbox
        dropbox_path = f"/Videos/{random_name}.mp4"
        shared_link = upload_to_dropbox(output_path, dropbox_path)

        # Modify link for direct access
        direct_link = modify_dropbox_link(shared_link)
        short_link = shorten_link(direct_link)

        bot.edit_message_text(f"✅ Download complete!\n\n📥 **Direct Download:** {short_link}", chat_id, bot_msg.message_id, parse_mode="Markdown")

        # Delete file from server
        os.remove(output_path)

    except Exception as e:
        bot.edit_message_text(f"❌ Error: {str(e)}", chat_id, bot_msg.message_id)

# Start bot polling
def run_bot():
    while True:
        try:
            bot.polling(timeout=60, long_polling_timeout=30, none_stop=True)
        except Exception as e:
            print(f"Bot crashed: {e}. Restarting in 5 seconds...")
            time.sleep(5)

# Run bot in the background
threading.Thread(target=run_bot).start()
