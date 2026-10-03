# -*- coding: utf-8 -*-
import telebot
import subprocess
import os
import zipfile
import tempfile
import shutil
from telebot import types
import time
from datetime import datetime, timedelta
import psutil
import sqlite3
import json
import logging
import signal
import threading
import re
import sys
import atexit
import requests

from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "ADITYA HOSTING BOT"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()
    print("Flask Keep-Alive server started.")

TOKEN = '8498008126:AAGcH-P-Z4KkrpWLL4dmFy6Sac0T1WCc72Q'
OWNER_ID = 6863389453
ADMIN_ID = 6863389453
YOUR_USERNAME = '@CURRENTTTTTTTT'
UPDATE_CHANNEL = 'https://t.me/newchannel1109'

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, 'upload_bots')
IROTECH_DIR = os.path.join(BASE_DIR, 'inf')
DATABASE_PATH = os.path.join(IROTECH_DIR, 'bot_data.db')
PENDING_DIR = os.path.join(BASE_DIR, 'pending_approvals')

FREE_USER_LIMIT = 1
SUBSCRIBED_USER_LIMIT = float('inf')
ADMIN_LIMIT = float('inf')
OWNER_LIMIT = float('inf')

os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
os.makedirs(IROTECH_DIR, exist_ok=True)
os.makedirs(PENDING_DIR, exist_ok=True)

bot = telebot.TeleBot(TOKEN)

bot_scripts = {}
user_subscriptions = {}
user_files = {}
active_users = set()
admin_ids = {ADMIN_ID, OWNER_ID}
bot_locked = False
pending_scripts = {}
approved_users = set()
pending_users = {}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

COMMAND_BUTTONS_LAYOUT_USER_SPEC = [
    ["📢 Updates Channel"],
    ["📤 Upload File", "📂 Check Files"],
    ["⚡ Bot Speed", "📊 Statistics"],
    ["📞 Contact Owner"]
]
ADMIN_COMMAND_BUTTONS_LAYOUT_USER_SPEC = [
    ["📢 Updates Channel"],
    ["📤 Upload File", "📂 Check Files"],
    ["⚡ Bot Speed", "📊 Statistics"],
    ["💳 Subscriptions", "📢 Broadcast"],
    ["🔒 Lock Bot", "🟢 Run All Codes"],
    ["🕓 Pending Scripts", "👑 Admin Panel"],
    ["📞 Contact Owner"]
]

def init_db():
    logger.info(f"Initializing DB at: {DATABASE_PATH}")
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('CREATE TABLE IF NOT EXISTS subscriptions (user_id INTEGER PRIMARY KEY, expiry TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS user_files (user_id INTEGER, file_name TEXT, file_type TEXT, PRIMARY KEY (user_id, file_name))')
        c.execute('CREATE TABLE IF NOT EXISTS active_users (user_id INTEGER PRIMARY KEY)')
        c.execute('CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)')
        c.execute('CREATE TABLE IF NOT EXISTS approved_users (user_id INTEGER PRIMARY KEY)')
        c.execute('CREATE TABLE IF NOT EXISTS pending_users (user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, timestamp TEXT)')
        c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (OWNER_ID,))
        if ADMIN_ID != OWNER_ID:
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (ADMIN_ID,))
        c.execute('INSERT OR IGNORE INTO approved_users (user_id) VALUES (?)', (OWNER_ID,))
        if ADMIN_ID != OWNER_ID:
            c.execute('INSERT OR IGNORE INTO approved_users (user_id) VALUES (?)', (ADMIN_ID,))
        conn.commit()
        conn.close()
        logger.info("DB initialized.")
    except Exception as e:
        logger.error(f"DB init error: {e}", exc_info=True)

def load_data():
    logger.info("Loading data...")
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT user_id, expiry FROM subscriptions')
        for uid, exp in c.fetchall():
            try:
                user_subscriptions[uid] = {'expiry': datetime.fromisoformat(exp)}
            except ValueError:
                pass
        c.execute('SELECT user_id, file_name, file_type FROM user_files')
        for uid, fn, ft in c.fetchall():
            user_files.setdefault(uid, []).append((fn, ft))
        c.execute('SELECT user_id FROM active_users')
        active_users.update(uid for (uid,) in c.fetchall())
        c.execute('SELECT user_id FROM admins')
        admin_ids.update(uid for (uid,) in c.fetchall())
        c.execute('SELECT user_id FROM approved_users')
        approved_users.update(uid for (uid,) in c.fetchall())
        c.execute('SELECT user_id, username, first_name, timestamp FROM pending_users')
        for uid, un, fn, ts in c.fetchall():
            pending_users[uid] = {'username': un, 'first_name': fn, 'timestamp': ts}
        conn.close()
        logger.info(f"Loaded: {len(active_users)} users")
    except Exception as e:
        logger.error(f"Load err: {e}", exc_info=True)

init_db()
load_data()

def get_user_folder(user_id):
    folder = os.path.join(UPLOAD_BOTS_DIR, str(user_id))
    os.makedirs(folder, exist_ok=True)
    return folder

def get_user_file_limit(user_id):
    if user_id == OWNER_ID: return OWNER_LIMIT
    if user_id in admin_ids: return ADMIN_LIMIT
    if user_id in user_subscriptions and user_subscriptions[user_id]['expiry'] > datetime.now():
        return SUBSCRIBED_USER_LIMIT
    return FREE_USER_LIMIT

def get_user_file_count(user_id):
    return len(user_files.get(user_id, []))

def is_user_approved(user_id):
    if user_id == OWNER_ID or user_id in admin_ids: return True
    return user_id in approved_users

def is_bot_running(owner_id, file_name):
    key = f"{owner_id}_{file_name}"
    info = bot_scripts.get(key)
    if info and info.get('process'):
        try:
            p = psutil.Process(info['process'].pid)
            running = p.is_running() and p.status() != psutil.STATUS_ZOMBIE
            if not running:
                _close_log(info); bot_scripts.pop(key, None)
            return running
        except psutil.NoSuchProcess:
            _close_log(info); bot_scripts.pop(key, None)
            return False
        except Exception:
            return False
    return False

def _close_log(info):
    lf = info.get('log_file')
    if lf and hasattr(lf, 'close') and not lf.closed:
        try: lf.close()
        except Exception: pass

def kill_process_tree(process_info):
    pid = None
    key = process_info.get('script_key', 'N/A')
    try:
        _close_log(process_info)
        process = process_info.get('process')
        if process and hasattr(process, 'pid'):
            pid = process.pid
            if pid:
                try:
                    parent = psutil.Process(pid)
                    children = parent.children(recursive=True)
                    for child in children:
                        try: child.terminate()
                        except Exception:
                            try: child.kill()
                            except Exception: pass
                    psutil.wait_procs(children, timeout=1)
                    for p in children:
                        try:
                            if p.is_running(): p.kill()
                        except Exception: pass
                    try:
                        parent.terminate()
                        try: parent.wait(timeout=1)
                        except psutil.TimeoutExpired: parent.kill()
                    except psutil.NoSuchProcess: pass
                except psutil.NoSuchProcess: pass
    except Exception as e:
        logger.error(f"kill err {pid} ({key}): {e}", exc_info=True)

def _log_path_for(user_folder, file_name):
    safe = file_name.replace(os.sep, '_')
    return os.path.join(user_folder, f"{safe}.log")

TELEGRAM_MODULES = {
    'telebot': 'pyTelegramBotAPI',
    'telegram': 'python-telegram-bot',
    'python_telegram_bot': 'python-telegram-bot',
    'aiogram': 'aiogram', 'pyrogram': 'pyrogram', 'telethon': 'telethon',
    'telethon.sync': 'telethon', 'telepot': 'telepot', 'pytg': 'pytg',
    'tgcrypto': 'tgcrypto', 'telegram_upload': 'telegram-upload',
    'telegram_send': 'telegram-send', 'telegram_text': 'telegram-text',
    'mtproto': 'telegram-mtproto', 'tl': 'telethon',
    'bs4': 'beautifulsoup4', 'requests': 'requests', 'pillow': 'Pillow',
    'cv2': 'opencv-python', 'yaml': 'PyYAML', 'dotenv': 'python-dotenv',
    'dateutil': 'python-dateutil', 'pandas': 'pandas', 'numpy': 'numpy',
    'flask': 'Flask', 'django': 'Django', 'sqlalchemy': 'SQLAlchemy',
    'psutil': 'psutil',
    'asyncio': None, 'json': None, 'datetime': None, 'os': None, 'sys': None,
    're': None, 'time': None, 'math': None, 'random': None, 'logging': None,
    'threading': None, 'subprocess': None, 'zipfile': None, 'tempfile': None,
    'shutil': None, 'sqlite3': None, 'atexit': None
}

def attempt_install_pip(module_name, message):
    pkg = TELEGRAM_MODULES.get(module_name.lower(), module_name)
    if pkg is None: return False
    try:
        bot.reply_to(message, f"🐍 Installing `{pkg}`...", parse_mode='Markdown')
        r = subprocess.run([sys.executable, '-m', 'pip', 'install', pkg],
                           capture_output=True, text=True, check=False, encoding='utf-8', errors='ignore')
        if r.returncode == 0:
            bot.reply_to(message, f"✅ `{pkg}` installed.", parse_mode='Markdown'); return True
        bot.reply_to(message, f"❌ Failed `{pkg}`", parse_mode='Markdown'); return False
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}"); return False

def attempt_install_npm(module_name, user_folder, message):
    try:
        bot.reply_to(message, f"🟠 Installing `{module_name}`...", parse_mode='Markdown')
        r = subprocess.run(['npm', 'install', module_name], capture_output=True, text=True,
                           check=False, cwd=user_folder, encoding='utf-8', errors='ignore')
        if r.returncode == 0:
            bot.reply_to(message, f"✅ `{module_name}` installed.", parse_mode='Markdown'); return True
        bot.reply_to(message, f"❌ Failed `{module_name}`", parse_mode='Markdown'); return False
    except FileNotFoundError:
        bot.reply_to(message, "❌ 'npm' not found."); return False
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}"); return False

