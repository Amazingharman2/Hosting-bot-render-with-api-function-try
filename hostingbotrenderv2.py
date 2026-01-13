import telebot
import os
import subprocess
import time
import threading
import importlib
import importlib.util
import sys
import json
import traceback
from datetime import datetime
from telebot import types
import logging
from logging.handlers import RotatingFileHandler

# --- Flask Imports for Render & API Hosting ---
from flask import Flask, request
from werkzeug.middleware.dispatcher import DispatcherMiddleware

# --- Configuration ---
# Use environment variables for Render, fallback to hardcoded for local testing
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8345947714:AAF84RZVwKzJMbRHdbEpFJ65pGb-wTCtfQo")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "2052400282"))
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "localhost:5000")

bot = telebot.TeleBot(BOT_TOKEN)

# Setup Flask App (Required for Render Web Service)
app = Flask(__name__)

# Root Route (Health Check)
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

# Setup Rotating Logs
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
log_file_path = os.path.join(LOG_DIR, 'bot.log')
log_handler = RotatingFileHandler(log_file_path, maxBytes=5*1024*1024, backupCount=5)
log_handler.setFormatter(log_formatter)
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(log_handler)

# State management
active_processes = {}
# Structure: { "Filename": { "app": FlaskAppObj, "path": "/uID/Filename", "user_id": ID } }
hosted_apis = {} 
bot_status = "running"
installed_packages = set()

# --- Helper Functions ---

def log_action(user_id, action, details=""):
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"{timestamp} - User {user_id}: {action} {details}"
        logger.info(log_entry)
        
        # Specific user log
        user_log_file = os.path.join(LOG_DIR, f"user_{user_id}.log")
        with open(user_log_file, 'a') as f:
            f.write(log_entry + "\n")
    except Exception as e:
        print(f"Logging error: {e}")

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
                pkg = parts[1].split('.')[0] if line.startswith('import ') else parts[1].split('.')[0]
                builtin = ['os', 'sys', 'json', 'datetime', 'time', 'logging', 'threading', 'math', 'random', 'flask', 'werkzeug']
                if pkg not in builtin:
                    required_packages.add(pkg)
        
        for pkg in required_packages:
            if pkg not in installed_packages:
                try:
                    subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
                    installed_packages.add(pkg)
                    log_action("system", f"Installed package: {pkg}")
                except: pass
        return list(required_packages)
    except Exception as e:
        logger.error(f"Requirement check error: {e}")
        return []

def update_middleware():
    """
    Updates the Flask DispatcherMiddleware with currently hosted APIs.
    This effectively mounts/unmounts apps at runtime.
    """
    # Rebuild the mount dictionary
    mounts = {}
    for name, info in hosted_apis.items():
        # info['app'] is the user's Flask app
        mounts[info['path']] = info['app'].wsgi_app
    
    # Wrap the main app with the new mounts
    # app is the main bot app
    app.wsgi_app = DispatcherMiddleware(app, mounts)

# --- Keyboards ---

def create_transparent_keyboard():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    keyboard.add(
        types.KeyboardButton('📤 Upload'),
        types.KeyboardButton('📂 Files'),
        types.KeyboardButton('⚡ Run'),
    )
    keyboard.add(
        types.KeyboardButton('🌐 Host API'),
        types.KeyboardButton('📱 Manage APIs'), # NEW
        types.KeyboardButton('🗑️ Delete'),
    )
    keyboard.add(
        types.KeyboardButton('⏹️ Stop Script'),
        types.KeyboardButton('🧹 Clear All'),
        types.KeyboardButton('📦 Install'),
    )
    keyboard.add(
        types.KeyboardButton('📊 Logs'),
        types.KeyboardButton('ℹ️ Status'),
        types.KeyboardButton('🌐 Ping'),
    )
    return keyboard

def create_file_selection_keyboard(file_list, prefix="", row_width=2):
    keyboard = types.InlineKeyboardMarkup(row_width=row_width)
    for file_name in file_list:
        icon = '🐍' if file_name.endswith('.py') else '📄'
        keyboard.add(
            types.InlineKeyboardButton(text=f"{icon} {file_name}", callback_data=f"{prefix}_{file_name}")
        )
    keyboard.add(types.InlineKeyboardButton(text="🔙 Back", callback_data="back_to_main"))
    return keyboard

def get_file_icon(filename):
    if filename.endswith('.py'): return '🐍'
    elif filename.endswith('.txt'): return '📄'
    elif filename.endswith('.json'): return '📋'
    elif filename.endswith('.log'): return '📊'
    else: return '📁'

