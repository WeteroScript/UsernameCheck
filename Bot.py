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

# ⚡️ ОПТИМИЗИРОВАННЫЕ НАСТРОЙКИ СКОРОСТИ
RATE_LIMITER = Semaphore(10)  # 10 одновременных проверок (было 2)
CHECK_DELAY = 0.3  # 0.3 секунды между проверками (было 2.0)
BATCH_SIZE = 10  # Проверяем по 10 юзернеймов одновременно
CONNECTION_LIMIT = 100  # Максимум соединений

# Глобальная HTTP сессия (переиспользуется)
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

def log_debug(username: str, method: str, status: str, html_snippet=""):
    try:
        with open(DEBUG_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f"\n{'='*80}\n")
            f.write(f"Time: {datetime.now().isoformat()}\n")
            f.write(f"Username: @{username}\n")
            f.write(f"Method: {method}\n")
            f.write(f"Status: {status}\n")
            if html_snippet:
                f.write(f"HTML snippet:\n{html_snippet[:500]}\n")
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
    max_combinations = 15000  # Увеличено с 10000
    
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
            ttl_dns_cache=300,  # Кэш DNS на 5 минут
            enable_cleanup_closed=True
        )
        timeout = aiohttp.ClientTimeout(total=10, connect=5)
        http_session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout
        )
    return http_session

# ============ БЫСТРАЯ ПРОВЕРКА ЧЕРЕЗ BOT API ============

async def check_username_bot_api_fast(username: str) -> Optional[bool]:
    """
    Быстрая проверка через Bot API
    Returns: True (свободен), False (занят), None (ошибка)
    """
    try:
        session = await get_http_session()
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChat"
        params = {"chat_id": f"@{username}"}
        
        async with session.get(url, params=params) as response:
            data = await response.json()
            
            if data.get("ok"):
                return False  # Найден = занят
            else:
                error_desc = data.get("description", "").lower()
                if "chat not found" in error_desc or "not occupied" in error_desc:
                    return True  # Свободен
                elif "occupied" in error_desc:
                    return False  # Занят
                return None
                
    except asyncio.TimeoutError:
        return None
    except Exception as e:
        logging.debug(f"Bot API error for @{username}: {e}")
        return None

# ============ БЫСТРАЯ ПРОВЕРКА ЧЕРЕЗ FRAGMENT ============

async def check_username_fragment_fast(username: str) -> Optional[bool]:
    """
    Быстрая проверка через Fragment
    """
    try:
        session = await get_http_session()
        url = f"https://fragment.com/username/{username}"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml",
        }
        
        async with session.get(url, headers=headers, allow_redirects=True) as response:
            status = response.status
            
            if status == 404:
                return True  # Свободен
            
            if status == 200:
                html = await response.text()
                html_lower = html.lower()
                
                # Признаки занятости
                if any(marker in html for marker in [
                    'tm-section-bid-button',
                    'Place a Bid',
                    'Current Bid',
                    'table-cell-value tm-value'
                ]):
                    return False  # На аукционе = занят
                
                if 'sold' in html_lower or 'taken' in html_lower:
                    if 'status' in html_lower:
                        return False  # Занят
                
                if 'not for sale' in html_lower:
                    return False  # Занят
                
                if re.search(r'\$\d+|TON\s*\d+', html):
                    return False  # Есть цена = занят
                
                # Если ничего не нашли - считаем свободным (оптимистично)
                if len(html) < 5000:
                    return True
                
                return None  # Неопределенно
            
            return None
                
    except asyncio.TimeoutError:
        return None
    except Exception as e:
        logging.debug(f"Fragment error for @{username}: {e}")
        return None

# ============ БЫСТРАЯ ПРОВЕРКА ЧЕРЕЗ T.ME ============

async def check_username_web_fast(username: str) -> Optional[bool]:
    """
    Быстрая проверка через t.me
    """
    try:
        session = await get_http_session()
        url = f"https://t.me/{username}"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "text/html",
        }
        
        async with session.get(url, headers=headers, allow_redirects=True) as response:
            status = response.status
            
            if status == 404:
                return True  # Свободен
            
            if status == 200:
                # Читаем только первые 3000 байт для скорости
                html = await response.text()
                html = html[:3000]
                
                # Признаки существующего профиля
                if any(marker in html for marker in [
                    'tgme_page_photo',
                    'tgme_page_title',
                    'tgme_page_description'
                ]):
                    return False  # Занят
                
                # Пустая страница
                if "if you have" in html.lower():
                    return True  # Свободен
                
                return None
            
            return None
                
    except asyncio.TimeoutError:
        return None
    except Exception as e:
        logging.debug(f"t.me error for @{username}: {e}")
        return None

# ============ ⚡️ ПАРАЛЛЕЛЬНАЯ ПРОВЕРКА (ГЛАВНАЯ) ============

