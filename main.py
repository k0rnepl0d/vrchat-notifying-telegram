# -*- coding: utf-8 -*-
#  vrchat_tg_bot.py

#  Телеграм-бот для уведомлений об онлайне пользователя VRChat.

#  Команды:
#  /help                - краткая помощь
#  /set_user_id <id>    - сохранить tracked user id
#  /set_chat_id <id>    - сохранить chat_id куда шлём уведомления (опционально)
#  /start_cookies       - начать режим приёма cookies (несколько сообщений)
#  /end_cookies         - закончить режим приёма cookies и сохранить
#  /upload_cookies_file - отправь файл (document) с cookies.json (бот примет автоматически)
#  /status              - проверить статус прямо сейчас
#  /show_config         - показать где хранятся файлы (без содержимого cookies)

import os
import json
import time
import threading
import logging
from typing import Optional, Dict, Any, List
import requests
import telebot
from dotenv import load_dotenv


load_dotenv()

# ---------- Настройки (можно через env) ----------
TG_TOKEN = os.getenv("TG_TOKEN")           # <- обязательно установи в окружении
TG_CHAT_ID = os.getenv("TG_CHAT_ID")       # <- можно установить или задать /set_chat_id
COOKIES_FILE = os.getenv("COOKIES_FILE", "cookies.json")
USER_ID_FILE = os.getenv("USER_ID_FILE", "user_id.txt")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "300"))  # сек
# -------------------------------------------------

if not TG_TOKEN:
    raise SystemExit("ERROR: set TG_TOKEN environment variable before running the bot")

bot = telebot.TeleBot(TG_TOKEN, parse_mode=None)

# Логирование
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("vrchat_bot")

# непосредственно вставляем гибридный User-Agent (пример)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 (VRChatStatusBot/1.0 https://t.me/your_username)"

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9"
}

# Состояния
_last_state_lock = threading.Lock()
_last_state: Optional[str] = None

# Буфер для multi-message upload cookies
_multi_upload_mode = {}
# structure: { chat_id: {"buffer": [str, ...], "started_by": username, "timestamp": float} }

