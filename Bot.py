import logging
import random
import string
import aiohttp
import os
import json
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramRetryAfter, TelegramAPIError
from datetime import datetime
import itertools
from asyncio import Semaphore
import re
from typing import Optional, Dict, List

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Токен бота
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# Инициализация
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# ⚡️ НАСТРОЙКИ
RATE_LIMITER = Semaphore(5)
CHECK_DELAY = 0.5
BATCH_SIZE = 5
CONNECTION_LIMIT = 50
MAX_RETRIES = 3

# Глобальная HTTP сессия
http_session: Optional[aiohttp.ClientSession] = None

# Пути к файлам
TAKEN_DB_FILE = "taken_usernames.json"
FREE_DB_FILE = "free_usernames.json"
BANNED_DB_FILE = "banned_usernames.json"
DEBUG_LOG_FILE = "debug_checks.log"

# Глобальные настройки
user_settings = {}
stop_flags = {}

# 🔥 ГЛАСНЫЕ И СОГЛАСНЫЕ
VOWELS = 'aeiou'
CONSONANTS = 'bcdfghjklmnpqrstvwxyz'

# ============ ФУНКЦИИ БАЗЫ ДАННЫХ ============

def load_db(file_path: str) -> Dict:
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Ошибка загрузки БД {file_path}: {e}")
            return {}
    return {}

def save_db(file_path: str, data: Dict):
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Ошибка сохранения БД {file_path}: {e}")

def add_to_taken_db(username: str, user_id=None, method="unknown", reason=""):
    db = load_db(TAKEN_DB_FILE)
    if username not in db:
        db[username] = {
            "checked_at": datetime.now().isoformat(),
            "checked_by": str(user_id) if user_id else "unknown",
            "method": method,
            "reason": reason
        }
        save_db(TAKEN_DB_FILE, db)
        return True
    return False

def add_to_free_db(username: str, user_id=None, method="unknown"):
    db = load_db(FREE_DB_FILE)
    if username not in db:
        db[username] = {
            "found_at": datetime.now().isoformat(),
            "found_by": str(user_id) if user_id else "unknown",
            "method": method,
            "verified": True
        }
        save_db(FREE_DB_FILE, db)
        return True
    return False

def add_to_banned_db(username: str, user_id=None, method="unknown"):
    db = load_db(BANNED_DB_FILE)
    if username not in db:
        db[username] = {
            "found_at": datetime.now().isoformat(),
            "found_by": str(user_id) if user_id else "unknown",
            "method": method,
            "status": "banned"
        }
        save_db(BANNED_DB_FILE, db)
        return True
    return False

def is_in_taken_db(username: str) -> bool:
    db = load_db(TAKEN_DB_FILE)
    return username in db

def is_in_free_db(username: str) -> bool:
    db = load_db(FREE_DB_FILE)
    return username in db

def is_in_banned_db(username: str) -> bool:
    db = load_db(BANNED_DB_FILE)
    return username in db

def log_debug(username: str, method: str, status: str, details=""):
    """Детальное логирование"""
    try:
        with open(DEBUG_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f"\n{'='*80}\n")
            f.write(f"Time: {datetime.now().isoformat()}\n")
            f.write(f"Username: @{username}\n")
            f.write(f"Method: {method}\n")
            f.write(f"Status: {status}\n")
            if details:
                f.write(f"Details:\n{details[:1000]}\n")
            f.write(f"{'='*80}\n")
    except:
        pass

# ============ НАСТРОЙКИ ПОЛЬЗОВАТЕЛЯ ============

def get_user_settings(user_id: int) -> Dict:
    if user_id not in user_settings:
        user_settings[user_id] = {
            "letter": "s",
            "repeat_count": 2,
            "use_full_alphabet": True,
            "beautiful_mode": False  # 🔥 НОВЫЙ РЕЖИМ
        }
    return user_settings[user_id]

def set_stop_flag(user_id: int, value: bool = True):
    """Установить флаг остановки для пользователя"""
    stop_flags[user_id] = value

def is_stopped(user_id: int) -> bool:
    """Проверить флаг остановки"""
    return stop_flags.get(user_id, False)

# ============ 🔥 ГЕНЕРАЦИЯ КРАСИВЫХ ЮЗЕРНЕЙМОВ ============

def generate_beautiful_username() -> str:
    """
    Генерация красивого юзернейма типа "fogot"
    Паттерн: согласная-гласная-согласная-гласная-согласная (CVСVC)
    Примеры: fogot, bebil, mesak, rikup
    """
    patterns = [
        'CVCVC',  # согласная-гласная-согласная-гласная-согласная (fogot)
        'VCCVC',  # гласная-согласная-согласная-гласная-согласная (oktet)
        'VCVCV',  # гласная-согласная-гласная-согласная-гласная (oniko)
        'CVCCV',  # согласная-гласная-согласная-согласная-гласная (biste)
        'CVCVV',  # согласная-гласная-согласная-гласная-гласная (bebea)
    ]
    
    pattern = random.choice(patterns)
    
    result = []
    for char in pattern:
        if char == 'C':
            result.append(random.choice(CONSONANTS))
        else:  # 'V'
            result.append(random.choice(VOWELS))
    
    return ''.join(result)

