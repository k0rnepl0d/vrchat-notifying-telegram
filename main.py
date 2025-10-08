# -*- coding: utf-8 -*-
# vrchat_tg_bot.py

"""
Телеграм-бот для уведомлений об онлайне пользователя VRChat.
"""

import os
import json
import time
import threading
import logging
import requests
import telebot
from dotenv import load_dotenv
from typing import Optional, Dict, Any, List

# ────────────────────────────────────────────────────────
# ENVIRONMENT
load_dotenv()

TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT_ID = os.getenv("TG_CHAT_ID")
COOKIES_FILE = os.getenv("COOKIES_FILE", "cookies.json")
USER_ID_FILE = os.getenv("USER_ID_FILE", "user_id.txt")
LOG_FILE = os.getenv("LOG_FILE", "vrchat_bot.log")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "300"))
PING_INTERVAL = int(os.getenv("PING_INTERVAL", "1800"))

if not TG_TOKEN:
    raise SystemExit("ERROR: set TG_TOKEN environment variable before running the bot")

# ────────────────────────────────────────────────────────
# LOGGING
log = logging.getLogger("vrchat_bot")
log.setLevel(logging.INFO)

_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

# file handler
fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
fh.setFormatter(_formatter)
log.addHandler(fh)

# console handler
ch = logging.StreamHandler()
ch.setFormatter(_formatter)
log.addHandler(ch)

# ────────────────────────────────────────────────────────
# TELEGRAM BOT
bot = telebot.TeleBot(TG_TOKEN, parse_mode=None)

# VRCHAT API CONFIG
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36 "
    "(VRChatStatusBot/1.0; +https://t.me/your_username)"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}

# ────────────────────────────────────────────────────────
_last_state_lock = threading.Lock()
_last_state: Optional[str] = None
_multi_upload_mode = {}