# ---------- утилиты для файлов ----------
def save_json_file(path: str, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def load_json_file(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_text_file(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

def load_text_file(path: str) -> Optional[str]:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()

# ---------- cookies / user id handlers ----------
def load_cookies_for_requests() -> Dict[str, str]:
    #	Возвращает cookies как dict suitable for requests.
    #	Поддерживает формат:
    #	- JSON array [{ "name": "...", "value": "..." }, ...]
    #	- Или dict {"cookie_name": "value", ...}
    #
    if not os.path.exists(COOKIES_FILE):
        return {}
    try:
        data = load_json_file(COOKIES_FILE)
        if isinstance(data, list):
            return {c.get("name"): c.get("value") for c in data if "name" in c and "value" in c}
        elif isinstance(data, dict):
            return data
        else:
            log.warning("Unknown cookies.json structure; returning empty")
            return {}
    except Exception as e:
        log.exception("Failed to load cookies file")
        return {}

def save_cookies_from_string(s: str) -> List[Dict[str,str]]:
    #	Попытаться распарсить строку s как JSON. Если это dict -> сохранить.
    #	Если это строка 'name=value; name2=value2' -> конвертировать.
    #	Возвращает список cookie dicts.

    # try json parse
    try:
        parsed = json.loads(s)
        # acceptable: list of dicts or dict
        if isinstance(parsed, list):
            save_json_file(COOKIES_FILE, parsed)
            return parsed
        if isinstance(parsed, dict):
            # convert to list of {"name","value"} for compatibility
            listified = [{"name": k, "value": str(v)} for k, v in parsed.items()]
            save_json_file(COOKIES_FILE, listified)
            return listified
    except Exception:
        pass

    # try semi-colon cookie string: "key=val; key2=val2"
    try:
        parts = [p.strip() for p in s.split(";") if "=" in p]
        result = []
        for p in parts:
            k, v = p.split("=", 1)
            result.append({"name": k.strip(), "value": v.strip()})
        if result:
            save_json_file(COOKIES_FILE, result)
            return result
    except Exception:
        pass

    raise ValueError("Не удалось распознать формат cookies. Ожидаются JSON или строка cookie 'k=v; k2=v2'.")

def load_user_id() -> Optional[str]:
    return load_text_file(USER_ID_FILE)

def save_user_id(uid: str) -> None:
    save_text_file(USER_ID_FILE, uid)

# ---------- Telegram handlers ----------
@bot.message_handler(commands=["help", "start"])
def cmd_help(msg):
    txt = (
        "VRChat статус-бот.\n\n"
        "Команды:\n"
        "/set_user_id <id> - задать отслеживаемого пользователя\n"
        "/set_chat_id <id> - задать chat_id для уведомлений (если не задан)\n"
        "/start_cookies - начать вставлять cookies в нескольких сообщениях\n"
        "/end_cookies - закончить вставку и сохранить cookies\n"
        "/upload_cookies_file - отправь файл с cookies (document)\n"
        "/status - проверить статус пользователя сейчас\n"
        "/show_config - путь к файлам конфигурации\n"
    )
    bot.reply_to(msg, txt)

@bot.message_handler(commands=["set_user_id"])
def cmd_set_user_id(msg):
    try:
        parts = msg.text.split(maxsplit=1)
        if len(parts) < 2:
            raise ValueError("Нужно: /set_user_id <user_id>")
        uid = parts[1].strip()
        save_user_id(uid)
        bot.reply_to(msg, f"User ID сохранён: {uid}")
    except Exception as e:
        bot.reply_to(msg, f"Ошибка: {e}")

@bot.message_handler(commands=["set_chat_id"])
def cmd_set_chat_id(msg):
    try:
        parts = msg.text.split(maxsplit=1)
        if len(parts) < 2:
            raise ValueError("Нужно: /set_chat_id <chat_id>")
        cid = parts[1].strip()
        # save to environment substitute file (simple)
        # We'll save to a small file 'chat_id.txt'
        save_text_file("chat_id.txt", cid)
        bot.reply_to(msg, f"chat_id сохранён в chat_id.txt: {cid}")
    except Exception as e:
        bot.reply_to(msg, f"Ошибка: {e}")

@bot.message_handler(commands=["show_config"])
def cmd_show_config(msg):
    chat_id_display = TG_CHAT_ID or (load_text_file("chat_id.txt") or "not set")
    uid = load_user_id() or "not set"
    bot.reply_to(msg, f"Файлы:\n cookies -> {os.path.abspath(COOKIES_FILE)}\n user_id -> {os.path.abspath(USER_ID_FILE)}\n chat_id -> {chat_id_display}\n tracked user_id -> {uid}")

# ----- multi-message cookies upload -----
@bot.message_handler(commands=["start_cookies"])
def cmd_start_cookies(msg):
    chat = msg.chat.id
    _multi_upload_mode[chat] = {"buffer": [], "started_by": msg.from_user.username or msg.from_user.id, "timestamp": time.time()}
    bot.reply_to(msg, "Режим приёма cookies включён. Теперь отправь куски cookies в несколько сообщений. Когда закончишь — отправь /end_cookies")

@bot.message_handler(commands=["end_cookies"])
def cmd_end_cookies(msg):
    chat = msg.chat.id
    state = _multi_upload_mode.pop(chat, None)
    if not state:
        bot.reply_to(msg, "Режим приёма не был включён. Используй /start_cookies")
        return
    full = "".join(state["buffer"])
    try:
        saved = save_cookies_from_string(full)
        bot.reply_to(msg, f"Cookies успешно сохранены (записано {len(saved)} cookie).")
    except Exception as e:
        bot.reply_to(msg, f"Не удалось распознать cookies: {e}")

@bot.message_handler(content_types=["text"])
def handle_text(msg):
    chat = msg.chat.id
    # if in multi-upload mode, collect
    if chat in _multi_upload_mode:
        _multi_upload_mode[chat]["buffer"].append(msg.text)
        # optional: auto-finish if size > X or special terminator
        bot.reply_to(msg, "Принял кусок cookies. Продолжай или отправь /end_cookies для завершения.")
        return
    # otherwise ignore ordinary texts (or could respond)
    # keep silence for normal texts

# ----- file upload handler -----
@bot.message_handler(content_types=["document"])
def handle_document(msg):
    try:
        doc = msg.document
        file_info = bot.get_file(doc.file_id)
        file_path = file_info.file_path
        file_url = f"https://api.telegram.org/file/bot{bot.token}/{file_path}"
        r = requests.get(file_url, timeout=20)
        if r.status_code != 200:
            bot.reply_to(msg, "Не удалось загрузить файл из Telegram.")
            return
        content = r.content.decode("utf-8")
        # try parse and save
        try:
            parsed = json.loads(content)
            # accept both dict/list
            if isinstance(parsed, list):
                save_json_file(COOKIES_FILE, parsed)
                bot.reply_to(msg, f"Cookies файл сохранён ({len(parsed)} items).")
                return
            if isinstance(parsed, dict):
                # accept dict -> convert to list format
                listified = [{"name": k, "value": str(v)} for k, v in parsed.items()]
                save_json_file(COOKIES_FILE, listified)
                bot.reply_to(msg, f"Cookies файл (dict) сохранён ({len(listified)} items).")
                return
        except json.JSONDecodeError:
            # maybe it's key=value; try the helper
            try:
                saved = save_cookies_from_string(content)
                bot.reply_to(msg, f"Cookies успешно распознаны и сохранены ({len(saved)} items).")
                return
            except Exception as e:
                bot.reply_to(msg, f"Не удалось распознать формат файла: {e}")
                return
    except Exception as e:
        bot.reply_to(msg, f"Ошибка при обработке документа: {e}")

# ----- manual single-message cookies update -----
@bot.message_handler(commands=["upload_cookies"])
def cmd_upload_cookies(msg):
    try:
        parts = msg.text.split(maxsplit=1)
        if len(parts) < 2:
            raise ValueError("Нужно: /upload_cookies <json_or_cookie_string>")
        payload = parts[1]
        saved = save_cookies_from_string(payload)
        bot.reply_to(msg, f"Cookies сохранены ({len(saved)} items).")
    except Exception as e:
        bot.reply_to(msg, f"Ошибка: {e}")

@bot.message_handler(commands=["status"])
def cmd_status_check(msg):
    try:
        res = check_status_blocking()
        bot.reply_to(msg, res)
    except Exception as e:
        bot.reply_to(msg, f"Ошибка проверки: {e}")

# ---------- VRChat status checking ----------
def get_target_chat_id() -> Optional[int]:
    # priority: env TG_CHAT_ID, saved chat_id.txt
    if TG_CHAT_ID:
        try:
            return int(TG_CHAT_ID)
        except Exception:
            pass
    saved = load_text_file("chat_id.txt")
    if saved:
        try:
            return int(saved)
        except Exception:
            pass
    # fallback to the chat that started bot? Not available -- require explicit
    return None

def check_status_blocking() -> str:
    #	Однаразовая синхронная проверка (для команды /status).
    #	Возвращает текстовый результат.
    cookies = load_cookies_for_requests()
    uid = load_user_id()
    if not uid:
        return "User ID не задан. Используй /set_user_id <id>"
    if not cookies:
        return "Cookies не найдены. Задай /start_cookies ... /end_cookies или загрузите файл."
    url = f"https://api.vrchat.cloud/api/1/users/{uid}"
    try:
        s = requests.Session()
        s.headers.update(HEADERS)
        # attach cookies
        for k, v in cookies.items():
            s.cookies.set(k, v)
        r = s.get(url, timeout=10)
        if r.status_code != 200:
            return f"Ошибка API {r.status_code}: {r.text[:400]}"
        data = r.json()
        state = data.get("state", "unknown")
        display = data.get("displayName", "N/A")
        status_text = data.get("status", "")
        return f"User {display} ({uid}): state={state}, status={status_text}"
    except Exception as e:
        return f"Исключение при обращении к API: {e}"

def status_checker_loop():
    global _last_state
    session = requests.Session()
    # Устанавливаем заранее корректные заголовки (включая User-Agent)
    session.headers.update(HEADERS)

    target_chat = get_target_chat_id()
    if not target_chat:
        log.warning("target chat_id not configured. Notifications won't be sent. Set TG_CHAT_ID env or /set_chat_id.")
    while True:
        try:
            cookies = load_cookies_for_requests()
            uid = load_user_id()
            if not uid or not cookies:
                log.debug("No user_id or cookies yet; sleeping")
                time.sleep(5)
                continue
            session.cookies.clear()
            for k, v in cookies.items():
                session.cookies.set(k, v)

            url = f"https://api.vrchat.cloud/api/1/users/{uid}"
            r = session.get(url, timeout=15)

            # Дополнительная обработка 403 с понятным логом и подсказкой:
            if r.status_code == 403:
                # логируем тело, но не сохраняем большой dump в лог (отрезаем)
                body = (r.text[:1000] + '...') if len(r.text) > 1000 else r.text
                log.warning("VRChat API response 403: %s", body)
                # Если хочешь — отправляем предупреждение в Telegram (однократно)
                tgt = get_target_chat_id()
                if tgt:
                    try:
                        bot.send_message(tgt, "VRChat API вернул 403 — проверь User-Agent (должны быть app name/version/contact).")
                    except Exception:
                        log.exception("Не удалось отправить предупреждение в Telegram")
                # подождём и продолжим
                time.sleep(POLL_INTERVAL)
                continue

            if r.status_code == 200:
                data = r.json()
                current_state = data.get("state")
                with _last_state_lock:
                    previous = _last_state
                    if current_state != previous:
                        if current_state == "online":
                            status_line = data.get("status", "")
                            disp = data.get("displayName", uid)
                            msg = f"Игрок теперь ONLINE!\nИмя: {disp}\nСтатус: {status_line}"
                        else:
                            msg = f"Игрок теперь OFFLINE (state={current_state})"
                        log.info("State changed: %s -> %s", previous, current_state)
                        tgt = get_target_chat_id()
                        if tgt:
                            try:
                                bot.send_message(tgt, msg)
                            except Exception:
                                log.exception("Failed to send TG message")
                        else:
                            log.info("No target chat set; would send: %s", msg)
                        _last_state = current_state
            else:
                log.warning("VRChat API response %s: %s", r.status_code, r.text[:300])
        except Exception as e:
            log.exception("Exception in status_checker_loop")
        time.sleep(POLL_INTERVAL)


# ---------- heartbeat / self-ping ----------
PING_INTERVAL = int(os.getenv("PING_INTERVAL", "1800"))  # каждые 30 минут по умолчанию
_last_ping_time = 0

def heartbeat_loop():
    #	Отправляет 'бот жив' сообщение в лог (и, опционально, в Telegram),
    #	чтобы понимать, что процесс не завис.
    global _last_ping_time
    while True:
        try:
            tgt = get_target_chat_id()
            msg = f"?? Heartbeat: бот активен, проверка выполняется. ({time.strftime('%Y-%m-%d %H:%M:%S')})"
            log.info(msg)
            if tgt:
                try:
                    bot.send_message(tgt, msg)
                except Exception as e:
                    log.warning(f"Не удалось отправить heartbeat в Telegram: {e}")
        except Exception:
            log.exception("Ошибка в heartbeat_loop")
        time.sleep(PING_INTERVAL)


# ---------- run ----------
def run_bot():
    # стартуем проверку статуса
    t1 = threading.Thread(target=status_checker_loop, daemon=True)
    t1.start()
    log.info("Started status checker thread.")

    # стартуем heartbeat (если включён)
    t2 = threading.Thread(target=heartbeat_loop, daemon=True)
    t2.start()
    log.info("Started heartbeat thread (interval=%s sec).", PING_INTERVAL)

    # запускаем телеграм-поллинг
    bot.infinity_polling(timeout=30, long_polling_timeout=30)


if __name__ == "__main__":
    log.info("Starting VRChat TG bot...")
    run_bot()