def run_script(script_path, owner_id, user_folder, file_name, message_obj, attempt=1):
    max_attempts = 2
    if attempt > max_attempts:
        bot.reply_to(message_obj, f"❌ Failed to run '{file_name}'.")
        return
    key = f"{owner_id}_{file_name}"
    logger.info(f"Run py attempt {attempt}: {script_path}")
    try:
        if not os.path.exists(script_path):
            bot.reply_to(message_obj, f"❌ '{file_name}' not found!")
            remove_user_file_db(owner_id, file_name)
            return
        if attempt == 1:
            cp = None
            try:
                cp = subprocess.Popen([sys.executable, script_path], cwd=user_folder,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True, encoding='utf-8', errors='ignore')
                _, stderr = cp.communicate(timeout=5)
                rc = cp.returncode
                if rc != 0 and stderr:
                    m = re.search(r"ModuleNotFoundError: No module named '(.+?)'", stderr)
                    if m:
                        mod = m.group(1).strip().strip("'\"")
                        if attempt_install_pip(mod, message_obj):
                            bot.reply_to(message_obj, f"🔄 Retrying '{file_name}'...")
                            time.sleep(2)
                            threading.Thread(target=run_script, args=(script_path, owner_id, user_folder, file_name, message_obj, attempt + 1)).start()
                            return
                        else: return
                    bot.reply_to(message_obj, f"❌ Pre-check err", parse_mode='Markdown'); return
            except subprocess.TimeoutExpired:
                if cp and cp.poll() is None: cp.kill(); cp.communicate()
            except FileNotFoundError:
                bot.reply_to(message_obj, f"❌ Python not found."); return
            except Exception as e:
                bot.reply_to(message_obj, f"❌ Pre-check err: {e}"); return
            finally:
                if cp and cp.poll() is None: cp.kill(); cp.communicate()
        log_path = _log_path_for(user_folder, file_name)
        log_file = None; process = None
        try:
            log_file = open(log_path, 'w', encoding='utf-8', errors='ignore')
        except Exception as e:
            bot.reply_to(message_obj, f"❌ Log err: {e}"); return
        try:
            startupinfo = None; flags = 0
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
            process = subprocess.Popen([sys.executable, script_path], cwd=user_folder,
                                       stdout=log_file, stderr=log_file, stdin=subprocess.PIPE,
                                       startupinfo=startupinfo, creationflags=flags,
                                       encoding='utf-8', errors='ignore')
            bot_scripts[key] = {
                'process': process, 'log_file': log_file, 'file_name': file_name,
                'chat_id': message_obj.chat.id, 'script_owner_id': owner_id,
                'start_time': datetime.now(), 'user_folder': user_folder,
                'type': 'py', 'script_key': key
            }
            bot.reply_to(message_obj, f"✅ Py '{file_name}' started! PID {process.pid}")
        except Exception as e:
            if log_file and not log_file.closed: log_file.close()
            bot.reply_to(message_obj, f"❌ Start err: {e}")
            if process and process.poll() is None:
                kill_process_tree({'process': process, 'log_file': log_file, 'script_key': key})
            bot_scripts.pop(key, None)
    except Exception as e:
        logger.error(f"run_script err: {e}", exc_info=True)
        bot.reply_to(message_obj, f"❌ Err: {e}")

def run_js_script(script_path, owner_id, user_folder, file_name, message_obj, attempt=1):
    max_attempts = 2
    if attempt > max_attempts:
        bot.reply_to(message_obj, f"❌ Failed to run '{file_name}'.")
        return
    key = f"{owner_id}_{file_name}"
    logger.info(f"Run js attempt {attempt}: {script_path}")
    try:
        if not os.path.exists(script_path):
            bot.reply_to(message_obj, f"❌ '{file_name}' not found!")
            remove_user_file_db(owner_id, file_name)
            return
        if attempt == 1:
            cp = None
            try:
                cp = subprocess.Popen(['node', script_path], cwd=user_folder,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True, encoding='utf-8', errors='ignore')
                _, stderr = cp.communicate(timeout=5)
                rc = cp.returncode
                if rc != 0 and stderr:
                    m = re.search(r"Cannot find module '(.+?)'", stderr)
                    if m:
                        mod = m.group(1).strip().strip("'\"")
                        if not mod.startswith('.') and not mod.startswith('/'):
                            if attempt_install_npm(mod, user_folder, message_obj):
                                bot.reply_to(message_obj, f"🔄 Retrying '{file_name}'...")
                                time.sleep(2)
                                threading.Thread(target=run_js_script, args=(script_path, owner_id, user_folder, file_name, message_obj, attempt + 1)).start()
                                return
                            else: return
                    bot.reply_to(message_obj, f"❌ Pre-check err"); return
            except subprocess.TimeoutExpired:
                if cp and cp.poll() is None: cp.kill(); cp.communicate()
            except FileNotFoundError:
                bot.reply_to(message_obj, "❌ 'node' not found."); return
            except Exception as e:
                bot.reply_to(message_obj, f"❌ Pre-check err: {e}"); return
            finally:
                if cp and cp.poll() is None: cp.kill(); cp.communicate()
        log_path = _log_path_for(user_folder, file_name)
        log_file = None; process = None
        try:
            log_file = open(log_path, 'w', encoding='utf-8', errors='ignore')
        except Exception as e:
            bot.reply_to(message_obj, f"❌ Log err: {e}"); return
        try:
            startupinfo = None; flags = 0
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
            process = subprocess.Popen(['node', script_path], cwd=user_folder,
                                       stdout=log_file, stderr=log_file, stdin=subprocess.PIPE,
                                       startupinfo=startupinfo, creationflags=flags,
                                       encoding='utf-8', errors='ignore')
            bot_scripts[key] = {
                'process': process, 'log_file': log_file, 'file_name': file_name,
                'chat_id': message_obj.chat.id, 'script_owner_id': owner_id,
                'start_time': datetime.now(), 'user_folder': user_folder,
                'type': 'js', 'script_key': key
            }
            bot.reply_to(message_obj, f"✅ JS '{file_name}' started! PID {process.pid}")
        except Exception as e:
            if log_file and not log_file.closed: log_file.close()
            bot.reply_to(message_obj, f"❌ Start err: {e}")
            if process and process.poll() is None:
                kill_process_tree({'process': process, 'log_file': log_file, 'script_key': key})
            bot_scripts.pop(key, None)
    except Exception as e:
        logger.error(f"run_js_script err: {e}", exc_info=True)
        bot.reply_to(message_obj, f"❌ Err: {e}")

DB_LOCK = threading.Lock()
def _db(): return sqlite3.connect(DATABASE_PATH, check_same_thread=False)

def save_user_file(user_id, file_name, file_type='py'):
    with DB_LOCK:
        conn = _db(); c = conn.cursor()
        try:
            c.execute('INSERT OR REPLACE INTO user_files (user_id, file_name, file_type) VALUES (?,?,?)', (user_id, file_name, file_type))
            conn.commit()
            lst = user_files.setdefault(user_id, [])
            user_files[user_id] = [(fn, ft) for fn, ft in lst if fn != file_name]
            user_files[user_id].append((file_name, file_type))
        except Exception as e: logger.error(f"save_user_file err: {e}")
        finally: conn.close()

def remove_user_file_db(user_id, file_name):
    with DB_LOCK:
        conn = _db(); c = conn.cursor()
        try:
            c.execute('DELETE FROM user_files WHERE user_id=? AND file_name=?', (user_id, file_name))
            conn.commit()
            if user_id in user_files:
                user_files[user_id] = [f for f in user_files[user_id] if f[0] != file_name]
                if not user_files[user_id]: del user_files[user_id]
        except Exception as e: logger.error(f"remove_user_file_db err: {e}")
        finally: conn.close()

def add_active_user(user_id):
    active_users.add(user_id)
    with DB_LOCK:
        conn = _db(); c = conn.cursor()
        try:
            c.execute('INSERT OR IGNORE INTO active_users (user_id) VALUES (?)', (user_id,))
            conn.commit()
        except Exception as e: logger.error(f"add_active_user err: {e}")
        finally: conn.close()

def save_subscription(user_id, expiry):
    with DB_LOCK:
        conn = _db(); c = conn.cursor()
        try:
            c.execute('INSERT OR REPLACE INTO subscriptions (user_id, expiry) VALUES (?,?)', (user_id, expiry.isoformat()))
            conn.commit()
            user_subscriptions[user_id] = {'expiry': expiry}
        except Exception as e: logger.error(f"save_sub err: {e}")
        finally: conn.close()

def remove_subscription_db(user_id):
    with DB_LOCK:
        conn = _db(); c = conn.cursor()
        try:
            c.execute('DELETE FROM subscriptions WHERE user_id=?', (user_id,))
            conn.commit()
            user_subscriptions.pop(user_id, None)
        except Exception as e: logger.error(f"rm sub err: {e}")
        finally: conn.close()

def add_admin_db(admin_id):
    with DB_LOCK:
        conn = _db(); c = conn.cursor()
        try:
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (admin_id,))
            c.execute('INSERT OR IGNORE INTO approved_users (user_id) VALUES (?)', (admin_id,))
            c.execute('DELETE FROM pending_users WHERE user_id=?', (admin_id,))
            conn.commit()
            admin_ids.add(admin_id); approved_users.add(admin_id); pending_users.pop(admin_id, None)
        except Exception as e: logger.error(f"add_admin err: {e}")
        finally: conn.close()

def remove_admin_db(admin_id):
    if admin_id == OWNER_ID: return False
    with DB_LOCK:
        conn = _db(); c = conn.cursor()
        try:
            c.execute('DELETE FROM admins WHERE user_id=?', (admin_id,))
            conn.commit(); admin_ids.discard(admin_id); return True
        except Exception as e: logger.error(f"rm admin err: {e}"); return False
        finally: conn.close()

def add_approved_user(user_id):
    with DB_LOCK:
        conn = _db(); c = conn.cursor()
        try:
            c.execute('INSERT OR IGNORE INTO approved_users (user_id) VALUES (?)', (user_id,))
            c.execute('DELETE FROM pending_users WHERE user_id=?', (user_id,))
            conn.commit()
            approved_users.add(user_id); pending_users.pop(user_id, None)
        except Exception as e: logger.error(f"add_approved err: {e}")
        finally: conn.close()

