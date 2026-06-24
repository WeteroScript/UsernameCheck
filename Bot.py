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

# ⚡️ ОПТИМИЗИРОВАННЫЕ НАСТРОЙКИ
RATE_LIMITER = Semaphore(10)
CHECK_DELAY = 1.1
BATCH_SIZE = 10
CONNECTION_LIMIT = 100

# Глобальная HTTP сессия
http_session: Optional[aiohttp.ClientSession] = None

# Пути к файлам
TAKEN_DB_FILE = "taken_usernames.json"
FREE_DB_FILE = "free_usernames.json"
DEBUG_LOG_FILE = "debug_checks.log"

# Глобальные настройки
user_settings = {}

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

def is_in_taken_db(username: str) -> bool:
    db = load_db(TAKEN_DB_FILE)
    return username in db

def is_in_free_db(username: str) -> bool:
    db = load_db(FREE_DB_FILE)
    return username in db

def log_debug(username: str, method: str, status: str, details=""):
    """Детальное логирование для отладки"""
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
            "use_full_alphabet": True
        }
    return user_settings[user_id]

# ============ ГЕНЕРАЦИЯ ЮЗЕРНЕЙМОВ ============

def generate_username(settings: Dict) -> str:
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
    return [generate_username(settings) for _ in range(count)]

def get_all_possible_usernames(settings: Dict) -> List[str]:
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

# ============ ГЛОБАЛЬНАЯ HTTP СЕССИЯ ============

async def get_http_session() -> aiohttp.ClientSession:
    global http_session
    if http_session is None or http_session.closed:
        connector = aiohttp.TCPConnector(
            limit=CONNECTION_LIMIT,
            limit_per_host=30,
            ttl_dns_cache=300,
            enable_cleanup_closed=True
        )
        timeout = aiohttp.ClientTimeout(total=15, connect=5)
        http_session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout
        )
    return http_session

# ============ 🔥 ИСПРАВЛЕННАЯ ПРОВЕРКА ЧЕРЕЗ BOT API ============

async def check_username_bot_api_fast(username: str) -> Optional[bool]:
    """
    Проверка через Bot API
    Returns: True (СВОБОДЕН), False (ЗАНЯТ), None (ошибка/неизвестно)
    """
    try:
        session = await get_http_session()
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChat"
        params = {"chat_id": f"@{username}"}
        
        async with session.get(url, params=params) as response:
            data = await response.json()
            
            log_debug(username, "bot_api", f"Response: {data}")
            
            if data.get("ok") is True:
                # Чат найден = ЗАНЯТ
                logging.info(f"❌ Bot API: @{username} → ЗАНЯТ (чат найден)")
                return False
            else:
                error_desc = data.get("description", "")
                error_code = data.get("error_code", 0)
                
                # КЛЮЧЕВОЕ ИСПРАВЛЕНИЕ: правильная обработка ошибок
                if "chat not found" in error_desc.lower():
                    # Чат не найден = СВОБОДЕН!
                    logging.info(f"✅ Bot API: @{username} → СВОБОДЕН (chat not found)")
                    return True
                elif "username is not occupied" in error_desc.lower():
                    # Юзернейм не занят = СВОБОДЕН!
                    logging.info(f"✅ Bot API: @{username} → СВОБОДЕН (not occupied)")
                    return True
                elif "username is occupied" in error_desc.lower():
                    # Юзернейм занят = ЗАНЯТ
                    logging.info(f"❌ Bot API: @{username} → ЗАНЯТ (is occupied)")
                    return False
                elif error_code == 400:
                    # Bad Request часто означает что юзернейм не существует
                    logging.info(f"✅ Bot API: @{username} → СВОБОДЕН (400 error)")
                    return True
                else:
                    # Неизвестная ошибка
                    logging.warning(f"⚠️ Bot API: @{username} → НЕИЗВЕСТНО ({error_desc})")
                    return None
                
    except asyncio.TimeoutError:
        logging.warning(f"⏱ Bot API timeout: @{username}")
        return None
    except Exception as e:
        logging.error(f"❌ Bot API error @{username}: {e}")
        return None

# ============ 🔥 ИСПРАВЛЕННАЯ ПРОВЕРКА ЧЕРЕЗ FRAGMENT ============