# ============ ОБЫЧНАЯ ГЕНЕРАЦИЯ ============

def generate_username(settings: Dict) -> str:
    """Генерация с повторяющейся буквой"""
    if settings["use_full_alphabet"]:
        letters = string.ascii_lowercase
    else:
        letters = 'abcdefghijkmnopqrstuvwxyz'
    
    main_letter = settings["letter"]
    repeat_count = settings["repeat_count"]
    
    if main_letter not in letters:
        main_letter = random.choice(letters)
    
    other_letters = [c for c in letters if c != main_letter]
    remaining_count = 5 - repeat_count
    
    if remaining_count <= 0 or remaining_count > 5:
        remaining_count = 3
    
    if len(other_letters) < remaining_count:
        chosen_others = [random.choice(other_letters) for _ in range(remaining_count)]
    else:
        chosen_others = random.sample(other_letters, remaining_count)
    
    random_position = random.randint(0, remaining_count)
    result = []
    result.extend(chosen_others[:random_position])
    result.extend([main_letter] * repeat_count)
    result.extend(chosen_others[random_position:])
    
    return ''.join(result)

def generate_examples(settings: Dict, count=4) -> List[str]:
    """Генерация примеров"""
    if settings.get("beautiful_mode", False):
        return [generate_beautiful_username() for _ in range(count)]
    else:
        return [generate_username(settings) for _ in range(count)]

def get_all_possible_usernames(settings: Dict) -> List[str]:
    """Генерация всех комбинаций"""
    
    # 🔥 ДЛЯ КРАСИВОГО РЕЖИМА - генерируем случайные (много комбинаций)
    if settings.get("beautiful_mode", False):
        # Генерируем 5000 случайных красивых юзернеймов
        usernames = set()
        while len(usernames) < 5000:
            usernames.add(generate_beautiful_username())
        return list(usernames)
    
    # ОБЫЧНЫЙ РЕЖИМ
    if settings["use_full_alphabet"]:
        letters = string.ascii_lowercase
    else:
        letters = 'abcdefghijkmnopqrstuvwxyz'
    
    main_letter = settings["letter"]
    repeat_count = settings["repeat_count"]
    
    if main_letter not in letters:
        return []
    
    other_letters = [c for c in letters if c != main_letter]
    remaining_count = 5 - repeat_count
    
    if remaining_count <= 0:
        return []
    
    all_usernames = set()
    max_combinations = 15000
    
    if len(other_letters) < remaining_count:
        for others in itertools.product(other_letters, repeat=remaining_count):
            for pos in range(remaining_count + 1):
                result = list(others[:pos]) + [main_letter] * repeat_count + list(others[pos:])
                all_usernames.add(''.join(result))
                if len(all_usernames) >= max_combinations:
                    break
            if len(all_usernames) >= max_combinations:
                break
    else:
        for others in itertools.permutations(other_letters, remaining_count):
            for pos in range(remaining_count + 1):
                result = list(others[:pos]) + [main_letter] * repeat_count + list(others[pos:])
                all_usernames.add(''.join(result))
                if len(all_usernames) >= max_combinations:
                    break
            if len(all_usernames) >= max_combinations:
                break
    
    return list(all_usernames)

# ============ HTTP СЕССИЯ ============

async def get_http_session() -> aiohttp.ClientSession:
    global http_session
    if http_session is None or http_session.closed:
        connector = aiohttp.TCPConnector(
            limit=CONNECTION_LIMIT,
            limit_per_host=20,
            ttl_dns_cache=300,
            enable_cleanup_closed=True
        )
        timeout = aiohttp.ClientTimeout(total=20, connect=5)
        http_session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout
        )
    return http_session

# ============ БЕЗОПАСНЫЕ ЗАПРОСЫ ============

async def safe_request_with_retry(func, *args, **kwargs):
    """Безопасный запрос с автоматическими повторами"""
    for attempt in range(MAX_RETRIES):
        try:
            return await func(*args, **kwargs)
        except TelegramRetryAfter as e:
            wait_time = e.retry_after + 1
            logging.warning(f"⏱ Rate limit! Жду {wait_time} сек...")
            await asyncio.sleep(wait_time)
            continue
        except TelegramAPIError as e:
            logging.error(f"❌ Telegram API error: {e}")
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)
                continue
            return None
        except aiohttp.ClientResponseError as e:
            if e.status == 429:
                wait_time = int(e.headers.get('Retry-After', 30))
                logging.warning(f"⏱ HTTP 429! Жду {wait_time} сек...")
                await asyncio.sleep(wait_time)
                continue
            elif e.status >= 500:
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(3)
                    continue
                return None
            else:
                logging.error(f"❌ HTTP {e.status}: {e.message}")
                return None
        except asyncio.TimeoutError:
            logging.warning(f"⏱ Timeout (попытка {attempt + 1}/{MAX_RETRIES})")
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(2)
                continue
            return None
        except Exception as e:
            logging.error(f"❌ Unexpected error: {e}")
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(2)
                continue
            return None
    
    return None

# ============ BOT API ============