async def check_username_parallel(username: str, user_id=None) -> bool:
    """
    СУПЕР-БЫСТРАЯ параллельная проверка через все методы одновременно!
    """
    
    # Проверяем в БД
    if is_in_free_db(username):
        return True
    if is_in_taken_db(username):
        return False
    
    async with RATE_LIMITER:
        # Небольшая задержка
        await asyncio.sleep(CHECK_DELAY)
        
        # ⚡️ ЗАПУСКАЕМ ВСЕ 3 ПРОВЕРКИ ПАРАЛЛЕЛЬНО!
        results = await asyncio.gather(
            check_username_bot_api_fast(username),
            check_username_fragment_fast(username),
            check_username_web_fast(username),
            return_exceptions=True
        )
        
        bot_api_result, fragment_result, web_result = results
        
        # Обработка исключений
        if isinstance(bot_api_result, Exception):
            bot_api_result = None
        if isinstance(fragment_result, Exception):
            fragment_result = None
        if isinstance(web_result, Exception):
            web_result = None
        
        # Логирование
        logging.info(f"@{username} → Bot:{bot_api_result} Fragment:{fragment_result} Web:{web_result}")
        
        # Подсчет голосов
        free_votes = sum(1 for v in [bot_api_result, fragment_result, web_result] if v is True)
        taken_votes = sum(1 for v in [bot_api_result, fragment_result, web_result] if v is False)
        
        # СТРОГАЯ ЛОГИКА:
        # Если хотя бы 1 метод говорит "занят" - считаем занятым
        if taken_votes >= 1:
            reason = f"Taken votes: {taken_votes}"
            add_to_taken_db(username, user_id, "parallel_check", reason)
            return False
        
        # Если минимум 2 метода говорят "свободен" - считаем свободным
        if free_votes >= 2:
            add_to_free_db(username, user_id, "parallel_check")
            return True
        
        # Если Bot API говорит "свободен" - доверяем ему
        if bot_api_result is True:
            add_to_free_db(username, user_id, "bot_api")
            return True
        
        # В остальных случаях - считаем занятым (безопаснее)
        add_to_taken_db(username, user_id, "safety", "Insufficient confirmations")
        return False

# ============ ⚡️ БАТЧ-ПРОВЕРКА ============

async def check_usernames_batch(usernames: List[str], user_id=None) -> Dict[str, bool]:
    """
    Проверка пачки юзернеймов одновременно (до 10 штук)
    Возвращает: {username: is_available}
    """
    tasks = []
    for username in usernames:
        tasks.append(check_username_parallel(username, user_id))
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    output = {}
    for username, result in zip(usernames, results):
        if isinstance(result, Exception):
            output[username] = False  # При ошибке считаем занятым
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
        f"⚡️ <b>ТУРБО-РЕЖИМ:</b>\n"
        f"✅ Параллельная проверка 3 методов\n"
        f"✅ До 10 юзернеймов одновременно\n"
        f"✅ Скорость: ~10-30 юзернеймов/мин\n\n"
        f"<b>Методы:</b> Bot API, Fragment, t.me\n\n"
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
        
        os.remove(taken_file)
        os.remove(free_file)
        
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

# ============ CALLBACK HANDLERS ============