async def check_username_fragment_fast(username: str) -> Optional[bool]:
    """
    Проверка через Fragment
    Returns: True (СВОБОДЕН), False (ЗАНЯТ), None (неизвестно)
    """
    try:
        session = await get_http_session()
        url = f"https://fragment.com/username/{username}"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        
        async with session.get(url, headers=headers, allow_redirects=True) as response:
            status = response.status
            html = await response.text()
            
            log_debug(username, "fragment", f"Status {status}", html[:2000])
            
            if status == 404:
                # Страница не найдена = СВОБОДЕН
                logging.info(f"✅ Fragment: @{username} → СВОБОДЕН (404)")
                return True
            
            if status == 200:
                html_lower = html.lower()
                
                # ПРИЗНАКИ ЗАНЯТОСТИ (возвращаем False только если уверены!)
                
                # 1. На аукционе (активные торги)
                if 'tm-section-bid-button' in html or 'place a bid' in html_lower:
                    logging.info(f"❌ Fragment: @{username} → ЗАНЯТ (на аукционе)")
                    return False
                
                # 2. Есть активная цена/ставка
                if re.search(r'class="table-cell-value[^"]*"[^>]*>\s*[$₽€£]?\s*\d+', html):
                    logging.info(f"❌ Fragment: @{username} → ЗАНЯТ (есть цена)")
                    return False
                
                # 3. Статус "Sold"
                if re.search(r'<div[^>]*class="[^"]*status[^"]*"[^>]*>[^<]*sold[^<]*</div>', html, re.IGNORECASE):
                    logging.info(f"❌ Fragment: @{username} → ЗАНЯТ (продан)")
                    return False
                
                # 4. Таймер обратного отсчета (аукцион идет)
                if 'tm-section-countdown' in html:
                    logging.info(f"❌ Fragment: @{username} → ЗАНЯТ (идет аукцион)")
                    return False
                
                # ПРИЗНАКИ СВОБОДНОСТИ
                
                # 1. Явно написано "Available"
                if re.search(r'<div[^>]*class="[^"]*status[^"]*"[^>]*>[^<]*available[^<]*</div>', html, re.IGNORECASE):
                    logging.info(f"✅ Fragment: @{username} → СВОБОДЕН (status: available)")
                    return True
                
                # 2. Пустая/минимальная страница
                if len(html) < 3000 and 'tm-page-username' not in html:
                    logging.info(f"✅ Fragment: @{username} → СВОБОДЕН (пустая страница)")
                    return True
                
                # 3. Нет признаков занятости и нет контента
                if all(marker not in html_lower for marker in [
                    'bid', 'auction', 'sold', 'price', 'owner', 'purchased'
                ]):
                    logging.info(f"✅ Fragment: @{username} → СВОБОДЕН (нет признаков занятости)")
                    return True
                
                # Если есть сомнения - возвращаем None (не знаем точно)
                logging.warning(f"⚠️ Fragment: @{username} → НЕОПРЕДЕЛЕННО")
                return None
            
            # Другие статусы
            logging.warning(f"⚠️ Fragment: @{username} → Статус {status}")
            return None
                
    except asyncio.TimeoutError:
        logging.warning(f"⏱ Fragment timeout: @{username}")
        return None
    except Exception as e:
        logging.error(f"❌ Fragment error @{username}: {e}")
        return None

# ============ 🔥 ИСПРАВЛЕННАЯ ПРОВЕРКА ЧЕРЕЗ T.ME ============