# --- Telegram Handlers ---

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    welcome_msg = """
🤖 *Bot Management System (Render Edition)*
    
*Commands:*
📤 Upload - Upload files
📂 Files - List uploaded files
⚡ Run - Execute Python files (Script Mode)
🌐 Host API - Host Flask API files (Web Mode)
📱 Manage APIs - Stop/Manage Hosted APIs
🗑️ Delete - Remove files
⏹️ Stop Script - Stop running python scripts
🧹 Clear All - Remove all files
📦 Install - Install Python packages
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

# --- Host API Logic ---

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
    
    # Check if already hosted
    if file_name in hosted_apis:
        bot.answer_callback_query(call.id, "⚠️ Already hosted!")
        return

    try:
        check_and_install_requirements(file_path)
        
        spec = importlib.util.spec_from_file_location(f"api_module_{call.from_user.id}", file_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        if not hasattr(module, 'app'):
            bot.answer_callback_query(call.id, "❌ No 'app' variable found in file!")
            return
            
        user_app = module.app
        mount_path = f"{user_prefix}/{file_name.replace('.py', '')}"
        
        # Store in global dict
        hosted_apis[file_name] = {
            'app': user_app,
            'path': mount_path,
            'user_id': call.from_user.id
        }
        
        # Update Flask Middleware
        update_middleware()
        
        base_url = f"https://{RENDER_EXTERNAL_URL}" if "render" in RENDER_EXTERNAL_URL else f"http://{RENDER_EXTERNAL_URL}"
        full_url = f"{base_url}{mount_path}/"
        
        bot.edit_message_text(
            f"🌐 *API Hosted Successfully!*\n\n🔗 *URL:* `{full_url}`\n\n📁 File: `{file_name}`\n👤 User: `{call.from_user.id}`",
            call.message.chat.id, call.message.message_id, parse_mode='Markdown'
        )
        log_action(call.from_user.id, f"Hosted API: {file_name} at {mount_path}")
        
    except Exception as e:
        error_trace = traceback.format_exc()
        bot.edit_message_text(f"❌ *Hosting Failed:* `{str(e)}`", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        log_action(call.from_user.id, f"Host API Error: {str(e)}")

# --- Manage APIs Logic ---

@bot.message_handler(func=lambda message: message.text == "📱 Manage APIs")
def manage_apis(message):
    if not hosted_apis:
        return bot.reply_to(message, "📭 *No APIs currently hosted*", parse_mode='Markdown')
    
    msg = "📱 *Active Hosted APIs:*\n\n"
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    
    for name, info in hosted_apis.items():
        # Only show if you are the owner or admin
        if info['user_id'] == message.from_user.id or message.from_user.id == ADMIN_ID:
            status_icon = "🟢"
            msg += f"{status_icon} *{name}*\n   👤 Owner: `{info['user_id']}`\n   🔗 `{info['path']}`\n\n"
            keyboard.add(types.InlineKeyboardButton(text=f"🛑 Stop {name}", callback_data=f"stop_api_{name}"))
    
    if len(hosted_apis) == 0:
        return bot.reply_to(message, "📭 *No APIs currently hosted*", parse_mode='Markdown')

    keyboard.add(types.InlineKeyboardButton(text="🔙 Back", callback_data="back_to_main"))
    bot.send_message(message.chat.id, msg, parse_mode='Markdown', reply_markup=keyboard)

@bot.callback_query_handler(func=lambda call: call.data.startswith('stop_api_'))
def stop_api_callback(call):
    file_name = call.data[9:]
    
    if file_name not in hosted_apis:
        return bot.answer_callback_query(call.id, "❌ API not found")
    
    # Check permission
    api_info = hosted_apis[file_name]
    if api_info['user_id'] != call.from_user.id and call.from_user.id != ADMIN_ID:
        return bot.answer_callback_query(call.id, "❌ You don't own this API")
    
    try:
        # Remove from dictionary
        del hosted_apis[file_name]
        # Update Middleware (Unmount)
        update_middleware()
        
        bot.answer_callback_query(call.id, "✅ API Stopped")
        bot.edit_message_text(
            f"🛑 *API Stopped:* `{file_name}`\n\n✅ Traffic to this URL has been terminated.",
            call.message.chat.id,
            call.message.message_id,
            parse_mode='Markdown'
        )
        log_action(call.from_user.id, f"Stopped API: {file_name}")
    except Exception as e:
        bot.answer_callback_query(call.id, "❌ Error stopping API")

# --- Run / Delete / Stop Scripts ---

@bot.callback_query_handler(func=lambda call: call.data.startswith('run_'))
def run_file_callback(call):
    file_name = call.data[4:]
    if file_name in active_processes:
        return bot.answer_callback_query(call.id, "⚠️ Already running!")
    
    # Warning for Flask files
    file_path = os.path.join(UPLOAD_DIR, file_name)
    try:
        with open(file_path, 'r') as f: content = f.read()
        if "Flask(__name__)" in content and "app.run" in content:
            bot.answer_callback_query(call.id, "⚠️ Warning: Use 'Host API' for Flask apps.")
    except: pass

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
        # If this file is being hosted as an API, stop it too
        if file_name in hosted_apis:
            del hosted_apis[file_name]
            update_middleware()
            
        bot.answer_callback_query(call.id, "✅ Deleted!")
        bot.edit_message_text(f"🗑️ Deleted: `{file_name}`", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        log_action(call.from_user.id, f"Deleted: {file_name}")
    except Exception as e:
        bot.answer_callback_query(call.id, "❌ Error")

@bot.message_handler(func=lambda message: message.text == "⏹️ Stop Script")
def stop_file(message):
    if not active_processes: return bot.reply_to(message, "⏹️ No active processes", parse_mode='Markdown')
    
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    for file_name in active_processes:
        keyboard.add(types.InlineKeyboardButton(text=f"⏹️ {file_name}", callback_data=f"stop_proc_{file_name}"))
    
    bot.reply_to(message, "🛑 *Select script to stop:*", parse_mode='Markdown', reply_markup=keyboard)

@bot.callback_query_handler(func=lambda call: call.data.startswith("stop_proc_"))
def stop_proc_callback(call):
    file_name = call.data[10:]
    if file_name in active_processes:
        try:
            active_processes[file_name]['process'].terminate()
            del active_processes[file_name]
            bot.answer_callback_query(call.id, "✅ Stopped!")
            bot.edit_message_text(f"⏹️ Stopped: `{file_name}`", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
            log_action(call.from_user.id, f"Stopped Script: {file_name}")
        except: pass

@bot.message_handler(func=lambda message: message.text == "🧹 Clear All")
def delete_all_files(message):
    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(
        types.InlineKeyboardButton("✅ Yes, delete all", callback_data="confirm_delete_all"),
        types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_delete_all")
    )
    bot.reply_to(message, "⚠️ *Warning:* This will delete ALL files & Stop ALL APIs!", parse_mode='Markdown', reply_markup=keyboard)

@bot.callback_query_handler(func=lambda call: call.data == "confirm_delete_all")
def confirm_delete_all_callback(call):
    try:
        for f in os.listdir(UPLOAD_DIR):
            os.remove(os.path.join(UPLOAD_DIR, f))
        # Stop all APIs
        hosted_apis.clear()
        update_middleware()
        
        bot.answer_callback_query(call.id, "✅ Cleared!")
        bot.edit_message_text("🧹 *All files and APIs cleared!*", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
    except Exception as e:
        bot.answer_callback_query(call.id, "❌ Error")

# --- Install Package ---

@bot.message_handler(func=lambda message: message.text == "📦 Install")
def handle_install_package(message):
    bot.reply_to(message, "📦 *Enter package name:*\n\nExample: `requests` or `numpy pandas`", parse_mode='Markdown')
    bot.register_next_step_handler(message, process_package_installation)

def process_package_installation(message):
    package_name = message.text.strip()
    if not package_name: return bot.reply_to(message, "❌ No package specified", parse_mode='Markdown')
    
    progress_msg = bot.reply_to(message, f"📦 *Installing:* `{package_name}`\n\n⏳ Please wait...", parse_mode='Markdown')
    
    def install_thread():
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "pip", "install"] + package_name.split(),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            stdout, stderr = process.communicate()
            
            response = f"📦 *Installation Complete:* `{package_name}`\n\n"
            if stdout: response += f"✅ *Output:*\n```\n{stdout[-1000:]}\n```\n\n"
            if stderr and "WARNING" not in stderr: response += f"⚠️ *Errors:*\n```\n{stderr[-1000:]}\n```"
            
            bot.edit_message_text(response, message.chat.id, progress_msg.message_id, parse_mode='Markdown')
            for pkg in package_name.split(): installed_packages.add(pkg.split('==')[0].split('>=')[0])
            log_action(message.from_user.id, f"Installed: {package_name}")
        except Exception as e:
            bot.edit_message_text(f"❌ *Error:* `{str(e)}`", message.chat.id, progress_msg.message_id, parse_mode='Markdown')
    
    threading.Thread(target=install_thread, daemon=True).start()

# --- Speedtest / Ping ---

@bot.message_handler(func=lambda message: message.text == "🌐 Ping")
def ping_check(message):
    progress_msg = bot.reply_to(message, "🌐 *Checking internet speed...*", parse_mode='Markdown')
    
    def speedtest_thread():
        try:
            import speedtest
            st = speedtest.Speedtest()
            st.get_best_server()
            download = st.download() / 1_000_000
            upload = st.upload() / 1_000_000
            ping = st.results.ping
            result = f"🌐 *Speed Test*\n📡 Download: `{download:.2f} Mbps`\n📤 Upload: `{upload:.2f} Mbps`\n🏓 Ping: `{ping:.0f} ms`"
            bot.edit_message_text(result, message.chat.id, progress_msg.message_id, parse_mode='Markdown')
        except ImportError:
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", "speedtest-cli"])
                # Retry
                ping_check(message)
            except:
                bot.edit_message_text("❌ *Speedtest failed*\nPlease install manually: `pip install speedtest-cli`", message.chat.id, progress_msg.message_id, parse_mode='Markdown')
        except Exception as e:
            bot.edit_message_text(f"❌ *Error:* `{str(e)}`", message.chat.id, progress_msg.message_id, parse_mode='Markdown')
    
    threading.Thread(target=speedtest_thread, daemon=True).start()

# --- Logs ---

@bot.message_handler(func=lambda message: message.text == "📊 Logs")
def view_logs(message):
    if message.from_user.id != ADMIN_ID:
        return bot.reply_to(message, "❌ *Admin only!*", parse_mode='Markdown')
    
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        types.InlineKeyboardButton("📋 System Logs", callback_data="view_system_logs"),
        types.InlineKeyboardButton("👤 My Logs", callback_data="view_my_logs")
    )
    bot.reply_to(message, "📊 *Log Management*\nSelect log type:", parse_mode='Markdown', reply_markup=keyboard)

@bot.callback_query_handler(func=lambda call: call.data.startswith("view_"))
def view_logs_callback(call):
    if call.from_user.id != ADMIN_ID: return bot.answer_callback_query(call.id, "❌ Admin only!")
    
    if call.data == "view_system_logs":
        send_log_content(call, log_file_path, "System Logs")
    elif call.data == "view_my_logs":
        user_log_file = os.path.join(LOG_DIR, f"user_{call.from_user.id}.log")
        send_log_content(call, user_log_file, "Your Logs")

def send_log_content(call, log_file, log_type):
    try:
        if not os.path.exists(log_file):
            return bot.answer_callback_query(call.id, f"No {log_type.lower()} found!")
        
        with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
        
        if not lines:
            return bot.answer_callback_query(call.id, f"No content in {log_type.lower()}!")
        
        # Send last 100 lines
        content = ''.join(lines[-100:])
        msg = f"📊 *{log_type}* (Last 100 lines):\n\n```\n{content[-3500:]}\n```" # Limit to 3500 chars
        
        bot.send_message(call.message.chat.id, msg, parse_mode='Markdown')
        bot.answer_callback_query(call.id, f"Sent {log_type.lower()}!")
    except Exception as e:
        bot.answer_callback_query(call.id, f"Error: {str(e)}")

# --- Status ---

@bot.message_handler(func=lambda message: message.text == "ℹ️ Status")
def bot_status_check(message):
    status = f"""
🤖 *Bot Status*
📁 Files: `{len(os.listdir(UPLOAD_DIR))}`
⚡ Scripts Running: `{len(active_processes)}`
🌐 Hosted APIs: `{len(hosted_apis)}`
📦 Installed Pkgs: `{len(installed_packages)}`
"""
    if hosted_apis:
        status += "\n*Active APIs:*\n"
        for name, info in hosted_apis.items():
            status += f"- {name} ({info['path']})\n"
            
    bot.reply_to(message, status, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data == "back_to_main")
def back_to_main_callback(call):
    bot.edit_message_text("🔙 Main Menu", call.message.chat.id, call.message.message_id, reply_markup=create_transparent_keyboard())

# --- Main Execution ---

def run_bot_polling():
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
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Starting Flask Server on port {port}...")
    app.run(host="0.0.0.0", port=port, use_reloader=False)