def add_pending_user(user_id, username, first_name):
    ts = datetime.now().isoformat()
    with DB_LOCK:
        conn = _db(); c = conn.cursor()
        try:
            c.execute('INSERT OR REPLACE INTO pending_users (user_id, username, first_name, timestamp) VALUES (?,?,?,?)', (user_id, username or '', first_name or '', ts))
            conn.commit()
            pending_users[user_id] = {'username': username or '', 'first_name': first_name or '', 'timestamp': ts}
        except Exception as e: logger.error(f"add_pending err: {e}")
        finally: conn.close()

def remove_pending_user(user_id):
    with DB_LOCK:
        conn = _db(); c = conn.cursor()
        try:
            c.execute('DELETE FROM pending_users WHERE user_id=?', (user_id,))
            conn.commit(); pending_users.pop(user_id, None)
        except Exception as e: logger.error(f"rm pending err: {e}")
        finally: conn.close()

def create_main_menu_inline(user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    b = [
        types.InlineKeyboardButton('📢 Updates Channel', url=UPDATE_CHANNEL),
        types.InlineKeyboardButton('📤 Upload File', callback_data='upload'),
        types.InlineKeyboardButton('📂 Check Files', callback_data='check_files'),
        types.InlineKeyboardButton('⚡ Bot Speed', callback_data='speed'),
        types.InlineKeyboardButton('📞 Contact Owner', url=f'https://t.me/{YOUR_USERNAME.replace("@", "")}')
    ]
    if user_id in admin_ids:
        a = [
            types.InlineKeyboardButton('💳 Subscriptions', callback_data='subscription'),
            types.InlineKeyboardButton('📊 Statistics', callback_data='stats'),
            types.InlineKeyboardButton('🔒 Lock Bot' if not bot_locked else '🔓 Unlock Bot', callback_data='lock_bot' if not bot_locked else 'unlock_bot'),
            types.InlineKeyboardButton('📢 Broadcast', callback_data='broadcast'),
            types.InlineKeyboardButton('👑 Admin Panel', callback_data='admin_panel'),
            types.InlineKeyboardButton('🟢 Run All User Scripts', callback_data='run_all_scripts'),
            types.InlineKeyboardButton('🕓 Pending Script Approvals', callback_data='list_pending_scripts')
        ]
        markup.add(b[0])
        markup.add(b[1], b[2])
        markup.add(b[3], a[0])
        markup.add(a[1], a[3])
        markup.add(a[2], a[5])
        markup.add(a[6])
        markup.add(a[4])
        markup.add(b[4])
    else:
        markup.add(b[0])
        markup.add(b[1], b[2])
        markup.add(b[3])
        markup.add(types.InlineKeyboardButton('📊 Statistics', callback_data='stats'))
        markup.add(b[4])
    return markup

def create_reply_keyboard_main_menu(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    layout = ADMIN_COMMAND_BUTTONS_LAYOUT_USER_SPEC if user_id in admin_ids else COMMAND_BUTTONS_LAYOUT_USER_SPEC
    for row in layout:
        markup.add(*[types.KeyboardButton(t) for t in row])
    return markup

def create_control_buttons(owner_id, file_name, is_running=True):
    markup = types.InlineKeyboardMarkup(row_width=2)
    if is_running:
        markup.row(
            types.InlineKeyboardButton("🔴 Stop", callback_data=f'stop_{owner_id}_{file_name}'),
            types.InlineKeyboardButton("🔄 Restart", callback_data=f'restart_{owner_id}_{file_name}')
        )
        markup.row(
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f'delete_{owner_id}_{file_name}'),
            types.InlineKeyboardButton("📜 Logs", callback_data=f'logs_{owner_id}_{file_name}')
        )
    else:
        markup.row(
            types.InlineKeyboardButton("🟢 Start", callback_data=f'start_{owner_id}_{file_name}'),
            types.InlineKeyboardButton("🗑️ Delete", callback_data=f'delete_{owner_id}_{file_name}')
        )
        markup.row(
            types.InlineKeyboardButton("📜 View Logs", callback_data=f'logs_{owner_id}_{file_name}')
        )
    markup.add(types.InlineKeyboardButton("🔙 Back to Files", callback_data='check_files'))
    return markup

def create_admin_panel():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('➕ Add Admin', callback_data='add_admin'),
        types.InlineKeyboardButton('➖ Remove Admin', callback_data='remove_admin')
    )
    markup.row(types.InlineKeyboardButton('📋 List Admins', callback_data='list_admins'))
    markup.row(
        types.InlineKeyboardButton('🕓 Pending Users', callback_data='list_pending'),
        types.InlineKeyboardButton('✅ Approved Users', callback_data='list_approved')
    )
    markup.row(types.InlineKeyboardButton('🔙 Back to Main', callback_data='back_to_main'))
    return markup

def create_subscription_menu():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton('➕ Add Subscription', callback_data='add_subscription'),
        types.InlineKeyboardButton('➖ Remove Subscription', callback_data='remove_subscription')
    )
    markup.row(types.InlineKeyboardButton('🔍 Check Subscription', callback_data='check_subscription'))
    markup.row(types.InlineKeyboardButton('🔙 Back to Main', callback_data='back_to_main'))
    return markup

def _add_pending_script(pending_id, owner_id, file_name, file_type, user_folder, source_chat_id, source_message_id):
    pending_scripts[pending_id] = {
        'owner_id': owner_id, 'file_name': file_name, 'file_type': file_type,
        'user_folder': user_folder, 'source_chat_id': source_chat_id,
        'source_message_id': source_message_id, 'created': datetime.now()
    }
    try:
        owner_msg = bot.send_message(
            OWNER_ID,
            f"🆕 *Script Hosting Approval*\n\n👤 User ID: `{owner_id}`\n📄 File: `{file_name}`\n📦 Type: `{file_type}`\n⏰ Time: {datetime.now():%Y-%m-%d %H:%M:%S}\n\n*Choose an action:*",
            parse_mode='Markdown', reply_markup=_pending_script_kb(pending_id)
        )
        pending_scripts[pending_id]['owner_msg_id'] = owner_msg.message_id
    except Exception as e:
        logger.error(f"Failed to notify owner: {e}")

def _pending_script_kb(pending_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton("✅ Approve & Run", callback_data=f'approve_script_{pending_id}'),
        types.InlineKeyboardButton("❌ Reject", callback_data=f'reject_script_{pending_id}')
    )
    return markup

