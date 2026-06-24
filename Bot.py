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

# ⚡️ БЕЗОПАСНЫЕ НАСТРОЙКИ (защита от rate limit)
RATE_LIMITER = Semaphore(5)  # Уменьшено с 10 до 5
CHECK_DELAY = 1.0  # Увеличено с 0.3 до 0.5
BATCH_SIZE = 5  # Уменьшено с 10 до 5
CONNECTION_LIMIT = 50  # Уменьшено с 100 до 50
MAX_RETRIES = 3  # Максимум повторов при ошибке

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

# ============ 🔥 ПРОВЕРКА С ОБРАБОТКОЙ ОШИБОК ============

async def safe_request_with_retry(func, *args, **kwargs):
    """
    Безопасный запрос с автоматическими повторами
    """
    for attempt in range(MAX_RETRIES):
        try:
            return await func(*args, **kwargs)
        except TelegramRetryAfter as e:
            # Telegram просит подождать
            wait_time = e.retry_after + 1
            logging.warning(f"⏱ Rate limit! Жду {wait_time} секунд...")
            await asyncio.sleep(wait_time)
            continue
        except TelegramAPIError as e:
            logging.error(f"❌ Telegram API error: {e}")
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)  # Экспоненциальная задержка
                continue
            return None
        except aiohttp.ClientResponseError as e:
            if e.status == 429:  # Too Many Requests
                wait_time = int(e.headers.get('Retry-After', 30))
                logging.warning(f"⏱ HTTP 429! Жду {wait_time} секунд...")
                await asyncio.sleep(wait_time)
                continue
            elif e.status >= 500:  # Server errors
                logging.error(f"❌ Server error {e.status}")
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(3)
                    continue
                return None
            else:
                logging.error(f"❌ HTTP error {e.status}: {e.message}")
                return None
        except asyncio.TimeoutError:
            logging.warning(f"⏱ Timeout (attempt {attempt + 1}/{MAX_RETRIES})")
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
    Проверка через Bot API с обработкой ошибок
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
                error_desc = data.get("description", "")
                
                if "chat not found" in error_desc.lower():
                    logging.info(f"✅ Bot API: @{username} → СВОБОДЕН")
                    return True
                elif "username is not occupied" in error_desc.lower():
                    logging.info(f"✅ Bot API: @{username} → СВОБОДЕН")
                    return True
                elif "username is occupied" in error_desc.lower():
                    logging.info(f"❌ Bot API: @{username} → ЗАНЯТ")
                    return False
                elif data.get("error_code") == 400:
                    logging.info(f"✅ Bot API: @{username} → СВОБОДЕН")
                    return True
                else:
                    logging.warning(f"⚠️ Bot API: @{username} → НЕИЗВЕСТНО")
                    return None
    
    return await safe_request_with_retry(_check)

# ============ FRAGMENT ============

async def check_username_fragment_fast(username: str) -> Optional[bool]:
    """
    Проверка Fragment с обработкой ошибок
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
                
                # Проверка на продажу
                sale_markers = [
                    'place a bid', 'buy now', 'tm-section-bid-button',
                    'tm-section-countdown', 'highest bidder', 'current bid'
                ]
                
                if any(marker in html_lower for marker in sale_markers):
                    logging.info(f"🛒 Fragment: @{username} → НА ПРОДАЖЕ")
                    return False
                
                if re.search(r'(?:\$|TON|₽)\s*\d+', html):
                    logging.info(f"🛒 Fragment: @{username} → НА ПРОДАЖЕ (цена)")
                    return False
                
                if 'sold' in html_lower or 'taken' in html_lower:
                    logging.info(f"❌ Fragment: @{username} → ЗАНЯТ")
                    return False
                
                if len(html) < 3000:
                    logging.info(f"✅ Fragment: @{username} → СВОБОДЕН")
                    return True
                
                logging.warning(f"⚠️ Fragment: @{username} → НЕОПРЕДЕЛЕННО")
                return None
            
            return None
    
    return await safe_request_with_retry(_check)

# ============ T.ME ============

async def check_username_web_fast(username: str) -> Optional[bool]:
    """
    Проверка t.me с обработкой ошибок
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
                
                if any(marker in html_short for marker in [
                    'tgme_page_photo', 'tgme_page_title', 'tgme_page_description'
                ]):
                    logging.info(f"❌ t.me: @{username} → ЗАНЯТ")
                    return False
                
                if "if you have" in html_short.lower():
                    logging.info(f"✅ t.me: @{username} → СВОБОДЕН")
                    return True
                
                logging.warning(f"⚠️ t.me: @{username} → НЕОПРЕДЕЛЕННО")
                return None
            
            return None
    
    return await safe_request_with_retry(_check)

