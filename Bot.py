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
CHECK_DELAY = 0.3
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

# ============ ПРОВЕРКА ЧЕРЕЗ BOT API ============

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
                logging.info(f"❌ Bot API: @{username} → ЗАНЯТ (чат найден)")
                return False
            else:
                error_desc = data.get("description", "")
                error_code = data.get("error_code", 0)
                
                if "chat not found" in error_desc.lower():
                    logging.info(f"✅ Bot API: @{username} → СВОБОДЕН (chat not found)")
                    return True
                elif "username is not occupied" in error_desc.lower():
                    logging.info(f"✅ Bot API: @{username} → СВОБОДЕН (not occupied)")
                    return True
                elif "username is occupied" in error_desc.lower():
                    logging.info(f"❌ Bot API: @{username} → ЗАНЯТ (is occupied)")
                    return False
                elif error_code == 400:
                    logging.info(f"✅ Bot API: @{username} → СВОБОДЕН (400 error)")
                    return True
                else:
                    logging.warning(f"⚠️ Bot API: @{username} → НЕИЗВЕСТНО ({error_desc})")
                    return None
                
    except asyncio.TimeoutError:
        logging.warning(f"⏱ Bot API timeout: @{username}")
        return None
    except Exception as e:
        logging.error(f"❌ Bot API error @{username}: {e}")
        return None

# ============ 🔥 УЛУЧШЕННАЯ ПРОВЕРКА FRAGMENT (ИГНОРИРУЕТ ПРОДАЖИ) ============