def approve_pending_script(pending_id, call):
    info = pending_scripts.get(pending_id)
    if not info:
        bot.answer_callback_query(call.id, "⚠️ Expired.", show_alert=True); return
    owner_id = info['owner_id']; file_name = info['file_name']
    file_type = info['file_type']; user_folder = info['user_folder']
    file_path = os.path.join(user_folder, file_name)
    pending_scripts.pop(pending_id, None)
    if not os.path.exists(file_path):
        bot.edit_message_text(f"⚠️ File `{file_name}` missing.", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        try: bot.send_message(owner_id, f"⚠️ Your file `{file_name}` missing on server.", parse_mode='Markdown')
        except Exception: pass
        return
    try:
        bot.edit_message_text(f"✅ *Approved:* `{file_name}` (User `{owner_id}`) — starting...", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
    except Exception: pass
    try:
        bot.send_message(owner_id, f"🎉 Your file `{file_name}` was *approved* and is now starting!", parse_mode='Markdown')
    except Exception: pass
    fake_msg = _FakeMessage(owner_id)
    if file_type == 'py':
        threading.Thread(target=run_script, args=(file_path, owner_id, user_folder, file_name, fake_msg)).start()
    elif file_type == 'js':
        threading.Thread(target=run_js_script, args=(file_path, owner_id, user_folder, file_name, fake_msg)).start()

def reject_pending_script(pending_id, call):
    info = pending_scripts.get(pending_id)
    if not info:
        bot.answer_callback_query(call.id, "⚠️ Expired.", show_alert=True); return
    owner_id = info['owner_id']; file_name = info['file_name']
    user_folder = info['user_folder']
    file_path = os.path.join(user_folder, file_name)
    log_path = _log_path_for(user_folder, file_name)
    pending_scripts.pop(pending_id, None)
    try:
        if os.path.exists(file_path): os.remove(file_path)
        if os.path.exists(log_path): os.remove(log_path)
    except Exception as e: logger.error(f"Reject delete err: {e}")
    remove_user_file_db(owner_id, file_name)
    try:
        bot.edit_message_text(f"❌ *Rejected:* `{file_name}` (User `{owner_id}`) — file deleted.", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
    except Exception: pass
    try:
        bot.send_message(owner_id, f"❌ Your file `{file_name}` was *rejected* and removed.", parse_mode='Markdown')
    except Exception: pass

class _FakeMessage:
    def __init__(self, user_id):
        self.chat = type('C', (), {'id': user_id})()
        self.message_id = 0
        self.from_user = type('U', (), {'id': user_id})()

_orig_reply_to = bot.reply_to
def _safe_reply_to(message, text, **kwargs):
    try:
        return _orig_reply_to(message, text, **kwargs)
    except Exception:
        try: return bot.send_message(message.chat.id, text, **kwargs)
        except Exception: return None
bot.reply_to = _safe_reply_to

def handle_zip_file(content, file_name_zip, message):
    user_id = message.from_user.id
    user_folder = get_user_folder(user_id)
    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp(prefix=f"user_{user_id}_zip_")
        zp = os.path.join(temp_dir, file_name_zip)
        with open(zp, 'wb') as f: f.write(content)
        with zipfile.ZipFile(zp, 'r') as z:
            for m in z.infolist():
                mp = os.path.abspath(os.path.join(temp_dir, m.filename))
                if not mp.startswith(os.path.abspath(temp_dir)):
                    raise zipfile.BadZipFile(f"Unsafe path: {m.filename}")
            z.extractall(temp_dir)
        items = os.listdir(temp_dir)
        pyf = [f for f in items if f.endswith('.py')]
        jsf = [f for f in items if f.endswith('.js')]
        req = 'requirements.txt' if 'requirements.txt' in items else None
        pkg = 'package.json' if 'package.json' in items else None
        if req:
            bot.reply_to(message, "🔄 Installing Python deps...")
            try:
                subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', os.path.join(temp_dir, req)],
                               capture_output=True, text=True, check=True, encoding='utf-8', errors='ignore')
                bot.reply_to(message, "✅ Python deps installed.")
            except subprocess.CalledProcessError as e:
                bot.reply_to(message, f"❌ pip failed"); return
        if pkg:
            bot.reply_to(message, "🔄 Installing Node deps...")
            try:
                subprocess.run(['npm', 'install'], capture_output=True, text=True, check=True, cwd=temp_dir, encoding='utf-8', errors='ignore')
                bot.reply_to(message, "✅ Node deps installed.")
            except FileNotFoundError:
                bot.reply_to(message, "❌ 'npm' not found."); return
            except subprocess.CalledProcessError as e:
                bot.reply_to(message, f"❌ npm failed"); return
        main_script = None; ft = None
        for p in ['main.py', 'bot.py', 'app.py']:
            if p in pyf: main_script = p; ft = 'py'; break
        if not main_script:
            for p in ['index.js', 'main.js', 'bot.js', 'app.js']:
                if p in jsf: main_script = p; ft = 'js'; break
        if not main_script:
            if pyf: main_script = pyf[0]; ft = 'py'
            elif jsf: main_script = jsf[0]; ft = 'js'
        if not main_script:
            bot.reply_to(message, "❌ No .py or .js found."); return
        for item in os.listdir(temp_dir):
            src = os.path.join(temp_dir, item)
            dst = os.path.join(user_folder, item)
            if os.path.isdir(dst): shutil.rmtree(dst)
            elif os.path.exists(dst): os.remove(dst)
            shutil.move(src, dst)
        save_user_file(user_id, main_script, ft)
        pending_id = f"{user_id}_{int(time.time()*1000)}"
        _add_pending_script(pending_id, user_id, main_script, ft, user_folder, message.chat.id, message.message_id)
        bot.reply_to(message, f"📨 File `{main_script}` sent to Owner for approval.", parse_mode='Markdown')
    except zipfile.BadZipFile as e:
        bot.reply_to(message, f"❌ Bad ZIP: {e}")
    except Exception as e:
        logger.error(f"zip err: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error: {e}")
    finally:
        if temp_dir and os.path.exists(temp_dir):
            try: shutil.rmtree(temp_dir)
            except Exception: pass

def handle_py_file(file_path, owner_id, user_folder, file_name, message):
    try:
        save_user_file(owner_id, file_name, 'py')
        pending_id = f"{owner_id}_{int(time.time()*1000)}"
        _add_pending_script(pending_id, owner_id, file_name, 'py', user_folder, message.chat.id, message.message_id)
        bot.reply_to(message, f"📨 Python file `{file_name}` sent to Owner for approval.", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"handle_py err: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error: {e}")

def handle_js_file(file_path, owner_id, user_folder, file_name, message):
    try:
        save_user_file(owner_id, file_name, 'js')
        pending_id = f"{owner_id}_{int(time.time()*1000)}"
        _add_pending_script(pending_id, owner_id, file_name, 'js', user_folder, message.chat.id, message.message_id)
        bot.reply_to(message, f"📨 JS file `{file_name}` sent to Owner for approval.", parse_mode='Markdown')
    except Exception as e:
        logger.error(f"handle_js err: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error: {e}")

def _send_approval_request_to_owner(new_user_id, username, first_name):
    uname = f"@{username}" if username else "(no username)"
    text = (f"🆕 *New User Approval*\n\n👤 {first_name}\n✳️ {uname}\n🆔 `{new_user_id}`\n⏰ {datetime.now():%Y-%m-%d %H:%M:%S}\n\nApprove?")
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton("✅ Approve", callback_data=f'approve_{new_user_id}'),
        types.InlineKeyboardButton("❌ Reject", callback_data=f'reject_{new_user_id}')
    )
    try: bot.send_message(OWNER_ID, text, parse_mode='Markdown', reply_markup=markup)
    except Exception as e: logger.error(f"Approval req err: {e}")

def _logic_send_welcome(message):
    user_id = message.from_user.id; chat_id = message.chat.id
    uname = message.from_user.first_name; uusername = message.from_user.username
    if bot_locked and user_id not in admin_ids:
        bot.send_message(chat_id, "⚠️ Bot locked."); return
    if user_id not in active_users:
        add_active_user(user_id)
        try:
            bot.send_message(OWNER_ID, f"🎉 New user!\n👤 {uname}\n✳️ @{uusername or 'N/A'}\n🆔 `{user_id}`", parse_mode='Markdown')
        except Exception: pass
    if not is_user_approved(user_id):
        if user_id not in pending_users:
            add_pending_user(user_id, uusername, uname)
            _send_approval_request_to_owner(user_id, uusername, uname)
        try:
            bot.send_message(chat_id, f"⏳ *Access Pending*\n\nHello {uname}, request sent to Owner.\n🆔 `{user_id}`", parse_mode='Markdown')
        except Exception: pass
        return
    limit = get_user_file_limit(user_id); current = get_user_file_count(user_id)
    ls = "Unlimited" if limit == float('inf') else str(limit)
    expiry_info = ""
    if user_id == OWNER_ID: us = "👑 Owner"
    elif user_id in admin_ids: us = "🛡️ Admin"
    elif user_id in user_subscriptions:
        exp = user_subscriptions[user_id].get('expiry')
        if exp and exp > datetime.now():
            us = "⭐ Premium"; expiry_info = f"\n⏳ Sub expires in {(exp-datetime.now()).days} days"
        else:
            us = "🆓 Free (Expired)"; remove_subscription_db(user_id)
    else: us = "🆓 Free User"
    text = (f"〽️ Welcome, {uname}!\n\n🆔 `{user_id}`\n✳️ `@{uusername or 'Not set'}`\n🔰 {us}{expiry_info}\n📁 Files: {current} / {ls}\n\n🤖 Upload `.py`/`.js`/`.zip` — Owner approves first.\n👇 Use buttons.")
    try:
        bot.send_message(chat_id, text, reply_markup=create_reply_keyboard_main_menu(user_id), parse_mode='Markdown')
    except Exception as e: logger.error(f"welcome err: {e}")

def _logic_updates_channel(message):
    m = types.InlineKeyboardMarkup()
    m.add(types.InlineKeyboardButton('📢 Updates Channel', url=UPDATE_CHANNEL))
    bot.reply_to(message, "Visit our channel:", reply_markup=m)

def _logic_upload_file(message):
    uid = message.from_user.id
    if bot_locked and uid not in admin_ids: bot.reply_to(message, "⚠️ Bot locked."); return
    if not is_user_approved(uid): bot.reply_to(message, "⏳ Waiting for approval."); return
    limit = get_user_file_limit(uid); cur = get_user_file_count(uid)
    if cur >= limit:
        ls = "Unlimited" if limit == float('inf') else str(limit)
        bot.reply_to(message, f"⚠️ Limit ({cur}/{ls})."); return
    bot.reply_to(message, "📤 Send `.py`, `.js`, or `.zip`.\n⚠️ File will be sent to Owner for approval.")

def _logic_check_files(message):
    uid = message.from_user.id
    if not is_user_approved(uid): bot.reply_to(message, "⏳ Waiting."); return
    files = user_files.get(uid, [])
    if not files: bot.reply_to(message, "📂 No files."); return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for fn, ft in sorted(files):
        running = is_bot_running(uid, fn)
        icon = "🟢 Running" if running else "🔴 Stopped"
        markup.add(types.InlineKeyboardButton(f"{fn} ({ft}) - {icon}", callback_data=f'file_{uid}_{fn}'))
    bot.reply_to(message, "📂 Your files:", reply_markup=markup)

def _logic_bot_speed(message):
    uid = message.from_user.id; chat_id = message.chat.id
    if not is_user_approved(uid): bot.reply_to(message, "⏳ Waiting."); return
    s = time.time(); w = bot.reply_to(message, "🏃 Testing...")
    try:
        bot.send_chat_action(chat_id, 'typing')
        rt = round((time.time() - s) * 1000, 2)
        st = "🔓 Unlocked" if not bot_locked else "🔒 Locked"
        if uid == OWNER_ID: lvl = "👑 Owner"
        elif uid in admin_ids: lvl = "🛡️ Admin"
        elif uid in user_subscriptions and user_subscriptions[uid].get('expiry', datetime.min) > datetime.now(): lvl = "⭐ Premium"
        else: lvl = "🆓 Free"
        bot.edit_message_text(f"⚡ Speed:\n⏱️ {rt} ms\n🚦 {st}\n👤 {lvl}", chat_id, w.message_id)
    except Exception: pass

def _logic_contact_owner(message):
    m = types.InlineKeyboardMarkup()
    m.add(types.InlineKeyboardButton('📞 Contact Owner', url=f'https://t.me/{YOUR_USERNAME.replace("@", "")}'))
    bot.reply_to(message, "Click to contact Owner:", reply_markup=m)

def _logic_subscriptions_panel(message):
    if message.from_user.id not in admin_ids: bot.reply_to(message, "⚠️ Admin only."); return
    bot.reply_to(message, "💳 Subscription Management", reply_markup=create_subscription_menu())

def _logic_statistics(message):
    uid = message.from_user.id
    if not is_user_approved(uid): bot.reply_to(message, "⏳ Waiting."); return
    tu = len(active_users); tf = sum(len(v) for v in user_files.values())
    rc = 0; ur = 0
    for k, info in list(bot_scripts.items()):
        try:
            oid = int(k.split('_', 1)[0])
            if is_bot_running(oid, info['file_name']):
                rc += 1
                if oid == uid: ur += 1
        except Exception: pass
    txt = f"📊 Statistics:\n\n👥 Users: {tu}\n📂 Files: {tf}\n🟢 Active Bots: {rc}\n"
    if uid in admin_ids:
        txt += f"🔒 Bot: {'🔴 Locked' if bot_locked else '🟢 Unlocked'}\n🕓 Pending Scripts: {len(pending_scripts)}\n🕓 Pending Users: {len(pending_users)}\n🤖 Your Running: {ur}"
    else: txt += f"🤖 Your Running: {ur}"
    bot.reply_to(message, txt)

def _logic_broadcast_init(message):
    if message.from_user.id not in admin_ids: bot.reply_to(message, "⚠️ Admin only."); return
    m = bot.reply_to(message, "📢 Send broadcast. /cancel to abort.")
    bot.register_next_step_handler(m, process_broadcast_message)

def _logic_toggle_lock_bot(message):
    if message.from_user.id not in admin_ids: bot.reply_to(message, "⚠️ Admin only."); return
    global bot_locked
    bot_locked = not bot_locked
    bot.reply_to(message, f"🔒 Bot {'locked' if bot_locked else 'unlocked'}.")

def _logic_admin_panel(message):
    if message.from_user.id not in admin_ids: bot.reply_to(message, "⚠️ Admin only."); return
    bot.reply_to(message, "👑 Admin Panel", reply_markup=create_admin_panel())

def _logic_list_pending_scripts(message):
    if message.from_user.id not in admin_ids: bot.reply_to(message, "⚠️ Admin only."); return
    if not pending_scripts: bot.reply_to(message, "✅ No pending scripts."); return
    text = "🕓 *Pending Script Approvals:*\n\n"
    for pid, info in list(pending_scripts.items()):
        text += f"• `{info['file_name']}` ({info['file_type']}) — User `{info['owner_id']}`\n"
    bot.reply_to(message, text, parse_mode='Markdown')

def _logic_list_pending_users(message):
    if message.from_user.id not in admin_ids: bot.reply_to(message, "⚠️ Admin only."); return
    if not pending_users: bot.reply_to(message, "✅ No pending users."); return
    txt = "🕓 *Pending Users:*\n\n"
    for uid, info in pending_users.items():
        txt += f"• `{uid}` - {info.get('first_name','')} (@{info.get('username','') or 'N/A'})\n"
    bot.reply_to(message, txt + "\nUse /approve <id> or /reject <id>.", parse_mode='Markdown')

def _logic_run_all_scripts(message_or_call):
    if isinstance(message_or_call, telebot.types.Message):
        aid = message_or_call.from_user.id; chat = message_or_call.chat.id
        reply = lambda t, **k: bot.reply_to(message_or_call, t, **k)
        mobj = message_or_call
    elif isinstance(message_or_call, telebot.types.CallbackQuery):
        aid = message_or_call.from_user.id; chat = message_or_call.message.chat.id
        bot.answer_callback_query(message_or_call.id)
        reply = lambda t, **k: bot.send_message(chat, t, **k)
        mobj = message_or_call.message
    else: return
    if aid not in admin_ids: reply("⚠️ Admin only."); return
    reply("⏳ Starting all user scripts...")
    started = 0; users = 0; skipped = 0
    snapshot = dict(user_files)
    for tuid, files in snapshot.items():
        if not files: continue
        users += 1
        folder = get_user_folder(tuid)
        for fn, ft in files:
            if not is_bot_running(tuid, fn):
                path = os.path.join(folder, fn)
                if os.path.exists(path):
                    try:
                        if ft == 'py':
                            threading.Thread(target=run_script, args=(path, tuid, folder, fn, mobj)).start()
                            started += 1
                        elif ft == 'js':
                            threading.Thread(target=run_js_script, args=(path, tuid, folder, fn, mobj)).start()
                            started += 1
                        else: skipped += 1
                        time.sleep(0.7)
                    except Exception: skipped += 1
                else: skipped += 1
    reply(f"✅ Started: {started}\n👥 Users: {users}\n⚠️ Skipped: {skipped}")

@bot.message_handler(commands=['start', 'help'])
def cmd_start(m): _logic_send_welcome(m)

@bot.message_handler(commands=['status', 'statistics'])
def cmd_status(m): _logic_statistics(m)

BUTTON_TEXT_TO_LOGIC = {
    "📢 Updates Channel": _logic_updates_channel,
    "📤 Upload File": _logic_upload_file,
    "📂 Check Files": _logic_check_files,
    "⚡ Bot Speed": _logic_bot_speed,
    "📞 Contact Owner": _logic_contact_owner,
    "📊 Statistics": _logic_statistics,
    "💳 Subscriptions": _logic_subscriptions_panel,
    "📢 Broadcast": _logic_broadcast_init,
    "🔒 Lock Bot": _logic_toggle_lock_bot,
    "🟢 Run All Code": _logic_run_all_scripts,
    "🕓 Pending Scripts": _logic_list_pending_scripts,
    "👑 Admin Panel": _logic_admin_panel,
}

@bot.message_handler(func=lambda m: m.text in BUTTON_TEXT_TO_LOGIC)
def handle_btn_text(m):
    fn = BUTTON_TEXT_TO_LOGIC.get(m.text)
    if fn: fn(m)

@bot.message_handler(commands=['updateschannel'])
def c_up(m): _logic_updates_channel(m)
@bot.message_handler(commands=['uploadfile'])
def c_ul(m): _logic_upload_file(m)
@bot.message_handler(commands=['checkfiles'])
def c_cf(m): _logic_check_files(m)
@bot.message_handler(commands=['botspeed'])
def c_bs(m): _logic_bot_speed(m)
@bot.message_handler(commands=['contactowner'])
def c_co(m): _logic_contact_owner(m)
@bot.message_handler(commands=['subscriptions'])
def c_sub(m): _logic_subscriptions_panel(m)
@bot.message_handler(commands=['broadcast'])
def c_bc(m): _logic_broadcast_init(m)
@bot.message_handler(commands=['lockbot'])
def c_lb(m): _logic_toggle_lock_bot(m)
@bot.message_handler(commands=['adminpanel'])
def c_ap(m): _logic_admin_panel(m)
@bot.message_handler(commands=['runningallcode'])
def c_rac(m): _logic_run_all_scripts(m)
@bot.message_handler(commands=['pending'])
def c_pd(m): _logic_list_pending_users(m)
@bot.message_handler(commands=['pendingscripts'])
def c_ps(m): _logic_list_pending_scripts(m)

@bot.message_handler(commands=['approve'])
def c_app(m):
    if m.from_user.id not in admin_ids: bot.reply_to(m, "⚠️ Admin only."); return
    parts = m.text.split()
    if len(parts) != 2: bot.reply_to(m, "Usage: /approve <id>"); return
    try: uid = int(parts[1])
    except ValueError: bot.reply_to(m, "Invalid."); return
    if uid in approved_users: bot.reply_to(m, "Already."); return
    add_approved_user(uid)
    bot.reply_to(m, f"✅ `{uid}` approved.", parse_mode='Markdown')
    try: bot.send_message(uid, "🎉 Approved! Send /start.")
    except Exception: pass

@bot.message_handler(commands=['reject'])
def c_rej(m):
    if m.from_user.id not in admin_ids: bot.reply_to(m, "⚠️ Admin only."); return
    parts = m.text.split()
    if len(parts) != 2: bot.reply_to(m, "Usage: /reject <id>"); return
    try: uid = int(parts[1])
    except ValueError: bot.reply_to(m, "Invalid."); return
    remove_pending_user(uid)
    bot.reply_to(m, f"❌ `{uid}` rejected.", parse_mode='Markdown')
    try: bot.send_message(uid, "❌ Rejected.")
    except Exception: pass

@bot.message_handler(commands=['ping'])
def cmd_ping(m):
    s = time.time()
    msg = bot.reply_to(m, "Pong!")
    bot.edit_message_text(f"Pong! {round((time.time()-s)*1000, 2)} ms", m.chat.id, msg.message_id)

@bot.message_handler(content_types=['document'])
def handle_file_upload_doc(message):
    uid = message.from_user.id; chat = message.chat.id
    doc = message.document
    if bot_locked and uid not in admin_ids: bot.reply_to(message, "⚠️ Bot locked."); return
    if not is_user_approved(uid): bot.reply_to(message, "⏳ Waiting."); return
    limit = get_user_file_limit(uid); cur = get_user_file_count(uid)
    if cur >= limit:
        ls = "Unlimited" if limit == float('inf') else str(limit)
        bot.reply_to(message, f"⚠️ Limit ({cur}/{ls})."); return
    fn = doc.file_name
    if not fn: bot.reply_to(message, "⚠️ No filename."); return
    ext = os.path.splitext(fn)[1].lower()
    if ext not in ['.py', '.js', '.zip']: bot.reply_to(message, "⚠️ Only .py/.js/.zip"); return
    if doc.file_size > 20 * 1024 * 1024: bot.reply_to(message, "⚠️ Max 20MB."); return
    try:
        try:
            bot.forward_message(OWNER_ID, chat, message.message_id)
            bot.send_message(OWNER_ID, f"⬆️ '{fn}' from {message.from_user.first_name} (`{uid}`)", parse_mode='Markdown')
        except Exception: pass
        wait = bot.reply_to(message, f"⏳ Downloading `{fn}`...")
        info = bot.get_file(doc.file_id)
        content = bot.download_file(info.file_path)
        bot.edit_message_text("✅ Downloaded. Processing...", chat, wait.message_id)
        folder = get_user_folder(uid)
        if ext == '.zip':
            handle_zip_file(content, fn, message)
        else:
            path = os.path.join(folder, fn)
            with open(path, 'wb') as f: f.write(content)
            if ext == '.js': handle_js_file(path, uid, folder, fn, message)
            elif ext == '.py': handle_py_file(path, uid, folder, fn, message)
    except telebot.apihelper.ApiTelegramException as e:
        logger.error(f"TG API err: {e}", exc_info=True)
        if "file is too big" in str(e).lower(): bot.reply_to(message, "❌ Too large.")
        else: bot.reply_to(message, f"❌ TG err: {e}")
    except Exception as e:
        logger.error(f"upload err: {e}", exc_info=True)
        bot.reply_to(message, f"❌ Error: {e}")

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    uid = call.from_user.id; data = call.data
    if data.startswith('approve_script_'):
        if uid != OWNER_ID and uid not in admin_ids:
            bot.answer_callback_query(call.id, "⚠️ Owner/Admin only.", show_alert=True); return
        pid = data[len('approve_script_'):]
        bot.answer_callback_query(call.id, "✅ Approving...")
        approve_pending_script(pid, call); return
    if data.startswith('reject_script_'):
        if uid != OWNER_ID and uid not in admin_ids:
            bot.answer_callback_query(call.id, "⚠️ Owner/Admin only.", show_alert=True); return
        pid = data[len('reject_script_'):]
        bot.answer_callback_query(call.id, "❌ Rejecting...")
        reject_pending_script(pid, call); return
    if data.startswith('approve_'):
        if uid != OWNER_ID: bot.answer_callback_query(call.id, "⚠️ Owner only.", show_alert=True); return
        try: target = int(data.split('_', 1)[1])
        except ValueError: bot.answer_callback_query(call.id, "Bad."); return
        add_approved_user(target)
        bot.answer_callback_query(call.id, f"✅ Approved {target}", show_alert=True)
        try: bot.edit_message_text(f"✅ User `{target}` APPROVED.", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        except Exception: pass
        try: bot.send_message(target, "🎉 Approved! Send /start.")
        except Exception: pass
        return
    if data.startswith('reject_'):
        if uid != OWNER_ID: bot.answer_callback_query(call.id, "⚠️ Owner only.", show_alert=True); return
        try: target = int(data.split('_', 1)[1])
        except ValueError: bot.answer_callback_query(call.id, "Bad."); return
        remove_pending_user(target)
        bot.answer_callback_query(call.id, f"❌ Rejected {target}", show_alert=True)
        try: bot.edit_message_text(f"❌ User `{target}` REJECTED.", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        except Exception: pass
        try: bot.send_message(target, "❌ Rejected.")
        except Exception: pass
        return
    if bot_locked and uid not in admin_ids and data not in ['back_to_main', 'speed', 'stats']:
        bot.answer_callback_query(call.id, "⚠️ Bot locked.", show_alert=True); return
    try:
        if data == 'upload': upload_callback(call)
        elif data == 'check_files': check_files_callback(call)
        elif data.startswith('file_'): file_control_callback(call)
        elif data.startswith('start_'): start_bot_callback(call)
        elif data.startswith('stop_'): stop_bot_callback(call)
        elif data.startswith('restart_'): restart_bot_callback(call)
        elif data.startswith('delete_'): delete_bot_callback(call)
        elif data.startswith('logs_'): logs_bot_callback(call)
        elif data == 'speed': speed_callback(call)
        elif data == 'back_to_main': back_to_main_callback(call)
        elif data.startswith('confirm_broadcast_'): handle_confirm_broadcast(call)
        elif data == 'cancel_broadcast': handle_cancel_broadcast(call)
        elif data == 'subscription': admin_cb(call, subscription_management_callback)
        elif data == 'stats': stats_callback(call)
        elif data == 'lock_bot': admin_cb(call, lock_bot_callback)
        elif data == 'unlock_bot': admin_cb(call, unlock_bot_callback)
        elif data == 'run_all_scripts': admin_cb(call, run_all_scripts_callback)
        elif data == 'broadcast': admin_cb(call, broadcast_init_callback)
        elif data == 'admin_panel': admin_cb(call, admin_panel_callback)
        elif data == 'add_admin': owner_cb(call, add_admin_init_callback)
        elif data == 'remove_admin': owner_cb(call, remove_admin_init_callback)
        elif data == 'list_admins': admin_cb(call, list_admins_callback)
        elif data == 'list_pending': admin_cb(call, list_pending_callback)
        elif data == 'list_approved': admin_cb(call, list_approved_callback)
        elif data == 'list_pending_scripts': admin_cb(call, list_pending_scripts_callback)
        elif data == 'add_subscription': admin_cb(call, add_subscription_init_callback)
        elif data == 'remove_subscription': admin_cb(call, remove_subscription_init_callback)
        elif data == 'check_subscription': admin_cb(call, check_subscription_init_callback)
        else: bot.answer_callback_query(call.id, "Unknown.")
    except Exception as e:
        logger.error(f"callback err '{data}': {e}", exc_info=True)
        try: bot.answer_callback_query(call.id, "Error.", show_alert=True)
        except Exception: pass

def admin_cb(call, fn):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Admin required.", show_alert=True); return
    fn(call)

def owner_cb(call, fn):
    if call.from_user.id != OWNER_ID:
        bot.answer_callback_query(call.id, "⚠️ Owner required.", show_alert=True); return
    fn(call)

def upload_callback(call):
    uid = call.from_user.id
    if not is_user_approved(uid): bot.answer_callback_query(call.id, "⏳ Waiting.", show_alert=True); return
    limit = get_user_file_limit(uid); cur = get_user_file_count(uid)
    if cur >= limit:
        ls = "Unlimited" if limit == float('inf') else str(limit)
        bot.answer_callback_query(call.id, f"⚠️ Limit ({cur}/{ls}).", show_alert=True); return
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "📤 Send `.py`, `.js`, or `.zip`.\n⚠️ Will be sent to Owner for approval.", parse_mode='Markdown')

def check_files_callback(call):
    uid = call.from_user.id; chat = call.message.chat.id
    if not is_user_approved(uid): bot.answer_callback_query(call.id, "⏳ Waiting.", show_alert=True); return
    files = user_files.get(uid, [])
    if not files:
        bot.answer_callback_query(call.id, "⚠️ No files.", show_alert=True)
        try:
            m = types.InlineKeyboardMarkup()
            m.add(types.InlineKeyboardButton("🔙 Back", callback_data='back_to_main'))
            bot.edit_message_text("📂 No files.", chat, call.message.message_id, reply_markup=m)
        except Exception: pass
        return
    bot.answer_callback_query(call.id)
    markup = types.InlineKeyboardMarkup(row_width=1)
    for fn, ft in sorted(files):
        running = is_bot_running(uid, fn)
        icon = "🟢" if running else "🔴"
        markup.add(types.InlineKeyboardButton(f"{icon} {fn} ({ft})", callback_data=f'file_{uid}_{fn}'))
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data='back_to_main'))
    try: bot.edit_message_text("📂 Your files:", chat, call.message.message_id, reply_markup=markup)
    except Exception: pass