# ────────────────────────────────────────────────────────
# FILE UTILITIES
def save_json_file(path: str, obj: Any):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def load_json_file(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_text_file(path: str, text: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

def load_text_file(path: str) -> Optional[str]:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()

# ────────────────────────────────────────────────────────
# COOKIE / USER ID HANDLING
def load_cookies_for_requests() -> Dict[str, str]:
    if not os.path.exists(COOKIES_FILE):
        return {}
    try:
        data = load_json_file(COOKIES_FILE)
        if isinstance(data, list):
            return {c.get("name"): c.get("value") for c in data if "name" in c}
        elif isinstance(data, dict):
            return data
    except Exception:
        log.exception("Failed to load cookies file")
    return {}

def save_cookies_from_string(s: str) -> List[Dict[str, str]]:
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            save_json_file(COOKIES_FILE, parsed)
            return parsed
        if isinstance(parsed, dict):
            lst = [{"name": k, "value": str(v)} for k, v in parsed.items()]
            save_json_file(COOKIES_FILE, lst)
            return lst
    except Exception:
        pass

    try:
        result = []
        for p in [p.strip() for p in s.split(";") if "=" in p]:
            k, v = p.split("=", 1)
            result.append({"name": k.strip(), "value": v.strip()})
        if result:
            save_json_file(COOKIES_FILE, result)
            return result
    except Exception:
        pass
    raise ValueError("Не удалось распознать формат cookies.")

def load_user_id() -> Optional[str]:
    return load_text_file(USER_ID_FILE)

def save_user_id(uid: str):
    save_text_file(USER_ID_FILE, uid)

# ────────────────────────────────────────────────────────
# TELEGRAM COMMANDS
@bot.message_handler(commands=["help", "start"])
def cmd_help(msg):
    txt = (
        "VRChat статус-бот.\n\n"
        "/set_user_id <id> — задать пользователя\n"
        "/set_chat_id <id> — задать чат для уведомлений\n"
        "/start_cookies — начать ввод cookies\n"
        "/end_cookies — закончить ввод cookies\n"
        "/upload_cookies_file — отправить cookies.json\n"
        "/status — проверить вручную\n"
        "/show_config — показать пути файлов\n"
    )
    bot.reply_to(msg, txt)

@bot.message_handler(commands=["set_user_id"])
def cmd_set_user_id(msg):
    try:
        parts = msg.text.split(maxsplit=1)
        if len(parts) < 2:
            raise ValueError("Нужно: /set_user_id <user_id>")
        save_user_id(parts[1].strip())
        bot.reply_to(msg, "User ID сохранён.")
    except Exception as e:
        bot.reply_to(msg, f"Ошибка: {e}")

@bot.message_handler(commands=["set_chat_id"])
def cmd_set_chat_id(msg):
    try:
        parts = msg.text.split(maxsplit=1)
        if len(parts) < 2:
            raise ValueError("Нужно: /set_chat_id <chat_id>")
        save_text_file("chat_id.txt", parts[1].strip())
        bot.reply_to(msg, "Chat ID сохранён.")
    except Exception as e:
        bot.reply_to(msg, f"Ошибка: {e}")

@bot.message_handler(commands=["show_config"])
def cmd_show_config(msg):
    chat_id_display = TG_CHAT_ID or (load_text_file("chat_id.txt") or "not set")
    uid = load_user_id() or "not set"
    bot.reply_to(
        msg,
        f"Файлы:\n cookies -> {os.path.abspath(COOKIES_FILE)}\n"
        f"user_id -> {os.path.abspath(USER_ID_FILE)}\n"
        f"chat_id -> {chat_id_display}\ntracked user -> {uid}"
    )

@bot.message_handler(commands=["start_cookies"])
def cmd_start_cookies(msg):
    _multi_upload_mode[msg.chat.id] = {"buffer": []}
    bot.reply_to(msg, "Введи cookies построчно. Когда закончишь — /end_cookies")

@bot.message_handler(commands=["end_cookies"])
def cmd_end_cookies(msg):
    chat = msg.chat.id
    state = _multi_upload_mode.pop(chat, None)
    if not state:
        bot.reply_to(msg, "Режим не активен.")
        return
    full = "".join(state["buffer"])
    try:
        saved = save_cookies_from_string(full)
        bot.reply_to(msg, f"Cookies сохранены ({len(saved)}).")
    except Exception as e:
        bot.reply_to(msg, f"Ошибка: {e}")

@bot.message_handler(content_types=["text"])
def handle_text(msg):
    if msg.chat.id in _multi_upload_mode:
        _multi_upload_mode[msg.chat.id]["buffer"].append(msg.text)
        bot.reply_to(msg, "Принято. Отправь /end_cookies для завершения.")

@bot.message_handler(commands=["status"])
def cmd_status(msg):
    bot.reply_to(msg, check_status_blocking())

# ────────────────────────────────────────────────────────
# VRCHAT STATUS
def get_target_chat_id() -> Optional[int]:
    if TG_CHAT_ID:
        return int(TG_CHAT_ID)
    saved = load_text_file("chat_id.txt")
    return int(saved) if saved else None

def check_status_blocking() -> str:
    cookies = load_cookies_for_requests()
    uid = load_user_id()
    if not uid:
        return "User ID не задан."
    if not cookies:
        return "Cookies отсутствуют."
    url = f"https://api.vrchat.cloud/api/1/users/{uid}"
    s = requests.Session()
    s.headers.update(HEADERS)
    for k, v in cookies.items():
        s.cookies.set(k, v)
    r = s.get(url, timeout=10)
    if r.status_code == 200:
        data = r.json()
        return f"{data.get('displayName')} — {data.get('state')}"
    return f"Ошибка API {r.status_code}: {r.text[:200]}"

def status_checker_loop():
    global _last_state
    session = requests.Session()
    session.headers.update(HEADERS)
    while True:
        try:
            cookies = load_cookies_for_requests()
            uid = load_user_id()
            if not (cookies and uid):
                time.sleep(5)
                continue
            session.cookies.clear()
            for k, v in cookies.items():
                session.cookies.set(k, v)
            r = session.get(f"https://api.vrchat.cloud/api/1/users/{uid}", timeout=10)
            if r.status_code == 200:
                data = r.json()
                cur = data.get("state")
                with _last_state_lock:
                    if cur != _last_state:
                        _last_state = cur
                        tgt = get_target_chat_id()
                        if tgt:
                            bot.send_message(tgt, f"{data.get('displayName')} теперь {cur}")
                        log.info("State changed -> %s", cur)
            elif r.status_code == 403:
                log.warning("403 Forbidden — проверь User-Agent.")
            else:
                log.warning("VRChat API %s", r.status_code)
        except Exception:
            log.exception("Exception in status_checker_loop")
        time.sleep(POLL_INTERVAL)

def heartbeat_loop():
    while True:
        try:
            tgt = get_target_chat_id()
            msg = f"❤️ Heartbeat — бот жив ({time.strftime('%H:%M:%S')})"
            log.info(msg)
            if tgt:
                bot.send_message(tgt, msg)
        except Exception:
            log.exception("Heartbeat error")
        time.sleep(PING_INTERVAL)

# ────────────────────────────────────────────────────────
def run_bot():
    threading.Thread(target=status_checker_loop, daemon=True).start()
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    bot.infinity_polling(timeout=30, long_polling_timeout=30)

# ────────────────────────────────────────────────────────
if __name__ == "__main__":
    log.info("Starting VRChat TG bot...")
    run_bot()