@dp.callback_query(lambda c: c.data == "show_stats")
async def show_stats(callback_query: types.CallbackQuery):
    await callback_query.answer()
    
    taken_db = load_db(TAKEN_DB_FILE)
    free_db = load_db(FREE_DB_FILE)
    
    await callback_query.message.edit_text(
        f"📊 <b>Статистика</b>\n\n"
        f"✅ Свободных: <code>{len(free_db)}</code>\n"
        f"❌ Занятых: <code>{len(taken_db)}</code>\n\n"
        f"⚡️ Турбо-режим активен!",
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
        "⚡️ <b>ТУРБО-ПОИСК...</b>\n\n"
        "<i>Параллельная проверка активна!</i>",
        parse_mode=ParseMode.HTML
    )
    
    max_attempts = 50  # Увеличено
    found = False
    
    for i in range(0, max_attempts, BATCH_SIZE):
        # Генерируем пачку юзернеймов
        batch = []
        for _ in range(min(BATCH_SIZE, max_attempts - i)):
            username = generate_username(settings)
            if not is_in_taken_db(username) and not is_in_free_db(username):
                batch.append(username)
        
        if not batch:
            continue
        
        # Обновляем прогресс
        if i > 0 and i % 20 == 0:
            try:
                await waiting_message.edit_text(
                    f"⚡️ <b>Проверяю...</b> {i}/{max_attempts}\n\n"
                    f"<i>Параллельно: {len(batch)} юзернеймов</i>",
                    parse_mode=ParseMode.HTML
                )
            except:
                pass
        
        # ⚡️ ПРОВЕРЯЕМ ПАЧКУ ПАРАЛЛЕЛЬНО
        results = await check_usernames_batch(batch, user_id)
        
        # Ищем свободный
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
                    f"🎉 <b>Найден!</b>\n\n"
                    f"✅ <code>@{username}</code>\n\n"
                    f"<b>Проверен параллельно!</b>",
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
                [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="generate_username")],
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
        f"⚡️ <b>ТУРБО-ПРОВЕРКА</b>\n\n"
        f"Комбинаций: <code>{total}</code>\n"
        f"Время: ~<b>{estimated_minutes} мин</b>\n\n"
        f"<b>Параллельно проверяется {BATCH_SIZE} юзернеймов!</b>\n\n"
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
    """⚡️ ТУРБО массовая проверка"""
    total = len(all_usernames)
    
    await message.edit_text(
        f"⚡️ <b>ТУРБО-РЕЖИМ АКТИВИРОВАН!</b>\n\n"
        f"Проверяю {total} комбинаций...\n"
        f"Параллельно: {BATCH_SIZE} юзернеймов",
        parse_mode=ParseMode.HTML
    )
    
    checked = 0
    found_free = []
    last_update = datetime.now()
    start_time = datetime.now()
    
    # Батч-обработка
    for i in range(0, len(all_usernames), BATCH_SIZE):
        batch = all_usernames[i:i + BATCH_SIZE]
        
        # Фильтруем уже проверенные
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
            # ⚡️ ПАРАЛЛЕЛЬНАЯ ПРОВЕРКА ПАЧКИ
            results = await check_usernames_batch(to_check, user_id)
            
            for username, is_free in results.items():
                if is_free:
                    found_free.append(username)
                    
                    # Отправляем найденный
                    keyboard = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [InlineKeyboardButton(text="✅ Забрать", url=f"https://t.me/{username}")],
                            [InlineKeyboardButton(text="🔎 Fragment", url=f"https://fragment.com/username/{username}")]
                        ]
                    )
                    
                    try:
                        await bot.send_message(
                            user_id,
                            f"🎉 Найден #{len(found_free)}!\n\n"
                            f"✅ <code>@{username}</code>",
                            parse_mode=ParseMode.HTML,
                            reply_markup=keyboard
                        )
                    except:
                        pass
                
                checked += 1
        
        # Обновляем прогресс каждые 10 секунд
        now = datetime.now()
        if (now - last_update).total_seconds() >= 10:
            try:
                progress = (checked / total) * 100
                elapsed = (now - start_time).total_seconds()
                speed = checked / elapsed if elapsed > 0 else 0
                remaining_sec = (total - checked) / speed if speed > 0 else 0
                remaining_min = int(remaining_sec / 60)
                
                await message.edit_text(
                    f"⚡️ <b>ТУРБО-ПРОВЕРКА</b>\n\n"
                    f"📊 {checked}/{total} ({progress:.1f}%)\n"
                    f"✅ Найдено: {len(found_free)}\n"
                    f"⚡️ Скорость: ~{speed:.1f}/сек\n"
                    f"⏱ Осталось: ~{remaining_min} мин",
                    parse_mode=ParseMode.HTML
                )
                last_update = now
            except:
                pass
    
    # Финал
    elapsed_total = (datetime.now() - start_time).total_seconds()
    elapsed_min = int(elapsed_total / 60)
    avg_speed = checked / elapsed_total if elapsed_total > 0 else 0
    
    if found_free:
        samples = "\n".join([f"• <code>@{u}</code>" for u in found_free[:20]])
        if len(found_free) > 20:
            samples += f"\n... +{len(found_free) - 20} еще"
        
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
            f"⏱ Время: {elapsed_min} мин\n"
            f"⚡️ Скорость: {avg_speed:.1f} юзернеймов/сек\n\n"
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
            f"😔 Не найдено свободных\n\n"
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
        f"⚡️ Турбо-режим активен!\n"
        f"Скорость: до 30 юзернеймов/мин",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard()
    )

# ============ ЗАПУСК ============

async def on_startup():
    """Инициализация при запуске"""
    logging.info("🚀 Бот запущен в ТУРБО-РЕЖИМЕ!")
    logging.info(f"⚡️ Параллельных проверок: {RATE_LIMITER._value}")
    logging.info(f"⚡️ Задержка: {CHECK_DELAY} сек")
    logging.info(f"⚡️ Размер пачки: {BATCH_SIZE}")
    
    if not os.path.exists(TAKEN_DB_FILE):
        save_db(TAKEN_DB_FILE, {})
    if not os.path.exists(FREE_DB_FILE):
        save_db(FREE_DB_FILE, {})

async def on_shutdown():
    """Очистка при остановке"""
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
        logging.info("⛔ Остановлено пользователем")