def file_control_callback(call):
    try:
        _, oid_s, fn = call.data.split('_', 2)
        oid = int(oid_s); req = call.from_user.id
        if not (req == oid or req in admin_ids): bot.answer_callback_query(call.id, "⚠️ Denied.", show_alert=True); return
        files = user_files.get(oid, [])
        if not any(f[0] == fn for f in files): bot.answer_callback_query(call.id, "⚠️ Not found.", show_alert=True); return
        bot.answer_callback_query(call.id)
        running = is_bot_running(oid, fn)
        st = '🟢 Running' if running else '🔴 Stopped'
        ft = next((f[1] for f in files if f[0] == fn), '?')
        try:
            bot.edit_message_text(f"⚙️ `{fn}` ({ft}) of User `{oid}`\nStatus: {st}", call.message.chat.id, call.message.message_id, reply_markup=create_control_buttons(oid, fn, running), parse_mode='Markdown')
        except Exception: pass
    except Exception as e: logger.error(f"file_ctrl err: {e}")

def start_bot_callback(call):
    try:
        _, oid_s, fn = call.data.split('_', 2)
        oid = int(oid_s); req = call.from_user.id; chat = call.message.chat.id
        if not (req == oid or req in admin_ids): bot.answer_callback_query(call.id, "⚠️ Denied.", show_alert=True); return
        files = user_files.get(oid, [])
        info = next((f for f in files if f[0] == fn), None)
        if not info: bot.answer_callback_query(call.id, "⚠️ Not found.", show_alert=True); return
        ft = info[1]; folder = get_user_folder(oid); path = os.path.join(folder, fn)
        if not os.path.exists(path):
            bot.answer_callback_query(call.id, f"⚠️ Missing.", show_alert=True); remove_user_file_db(oid, fn); return
        if is_bot_running(oid, fn): bot.answer_callback_query(call.id, "⚠️ Already running.", show_alert=True); return
        bot.answer_callback_query(call.id, "⏳ Starting...")
        if ft == 'py': threading.Thread(target=run_script, args=(path, oid, folder, fn, call.message)).start()
        elif ft == 'js': threading.Thread(target=run_js_script, args=(path, oid, folder, fn, call.message)).start()
        time.sleep(1.5)
        running = is_bot_running(oid, fn)
        st = '🟢 Running' if running else '🟡 Starting/failed'
        try: bot.edit_message_text(f"⚙️ `{fn}` ({ft}) of User `{oid}`\nStatus: {st}", chat, call.message.message_id, reply_markup=create_control_buttons(oid, fn, running), parse_mode='Markdown')
        except Exception: pass
    except Exception as e: logger.error(f"start_cb err: {e}")