async def check_username_bot_api_fast(username: str) -> Optional[bool]:
    """
    Проверка через Bot API
    """
    async def _check():
        session = await get_http_session()
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChat"
        params = {"chat_id": f"@{username}"}
        
        async with session.get(url, params=params) as response:
            data = await response.json()
            
            log_debug(username, "bot_api", f"Response: {data}")
            
            if data.get("ok") is True:
                logging.info(f"❌ Bot API: @{username} → ЗАНЯТ")
                return False
            else:
                error_desc = data.get("description", "").lower()
                
                # Детекция блокировки
                if "user is deactivated" in error_desc or "user_deactivated" in error_desc:
                    logging.info(f"🚫 Bot API: @{username} → ЗАБЛОКИРОВАН")
                    return "BANNED"
                elif "forbidden" in error_desc and "banned" in error_desc:
                    logging.info(f"🚫 Bot API: @{username} → ЗАБЛОКИРОВАН")
                    return "BANNED"
                
                # Проверки на свободность
                if "chat not found" in error_desc:
                    logging.info(f"✅ Bot API: @{username} → СВОБОДЕН")
                    return True
                elif "username is not occupied" in error_desc:
                    logging.info(f"✅ Bot API: @{username} → СВОБОДЕН")
                    return True
                elif "not found" in error_desc:
                    logging.info(f"✅ Bot API: @{username} → СВОБОДЕН")
                    return True
                elif "username is occupied" in error_desc:
                    logging.info(f"❌ Bot API: @{username} → ЗАНЯТ")
                    return False
                elif data.get("error_code") == 400:
                    # 400 часто означает что юзернейм не существует
                    logging.info(f"✅ Bot API: @{username} → СВОБОДЕН (400)")
                    return True
                else:
                    logging.warning(f"⚠️ Bot API: @{username} → НЕИЗВЕСТНО")
                    return None
    
    return await safe_request_with_retry(_check)

# ============ FRAGMENT ============

async def check_username_fragment_fast(username: str) -> Optional[bool]:
    """
    Проверка Fragment
    """
    async def _check():
        session = await get_http_session()
        url = f"https://fragment.com/username/{username}"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml",
        }
        
        async with session.get(url, headers=headers, allow_redirects=True) as response:
            status = response.status
            
            if status == 404:
                logging.info(f"✅ Fragment: @{username} → СВОБОДЕН (404)")
                return True
            
            if status == 200:
                html = await response.text()
                html_lower = html.lower()
                
                log_debug(username, "fragment", f"Status {status}", html[:2000])
                
                # Проверка на продажу/аукцион
                if re.search(r'<(?:button|a)[^>]*(?:place.*bid|buy.*now|make.*offer)', html_lower):
                    logging.info(f"🛒 Fragment: @{username} → НА ПРОДАЖЕ")
                    return False
                
                if 'tm-section-bid-button' in html or 'tm-section-countdown' in html:
                    logging.info(f"🛒 Fragment: @{username} → НА АУКЦИОНЕ")
                    return False
                
                if re.search(r'table-cell-value[^>]*>\s*(?:TON|USD|\$)\s*[\d,]+', html):
                    logging.info(f"🛒 Fragment: @{username} → НА ПРОДАЖЕ")
                    return False
                
                if re.search(r'(?:highest|current|minimum)\s+(?:bid|price)', html_lower):
                    logging.info(f"🛒 Fragment: @{username} → НА АУКЦИОНЕ")
                    return False
                
                # Детекция блокировки
                if 'banned' in html_lower or 'restricted' in html_lower or 'deactivated' in html_lower:
                    if 'status' in html_lower or 'username' in html_lower:
                        logging.info(f"🚫 Fragment: @{username} → ЗАБЛОКИРОВАН")
                        return "BANNED"
                
                # Проверка на занятость
                if 'sold' in html_lower or 'taken' in html_lower:
                    logging.info(f"❌ Fragment: @{username} → ЗАНЯТ")
                    return False
                
                if re.search(r'owned\s+by', html_lower):
                    logging.info(f"❌ Fragment: @{username} → ЗАНЯТ")
                    return False
                
                # Свободен
                if len(html) < 5000:
                    logging.info(f"✅ Fragment: @{username} → СВОБОДЕН")
                    return True
                
                if not any(marker in html_lower for marker in [
                    'auction', 'bid', 'price', 'sold', 'owner', 'ton', 'usd', '$'
                ]):
                    logging.info(f"✅ Fragment: @{username} → СВОБОДЕН")
                    return True
                
                logging.warning(f"⚠️ Fragment: @{username} → НЕОПРЕДЕЛЕННО")
                return None
            
            return None
    
    return await safe_request_with_retry(_check)

# ============ T.ME ============

async def check_username_web_fast(username: str) -> Optional[bool]:
    """
    Проверка t.me
    """
    async def _check():
        session = await get_http_session()
        url = f"https://t.me/{username}"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "text/html",
        }
        
        async with session.get(url, headers=headers, allow_redirects=True) as response:
            status = response.status
            
            if status == 404:
                logging.info(f"✅ t.me: @{username} → СВОБОДЕН (404)")
                return True
            
            if status == 200:
                html = await response.text()
                html_short = html[:3000]
                
                log_debug(username, "t.me", f"Status {status}", html_short)
                
                # Признаки занятости
                if any(marker in html_short for marker in [
                    'tgme_page_photo', 'tgme_page_title', 'tgme_page_description'
                ]):
                    logging.info(f"❌ t.me: @{username} → ЗАНЯТ")
                    return False
                
                # Признаки свободности
                if "if you have" in html_short.lower() and "telegram" in html_short.lower():
                    logging.info(f"✅ t.me: @{username} → СВОБОДЕН")
                    return True
                
                logging.warning(f"⚠️ t.me: @{username} → НЕОПРЕДЕЛЕННО")
                return None
            
            return None
    
    return await safe_request_with_retry(_check)

