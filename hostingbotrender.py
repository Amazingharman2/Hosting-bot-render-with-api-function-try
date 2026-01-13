import telebot
import os
import subprocess
import time
import shutil
import threading
import importlib
import importlib.util
import sys
import json
from datetime import datetime
from telebot import types
import logging
from logging.handlers import RotatingFileHandler

# --- Flask Imports for Render & API Hosting ---
from flask import Flask, request
from werkzeug.middleware.dispatcher import DispatcherMiddleware

# --- Configuration ---
# Use environment variables for Render, fallback to hardcoded for local testing
BOT_TOKEN = os.environ.get("BOT_TOKEN", "7553104853:AAFl4aTRvSbGrR0nkEHpfYBoCp6rpeSVwF4")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "2052400282"))
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "localhost:5000")

bot = telebot.TeleBot(BOT_TOKEN)

# Setup Flask App (Required for Render Web Service)
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running! Web server is active."

# --- Directories & Logging ---
UPLOAD_DIR = "uploads"
LOG_DIR = "logs"

if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
log_handler = RotatingFileHandler(
    os.path.join(LOG_DIR, 'bot.log'), 
    maxBytes=5*1024*1024, 
    backupCount=5
)
log_handler.setFormatter(log_formatter)
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(log_handler)

# State management
active_processes = {}
user_sessions = {}
hosted_apis = {} # Store mounted API info
bot_status = "running"
installed_packages = set()

def log_action(user_id, action, details=""):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"{timestamp} - User {user_id}: {action} {details}"
    logger.info(log_entry)
    user_log_file = os.path.join(LOG_DIR, f"user_{user_id}.log")
    with open(user_log_file, 'a') as f:
        f.write(log_entry + "\n")

def check_and_install_requirements(file_path):
    try:
        with open(file_path, 'r') as f:
            content = f.read()
        required_packages = set()
        lines = content.split('\n')
        for line in lines:
            line = line.strip()
            if line.startswith('import ') or line.startswith('from '):
                parts = line.split()
                if line.startswith('import '):
                    package = parts[1].split('.')[0]
                else: 
                    package = parts[1].split('.')[0]
                builtin_modules = ['os', 'sys', 'json', 'datetime', 'time', 
                                  'logging', 'threading', 'math', 'random', 'flask', 'werkzeug']
                if package not in builtin_modules:
                    required_packages.add(package)
        
        for package in required_packages:
            if package not in installed_packages:
                try:
                    subprocess.check_call([sys.executable, "-m", "pip", "install", package])
                    installed_packages.add(package)
                    log_action("system", f"Auto-installed package: {package}")
                except:
                    pass
        return list(required_packages)
    except Exception as e:
        logger.error(f"Error checking requirements: {e}")
        return []

# --- Keyboards ---
def create_transparent_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    keyboard.add(
        types.KeyboardButton('📤 Upload'),
        types.KeyboardButton('📂 Files'),
        types.KeyboardButton('⚡ Run'),
    )
    keyboard.add(
        types.KeyboardButton('🌐 Host API'), # New Feature
        types.KeyboardButton('🗑️ Delete'),
        types.KeyboardButton('⏹️ Stop'),
    )
    keyboard.add(
        types.KeyboardButton('🧹 Clear All'),
        types.KeyboardButton('📦 Install'),
        types.KeyboardButton('🌐 Ping'),
    )
    keyboard.add(
        types.KeyboardButton('📊 Logs'),
        types.KeyboardButton('🆕 New Bot'),
        types.KeyboardButton('ℹ️ Status'),
    )
    return keyboard

def create_file_selection_keyboard(file_list, prefix="", row_width=2):
    keyboard = types.InlineKeyboardMarkup(row_width=row_width)
    for file_name in file_list:
        icon = get_file_icon(file_name)
        keyboard.add(
            types.InlineKeyboardButton(
                text=f"{icon} {file_name}", 
                callback_data=f"{prefix}_{file_name}"
            )
        )
    keyboard.add(types.InlineKeyboardButton(text="🔙 Back", callback_data="back_to_main"))
    return keyboard