def stop_bot_callback(call):
    try:
        _, oid_s, fn = call.data.split('_', 2)
        oid = int(oid_s); req = call.from_user.id; chat = call.message.chat.id
        if not (req == oid or req in admin_ids): bot.answer_callback_query(call.id, "⚠️ Denied.", show_alert=True); return
        files = user_files.get(oid, [])
        info = next((f for f in files if f[0] == fn), None)
        if not info: bot.answer_callback_query(call.id, "⚠️ Not found.", show_alert=True); return
        ft = info[1]; key = f"{oid}_{fn}"
        if not is_bot_running(oid, fn):
            bot.answer_callback_query(call.id, "ℹ️ Already stopped.", show_alert=True)
            try: bot.edit_message_text(f"⚙️ `{fn}` ({ft}) of User `{oid}`\nStatus: 🔴 Stopped", chat, call.message.message_id, reply_markup=create_control_buttons(oid, fn, False), parse_mode='Markdown')
            except Exception: pass
            return
        bot.answer_callback_query(call.id, "⏳ Stopping...")
        i = bot_scripts.get(key)
        if i: kill_process_tree(i)
        bot_scripts.pop(key, None)
        try: bot.edit_message_text(f"⚙️ `{fn}` ({ft}) of User `{oid}`\nStatus: 🔴 Stopped", chat, call.message.message_id, reply_markup=create_control_buttons(oid, fn, False), parse_mode='Markdown')
        except Exception: pass
    except Exception as e: logger.error(f"stop_cb err: {e}")