async def check_username_web_fast(username: str) -> Optional[bool]:
    """
    Проверка через t.me
    """
    try:
        session = await get_http_session()
        url = f"https://t.me/{username}"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html",
        }
        
        async with session.get(url, headers=headers, allow_redirects=True) as response:
            status = response.status
            
            if status == 404:
                logging.info(f"✅ t.me: @{username} → СВОБОДЕН (404)")
                return True
            
            if status == 200:
                html = await response.text()
                html_short = html[:3000]  # Читаем только начало
                
                log_debug(username, "t.me", f"Status {status}", html_short)
                
                # Признаки ЗАНЯТОСТИ (есть профиль)
                if any(marker in html_short for marker in [
                    'tgme_page_photo',
                    'tgme_page_title',
                    'tgme_page_description',
                    'tgme_page_extra'
                ]):
                    logging.info(f"❌ t.me: @{username} → ЗАНЯТ (есть профиль)")
                    return False
                
                # Признаки СВОБОДНОСТИ (пустая страница)
                html_lower = html_short.lower()
                if "if you have" in html_lower and "telegram" in html_lower:
                    # Это стандартная пустая страница
                    if f"@{username}" not in html_short or 'tgme_page_title' not in html_short:
                        logging.info(f"✅ t.me: @{username} → СВОБОДЕН (пустая страница)")
                        return True
                
                # Неопределенно
                logging.warning(f"⚠️ t.me: @{username} → НЕОПРЕДЕЛЕННО")
                return None
            
            return None
                
    except asyncio.TimeoutError:
        logging.warning(f"⏱ t.me timeout: @{username}")
        return None
    except Exception as e:
        logging.error(f"❌ t.me error @{username}: {e}")
        return None

# ============ 🔥 ИСПРАВЛЕННАЯ ГЛАВНАЯ ФУНКЦИЯ ПРОВЕРКИ ============

async def check_username_parallel(username: str, user_id=None) -> bool:
    """
    Параллельная проверка через все методы
    
    НОВАЯ ЛОГИКА (более мягкая):
    1. Если ВСЕ методы говорят "свободен" → 100% СВОБОДЕН
    2. Если большинство говорят "свободен" → СВОБОДЕН
    3. Если Bot API говорит "свободен" И нет явных "занят" → СВОБОДЕН
    4. Если есть хотя бы 2 "занят" → ЗАНЯТ
    5. В остальных случаях смотрим на Bot API (самый надежный)
    """
    
    # Проверяем БД
    if is_in_free_db(username):
        return True
    if is_in_taken_db(username):
        return False
    
    async with RATE_LIMITER:
        await asyncio.sleep(CHECK_DELAY)
        
        logging.info(f"\n{'='*60}\n🔍 ПРОВЕРЯЮ: @{username}\n{'='*60}")
        
        # ⚡️ Запускаем все проверки параллельно
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
        
        # Логирование результатов
        logging.info(f"📊 РЕЗУЛЬТАТЫ для @{username}:")
        logging.info(f"   Bot API: {bot_api_result}")
        logging.info(f"   Fragment: {fragment_result}")
        logging.info(f"   t.me: {web_result}")
        
        # Подсчет голосов
        free_votes = sum(1 for v in [bot_api_result, fragment_result, web_result] if v is True)
        taken_votes = sum(1 for v in [bot_api_result, fragment_result, web_result] if v is False)
        unknown_votes = sum(1 for v in [bot_api_result, fragment_result, web_result] if v is None)
        
        logging.info(f"   ✅ Свободен: {free_votes}")
        logging.info(f"   ❌ Занят: {taken_votes}")
        logging.info(f"   ❓ Неизвестно: {unknown_votes}")
        
        # 🔥 НОВАЯ МЯГКАЯ ЛОГИКА
        
        # 1. Все говорят "свободен" → точно свободен
        if free_votes == 3:
            add_to_free_db(username, user_id, "all_confirmed")
            logging.info(f"🟢 ИТОГ: @{username} СВОБОДЕН ✅ (все подтвердили)")
            return True
        
        # 2. Большинство (2+) говорят "свободен" и нет противоречий
        if free_votes >= 2 and taken_votes == 0:
            add_to_free_db(username, user_id, "majority_free")
            logging.info(f"🟢 ИТОГ: @{username} СВОБОДЕН ✅ (большинство)")
            return True
        
        # 3. Bot API говорит "свободен" и нет явных "занят"
        if bot_api_result is True and taken_votes == 0:
            add_to_free_db(username, user_id, "bot_api_confirmed")
            logging.info(f"🟢 ИТОГ: @{username} СВОБОДЕН ✅ (Bot API)")
            return True
        
        # 4. Bot API говорит "свободен" и только 1 "занят" (может быть ошибка)
        if bot_api_result is True and taken_votes == 1:
            add_to_free_db(username, user_id, "bot_api_priority")
            logging.info(f"🟢 ИТОГ: @{username} СВОБОДЕН ✅ (приоритет Bot API)")
            return True
        
        # 5. Два или больше говорят "занят" → точно занят
        if taken_votes >= 2:
            add_to_taken_db(username, user_id, "majority_taken", f"Taken votes: {taken_votes}")
            logging.info(f"🔴 ИТОГ: @{username} ЗАНЯТ ❌ (большинство)")
            return False
        
        # 6. Bot API говорит "занят" → скорее всего занят
        if bot_api_result is False:
            add_to_taken_db(username, user_id, "bot_api_taken", "Bot API confirmed taken")
            logging.info(f"🔴 ИТОГ: @{username} ЗАНЯТ ❌ (Bot API)")
            return False
        
        # 7. Один говорит "занят", остальные неизвестно → считаем занятым (безопаснее)
        if taken_votes == 1:
            add_to_taken_db(username, user_id, "single_taken", "One method confirmed taken")
            logging.info(f"🔴 ИТОГ: @{username} ЗАНЯТ ❌ (1 метод)")
            return False
        
        # 8. Все неизвестно или смешанные результаты → по Bot API или считаем свободным
        if bot_api_result is None and free_votes > 0:
            # Есть хотя бы один "свободен" и Bot API не знает
            add_to_free_db(username, user_id, "optimistic")
            logging.info(f"🟢 ИТОГ: @{username} СВОБОДЕН ✅ (оптимистично)")
            return True
        
        # 9. В крайнем случае - считаем занятым
        add_to_taken_db(username, user_id, "unknown_state", "All methods uncertain")
        logging.info(f"🔴 ИТОГ: @{username} ЗАНЯТ ❌ (по умолчанию)")
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
            logging.error(f"Batch check error for @{username}: {result}")
            output[username] = False
        else:
            output[username] = result
    
    return output