def get_file_icon(filename):
    if filename.endswith('.py'): return '🐍'
    elif filename.endswith('.txt'): return '📄'
    elif filename.endswith('.json'): return '📋'
    elif filename.endswith('.log'): return '📊'
    else: return '📁'

# --- Handlers ---
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    welcome_msg = """
🤖 *Bot Management System (Render Edition)*
    
*Commands:*
📤 Upload - Upload files
📂 Files - List uploaded files
⚡ Run - Execute Python files (Script Mode)
🌐 Host API - Host Flask API files (Web Mode)
🗑️ Delete - Remove files
⏹️ Stop - Stop running scripts
🧹 Clear All - Remove all files
📦 Install - Install Python packages
🌐 Ping - Check internet speed
📊 Logs - View system logs
ℹ️ Status - Check bot status
    """
    bot.send_message(message.chat.id, welcome_msg, parse_mode='Markdown', reply_markup=create_transparent_keyboard())
    log_action(message.from_user.id, "Started bot")

@bot.message_handler(func=lambda message: message.text == "📤 Upload")
def handle_upload_request(message):
    bot.reply_to(message, "📎 *Send me the file you want to upload*", parse_mode='Markdown')

@bot.message_handler(content_types=['document'])
def handle_document(message):
    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        file_name = os.path.join(UPLOAD_DIR, message.document.file_name)
        with open(file_name, 'wb') as f:
            f.write(downloaded_file)

        if file_name.endswith('.py'):
            installed = check_and_install_requirements(file_name)
            msg = f"✅ *File uploaded:* `{message.document.file_name}`"
            if installed: msg += f"\n📦 Auto-installed: {', '.join(installed)}"
            bot.reply_to(message, msg, parse_mode='Markdown')
        else:
            bot.reply_to(message, f"✅ *File uploaded:* `{message.document.file_name}`", parse_mode='Markdown')
        
        log_action(message.from_user.id, f"Uploaded: {message.document.file_name}")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: `{str(e)}`", parse_mode='Markdown')

@bot.message_handler(func=lambda message: message.text == "📂 Files")
def list_files(message):
    files = os.listdir(UPLOAD_DIR)
    if files:
        file_list = "\n".join([f"{get_file_icon(f)} `{f}`" for f in files])
        bot.send_message(message.chat.id, f"📁 *Files:*\n\n{file_list}", parse_mode='Markdown')
    else:
        bot.reply_to(message, "📭 No files found", parse_mode='Markdown')

@bot.message_handler(func=lambda message: message.text == "⚡ Run")
def handle_run_file_request(message):
    files = os.listdir(UPLOAD_DIR)
    if not files: return bot.reply_to(message, "📭 No files", parse_mode='Markdown')
    bot.send_message(message.chat.id, "⚡ *Select script to run:*", parse_mode='Markdown', reply_markup=create_file_selection_keyboard(files, "run"))