def restart_bot_callback(call):
    try:
        _, oid_s, fn = call.data.split('_', 2)
        oid = int(oid_s); req = call.from_user.id; chat = call.message.chat.id
        if not (req == oid or req in admin_ids): bot.answer_callback_query(call.id, "⚠️ Denied.", show_alert=True); return
        files = user_files.get(oid, [])
        info = next((f for f in files if f[0] == fn), None)
        if not info: bot.answer_callback_query(call.id, "⚠️ Not found.", show_alert=True); return
        ft = info[1]; folder = get_user_folder(oid); path = os.path.join(folder, fn); key = f"{oid}_{fn}"
        if not os.path.exists(path):
            bot.answer_callback_query(call.id, f"⚠️ Missing.", show_alert=True); remove_user_file_db(oid, fn); bot_scripts.pop(key, None); return
        bot.answer_callback_query(call.id, "⏳ Restarting...")
        if is_bot_running(oid, fn):
            i = bot_scripts.get(key)
            if i: kill_process_tree(i)
            bot_scripts.pop(key, None)
            time.sleep(1.5)
        if ft == 'py': threading.Thread(target=run_script, args=(path, oid, folder, fn, call.message)).start()
        elif ft == 'js': threading.Thread(target=run_js_script, args=(path, oid, folder, fn, call.message)).start()
        time.sleep(1.5)
        running = is_bot_running(oid, fn)
        st = '🟢 Running' if running else '🟡 Starting/failed'
        try: bot.edit_message_text(f"⚙️ `{fn}` ({ft}) of User `{oid}`\nStatus: {st}", chat, call.message.message_id, reply_markup=create_control_buttons(oid, fn, running), parse_mode='Markdown')
        except Exception: pass
    except Exception as e: logger.error(f"restart_cb err: {e}")

def delete_bot_callback(call):
    try:
        _, oid_s, fn = call.data.split('_', 2)
        oid = int(oid_s); req = call.from_user.id; chat = call.message.chat.id
        if not (req == oid or req in admin_ids): bot.answer_callback_query(call.id, "⚠️ Denied.", show_alert=True); return
        files = user_files.get(oid, [])
        if not any(f[0] == fn for f in files): bot.answer_callback_query(call.id, "⚠️ Not found.", show_alert=True); return
        bot.answer_callback_query(call.id, "🗑️ Deleting...")
        key = f"{oid}_{fn}"
        if is_bot_running(oid, fn):
            i = bot_scripts.get(key)
            if i: kill_process_tree(i)
            bot_scripts.pop(key, None)
            time.sleep(0.5)
        folder = get_user_folder(oid); path = os.path.join(folder, fn); log = _log_path_for(folder, fn)
        if os.path.exists(path):
            try: os.remove(path)
            except Exception: pass
        if os.path.exists(log):
            try: os.remove(log)
            except Exception: pass
        remove_user_file_db(oid, fn)
        try: bot.edit_message_text(f"🗑️ Deleted `{fn}`.", chat, call.message.message_id, parse_mode='Markdown')
        except Exception: pass
    except Exception as e: logger.error(f"del_cb err: {e}")

def logs_bot_callback(call):
    try:
        _, oid_s, fn = call.data.split('_', 2)
        oid = int(oid_s); req = call.from_user.id; chat = call.message.chat.id
        if not (req == oid or req in admin_ids): bot.answer_callback_query(call.id, "⚠️ Denied.", show_alert=True); return
        folder = get_user_folder(oid); log = _log_path_for(folder, fn)
        if not os.path.exists(log): bot.answer_callback_query(call.id, "⚠️ No logs.", show_alert=True); return
        bot.answer_callback_query(call.id)
        sz = os.path.getsize(log); mx = 100; msg_mx = 4096
        if sz == 0: content = "(empty)"
        elif sz > mx * 1024:
            with open(log, 'rb') as f:
                f.seek(-mx * 1024, os.SEEK_END)
                content = f"(Last {mx} KB)\n...\n" + f.read().decode('utf-8', errors='ignore')
        else:
            with open(log, 'r', encoding='utf-8', errors='ignore') as f: content = f.read()
        if len(content) > msg_mx: content = "...\n" + content[-msg_mx:]
        if not content.strip(): content = "(no content)"
        bot.send_message(chat, f"📜 Logs `{fn}` (User `{oid}`):\n```\n{content}\n```", parse_mode='Markdown')
    except Exception as e: logger.error(f"logs_cb err: {e}")

def speed_callback(call):
    uid = call.from_user.id; chat = call.message.chat.id; s = time.time()
    try:
        bot.edit_message_text("🏃 Testing...", chat, call.message.message_id)
        bot.send_chat_action(chat, 'typing')
        rt = round((time.time() - s) * 1000, 2)
        st = "🔓 Unlocked" if not bot_locked else "🔒 Locked"
        if uid == OWNER_ID: lvl = "👑 Owner"
        elif uid in admin_ids: lvl = "🛡️ Admin"
        elif uid in user_subscriptions and user_subscriptions[uid].get('expiry', datetime.min) > datetime.now(): lvl = "⭐ Premium"
        else: lvl = "🆓 Free"
        bot.answer_callback_query(call.id)
        bot.edit_message_text(f"⚡ Speed:\n⏱️ {rt} ms\n🚦 {st}\n👤 {lvl}", chat, call.message.message_id, reply_markup=create_main_menu_inline(uid))
    except Exception as e: logger.error(f"speed_cb err: {e}")

def back_to_main_callback(call):
    uid = call.from_user.id; chat = call.message.chat.id
    if not is_user_approved(uid): bot.answer_callback_query(call.id, "⏳ Waiting.", show_alert=True); return
    limit = get_user_file_limit(uid); cur = get_user_file_count(uid)
    ls = "Unlimited" if limit == float('inf') else str(limit)
    expiry_info = ""
    if uid == OWNER_ID: us = "👑 Owner"
    elif uid in admin_ids: us = "🛡️ Admin"
    elif uid in user_subscriptions:
        exp = user_subscriptions[uid].get('expiry')
        if exp and exp > datetime.now():
            us = "⭐ Premium"; expiry_info = f"\n⏳ {((exp-datetime.now()).days)} days left"
        else: us = "🆓 Free (expired)"
    else: us = "🆓 Free"
    text = f"〽️ Welcome back, {call.from_user.first_name}!\n\n🆔 `{uid}`\n🔰 {us}{expiry_info}\n📁 Files: {cur} / {ls}"
    try:
        bot.answer_callback_query(call.id)
        bot.edit_message_text(text, chat, call.message.message_id, reply_markup=create_main_menu_inline(uid), parse_mode='Markdown')
    except Exception: pass

def subscription_management_callback(call):
    bot.answer_callback_query(call.id)
    try: bot.edit_message_text("💳 Subscription Management", call.message.chat.id, call.message.message_id, reply_markup=create_subscription_menu())
    except Exception: pass

def stats_callback(call):
    bot.answer_callback_query(call.id)
    _logic_statistics(call.message)
    try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=create_main_menu_inline(call.from_user.id))
    except Exception: pass

def lock_bot_callback(call):
    global bot_locked; bot_locked = True
    bot.answer_callback_query(call.id, "🔒 Locked.")
    try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=create_main_menu_inline(call.from_user.id))
    except Exception: pass

def unlock_bot_callback(call):
    global bot_locked; bot_locked = False
    bot.answer_callback_query(call.id, "🔓 Unlocked.")
    try: bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=create_main_menu_inline(call.from_user.id))
    except Exception: pass

def run_all_scripts_callback(call):
    _logic_run_all_scripts(call)

def broadcast_init_callback(call):
    bot.answer_callback_query(call.id)
    m = bot.send_message(call.message.chat.id, "📢 Send broadcast. /cancel to abort.")
    bot.register_next_step_handler(m, process_broadcast_message)

def process_broadcast_message(message):
    uid = message.from_user.id
    if uid not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    if message.text and message.text.lower() == '/cancel': bot.reply_to(message, "Cancelled."); return
    content = message.text
    if not content and not (message.photo or message.video or message.document or message.sticker or message.voice or message.audio):
        bot.reply_to(message, "⚠️ Empty.")
        m = bot.send_message(message.chat.id, "📢 Send or /cancel.")
        bot.register_next_step_handler(m, process_broadcast_message); return
    target = len(active_users)
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("✅ Confirm", callback_data=f"confirm_broadcast_{message.message_id}"),
        types.InlineKeyboardButton("❌ Cancel", callback_data="cancel_broadcast")
    )
    preview = (content[:1000].strip() if content else "(Media)")
    bot.reply_to(message, f"⚠️ Confirm broadcast to {target} users:\n\n```\n{preview}\n```", reply_markup=markup, parse_mode='Markdown')

def handle_confirm_broadcast(call):
    uid = call.from_user.id; chat = call.message.chat.id
    if uid not in admin_ids: bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True); return
    try:
        orig = call.message.reply_to_message
        if not orig: raise ValueError("Orig not found.")
        text = photo = video = None
        if orig.text: text = orig.text
        elif orig.photo: photo = orig.photo[-1].file_id
        elif orig.video: video = orig.video.file_id
        else: raise ValueError("No supported content.")
        bot.answer_callback_query(call.id, "🚀 Starting...")
        bot.edit_message_text(f"📢 Broadcasting to {len(active_users)} users...", chat, call.message.message_id, reply_markup=None)
        caption = orig.caption if (photo or video) else None
        threading.Thread(target=execute_broadcast, args=(text, photo, video, caption, chat)).start()
    except Exception as e:
        logger.error(f"conf bc err: {e}", exc_info=True)
        try: bot.edit_message_text(f"❌ Error: {e}", chat, call.message.message_id, reply_markup=None)
        except Exception: pass

def handle_cancel_broadcast(call):
    bot.answer_callback_query(call.id, "Cancelled.")
    try: bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception: pass

def execute_broadcast(text, photo, video, caption, admin_chat):
    sent = failed = blocked = 0; start = time.time()
    users = list(active_users); total = len(users)
    for i, uid in enumerate(users):
        try:
            if text: bot.send_message(uid, text, parse_mode='Markdown')
            elif photo: bot.send_photo(uid, photo, caption=caption, parse_mode='Markdown' if caption else None)
            elif video: bot.send_video(uid, video, caption=caption, parse_mode='Markdown' if caption else None)
            sent += 1
        except telebot.apihelper.ApiTelegramException as e:
            d = str(e).lower()
            if any(s in d for s in ["blocked", "deactivated", "chat not found", "kicked", "restricted"]): blocked += 1
            elif "flood" in d or "too many" in d:
                m = re.search(r"retry after (\d+)", d)
                w = int(m.group(1)) + 1 if m else 5
                time.sleep(w)
                try:
                    if text: bot.send_message(uid, text, parse_mode='Markdown')
                    elif photo: bot.send_photo(uid, photo, caption=caption)
                    elif video: bot.send_video(uid, video, caption=caption)
                    sent += 1
                except Exception: failed += 1
            else: failed += 1
        except Exception: failed += 1
        if (i + 1) % 25 == 0 and i < total - 1: time.sleep(1.5)
        elif i % 5 == 0: time.sleep(0.2)
    dur = round(time.time() - start, 2)
    res = f"📢 Broadcast Done!\n✅ Sent: {sent}\n❌ Failed: {failed}\n🚫 Blocked: {blocked}\n👥 Targets: {total}\n⏱️ {dur}s"
    try: bot.send_message(admin_chat, res)
    except Exception: pass