# ============ ГЛАВНАЯ ПРОВЕРКА ============

async def check_username_parallel(username: str, user_id=None) -> bool:
    """
    Параллельная проверка с защитой от rate limit
    """
    
    if is_in_free_db(username):
        return True
    if is_in_taken_db(username):
        return False
    
    async with RATE_LIMITER:
        # Задержка для защиты от rate limit
        await asyncio.sleep(CHECK_DELAY)
        
        logging.info(f"\n{'='*60}\n🔍 ПРОВЕРЯЮ: @{username}\n{'='*60}")
        
        # Запускаем проверки параллельно
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
        
        logging.info(f"📊 РЕЗУЛЬТАТЫ для @{username}:")
        logging.info(f"   Bot API: {bot_api_result}")
        logging.info(f"   Fragment: {fragment_result}")
        logging.info(f"   t.me: {web_result}")
        
        # Логика принятия решения
        if fragment_result is False:
            add_to_taken_db(username, user_id, "fragment", "На продаже или занят")
            logging.info(f"🔴 ИТОГ: @{username} ЗАНЯТ/ПРОДАЖА ❌")
            return False
        
        if bot_api_result is True and fragment_result != False:
            add_to_free_db(username, user_id, "bot_api")
            logging.info(f"🟢 ИТОГ: @{username} СВОБОДЕН ✅")
            return True
        
        free_votes = sum(1 for v in [bot_api_result, fragment_result, web_result] if v is True)
        if free_votes >= 2:
            add_to_free_db(username, user_id, "majority")
            logging.info(f"🟢 ИТОГ: @{username} СВОБОДЕН ✅")
            return True
        
        if bot_api_result is False or web_result is False:
            add_to_taken_db(username, user_id, "confirmed", "Занят")
            logging.info(f"🔴 ИТОГ: @{username} ЗАНЯТ ❌")
            return False
        
        add_to_taken_db(username, user_id, "uncertain", "Неопределенно")
        logging.info(f"🔴 ИТОГ: @{username} ЗАНЯТ ❌ (по умолчанию)")
        return False

# ============ БАТЧ-ПРОВЕРКА ============

async def check_usernames_batch(usernames: List[str], user_id=None) -> Dict[str, bool]:
    """
    Проверка пачки с защитой от rate limit
    """
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

# ============ БЕЗОПАСНАЯ ОТПРАВКА СООБЩЕНИЙ ============

async def safe_send_message(chat_id: int, text: str, **kwargs):
    """
    Отправка с обработкой rate limit
    """
    try:
        return await bot.send_message(chat_id, text, **kwargs)
    except TelegramRetryAfter as e:
        logging.warning(f"⏱ Rate limit при отправке! Жду {e.retry_after} сек")
        await asyncio.sleep(e.retry_after + 1)
        return await bot.send_message(chat_id, text, **kwargs)
    except Exception as e:
        logging.error(f"❌ Ошибка отправки: {e}")
        return None

async def safe_edit_message(message: types.Message, text: str, **kwargs):
    """
    Редактирование с обработкой rate limit
    """
    try:
        return await message.edit_text(text, **kwargs)
    except TelegramRetryAfter as e:
        logging.warning(f"⏱ Rate limit при редактировании! Жду {e.retry_after} сек")
        await asyncio.sleep(e.retry_after + 1)
        return await message.edit_text(text, **kwargs)
    except Exception as e:
        logging.error(f"❌ Ошибка редактирования: {e}")
        return None

# ============ КЛАВИАТУРЫ ============