# ============ 🔥 ИСПРАВЛЕННАЯ ГЛАВНАЯ ПРОВЕРКА ============

async def check_username_parallel(username: str, user_id=None) -> bool:
    """
    ИСПРАВЛЕННАЯ ЛОГИКА:
    
    СВОБОДЕН только если:
    1. Bot API говорит "свободен" (TRUE)
    2. ИЛИ Fragment говорит "свободен" И нет явных "занят"
    3. И не заблокирован
    
    ЗАНЯТ если:
    1. Fragment на продаже (FALSE)
    2. ИЛИ Bot API говорит "занят" (FALSE)
    3. ИЛИ заблокирован (BANNED)
    """
    
    if is_in_free_db(username):
        return True
    if is_in_taken_db(username):
        return False
    if is_in_banned_db(username):
        return False
    
    async with RATE_LIMITER:
        await asyncio.sleep(CHECK_DELAY)
        
        logging.info(f"\n{'='*60}\n🔍 ПРОВЕРЯЮ: @{username}\n{'='*60}")
        
        # Запускаем все проверки параллельно
        results = await asyncio.gather(
            check_username_bot_api_fast(username),
            check_username_fragment_fast(username),
            check_username_web_fast(username),
            return_exceptions=True
        )
        
        bot_api_result, fragment_result, web_result = results
        
        # Обработка исключений
        if isinstance(bot_api_result, Exception):
            logging.error(f"Bot API exception: {bot_api_result}")
            bot_api_result = None
        if isinstance(fragment_result, Exception):
            logging.error(f"Fragment exception: {fragment_result}")
            fragment_result = None
        if isinstance(web_result, Exception):
            logging.error(f"Web exception: {web_result}")
            web_result = None
        
        logging.info(f"📊 РЕЗУЛЬТАТЫ:")
        logging.info(f"   Bot API: {bot_api_result}")
        logging.info(f"   Fragment: {fragment_result}")
        logging.info(f"   t.me: {web_result}")
        
        # 🔥 ПРОВЕРКА НА БЛОКИРОВКУ
        if bot_api_result == "BANNED" or fragment_result == "BANNED":
            add_to_banned_db(username, user_id, "detected")
            logging.info(f"🚫 ИТОГ: ЗАБЛОКИРОВАН ❌")
            return False
        
        # 🔥 ПРОВЕРКА НА ПРОДАЖУ (Fragment)
        if fragment_result is False:
            add_to_taken_db(username, user_id, "fragment", "На продаже")
            logging.info(f"🛒 ИТОГ: НА ПРОДАЖЕ ❌")
            return False
        
        # 🔥 СТРОГАЯ ПРОВЕРКА: Bot API = TRUE (приоритет)
        if bot_api_result is True:
            # Дополнительно проверяем что Fragment не против
            if fragment_result is not False:
                add_to_free_db(username, user_id, "bot_api")
                logging.info(f"🟢 ИТОГ: СВОБОДЕН ✅ (Bot API)")
                return True
        
        # 🔥 Fragment = TRUE и нет противоречий
        if fragment_result is True:
            # Проверяем что Bot API не против (не False)
            if bot_api_result is not False:
                add_to_free_db(username, user_id, "fragment")
                logging.info(f"🟢 ИТОГ: СВОБОДЕН ✅ (Fragment)")
                return True
        
        # 🔥 t.me = TRUE и другие не против
        if web_result is True:
            if bot_api_result is not False and fragment_result is not False:
                add_to_free_db(username, user_id, "web")
                logging.info(f"🟢 ИТОГ: СВОБОДЕН ✅ (t.me)")
                return True
        
        # Если Bot API или Fragment говорят "занят" - считаем занятым
        if bot_api_result is False or fragment_result is False or web_result is False:
            add_to_taken_db(username, user_id, "confirmed", "Подтвержден занятым")
            logging.info(f"🔴 ИТОГ: ЗАНЯТ ❌")
            return False
        
        # Все неопределенно (None) - считаем занятым (безопаснее)
        add_to_taken_db(username, user_id, "uncertain", "Неопределенный результат")
        logging.info(f"🔴 ИТОГ: ЗАНЯТ ❌ (неопределенно)")
        return False

# ============ БАТЧ-ПРОВЕРКА ============

async def check_usernames_batch(usernames: List[str], user_id=None) -> Dict[str, bool]:
    """Проверка пачки юзернеймов"""
    tasks = []
    for username in usernames:
        tasks.append(check_username_parallel(username, user_id))
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    output = {}
    for username, result in zip(usernames, results):
        if isinstance(result, Exception):
            logging.error(f"Batch error @{username}: {result}")
            output[username] = False
        else:
            output[username] = result
    
    return output

# ============ БЕЗОПАСНАЯ ОТПРАВКА ============