def admin_panel_callback(call):
    bot.answer_callback_query(call.id)
    try: bot.edit_message_text("👑 Admin Panel", call.message.chat.id, call.message.message_id, reply_markup=create_admin_panel())
    except Exception: pass

def list_pending_scripts_callback(call):
    bot.answer_callback_query(call.id)
    if not pending_scripts:
        try: bot.edit_message_text("✅ No pending script approvals.", call.message.chat.id, call.message.message_id, reply_markup=create_admin_panel())
        except Exception: pass
        return
    txt = "🕓 *Pending Script Approvals:*\n\n"
    markup = types.InlineKeyboardMarkup(row_width=2)
    for pid, info in list(pending_scripts.items()):
        txt += f"• `{info['file_name']}` ({info['file_type']}) — User `{info['owner_id']}`\n"
        markup.row(
            types.InlineKeyboardButton(f"✅ {info['file_name'][:15]}", callback_data=f'approve_script_{pid}'),
            types.InlineKeyboardButton(f"❌ {info['file_name'][:15]}", callback_data=f'reject_script_{pid}')
        )
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data='admin_panel'))
    try: bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode='Markdown')
    except Exception: pass

def add_admin_init_callback(call):
    bot.answer_callback_query(call.id)
    m = bot.send_message(call.message.chat.id, "👑 Enter User ID. /cancel to abort.")
    bot.register_next_step_handler(m, process_add_admin_id)

def process_add_admin_id(message):
    if message.from_user.id != OWNER_ID: bot.reply_to(message, "⚠️ Owner only."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Cancelled."); return
    try:
        nid = int(message.text.strip())
        if nid <= 0: raise ValueError()
        if nid == OWNER_ID: bot.reply_to(message, "Already Owner."); return
        if nid in admin_ids: bot.reply_to(message, "Already Admin."); return
        add_admin_db(nid)
        bot.reply_to(message, f"✅ `{nid}` promoted.", parse_mode='Markdown')
        try: bot.send_message(nid, "🎉 You're now Admin!")
        except Exception: pass
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid ID.")
        m = bot.send_message(message.chat.id, "👑 Enter ID or /cancel.")
        bot.register_next_step_handler(m, process_add_admin_id)

def remove_admin_init_callback(call):
    bot.answer_callback_query(call.id)
    m = bot.send_message(call.message.chat.id, "👑 Enter User ID. /cancel to abort.")
    bot.register_next_step_handler(m, process_remove_admin_id)

def process_remove_admin_id(message):
    if message.from_user.id != OWNER_ID: bot.reply_to(message, "⚠️ Owner only."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Cancelled."); return
    try:
        aid = int(message.text.strip())
        if aid == OWNER_ID: bot.reply_to(message, "Cannot remove Owner."); return
        if aid not in admin_ids: bot.reply_to(message, "Not Admin."); return
        if remove_admin_db(aid):
            bot.reply_to(message, f"✅ `{aid}` removed.", parse_mode='Markdown')
            try: bot.send_message(aid, "ℹ️ You are no longer Admin.")
            except Exception: pass
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid ID.")
        m = bot.send_message(message.chat.id, "👑 Enter ID or /cancel.")
        bot.register_next_step_handler(m, process_remove_admin_id)

def list_admins_callback(call):
    bot.answer_callback_query(call.id)
    try:
        lst = "\n".join(f"- `{a}` {'(Owner)' if a == OWNER_ID else ''}" for a in sorted(admin_ids))
        if not lst: lst = "(none)"
        bot.edit_message_text(f"👑 Admins:\n\n{lst}", call.message.chat.id, call.message.message_id, reply_markup=create_admin_panel(), parse_mode='Markdown')
    except Exception: pass

def list_pending_callback(call):
    bot.answer_callback_query(call.id)
    if not pending_users:
        try: bot.edit_message_text("✅ No pending users.", call.message.chat.id, call.message.message_id, reply_markup=create_admin_panel())
        except Exception: pass
        return
    txt = "🕓 Pending Users:\n\n"
    markup = types.InlineKeyboardMarkup(row_width=2)
    for uid, info in list(pending_users.items()):
        txt += f"• `{uid}` - {info.get('first_name','')} (@{info.get('username','') or 'N/A'})\n"
        markup.row(
            types.InlineKeyboardButton(f"✅ {uid}", callback_data=f'approve_{uid}'),
            types.InlineKeyboardButton(f"❌ {uid}", callback_data=f'reject_{uid}')
        )
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data='admin_panel'))
    try: bot.edit_message_text(txt, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode='Markdown')
    except Exception: pass

def list_approved_callback(call):
    bot.answer_callback_query(call.id)
    try:
        lst = "\n".join(f"- `{a}`" for a in sorted(approved_users)) or "(none)"
        bot.edit_message_text(f"✅ Approved:\n\n{lst}", call.message.chat.id, call.message.message_id, reply_markup=create_admin_panel(), parse_mode='Markdown')
    except Exception: pass

def add_subscription_init_callback(call):
    bot.answer_callback_query(call.id)
    m = bot.send_message(call.message.chat.id, "💳 Enter ID & days (e.g. `123 30`). /cancel to abort.")
    bot.register_next_step_handler(m, process_add_subscription_details)

def process_add_subscription_details(message):
    if message.from_user.id not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Cancelled."); return
    try:
        parts = message.text.split()
        if len(parts) != 2: raise ValueError("Need ID and days")
        uid = int(parts[0]); days = int(parts[1])
        if uid <= 0 or days <= 0: raise ValueError("Positive values")
        cur = user_subscriptions.get(uid, {}).get('expiry')
        start = datetime.now()
        if cur and cur > start: start = cur
        new_exp = start + timedelta(days=days)
        save_subscription(uid, new_exp)
        bot.reply_to(message, f"✅ Sub for `{uid}` +{days} days. Expiry: {new_exp:%Y-%m-%d}", parse_mode='Markdown')
        try: bot.send_message(uid, f"🎉 Sub activated +{days} days. Expires {new_exp:%Y-%m-%d}.")
        except Exception: pass
    except ValueError as e:
        bot.reply_to(message, f"⚠️ Invalid: {e}")
        m = bot.send_message(message.chat.id, "💳 Enter ID & days or /cancel.")
        bot.register_next_step_handler(m, process_add_subscription_details)

def remove_subscription_init_callback(call):
    bot.answer_callback_query(call.id)
    m = bot.send_message(call.message.chat.id, "💳 Enter User ID. /cancel to abort.")
    bot.register_next_step_handler(m, process_remove_subscription_id)

def process_remove_subscription_id(message):
    if message.from_user.id not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Cancelled."); return
    try:
        uid = int(message.text.strip())
        if uid <= 0: raise ValueError()
        if uid not in user_subscriptions: bot.reply_to(message, f"⚠️ No sub for `{uid}`.", parse_mode='Markdown'); return
        remove_subscription_db(uid)
        bot.reply_to(message, f"✅ Sub removed for `{uid}`.", parse_mode='Markdown')
        try: bot.send_message(uid, "ℹ️ Your subscription was removed.")
        except Exception: pass
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid ID.")
        m = bot.send_message(message.chat.id, "💳 Enter ID or /cancel.")
        bot.register_next_step_handler(m, process_remove_subscription_id)

def check_subscription_init_callback(call):
    bot.answer_callback_query(call.id)
    m = bot.send_message(call.message.chat.id, "💳 Enter User ID. /cancel to abort.")
    bot.register_next_step_handler(m, process_check_subscription_id)

def process_check_subscription_id(message):
    if message.from_user.id not in admin_ids: bot.reply_to(message, "⚠️ Not authorized."); return
    if message.text.lower() == '/cancel': bot.reply_to(message, "Cancelled."); return
    try:
        uid = int(message.text.strip())
        if uid <= 0: raise ValueError()
        if uid in user_subscriptions:
            exp = user_subscriptions[uid].get('expiry')
            if exp:
                if exp > datetime.now():
                    dl = (exp - datetime.now()).days
                    bot.reply_to(message, f"✅ `{uid}` active. Expires {exp:%Y-%m-%d %H:%M} ({dl} days left).", parse_mode='Markdown')
                else:
                    bot.reply_to(message, f"⚠️ `{uid}` expired ({exp:%Y-%m-%d}).", parse_mode='Markdown')
                    remove_subscription_db(uid)
            else: bot.reply_to(message, f"⚠️ `{uid}` no expiry.", parse_mode='Markdown')
        else: bot.reply_to(message, f"ℹ️ No sub for `{uid}`.", parse_mode='Markdown')
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid ID.")
        m = bot.send_message(message.chat.id, "💳 Enter ID or /cancel.")
        bot.register_next_step_handler(m, process_check_subscription_id)

def cleanup():
    logger.warning("Shutdown cleanup...")
    for k in list(bot_scripts.keys()):
        if k in bot_scripts:
            logger.info(f"Stopping {k}")
            kill_process_tree(bot_scripts[k])
    logger.warning("Cleanup done.")
atexit.register(cleanup)

if __name__ == '__main__':
    logger.info("=" * 40 + f"\n🤖 Bot Starting...\n🔑 Owner: {OWNER_ID}\n" + "=" * 40)
    keep_alive()
    logger.info("🚀 Starting polling...")
    while True:
        try:
            bot.infinity_polling(logger_level=logging.INFO, timeout=60, long_polling_timeout=30)
        except requests.exceptions.ReadTimeout:
            logger.warning("ReadTimeout. 5s..."); time.sleep(5)
        except requests.exceptions.ConnectionError as ce:
            logger.error(f"ConnErr: {ce}. 15s..."); time.sleep(15)
        except Exception as e:
            logger.critical(f"💥 Polling err: {e}", exc_info=True)
            time.sleep(30)
        finally:
            time.sleep(1)