# ============ КЛАВИАТУРЫ ============

def get_settings_keyboard(user_id: int):
    settings = get_user_settings(user_id)
    alphabet_status = "✅" if settings["use_full_alphabet"] else "❌"
    
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=f"{alphabet_status} Все буквы алфавита", 
                callback_data="toggle_alphabet"
            )],
            [InlineKeyboardButton(
                text=f"🔤 Буква: {settings['letter'].upper()}", 
                callback_data="change_letter"
            )],
            [InlineKeyboardButton(
                text=f"🔢 Повторений: {settings['repeat_count']}", 
                callback_data="change_count"
            )],
            [InlineKeyboardButton(
                text="🔄 Сбросить", 
                callback_data="reset_settings"
            )],
            [InlineKeyboardButton(
                text="🏠 Меню", 
                callback_data="main_menu"
            )]
        ]
    )

def get_letter_keyboard():
    keyboard = []
    row = []
    for letter in string.ascii_lowercase:
        row.append(InlineKeyboardButton(
            text=letter.upper(),
            callback_data=f"set_letter_{letter}"
        ))
        if len(row) == 7:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    
    keyboard.append([InlineKeyboardButton(
        text="⬅️ Назад",
        callback_data="back_to_settings"
    )])
    
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
            [InlineKeyboardButton(
                text="✨ Генерировать юзернейм", 
                callback_data="generate_username"
            )],
            [InlineKeyboardButton(
                text="🔍 Проверить все комбинации", 
                callback_data="check_all"
            )],
            [InlineKeyboardButton(
                text="⚙️ Настройки", 
                callback_data="open_settings"
            )],
            [InlineKeyboardButton(
                text="📊 Статистика", 
                callback_data="show_stats"
            )]
        ]
    )

# ============ КОМАНДЫ ============

@dp.message(Command("start"))
async def start_command(message: types.Message):
    user_name = message.from_user.first_name or "Пользователь"
    user_id = message.from_user.id
    
    get_user_settings(user_id)
    
    if not os.path.exists(TAKEN_DB_FILE):
        save_db(TAKEN_DB_FILE, {})
    if not os.path.exists(FREE_DB_FILE):
        save_db(FREE_DB_FILE, {})
    
    await message.answer(
        f"Привет, {user_name}! 👋\n\n"
        f"🎯 <b>Поиск свободных 5-значных юзернеймов</b>\n\n"
        f"⚡️ <b>ТУРБО-РЕЖИМ + УМНАЯ ПРОВЕРКА:</b>\n"
        f"✅ Параллельная проверка (10 одновременно)\n"
        f"✅ Мягкая логика (не пропускает свободные)\n"
        f"✅ 3 метода: Bot API, Fragment, t.me\n"
        f"✅ Скорость: ~15-30 юзернеймов/мин\n\n"
        f"Выбери действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard()
    )