async def safe_send_message(chat_id: int, text: str, **kwargs):
    """Отправка с обработкой rate limit"""
    try:
        return await bot.send_message(chat_id, text, **kwargs)
    except TelegramRetryAfter as e:
        logging.warning(f"⏱ Rate limit! Жду {e.retry_after} сек")
        await asyncio.sleep(e.retry_after + 1)
        return await bot.send_message(chat_id, text, **kwargs)
    except Exception as e:
        logging.error(f"❌ Ошибка отправки: {e}")
        return None

async def safe_edit_message(message: types.Message, text: str, **kwargs):
    """Редактирование с обработкой rate limit"""
    try:
        return await message.edit_text(text, **kwargs)
    except TelegramRetryAfter as e:
        logging.warning(f"⏱ Rate limit! Жду {e.retry_after} сек")
        await asyncio.sleep(e.retry_after + 1)
        return await message.edit_text(text, **kwargs)
    except Exception as e:
        logging.error(f"❌ Ошибка редактирования: {e}")
        return None

# ============ 🔥 ОБНОВЛЕННЫЕ КЛАВИАТУРЫ ============

def get_settings_keyboard(user_id: int):
    settings = get_user_settings(user_id)
    
    beautiful_mode = settings.get("beautiful_mode", False)
    
    if beautiful_mode:
        # В режиме "Красивые" показываем только переключатель
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Просто красивые", callback_data="toggle_beautiful")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")]
            ]
        )
    else:
        alphabet_status = "✅" if settings["use_full_alphabet"] else "❌"
        
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="❌ Просто красивые", callback_data="toggle_beautiful")],
                [InlineKeyboardButton(text=f"{alphabet_status} Все буквы", callback_data="toggle_alphabet")],
                [InlineKeyboardButton(text=f"🔤 Буква: {settings['letter'].upper()}", callback_data="change_letter")],
                [InlineKeyboardButton(text=f"🔢 Повторений: {settings['repeat_count']}", callback_data="change_count")],
                [InlineKeyboardButton(text="🔄 Сбросить", callback_data="reset_settings")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")]
            ]
        )

def get_letter_keyboard():
    keyboard = []
    row = []
    for letter in string.ascii_lowercase:
        row.append(InlineKeyboardButton(text=letter.upper(), callback_data=f"set_letter_{letter}"))
        if len(row) == 7:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_settings")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

def get_count_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="2", callback_data="set_count_2"),
                InlineKeyboardButton(text="3", callback_data="set_count_3"),
                InlineKeyboardButton(text="4", callback_data="set_count_4")
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_settings")]
        ]
    )

def get_main_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✨ Генерировать", callback_data="generate_username")],
            [InlineKeyboardButton(text="🔍 Проверить все", callback_data="check_all")],
            [InlineKeyboardButton(text="⚙️ Настройки", callback_data="open_settings")],
            [InlineKeyboardButton(text="📊 Статистика", callback_data="show_stats")]
        ]
    )

# ============ КОМАНДЫ ============

@dp.message(Command("start"))
async def start_command(message: types.Message):
    user_name = message.from_user.first_name or "Пользователь"
    user_id = message.from_user.id
    
    get_user_settings(user_id)
    set_stop_flag(user_id, False)
    
    if not os.path.exists(TAKEN_DB_FILE):
        save_db(TAKEN_DB_FILE, {})
    if not os.path.exists(FREE_DB_FILE):
        save_db(FREE_DB_FILE, {})
    if not os.path.exists(BANNED_DB_FILE):
        save_db(BANNED_DB_FILE, {})
    
    await safe_send_message(
        message.chat.id,
        f"Привет, {user_name}! 👋\n\n"
        f"🎯 <b>Поиск СВОБОДНЫХ юзернеймов</b>\n\n"
        f"⚡️ <b>ВОЗМОЖНОСТИ:</b>\n"
        f"✨ Режим \"Просто красивые\" (fogot, bebil)\n"
        f"✅ Повторяющиеся буквы (ssabc)\n"
        f"✅ Игнорирует заблокированные\n"
        f"✅ Фильтрует продажи\n"
        f"✅ /stop для остановки\n\n"
        f"Выбери действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard()
    )

@dp.message(Command("stop"))
async def stop_command(message: types.Message):
    user_id = message.from_user.id
    set_stop_flag(user_id, True)
    
    await safe_send_message(
        message.chat.id,
        "🛑 <b>Остановка поиска...</b>",
        parse_mode=ParseMode.HTML
    )