def get_settings_keyboard(user_id: int):
    settings = get_user_settings(user_id)
    alphabet_status = "✅" if settings["use_full_alphabet"] else "❌"
    
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=f"{alphabet_status} Все буквы", 
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
    
    if not os.path.exists(TAKEN_DB_FILE):
        save_db(TAKEN_DB_FILE, {})
    if not os.path.exists(FREE_DB_FILE):
        save_db(FREE_DB_FILE, {})
    
    await safe_send_message(
        message.chat.id,
        f"Привет, {user_name}! 👋\n\n"
        f"🎯 <b>Поиск свободных юзернеймов</b>\n\n"
        f"🛡 <b>ЗАЩИТА ОТ RATE LIMIT:</b>\n"
        f"✅ Автоматические повторы\n"
        f"✅ Умные задержки\n"
        f"✅ Безопасная скорость\n"
        f"✅ Игнорирует продажи\n\n"
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
        
        await safe_send_message(
            message.chat.id,
            f"📊 Занятых: {len(taken_db)}, Свободных: {len(free_db)}",
            parse_mode=ParseMode.HTML
        )
        
        await message.answer_document(types.FSInputFile(taken_file), caption=f"📁 Занятые ({len(taken_db)})")
        await message.answer_document(types.FSInputFile(free_file), caption=f"✅ Свободные ({len(free_db)})")
        
        if os.path.exists(DEBUG_LOG_FILE):
            await message.answer_document(types.FSInputFile(DEBUG_LOG_FILE), caption=f"🔍 Debug")
        
        os.remove(taken_file)
        os.remove(free_file)
        
    except Exception as e:
        await safe_send_message(message.chat.id, f"❌ Ошибка: {e}")

@dp.message(Command("check"))
async def check_command(message: types.Message):
    try:
        username = message.text.split()[1].replace("@", "")
        
        msg = await safe_send_message(message.chat.id, f"🔍 Проверяю @{username}...")
        
        is_free = await check_username_parallel(username, message.from_user.id)
        
        if is_free:
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
    
    await safe_edit_message(
        callback_query.message,
        f"📊 <b>Статистика</b>\n\n"
        f"✅ Свободных: {len(free_db)}\n"
        f"❌ Занятых: {len(taken_db)}\n\n"
        f"🛡 Защита от rate limit активна!",
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
    
    await safe_edit_message(
        callback_query.message,
        f"⚙️ <b>Настройки</b>\n\n"
        f"📌 Буква: <b>{settings['letter'].upper()}</b>\n"
        f"📌 Повторений: <b>{settings['repeat_count']}</b>\n\n"
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
    await safe_edit_message(
        callback_query.message,
        "🔤 Выбери букву:",
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
    await safe_edit_message(
        callback_query.message,
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
    user_settings[user_id] = {"letter": "s", "repeat_count": 2, "use_full_alphabet": True}
    await callback_query.answer("✅ Сброшено")
    await open_settings(callback_query)

@dp.callback_query(lambda c: c.data == "generate_username")
async def process_generate_username(callback_query: types.CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    settings = get_user_settings(user_id)
    
    waiting_message = await safe_edit_message(
        callback_query.message,
        "⚡️ <b>ПОИСК...</b>\n\n<i>Безопасный режим</i>",
        parse_mode=ParseMode.HTML
    )
    
    max_attempts = 30  # Уменьшено для безопасности
    found = False
    
    for i in range(0, max_attempts, BATCH_SIZE):
        batch = []
        for _ in range(min(BATCH_SIZE, max_attempts - i)):
            username = generate_username(settings)
            if not is_in_taken_db(username) and not is_in_free_db(username):
                batch.append(username)
        
        if not batch:
            continue
        
        if i > 0 and i % 10 == 0:
            await safe_edit_message(
                waiting_message,
                f"⚡️ <b>Проверяю...</b> {i}/{max_attempts}",
                parse_mode=ParseMode.HTML
            )
        
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
                await safe_edit_message(
                    waiting_message,
                    f"🎉 <b>НАЙДЕН!</b>\n\n✅ <code>@{username}</code>",
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
                [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")]
            ]
        )
        await safe_edit_message(
            waiting_message,
            f"😔 Не найдено за {max_attempts} попыток",
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
        f"⚡️ <b>МАССОВАЯ ПРОВЕРКА</b>\n\n"
        f"Комбинаций: {total}\n"
        f"Время: ~{estimated_minutes} мин\n\n"
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
    total = len(all_usernames)
    
    await safe_edit_message(
        message,
        f"⚡️ <b>ЗАПУСК!</b>\n\nВсего: {total}",
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
                f"⚡️ <b>ПРОВЕРКА...</b>\n\n"
                f"📊 {checked}/{total} ({progress:.1f}%)\n"
                f"✅ Найдено: {len(found_free)}",
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
            f"Время: {elapsed_min} мин\n\n"
            f"Примеры:\n{samples}",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")]]
            )
        )
    else:
        await safe_edit_message(
            message,
            f"😔 Не найдено\n\nПроверено: {checked}\nВремя: {elapsed_min} мин",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")]]
            )
        )

@dp.callback_query(lambda c: c.data == "get_db")
async def get_db_callback(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await get_db_command(callback_query.message)

@dp.callback_query(lambda c: c.data == "main_menu")
async def main_menu(callback_query: types.CallbackQuery):
    await callback_query.answer()
    
    await safe_edit_message(
        callback_query.message,
        f"🏠 <b>Главное меню</b>\n\n🛡 Защита от rate limit активна!",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard()
    )

# ============ ЗАПУСК ============

async def on_startup():
    logging.info("🚀 Бот запущен!")
    logging.info("🛡 Защита от rate limit активна!")
    logging.info(f"⚡️ Параллельных проверок: {RATE_LIMITER._value}")
    logging.info(f"⚡️ Задержка: {CHECK_DELAY} сек")
    logging.info(f"⚡️ Батч: {BATCH_SIZE}")
    
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