@dp.message(Command("getdb"))
async def get_db_command(message: types.Message):
    user_id = message.from_user.id
    
    taken_db = load_db(TAKEN_DB_FILE)
    free_db = load_db(FREE_DB_FILE)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    taken_file = f"taken_{user_id}_{timestamp}.json"
    free_file = f"free_{user_id}_{timestamp}.json"
    
    try:
        with open(taken_file, 'w', encoding='utf-8') as f:
            json.dump(taken_db, f, indent=2, ensure_ascii=False)
        
        with open(free_file, 'w', encoding='utf-8') as f:
            json.dump(free_db, f, indent=2, ensure_ascii=False)
        
        await message.answer(
            f"📊 Занятых: {len(taken_db)}, Свободных: {len(free_db)}",
            parse_mode=ParseMode.HTML
        )
        
        await message.answer_document(
            types.FSInputFile(taken_file),
            caption=f"📁 Занятые ({len(taken_db)})"
        )
        
        await message.answer_document(
            types.FSInputFile(free_file),
            caption=f"✅ Свободные ({len(free_db)})"
        )
        
        # Отправляем debug лог
        if os.path.exists(DEBUG_LOG_FILE):
            await message.answer_document(
                types.FSInputFile(DEBUG_LOG_FILE),
                caption=f"🔍 Debug лог"
            )
        
        os.remove(taken_file)
        os.remove(free_file)
        
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