@dp.message(Command("getdb"))
async def get_db_command(message: types.Message):
    user_id = message.from_user.id
    
    taken_db = load_db(TAKEN_DB_FILE)
    free_db = load_db(FREE_DB_FILE)
    banned_db = load_db(BANNED_DB_FILE)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    taken_file = f"taken_{user_id}_{timestamp}.json"
    free_file = f"free_{user_id}_{timestamp}.json"
    banned_file = f"banned_{user_id}_{timestamp}.json"
    
    try:
        with open(taken_file, 'w', encoding='utf-8') as f:
            json.dump(taken_db, f, indent=2, ensure_ascii=False)
        
        with open(free_file, 'w', encoding='utf-8') as f:
            json.dump(free_db, f, indent=2, ensure_ascii=False)
        
        with open(banned_file, 'w', encoding='utf-8') as f:
            json.dump(banned_db, f, indent=2, ensure_ascii=False)
        
        await safe_send_message(
            message.chat.id,
            f"📊 Свободных: {len(free_db)}\n"
            f"❌ Занятых: {len(taken_db)}\n"
            f"🚫 Заблокированных: {len(banned_db)}"
        )
        
        await message.answer_document(types.FSInputFile(free_file), caption=f"✅ Свободные ({len(free_db)})")
        await message.answer_document(types.FSInputFile(taken_file), caption=f"📁 Занятые ({len(taken_db)})")
        await message.answer_document(types.FSInputFile(banned_file), caption=f"🚫 Заблокированные ({len(banned_db)})")
        
        if os.path.exists(DEBUG_LOG_FILE):
            await message.answer_document(types.FSInputFile(DEBUG_LOG_FILE), caption=f"🔍 Debug")
        
        os.remove(taken_file)
        os.remove(free_file)
        os.remove(banned_file)
        
    except Exception as e:
        await safe_send_message(message.chat.id, f"❌ Ошибка: {e}")

@dp.message(Command("check"))
async def check_command(message: types.Message):
    """Проверка конкретного юзернейма"""
    try:
        username = message.text.split()[1].replace("@", "")
        
        msg = await safe_send_message(message.chat.id, f"🔍 Проверяю @{username}...")
        
        is_free = await check_username_parallel(username, message.from_user.id)
        
        if is_in_banned_db(username):
            await safe_edit_message(
                msg,
                f"🚫 <b>@{username} ЗАБЛОКИРОВАН</b>",
                parse_mode=ParseMode.HTML
            )
        elif is_free:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Забрать", url=f"https://t.me/{username}")],
                    [InlineKeyboardButton(text="🔎 Fragment", url=f"https://fragment.com/username/{username}")]
                ]
            )
            await safe_edit_message(
                msg,
                f"✅ <b>@{username} СВОБОДЕН!</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )
        else:
            await safe_edit_message(
                msg,
                f"❌ <b>@{username} ЗАНЯТ</b>",
                parse_mode=ParseMode.HTML
            )
    except IndexError:
        await safe_send_message(message.chat.id, "Использование: /check username")
    except Exception as e:
        await safe_send_message(message.chat.id, f"❌ Ошибка: {e}")

# ============ CALLBACK HANDLERS ============

@dp.callback_query(lambda c: c.data == "show_stats")
async def show_stats(callback_query: types.CallbackQuery):
    await callback_query.answer()
    
    taken_db = load_db(TAKEN_DB_FILE)
    free_db = load_db(FREE_DB_FILE)
    banned_db = load_db(BANNED_DB_FILE)
    
    await safe_edit_message(
        callback_query.message,
        f"📊 <b>Статистика</b>\n\n"
        f"✅ Свободных: {len(free_db)}\n"
        f"❌ Занятых: {len(taken_db)}\n"
        f"🚫 Заблокированных: {len(banned_db)}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📥 Скачать", callback_data="get_db")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")]
            ]
        )
    )