async def check_username_fragment_fast(username: str) -> Optional[bool]:
    """
    Проверка через Fragment с детекцией продаж
    Returns: 
    - True (СВОБОДЕН и НЕ на продаже)
    - False (ЗАНЯТ или НА ПРОДАЖЕ/АУКЦИОНЕ)
    - None (неизвестно)
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
                logging.info(f"✅ Fragment: @{username} → СВОБОДЕН (404)")
                return True
            
            if status == 200:
                html_lower = html.lower()
                
                # 🔥 ПРОВЕРКА НА ПРОДАЖУ/АУКЦИОН (ГЛАВНОЕ!)
                
                # 1. Кнопка "Place a Bid" или "Buy Now"
                if any(marker in html_lower for marker in [
                    'place a bid',
                    'place bid',
                    'buy now',
                    'tm-section-bid-button',
                    'tm-bid-button'
                ]):
                    logging.info(f"🛒 Fragment: @{username} → НА ПРОДАЖЕ (кнопка покупки)")
                    return False
                
                # 2. Цена в любом формате
                if re.search(r'(?:price|bid|cost)[\s\S]{0,50}?(?:\$|USD|TON|₽|€|£)\s*\d+', html, re.IGNORECASE):
                    logging.info(f"🛒 Fragment: @{username} → НА ПРОДАЖЕ (есть цена)")
                    return False
                
                # 3. Таблица с ценой
                if re.search(r'<div[^>]*class="[^"]*table-cell-value[^"]*"[^>]*>\s*(?:\$|TON|₽)\s*\d+', html):
                    logging.info(f"🛒 Fragment: @{username} → НА ПРОДАЖЕ (таблица цен)")
                    return False
                
                # 4. Таймер аукциона
                if 'tm-section-countdown' in html or 'auction-timer' in html_lower:
                    logging.info(f"🛒 Fragment: @{username} → НА АУКЦИОНЕ (таймер)")
                    return False
                
                # 5. Статус "On Auction"
                if re.search(r'(?:status|state)[\s\S]{0,100}?(?:auction|on sale|for sale)', html_lower):
                    logging.info(f"🛒 Fragment: @{username} → НА АУКЦИОНЕ (статус)")
                    return False
                
                # 6. Информация о владельце/покупателе
                if any(marker in html_lower for marker in [
                    'highest bidder',
                    'current bid',
                    'minimum bid',
                    'starting price',
                    'reserve price'
                ]):
                    logging.info(f"🛒 Fragment: @{username} → НА АУКЦИОНЕ (детали ставок)")
                    return False
                
                # 7. Продан
                if re.search(r'(?:status|state)[\s\S]{0,100}?sold', html_lower):
                    logging.info(f"❌ Fragment: @{username} → ПРОДАН")
                    return False
                
                # 8. Занят
                if re.search(r'(?:status|state)[\s\S]{0,100}?taken', html_lower):
                    logging.info(f"❌ Fragment: @{username} → ЗАНЯТ")
                    return False
                
                # 9. Не продается (значит занят владельцем)
                if 'not for sale' in html_lower or 'unavailable' in html_lower:
                    logging.info(f"❌ Fragment: @{username} → ЗАНЯТ (not for sale)")
                    return False
                
                # 10. Любое упоминание TON или $ с цифрами
                if re.search(r'(?:TON|USD|\$)\s*[\d,]+', html):
                    logging.info(f"🛒 Fragment: @{username} → НА ПРОДАЖЕ (найдена валюта)")
                    return False
                
                # ПРИЗНАКИ СВОБОДНОСТИ (только если НЕТ признаков продажи!)
                
                # Явный статус "Available"
                if re.search(r'<div[^>]*class="[^"]*status[^"]*"[^>]*>[^<]*available[^<]*</div>', html, re.IGNORECASE):
                    # Дополнительно проверяем что нет цены
                    if not re.search(r'(?:\$|TON)\s*\d+', html):
                        logging.info(f"✅ Fragment: @{username} → СВОБОДЕН (available, нет цены)")
                        return True
                    else:
                        logging.info(f"🛒 Fragment: @{username} → НА ПРОДАЖЕ (available но есть цена)")
                        return False
                
                # Пустая/минимальная страница
                if len(html) < 3000 and 'tm-page-username' not in html:
                    logging.info(f"✅ Fragment: @{username} → СВОБОДЕН (пустая страница)")
                    return True
                
                # Нет никаких признаков занятости и продажи
                if all(marker not in html_lower for marker in [
                    'bid', 'auction', 'sold', 'price', 'owner', 'purchased', 
                    'ton', 'usd', '$', '₽', 'buy', 'sale'
                ]):
                    logging.info(f"✅ Fragment: @{username} → СВОБОДЕН (нет признаков)")
                    return True
                
                # Если что-то есть но непонятно что - считаем занятым
                logging.warning(f"⚠️ Fragment: @{username} → НЕОПРЕДЕЛЕННО (считаем занятым)")
                return False
            
            logging.warning(f"⚠️ Fragment: @{username} → Статус {status}")
            return None
                
    except asyncio.TimeoutError:
        logging.warning(f"⏱ Fragment timeout: @{username}")
        return None
    except Exception as e:
        logging.error(f"❌ Fragment error @{username}: {e}")
        return None

# ============ ПРОВЕРКА ЧЕРЕЗ T.ME ============

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
                html_short = html[:3000]
                
                log_debug(username, "t.me", f"Status {status}", html_short)
                
                # Признаки ЗАНЯТОСТИ
                if any(marker in html_short for marker in [
                    'tgme_page_photo',
                    'tgme_page_title',
                    'tgme_page_description',
                    'tgme_page_extra'
                ]):
                    logging.info(f"❌ t.me: @{username} → ЗАНЯТ (есть профиль)")
                    return False
                
                # Признаки СВОБОДНОСТИ
                html_lower = html_short.lower()
                if "if you have" in html_lower and "telegram" in html_lower:
                    if f"@{username}" not in html_short or 'tgme_page_title' not in html_short:
                        logging.info(f"✅ t.me: @{username} → СВОБОДЕН (пустая страница)")
                        return True
                
                logging.warning(f"⚠️ t.me: @{username} → НЕОПРЕДЕЛЕННО")
                return None
            
            return None
                
    except asyncio.TimeoutError:
        logging.warning(f"⏱ t.me timeout: @{username}")
        return None
    except Exception as e:
        logging.error(f"❌ t.me error @{username}: {e}")
        return None

# ============ ГЛАВНАЯ ФУНКЦИЯ ПРОВЕРКИ ============

async def check_username_parallel(username: str, user_id=None) -> bool:
    """
    Параллельная проверка с ФИЛЬТРОМ ПРОДАЖ
    
    ЛОГИКА:
    1. Если Fragment говорит "на продаже" → ЗАНЯТ (в БД)
    2. Если Bot API говорит "свободен" И Fragment НЕ на продаже → СВОБОДЕН
    3. Если есть противоречия → приоритет Bot API
    """
    
    # Проверяем БД
    if is_in_free_db(username):
        return True
    if is_in_taken_db(username):
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
        
        # Логирование результатов
        logging.info(f"📊 РЕЗУЛЬТАТЫ для @{username}:")
        logging.info(f"   Bot API: {bot_api_result}")
        logging.info(f"   Fragment: {fragment_result}")
        logging.info(f"   t.me: {web_result}")
        
        # 🔥 НОВАЯ ЛОГИКА С УЧЕТОМ ПРОДАЖ
        
        # 1. Fragment говорит False (занят ИЛИ на продаже) → ПРОПУСКАЕМ
        if fragment_result is False:
            add_to_taken_db(username, user_id, "fragment_taken_or_sale", "Fragment: занят или на продаже")
            logging.info(f"🔴 ИТОГ: @{username} ПРОПУЩЕН ❌ (Fragment: занят/продажа)")
            return False
        
        # 2. Bot API говорит "свободен" И Fragment НЕ против (True или None)
        if bot_api_result is True and fragment_result != False:
            add_to_free_db(username, user_id, "bot_api_confirmed")
            logging.info(f"🟢 ИТОГ: @{username} СВОБОДЕН ✅ (Bot API + Fragment OK)")
            return True
        
        # 3. Все методы согласны что свободен
        free_votes = sum(1 for v in [bot_api_result, fragment_result, web_result] if v is True)
        if free_votes >= 2:
            add_to_free_db(username, user_id, "majority_free")
            logging.info(f"🟢 ИТОГ: @{username} СВОБОДЕН ✅ (большинство)")
            return True
        
        # 4. Bot API говорит "занят"
        if bot_api_result is False:
            add_to_taken_db(username, user_id, "bot_api_taken", "Bot API: occupied")
            logging.info(f"🔴 ИТОГ: @{username} ЗАНЯТ ❌ (Bot API)")
            return False
        
        # 5. t.me говорит "занят"
        if web_result is False:
            add_to_taken_db(username, user_id, "web_taken", "t.me: has profile")
            logging.info(f"🔴 ИТОГ: @{username} ЗАНЯТ ❌ (t.me)")
            return False
        
        # 6. Все неопределенно → по умолчанию считаем занятым
        add_to_taken_db(username, user_id, "uncertain", "All methods uncertain")
        logging.info(f"🔴 ИТОГ: @{username} ЗАНЯТ ❌ (неопределенность)")
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
        f"🎯 <b>Поиск ДЕЙСТВИТЕЛЬНО СВОБОДНЫХ юзернеймов</b>\n\n"
        f"⚡️ <b>УМНАЯ ПРОВЕРКА:</b>\n"
        f"✅ Игнорирует юзернеймы на продаже\n"
        f"✅ Параллельная проверка (10 шт)\n"
        f"✅ 3 метода: Bot API, Fragment, t.me\n"
        f"✅ Только незанятые и не продающиеся!\n\n"
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
            caption=f"📁 Занятые/На продаже ({len(taken_db)})"
        )
        
        await message.answer_document(
            types.FSInputFile(free_file),
            caption=f"✅ Свободные ({len(free_db)})"
        )
        
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
                f"✅ <b>@{username} СВОБОДЕН!</b>\n\n"
                f"(не занят и не продается)",
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )
        else:
            await message.answer(
                f"❌ <b>@{username} ЗАНЯТ</b>\n\n"
                f"(или на продаже на Fragment)",
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
    
    # Подсчет причин для занятых
    reasons = {}
    for username, data in taken_db.items():
        reason = data.get("reason", "unknown")
        reasons[reason] = reasons.get(reason, 0) + 1
    
    reasons_text = "\n".join([f"  • {r[:30]}: {c}" for r, c in list(reasons.items())[:5]])
    
    await callback_query.message.edit_text(
        f"📊 <b>Статистика</b>\n\n"
        f"✅ Свободных: <code>{len(free_db)}</code>\n"
        f"❌ Занятых/На продаже: <code>{len(taken_db)}</code>\n\n"
        f"🛒 Топ причин занятости:\n"
        f"{reasons_text if reasons_text else '  (нет данных)'}\n\n"
        f"⚡️ Игнорируются продажи на Fragment!",
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
        "⚡️ <b>ПОИСК...</b>\n\n"
        "<i>Игнорирую продажи на Fragment!</i>",
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
                    f"<i>Фильтрую продажи...</i>",
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
                    f"🎉 <b>НАЙДЕН!</b>\n\n"
                    f"✅ <code>@{username}</code>\n\n"
                    f"<b>✓ Свободен</b>\n"
                    f"<b>✓ Не продается</b>",
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
            f"Попробуй изменить настройки!",
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
        f"<b>Будут проигнорированы продажи!</b>\n\n"
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
        f"⚡️ <b>СТАРТ!</b>\n\n"
        f"Всего: {total}\n"
        f"Игнорирую продажи!",
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
                            f"🎉 <b>#{len(found_free)}!</b>\n\n"
                            f"✅ <code>@{username}</code>\n\n"
                            f"✓ Свободен\n"
                            f"✓ Не продается",
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
            f"✅ Найдено свободных: <b>{len(found_free)}</b>\n"
            f"⏱ {elapsed_min} мин\n"
            f"⚡️ {avg_speed:.1f}/сек\n\n"
            f"📝 Примеры:\n{samples}\n\n"
            f"<b>Все не на продаже!</b>",
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
            f"Время: {elapsed_min} мин",
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
        f"⚡️ Умная проверка активна!\n"
        f"🛒 Игнорирую продажи на Fragment",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard()
    )

# ============ ЗАПУСК ============

async def on_startup():
    logging.info("🚀 Бот запущен!")
    logging.info("🛒 Фильтр продаж Fragment активен!")
    logging.info(f"⚡️ Параллельных проверок: {RATE_LIMITER._value}")
    
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