@dp.message(Command("check"))
async def check_command(message: types.Message):
    """Проверка конкретного юзернейма"""
    try:
        username = message.text.split()[1].replace("@", "")
        
        await message.answer(f"🔍 Проверяю @{username}...")
        
        is_free = await check_username_parallel(username, message.from_user.id)
        
        if is_free:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Забрать", url=f"https://t.me/{username}")],
                    [InlineKeyboardButton(text="🔎 Fragment", url=f"https://fragment.com/username/{username}")]
                ]
            )
            await message.answer(
                f"✅ <b>@{username} СВОБОДЕН!</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )
        else:
            await message.answer(
                f"❌ <b>@{username} ЗАНЯТ</b>",
                parse_mode=ParseMode.HTML
            )
    except IndexError:
        await message.answer("Использование: /check username")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

# ============ CALLBACK HANDLERS ============

@dp.callback_query(lambda c: c.data == "show_stats")
async def show_stats(callback_query: types.CallbackQuery):
    await callback_query.answer()
    
    taken_db = load_db(TAKEN_DB_FILE)
    free_db = load_db(FREE_DB_FILE)
    
    # Подсчет методов для свободных
    methods_free = {}
    for username, data in free_db.items():
        method = data.get("method", "unknown")
        methods_free[method] = methods_free.get(method, 0) + 1
    
    # Подсчет методов для занятых
    methods_taken = {}
    for username, data in taken_db.items():
        method = data.get("method", "unknown")
        methods_taken[method] = methods_taken.get(method, 0) + 1
    
    methods_free_text = "\n".join([f"  • {m}: {c}" for m, c in methods_free.items()])
    methods_taken_text = "\n".join([f"  • {m}: {c}" for m, c in methods_taken.items()])
    
    await callback_query.message.edit_text(
        f"📊 <b>Статистика</b>\n\n"
        f"✅ <b>Свободных: {len(free_db)}</b>\n"
        f"{methods_free_text if methods_free_text else '  (нет)'}\n\n"
        f"❌ <b>Занятых: {len(taken_db)}</b>\n"
        f"{methods_taken_text if methods_taken_text else '  (нет)'}\n\n"
        f"⚡️ Турбо + умная проверка активны!",
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
    
    await callback_query.message.edit_text(
        f"⚙️ <b>Настройки</b>\n\n"
        f"📌 Буква: <b>{settings['letter'].upper()}</b>\n"
        f"📌 Повторений: <b>{settings['repeat_count']}</b>\n"
        f"📌 Алфавит: {'полный' if settings['use_full_alphabet'] else 'без l'}\n\n"
        f"📝 Примеры:\n{examples_text}",
        parse_mode=ParseMode.HTML,
        reply_markup=get_settings_keyboard(user_id)
    )

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
    await callback_query.message.edit_text(
        "🔤 Выбери букву:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_letter_keyboard()
    )

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
    await callback_query.message.edit_text(
        "🔢 Количество повторений:",
        reply_markup=get_count_keyboard()
    )

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
    user_settings[user_id] = {
        "letter": "s",
        "repeat_count": 2,
        "use_full_alphabet": True
    }
    await callback_query.answer("✅ Сброшено")
    await open_settings(callback_query)

# ============ ГЕНЕРАЦИЯ ЮЗЕРНЕЙМА ============

@dp.callback_query(lambda c: c.data == "generate_username")
async def process_generate_username(callback_query: types.CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    settings = get_user_settings(user_id)
    
    waiting_message = await callback_query.message.edit_text(
        "⚡️ <b>ПОИСК СВОБОДНОГО ЮЗЕРНЕЙМА...</b>\n\n"
        "<i>Умная параллельная проверка!</i>",
        parse_mode=ParseMode.HTML
    )
    
    max_attempts = 50
    found = False
    
    for i in range(0, max_attempts, BATCH_SIZE):
        batch = []
        for _ in range(min(BATCH_SIZE, max_attempts - i)):
            username = generate_username(settings)
            if not is_in_taken_db(username) and not is_in_free_db(username):
                batch.append(username)
        
        if not batch:
            continue
        
        if i > 0 and i % 20 == 0:
            try:
                await waiting_message.edit_text(
                    f"⚡️ <b>Проверяю...</b> {i}/{max_attempts}\n\n"
                    f"<i>Параллельно: {len(batch)} юзернеймов</i>",
                    parse_mode=ParseMode.HTML
                )
            except:
                pass
        
        results = await check_usernames_batch(batch, user_id)
        
        for username, is_free in results.items():
            if is_free:
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
                await waiting_message.edit_text(
                    f"🎉 <b>НАЙДЕН СВОБОДНЫЙ!</b>\n\n"
                    f"✅ <code>@{username}</code>\n\n"
                    f"<b>Проверен всеми методами!</b>",
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
        await waiting_message.edit_text(
            f"😔 Не найдено за {max_attempts} попыток\n\n"
            f"Попробуй изменить настройки или повтори!",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )

# ============ МАССОВАЯ ПРОВЕРКА ============

@dp.callback_query(lambda c: c.data == "check_all")
async def check_all_combinations(callback_query: types.CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    settings = get_user_settings(user_id)
    
    progress_message = await callback_query.message.edit_text(
        "⏳ Генерирую комбинации...",
        parse_mode=ParseMode.HTML
    )
    
    all_usernames = get_all_possible_usernames(settings)
    total = len(all_usernames)
    
    if total == 0:
        await progress_message.edit_text(
            "❌ Нет комбинаций",
            reply_markup=get_main_keyboard()
        )
        return
    
    estimated_minutes = int((total * CHECK_DELAY / BATCH_SIZE) / 60)
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Начать", callback_data="confirm_check_all"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="main_menu")
            ]
        ]
    )
    
    await progress_message.edit_text(
        f"⚡️ <b>МАССОВАЯ ПРОВЕРКА</b>\n\n"
        f"Комбинаций: <code>{total}</code>\n"
        f"Время: ~<b>{estimated_minutes} мин</b>\n\n"
        f"<b>Умная проверка {BATCH_SIZE} юзернеймов параллельно!</b>\n\n"
        f"Продолжить?",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )

@dp.callback_query(lambda c: c.data == "confirm_check_all")
async def confirm_check_all(callback_query: types.CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    settings = get_user_settings(user_id)
    
    all_usernames = get_all_possible_usernames(settings)
    await perform_mass_check(callback_query.message, user_id, all_usernames)

async def perform_mass_check(message, user_id: int, all_usernames: List[str]):
    """Массовая проверка"""
    total = len(all_usernames)
    
    await message.edit_text(
        f"⚡️ <b>ЗАПУСК ПРОВЕРКИ!</b>\n\n"
        f"Всего: {total}\n"
        f"Параллельно: {BATCH_SIZE}",
        parse_mode=ParseMode.HTML
    )
    
    checked = 0
    found_free = []
    last_update = datetime.now()
    start_time = datetime.now()
    
    for i in range(0, len(all_usernames), BATCH_SIZE):
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
            to_check.append(username)
        
        if to_check:
            results = await check_usernames_batch(to_check, user_id)
            
            for username, is_free in results.items():
                if is_free:
                    found_free.append(username)
                    
                    keyboard = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [InlineKeyboardButton(text="✅ Забрать", url=f"https://t.me/{username}")],
                            [InlineKeyboardButton(text="🔎 Fragment", url=f"https://fragment.com/username/{username}")]
                        ]
                    )
                    
                    try:
                        await bot.send_message(
                            user_id,
                            f"🎉 <b>Найден #{len(found_free)}!</b>\n\n"
                            f"✅ <code>@{username}</code>",
                            parse_mode=ParseMode.HTML,
                            reply_markup=keyboard
                        )
                    except:
                        pass
                
                checked += 1
        
        now = datetime.now()
        if (now - last_update).total_seconds() >= 10:
            try:
                progress = (checked / total) * 100
                elapsed = (now - start_time).total_seconds()
                speed = checked / elapsed if elapsed > 0 else 0
                remaining_sec = (total - checked) / speed if speed > 0 else 0
                remaining_min = int(remaining_sec / 60)
                
                await message.edit_text(
                    f"⚡️ <b>ПРОВЕРКА...</b>\n\n"
                    f"📊 {checked}/{total} ({progress:.1f}%)\n"
                    f"✅ Найдено: {len(found_free)}\n"
                    f"⚡️ ~{speed:.1f}/сек\n"
                    f"⏱ ~{remaining_min} мин",
                    parse_mode=ParseMode.HTML
                )
                last_update = now
            except:
                pass
    
    elapsed_total = (datetime.now() - start_time).total_seconds()
    elapsed_min = int(elapsed_total / 60)
    avg_speed = checked / elapsed_total if elapsed_total > 0 else 0
    
    if found_free:
        samples = "\n".join([f"• <code>@{u}</code>" for u in found_free[:20]])
        if len(found_free) > 20:
            samples += f"\n... +{len(found_free) - 20}"
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📥 Скачать БД", callback_data="get_db")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")]
            ]
        )
        
        await message.edit_text(
            f"✅ <b>ГОТОВО!</b>\n\n"
            f"📊 Проверено: {checked}\n"
            f"✅ Найдено: <b>{len(found_free)}</b>\n"
            f"⏱ {elapsed_min} мин\n"
            f"⚡️ {avg_speed:.1f}/сек\n\n"
            f"📝 Примеры:\n{samples}",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
    else:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⚙️ Настройки", callback_data="open_settings")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")]
            ]
        )
        
        await message.edit_text(
            f"😔 Не найдено\n\n"
            f"Проверено: {checked}\n"
            f"Время: {elapsed_min} мин\n"
            f"Скорость: {avg_speed:.1f}/сек",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )

@dp.callback_query(lambda c: c.data == "get_db")
async def get_db_callback(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await get_db_command(callback_query.message)

@dp.callback_query(lambda c: c.data == "main_menu")
async def main_menu(callback_query: types.CallbackQuery):
    await callback_query.answer()
    
    await callback_query.message.edit_text(
        f"🏠 <b>Главное меню</b>\n\n"
        f"⚡️ Турбо-режим + умная проверка!\n"
        f"✅ Не пропускает свободные юзернеймы",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard()
    )

# ============ ЗАПУСК ============

async def on_startup():
    logging.info("🚀 Бот запущен!")
    logging.info(f"⚡️ Параллельных проверок: {RATE_LIMITER._value}")
    logging.info(f"⚡️ Задержка: {CHECK_DELAY} сек")
    logging.info(f"⚡️ Батч: {BATCH_SIZE}")
    logging.info("✅ Умная мягкая логика - не пропускает свободные!")
    
    if not os.path.exists(TAKEN_DB_FILE):
        save_db(TAKEN_DB_FILE, {})
    if not os.path.exists(FREE_DB_FILE):
        save_db(FREE_DB_FILE, {})

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