@dp.callback_query(lambda c: c.data == "open_settings")
async def open_settings(callback_query: types.CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    settings = get_user_settings(user_id)
    
    examples = generate_examples(settings, 4)
    examples_text = "\n".join([f"• <code>{ex}</code>" for ex in examples])
    
    if settings.get("beautiful_mode", False):
        mode_text = "✨ <b>Режим: Просто красивые</b>\n\nПаттерн: CVCVC (согласная-гласная-согласная-гласная-согласная)\nПримеры: fogot, bebil, mesak, rikup"
    else:
        mode_text = (
            f"📌 <b>Режим: Повторяющиеся буквы</b>\n\n"
            f"Буква: <b>{settings['letter'].upper()}</b>\n"
            f"Повторений: <b>{settings['repeat_count']}</b>\n"
            f"Алфавит: {'полный' if settings['use_full_alphabet'] else 'без l'}"
        )
    
    await safe_edit_message(
        callback_query.message,
        f"⚙️ <b>Настройки</b>\n\n"
        f"{mode_text}\n\n"
        f"📝 <b>Примеры:</b>\n{examples_text}",
        parse_mode=ParseMode.HTML,
        reply_markup=get_settings_keyboard(user_id)
    )

# 🔥 ПЕРЕКЛЮЧЕНИЕ РЕЖИМА "КРАСИВЫЕ"
@dp.callback_query(lambda c: c.data == "toggle_beautiful")
async def toggle_beautiful(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    settings = get_user_settings(user_id)
    
    settings["beautiful_mode"] = not settings.get("beautiful_mode", False)
    
    if settings["beautiful_mode"]:
        await callback_query.answer("✨ Режим 'Просто красивые' включен!", show_alert=True)
    else:
        await callback_query.answer("✅ Обычный режим", show_alert=False)
    
    await open_settings(callback_query)

@dp.callback_query(lambda c: c.data == "back_to_settings")
async def back_to_settings(callback_query: types.CallbackQuery):
    await open_settings(callback_query)

@dp.callback_query(lambda c: c.data == "toggle_alphabet")
async def toggle_alphabet(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    settings = get_user_settings(user_id)
    settings["use_full_alphabet"] = not settings["use_full_alphabet"]
    await callback_query.answer("✅ Изменено")
    await open_settings(callback_query)

@dp.callback_query(lambda c: c.data == "change_letter")
async def change_letter(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await safe_edit_message(callback_query.message, "🔤 Выбери букву:", reply_markup=get_letter_keyboard())

@dp.callback_query(lambda c: c.data.startswith("set_letter_"))
async def set_letter(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    letter = callback_query.data.replace("set_letter_", "")
    settings = get_user_settings(user_id)
    settings["letter"] = letter
    await callback_query.answer(f"✅ Буква: {letter.upper()}")
    await open_settings(callback_query)

@dp.callback_query(lambda c: c.data == "change_count")
async def change_count(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await safe_edit_message(callback_query.message, "🔢 Количество:", reply_markup=get_count_keyboard())

@dp.callback_query(lambda c: c.data.startswith("set_count_"))
async def set_count(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    count = int(callback_query.data.replace("set_count_", ""))
    settings = get_user_settings(user_id)
    settings["repeat_count"] = count
    await callback_query.answer(f"✅ Повторений: {count}")
    await open_settings(callback_query)

@dp.callback_query(lambda c: c.data == "reset_settings")
async def reset_settings(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    user_settings[user_id] = {"letter": "s", "repeat_count": 2, "use_full_alphabet": True, "beautiful_mode": False}
    await callback_query.answer("✅ Сброшено")
    await open_settings(callback_query)

@dp.callback_query(lambda c: c.data == "generate_username")
async def process_generate_username(callback_query: types.CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    settings = get_user_settings(user_id)
    
    set_stop_flag(user_id, False)
    
    mode_text = "✨ Красивые" if settings.get("beautiful_mode", False) else "⚡️ Обычный"
    
    waiting_message = await safe_edit_message(
        callback_query.message,
        f"{mode_text} <b>режим</b>\n\n⏳ Ищу свободные...\n\n<i>/stop для остановки</i>",
        parse_mode=ParseMode.HTML
    )
    
    max_attempts = 50
    found = False
    
    for i in range(0, max_attempts, BATCH_SIZE):
        if is_stopped(user_id):
            await safe_edit_message(
                waiting_message,
                f"🛑 <b>ОСТАНОВЛЕНО</b>\n\nПроверено: {i}",
                parse_mode=ParseMode.HTML,
                reply_markup=get_main_keyboard()
            )
            set_stop_flag(user_id, False)
            return
        
        batch = []
        for _ in range(min(BATCH_SIZE, max_attempts - i)):
            if settings.get("beautiful_mode", False):
                username = generate_beautiful_username()
            else:
                username = generate_username(settings)
            
            if not is_in_taken_db(username) and not is_in_free_db(username) and not is_in_banned_db(username):
                batch.append(username)
        
        if not batch:
            continue
        
        if i > 0 and i % 10 == 0:
            await safe_edit_message(
                waiting_message,
                f"{mode_text} <b>режим</b>\n\n⏳ {i}/{max_attempts}...\n\n<i>/stop для остановки</i>",
                parse_mode=ParseMode.HTML
            )
        
        results = await check_usernames_batch(batch, user_id)
        
        for username, is_free in results.items():
            if is_free and not is_in_banned_db(username):
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="✅ Забрать", url=f"https://t.me/{username}")],
                        [InlineKeyboardButton(text="🔎 Fragment", url=f"https://fragment.com/username/{username}")],
                        [
                            InlineKeyboardButton(text="🔄 Еще", callback_data="generate_username"),
                            InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")
                        ]
                    ]
                )
                await safe_edit_message(
                    waiting_message,
                    f"🎉 <b>НАЙДЕН!</b>\n\n✅ <code>@{username}</code>\n\n{mode_text} режим",
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard
                )
                found = True
                break
        
        if found:
            break
    
    if not found:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Снова", callback_data="generate_username")],
                [InlineKeyboardButton(text="⚙️ Настройки", callback_data="open_settings")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")]
            ]
        )
        await safe_edit_message(
            waiting_message,
            f"😔 Не найдено за {max_attempts} попыток\n\n{mode_text} режим",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )

@dp.callback_query(lambda c: c.data == "check_all")
async def check_all_combinations(callback_query: types.CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    settings = get_user_settings(user_id)
    
    all_usernames = get_all_possible_usernames(settings)
    total = len(all_usernames)
    
    if total == 0:
        await safe_send_message(callback_query.message.chat.id, "❌ Нет комбинаций")
        return
    
    estimated_minutes = int((total * CHECK_DELAY / BATCH_SIZE) / 60)
    
    mode_text = "✨ Красивые" if settings.get("beautiful_mode", False) else "⚡️ Обычный"
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Начать", callback_data="confirm_check_all"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="main_menu")
            ]
        ]
    )
    
    await safe_edit_message(
        callback_query.message,
        f"🔍 <b>МАССОВАЯ ПРОВЕРКА</b>\n\n"
        f"{mode_text} режим\n"
        f"Комбинаций: {total}\n"
        f"Время: ~{estimated_minutes} мин\n\n"
        f"/stop для остановки\n\n"
        f"Продолжить?",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )

@dp.callback_query(lambda c: c.data == "confirm_check_all")
async def confirm_check_all(callback_query: types.CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    settings = get_user_settings(user_id)
    
    set_stop_flag(user_id, False)
    
    all_usernames = get_all_possible_usernames(settings)
    await perform_mass_check(callback_query.message, user_id, all_usernames)

async def perform_mass_check(message, user_id: int, all_usernames: List[str]):
    total = len(all_usernames)
    
    await safe_edit_message(
        message,
        f"⚡️ <b>СТАРТ!</b>\n\nВсего: {total}\n\n<i>/stop для остановки</i>",
        parse_mode=ParseMode.HTML
    )
    
    checked = 0
    found_free = []
    skipped_banned = 0
    last_update = datetime.now()
    start_time = datetime.now()
    
    for i in range(0, len(all_usernames), BATCH_SIZE):
        if is_stopped(user_id):
            elapsed_min = int((datetime.now() - start_time).total_seconds() / 60)
            await safe_edit_message(
                message,
                f"🛑 <b>ОСТАНОВЛЕНО</b>\n\n"
                f"Проверено: {checked}/{total}\n"
                f"Найдено: {len(found_free)}\n"
                f"Время: {elapsed_min} мин",
                parse_mode=ParseMode.HTML,
                reply_markup=get_main_keyboard()
            )
            set_stop_flag(user_id, False)
            return
        
        batch = all_usernames[i:i + BATCH_SIZE]
        
        to_check = []
        for username in batch:
            if is_in_taken_db(username):
                checked += 1
                continue
            if is_in_free_db(username):
                found_free.append(username)
                checked += 1
                continue
            if is_in_banned_db(username):
                checked += 1
                skipped_banned += 1
                continue
            to_check.append(username)
        
        if to_check:
            results = await check_usernames_batch(to_check, user_id)
            
            for username, is_free in results.items():
                if is_in_banned_db(username):
                    skipped_banned += 1
                elif is_free:
                    found_free.append(username)
                    
                    keyboard = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [InlineKeyboardButton(text="✅ Забрать", url=f"https://t.me/{username}")],
                            [InlineKeyboardButton(text="🔎 Fragment", url=f"https://fragment.com/username/{username}")]
                        ]
                    )
                    
                    await safe_send_message(
                        user_id,
                        f"🎉 <b>#{len(found_free)}!</b>\n\n✅ <code>@{username}</code>",
                        parse_mode=ParseMode.HTML,
                        reply_markup=keyboard
                    )
                
                checked += 1
        
        now = datetime.now()
        if (now - last_update).total_seconds() >= 15:
            progress = (checked / total) * 100
            await safe_edit_message(
                message,
                f"⚡️ {checked}/{total} ({progress:.1f}%)\n"
                f"✅ Найдено: {len(found_free)}\n"
                f"🚫 Заблокировано: {skipped_banned}\n\n"
                f"<i>/stop для остановки</i>",
                parse_mode=ParseMode.HTML
            )
            last_update = now
    
    elapsed_min = int((datetime.now() - start_time).total_seconds() / 60)
    
    if found_free:
        samples = "\n".join([f"• <code>@{u}</code>" for u in found_free[:20]])
        if len(found_free) > 20:
            samples += f"\n... +{len(found_free) - 20}"
        
        await safe_edit_message(
            message,
            f"✅ <b>ГОТОВО!</b>\n\n"
            f"Проверено: {checked}\n"
            f"Найдено: {len(found_free)}\n"
            f"Заблокировано: {skipped_banned}\n"
            f"Время: {elapsed_min} мин\n\n"
            f"{samples}",
            parse_mode=ParseMode.HTML,
            reply_markup=get_main_keyboard()
        )
    else:
        await safe_edit_message(
            message,
            f"😔 Не найдено\n\n"
            f"Проверено: {checked}\n"
            f"Заблокировано: {skipped_banned}\n"
            f"Время: {elapsed_min} мин",
            parse_mode=ParseMode.HTML,
            reply_markup=get_main_keyboard()
        )

@dp.callback_query(lambda c: c.data == "get_db")
async def get_db_callback(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await get_db_command(callback_query.message)

@dp.callback_query(lambda c: c.data == "main_menu")
async def main_menu(callback_query: types.CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    set_stop_flag(user_id, False)
    
    await safe_edit_message(
        callback_query.message,
        f"🏠 <b>Главное меню</b>\n\n"
        f"✨ Режим 'Просто красивые' доступен!\n"
        f"🛑 /stop для остановки",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard()
    )

# ============ ЗАПУСК ============

async def on_startup():
    logging.info("🚀 Бот запущен!")
    logging.info("✨ Режим 'Просто красивые' добавлен!")
    logging.info("🔥 Исправлена логика проверки")
    
    if not os.path.exists(TAKEN_DB_FILE):
        save_db(TAKEN_DB_FILE, {})
    if not os.path.exists(FREE_DB_FILE):
        save_db(FREE_DB_FILE, {})
    if not os.path.exists(BANNED_DB_FILE):
        save_db(BANNED_DB_FILE, {})

async def on_shutdown():
    global http_session
    if http_session and not http_session.closed:
        await http_session.close()
    logging.info("⛔ Бот остановлен")

async def main():
    await on_startup()
    try:
        await dp.start_polling(bot)
    finally:
        await on_shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("⛔ Остановлено")