def run_file_in_thread(file_path, file_name, chat_id):
    try:
        if file_path.endswith('.py'):
            check_and_install_requirements(file_path)
        
        # Notify user immediately that it started
        bot.send_message(chat_id, f"🚀 *Started Execution:* `{file_name}`\n\n⏳ Processing...", parse_mode='Markdown')
        
        process = subprocess.Popen(
            ['python', file_path], 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        
        active_processes[file_name] = {'process': process, 'start_time': datetime.now(), 'chat_id': chat_id}
        output_lines = []
        error_lines = []
        
        for line in process.stdout:
            if line:
                output_lines.append(line)
                if len(output_lines) % 10 == 0:
                    bot.send_message(chat_id, f"⚡ Running {file_name}...\nLines: {len(output_lines)}", parse_mode='Markdown')
        
        for line in process.stderr:
            if line: error_lines.append(line)
        
        process.wait()
        
        output = ''.join(output_lines[-50:])
        error = ''.join(error_lines)
        
        response = f"✅ *Finished:* `{file_name}`\n\n"
        if output: response += f"📝 *Output:*\n```\n{output[-2000:]}\n```\n\n"
        if error: response += f"⚠️ *Errors:*\n```\n{error[-1000:]}\n```"
        if not output and not error: response += "No output."
        
        bot.send_message(chat_id, response, parse_mode='Markdown')
        if file_name in active_processes: del active_processes[file_name]
            
    except Exception as e:
        bot.send_message(chat_id, f"❌ Error: `{str(e)}`", parse_mode='Markdown')
        if file_name in active_processes: del active_processes[file_name]

# --- New Feature: Host Flask API ---
@bot.message_handler(func=lambda message: message.text == "🌐 Host API")
def handle_host_request(message):
    files = [f for f in os.listdir(UPLOAD_DIR) if f.endswith('.py')]
    if not files: return bot.reply_to(message, "📭 No .py files to host", parse_mode='Markdown')
    bot.send_message(message.chat.id, "🌐 *Select file to Host as API:*", parse_mode='Markdown', reply_markup=create_file_selection_keyboard(files, "host"))

@bot.callback_query_handler(func=lambda call: call.data.startswith('host_'))
def host_api_callback(call):
    file_name = call.data[5:]
    file_path = os.path.join(UPLOAD_DIR, file_name)
    user_prefix = f"/u{call.from_user.id}"
    
    try:
        # 1. Load Module
        spec = importlib.util.spec_from_file_location(f"dynamic_module_{call.from_user.id}", file_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        # 2. Check for Flask App
        if not hasattr(module, 'app'):
            bot.answer_callback_query(call.id, "❌ No 'app' variable found!")
            return
            
        user_app = module.app
        mount_path = f"{user_prefix}/{file_name.replace('.py', '')}"
        
        # 3. Mount App using DispatcherMiddleware
        # This wraps the main app so requests to /prefix go to user app
        current_app = app.wsgi_app
        if isinstance(current_app, DispatcherMiddleware):
            # If already wrapped, just add to the map
            current_app.mounts[mount_path] = user_app.wsgi_app
        else:
            # First time wrapping
            app.wsgi_app = DispatcherMiddleware(app.wsgi_app, {mount_path: user_app.wsgi_app})
            
        hosted_apis[file_name] = call.from_user.id
        
        # 4. Send URL
        # If on Render, use https:// + RENDER_EXTERNAL_URL
        base_url = f"https://{RENDER_EXTERNAL_URL}" if "render" in RENDER_EXTERNAL_URL else f"http://{RENDER_EXTERNAL_URL}"
        full_url = f"{base_url}{mount_path}/"
        
        bot.edit_message_text(
            f"🌐 *API Hosted Successfully!*\n\n🔗 *URL:* `{full_url}`\n\n📁 File: `{file_name}`\n👤 User: `{call.from_user.id}`",
            call.message.chat.id, call.message.message_id, parse_mode='Markdown'
        )
        log_action(call.from_user.id, f"Hosted API: {file_name} at {mount_path}")
        
    except Exception as e:
        bot.edit_message_text(f"❌ *Hosting Failed:* `{str(e)}`", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        log_action(call.from_user.id, f"Host API Error: {str(e)}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('run_'))
def run_file_callback(call):
    file_name = call.data[4:]
    if file_name in active_processes:
        return bot.answer_callback_query(call.id, "⚠️ Already running!")
    
    # Basic check if it looks like a Flask app to warn user
    file_path = os.path.join(UPLOAD_DIR, file_name)
    with open(file_path, 'r') as f:
        content = f.read()
        if "Flask(__name__)" in content and "app.run" in content:
            bot.answer_callback_query(call.id, "⚠️ Warning: Looks like a Flask app. Use 'Host API' button for web apps.")
            # We let it run anyway, but it might fail on port conflict

    bot.answer_callback_query(call.id, "⚡ Starting...")
    thread = threading.Thread(target=run_file_in_thread, args=(file_path, file_name, call.message.chat.id))
    thread.daemon = True
    thread.start()
    
    bot.edit_message_text(f"⚡ *Running:* `{file_name}`", call.message.chat.id, call.message.message_id, parse_mode='Markdown')

@bot.message_handler(func=lambda message: message.text == "🗑️ Delete")
def handle_delete_request(message):
    files = os.listdir(UPLOAD_DIR)
    if not files: return bot.reply_to(message, "📭 No files", parse_mode='Markdown')
    bot.send_message(message.chat.id, "🗑️ *Select file to delete:*", parse_mode='Markdown', reply_markup=create_file_selection_keyboard(files, "delete"))

@bot.callback_query_handler(func=lambda call: call.data.startswith('delete_'))
def delete_file_callback(call):
    file_name = call.data[7:]
    try:
        os.remove(os.path.join(UPLOAD_DIR, file_name))
        bot.answer_callback_query(call.id, "✅ Deleted!")
        bot.edit_message_text(f"🗑️ Deleted: `{file_name}`", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        if file_name in hosted_apis:
            # Note: Real unmounting in DispatcherMiddleware is complex without restart. 
            # We just remove from our tracking list.
            del hosted_apis[file_name]
        log_action(call.from_user.id, f"Deleted: {file_name}")
    except Exception as e:
        bot.answer_callback_query(call.id, "❌ Error")

@bot.message_handler(func=lambda message: message.text == "⏹️ Stop")
def stop_file(message):
    if not active_processes: return bot.reply_to(message, "⏹️ No active processes", parse_mode='Markdown')
    
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    for file_name in active_processes:
        keyboard.add(types.InlineKeyboardButton(text=f"⏹️ {file_name}", callback_data=f"stop_{file_name}"))
    
    bot.reply_to(message, "🛑 *Select process to stop:*", parse_mode='Markdown', reply_markup=keyboard)

@bot.callback_query_handler(func=lambda call: call.data.startswith("stop_"))
def stop_file_callback(call):
    file_name = call.data[5:]
    if file_name in active_processes:
        try:
            active_processes[file_name]['process'].terminate()
            del active_processes[file_name]
            bot.answer_callback_query(call.id, "✅ Stopped!")
            bot.edit_message_text(f"⏹️ Stopped: `{file_name}`", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
            log_action(call.from_user.id, f"Stopped: {file_name}")
        except: pass

# Keep other existing commands (Install, Ping, Logs, Status, Clear All, New Bot) mostly as is
# You can paste the rest of the original handlers for Install, Ping, etc. here. 
# For brevity, I am including the main ones modified for the Render context.

@bot.message_handler(func=lambda message: message.text == "🧹 Clear All")
def delete_all_files(message):
    # Add confirmation logic here
    for f in os.listdir(UPLOAD_DIR):
        try: os.remove(os.path.join(UPLOAD_DIR, f))
        except: pass
    bot.reply_to(message, "🧹 All files cleared.", parse_mode='Markdown')
    # Note: This won't unmount APIs without restart

@bot.message_handler(func=lambda message: message.text == "ℹ️ Status")
def bot_status_check(message):
    status = f"""
🤖 *Bot Status*
📁 Files: `{len(os.listdir(UPLOAD_DIR))}`
⚡ Processes: `{len(active_processes)}`
🌐 Hosted APIs: `{len(hosted_apis)}`
"""
    bot.reply_to(message, status, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data == "back_to_main")
def back_to_main_callback(call):
    bot.edit_message_text("🔙 Main Menu", call.message.chat.id, call.message.message_id, reply_markup=create_transparent_keyboard())

def run_bot_polling():
    """Runs the bot polling in a separate thread"""
    while True:
        try:
            log_action("system", "Bot polling started")
            bot.polling(none_stop=True, interval=1, timeout=20)
        except Exception as e:
            log_action("system", f"Bot polling error: {e}, restarting in 5s...")
            time.sleep(5)

if __name__ == '__main__':
    # 1. Start Bot Polling in Thread
    bot_thread = threading.Thread(target=run_bot_polling)
    bot_thread.daemon = True
    bot_thread.start()
    
    # 2. Run Flask App (Main thread for Render)
    # Render expects the app to bind to the port in the PORT env var
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Starting Flask Server on port {port}...")
    app.run(host="0.0.0.0", port=port, use_reloader=False)
