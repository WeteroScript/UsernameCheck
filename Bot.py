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
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from datetime import datetime
import itertools
from asyncio import Semaphore

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Токен бота из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# Инициализация бота и диспетчера
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# Rate limiting
RATE_LIMITER = Semaphore(2)  # Максимум 2 одновременных проверки
CHECK_DELAY = 1.5  # Задержка между проверками в секундах

# Класс для состояний
class SettingsStates(StatesGroup):
    waiting_for_letter = State()
    waiting_for_count = State()

# Пути к файлам базы данных
TAKEN_DB_FILE = "taken_usernames.json"
FREE_DB_FILE = "free_usernames.json"

# Глобальные настройки
user_settings = {}

# Функции для работы с базой данных
def load_db(file_path):
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Ошибка загрузки БД {file_path}: {e}")
            return {}
    return {}

def save_db(file_path, data):
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Ошибка сохранения БД {file_path}: {e}")

def add_to_taken_db(username, user_id=None, method="unknown"):
    db = load_db(TAKEN_DB_FILE)
    if username not in db:
        db[username] = {
            "checked_at": datetime.now().isoformat(),
            "checked_by": str(user_id) if user_id else "unknown",
            "method": method
        }
        save_db(TAKEN_DB_FILE, db)
        return True
    return False

def add_to_free_db(username, user_id=None, method="unknown"):
    db = load_db(FREE_DB_FILE)
    if username not in db:
        db[username] = {
            "found_at": datetime.now().isoformat(),
            "found_by": str(user_id) if user_id else "unknown",
            "method": method
        }
        save_db(FREE_DB_FILE, db)
        return True
    return False

def is_in_taken_db(username):
    db = load_db(TAKEN_DB_FILE)
    return username in db

def is_in_free_db(username):
    db = load_db(FREE_DB_FILE)
    return username in db

def get_user_settings(user_id):
    if user_id not in user_settings:
        user_settings[user_id] = {
            "letter": "s",
            "repeat_count": 2,
            "use_full_alphabet": True
        }
    return user_settings[user_id]

def generate_username(settings):
    """Генерация юзернейма с повторяющейся буквой ПОДРЯД"""
    
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

def generate_examples(settings, count=4):
    examples = []
    for _ in range(count):
        examples.append(generate_username(settings))
    return examples

def get_all_possible_usernames(settings):
    """Генерирует ВСЕ возможные комбинации юзернеймов"""
    
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
    
    # Ограничение на количество комбинаций
    max_combinations = 10000
    
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

# ======== ПРОВЕРКА ЧЕРЕЗ FRAGMENT.COM ========
async def check_username_fragment(username):
    """
    Проверка юзернейма через Fragment.com
    Fragment - официальная платформа Telegram для продажи юзернеймов
    """
    try:
        # URL для проверки на Fragment
        url = f"https://fragment.com/username/{username}"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
            "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"'
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=15, allow_redirects=True) as response:
                status = response.status
                html = await response.text()
                
                logging.info(f"Fragment ответ для @{username}: статус {status}")
                
                # Анализируем ответ
                if status == 200:
                    # Проверяем, что юзернейм на аукционе или продан
                    if 'class="table-cell-value tm-value"' in html:
                        # Юзернейм на аукционе = занят
                        logging.info(f"Fragment: @{username} на аукционе → ЗАНЯТ")
                        return False
                    elif 'tm-section-bid-button' in html:
                        # Есть кнопка ставки = на аукционе = занят
                        logging.info(f"Fragment: @{username} на аукционе → ЗАНЯТ")
                        return False
                    elif 'Status</div>' in html and 'Sold' in html:
                        # Продан = занят
                        logging.info(f"Fragment: @{username} продан → ЗАНЯТ")
                        return False
                    elif 'Status</div>' in html and 'Taken' in html:
                        # Занят
                        logging.info(f"Fragment: @{username} занят → ЗАНЯТ")
                        return False
                    elif 'Status</div>' in html and 'Available' in html:
                        # Доступен на Fragment
                        logging.info(f"Fragment: @{username} доступен на Fragment → СВОБОДЕН")
                        return True
                    elif 'This username is not for sale' in html:
                        # Не продается = кто-то владеет = занят
                        logging.info(f"Fragment: @{username} не продается → ЗАНЯТ")
                        return False
                    elif 'tm-page-username' in html:
                        # Страница юзернейма существует = скорее всего занят
                        logging.info(f"Fragment: @{username} имеет страницу → ЗАНЯТ")
                        return False
                    else:
                        # Не можем определить точно
                        logging.warning(f"Fragment: @{username} неопределенный статус")
                        return None
                        
                elif status == 404:
                    # Не найден на Fragment = может быть свободен
                    logging.info(f"Fragment: @{username} не найден (404) → возможно СВОБОДЕН")
                    return True
                else:
                    logging.warning(f"Fragment: неожиданный статус {status}")
                    return None
                    
    except asyncio.TimeoutError:
        logging.error(f"Fragment: таймаут для @{username}")
        return None
    except Exception as e:
        logging.error(f"Fragment: ошибка для @{username}: {e}")
        return None

# ======== ПРОВЕРКА ЧЕРЕЗ TELEGRAM BOT API ========
async def check_username_bot_api(username):
    """
    Проверка через официальный Telegram Bot API
    Самый надежный метод!
    """
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChat"
        params = {"chat_id": f"@{username}"}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=10) as response:
                data = await response.json()
                
                if data.get("ok"):
                    # Юзернейм существует и найден
                    chat_type = data.get("result", {}).get("type", "")
                    logging.info(f"Bot API: @{username} найден (тип: {chat_type}) → ЗАНЯТ")
                    return False
                else:
                    error_description = data.get("description", "").lower()
                    if "chat not found" in error_description:
                        # Чат не найден = свободен
                        logging.info(f"Bot API: @{username} не найден → СВОБОДЕН")
                        return True
                    elif "username is not occupied" in error_description:
                        # Юзернейм не занят
                        logging.info(f"Bot API: @{username} не занят → СВОБОДЕН")
                        return True
                    else:
                        logging.warning(f"Bot API: неизвестная ошибка для @{username}: {error_description}")
                        return None
                        
    except asyncio.TimeoutError:
        logging.error(f"Bot API: таймаут для @{username}")
        return None
    except Exception as e:
        logging.error(f"Bot API: ошибка для @{username}: {e}")
        return None

# ======== ПРОВЕРКА ЧЕРЕЗ T.ME ========
async def check_username_web(username):
    """
    Проверка через веб-интерфейс t.me
    Запасной метод
    """
    try:
        url = f"https://t.me/{username}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10, allow_redirects=True) as response:
                if response.status == 200:
                    html = await response.text()
                    
                    # Признаки существующего аккаунта
                    if 'tgme_page_photo' in html or 'tgme_page_title' in html:
                        logging.info(f"t.me: @{username} имеет страницу → ЗАНЯТ")
                        return False
                    # Признаки несуществующего аккаунта
                    elif "If you have <strong>Telegram</strong>, you can contact" in html:
                        logging.info(f"t.me: @{username} стандартная страница → СВОБОДЕН")
                        return True
                    elif "doesn't exist" in html.lower() or "not found" in html.lower():
                        logging.info(f"t.me: @{username} не существует → СВОБОДЕН")
                        return True
                    else:
                        return None
                elif response.status == 404:
                    logging.info(f"t.me: @{username} 404 → СВОБОДЕН")
                    return True
                else:
                    return None
                    
    except Exception as e:
        logging.error(f"t.me: ошибка для @{username}: {e}")
        return None

# ======== ГЛАВНАЯ ФУНКЦИЯ ПРОВЕРКИ ========
async def check_username(username, user_id=None):
    """
    Комплексная проверка юзернейма через несколько методов
    Приоритет: Bot API > Fragment > t.me
    """
    # Сначала проверяем в БД
    if is_in_free_db(username):
        logging.info(f"@{username} уже в БД свободных")
        return True
    if is_in_taken_db(username):
        logging.info(f"@{username} уже в БД занятых")
        return False
    
    # Rate limiting
    async with RATE_LIMITER:
        # Задержка между запросами
        await asyncio.sleep(CHECK_DELAY)
        
        # МЕТОД 1: Telegram Bot API (самый надежный)
        logging.info(f"Проверяю @{username} через Bot API...")
        bot_api_result = await check_username_bot_api(username)
        
        if bot_api_result is not None:
            if bot_api_result:
                add_to_free_db(username, user_id, "bot_api")
                logging.info(f"✅ @{username} СВОБОДЕН (Bot API)")
                return True
            else:
                add_to_taken_db(username, user_id, "bot_api")
                logging.info(f"❌ @{username} ЗАНЯТ (Bot API)")
                return False
        
        # МЕТОД 2: Fragment.com
        logging.info(f"Проверяю @{username} через Fragment...")
        fragment_result = await check_username_fragment(username)
        
        if fragment_result is not None:
            if fragment_result:
                add_to_free_db(username, user_id, "fragment")
                logging.info(f"✅ @{username} СВОБОДЕН (Fragment)")
                return True
            else:
                add_to_taken_db(username, user_id, "fragment")
                logging.info(f"❌ @{username} ЗАНЯТ (Fragment)")
                return False
        
        # МЕТОД 3: t.me (запасной)
        logging.info(f"Проверяю @{username} через t.me...")
        web_result = await check_username_web(username)
        
        if web_result is not None:
            if web_result:
                add_to_free_db(username, user_id, "web")
                logging.info(f"✅ @{username} СВОБОДЕН (t.me)")
                return True
            else:
                add_to_taken_db(username, user_id, "web")
                logging.info(f"❌ @{username} ЗАНЯТ (t.me)")
                return False
        
        # Если все методы не сработали
        logging.error(f"⚠️ Не удалось проверить @{username} ни одним методом")
        add_to_taken_db(username, user_id, "unknown")
        return False

# Клавиатура настроек
def get_settings_keyboard(user_id):
    settings = get_user_settings(user_id)
    
    alphabet_status = "✅" if settings["use_full_alphabet"] else "❌"
    letter_info = f"Буква: {settings['letter'].upper()}"
    count_info = f"Повторений: {settings['repeat_count']}"
    
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{alphabet_status} Все буквы алфавита", 
                    callback_data="toggle_alphabet"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"🔤 {letter_info}", 
                    callback_data="change_letter"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"🔢 {count_info}", 
                    callback_data="change_count"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Сбросить настройки", 
                    callback_data="reset_settings"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 Главное меню", 
                    callback_data="main_menu"
                )
            ]
        ]
    )

# Клавиатура для выбора буквы
def get_letter_keyboard():
    keyboard = []
    row = []
    for i, letter in enumerate(string.ascii_lowercase):
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
        text="⬅️ Назад к настройкам",
        callback_data="back_to_settings"
    )])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# Клавиатура для выбора количества повторений
def get_count_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="2 раза", callback_data="set_count_2"),
                InlineKeyboardButton(text="3 раза", callback_data="set_count_3"),
                InlineKeyboardButton(text="4 раза", callback_data="set_count_4")
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_settings")
            ]
        ]
    )

# Главное меню
def get_main_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✨ Создать 5-значный юзернейм", 
                    callback_data="generate_username"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔍 Проверить все комбинации", 
                    callback_data="check_all"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚙️ Настройки", 
                    callback_data="open_settings"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Статистика", 
                    callback_data="show_stats"
                )
            ]
        ]
    )

# Команда /start
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
        f"🎯 <b>Я помогу найти красивый 5-значный юзернейм для Telegram!</b>\n\n"
        f"<b>Методы проверки:</b>\n"
        f"✅ Telegram Bot API\n"
        f"✅ Fragment.com (официальная площадка)\n"
        f"✅ t.me веб-интерфейс\n\n"
        f"<b>Что я умею:</b>\n"
        f"• Генерировать уникальные юзернеймы\n"
        f"• Проверять их доступность\n"
        f"• Массовая проверка комбинаций\n"
        f"• Настраивать параметры генерации\n\n"
        f"Выбери действие ниже:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard()
    )

# Команда /getdb
@dp.message(Command("getdb"))
async def get_db_command(message: types.Message):
    user_id = message.from_user.id
    
    if not os.path.exists(TAKEN_DB_FILE) or not os.path.exists(FREE_DB_FILE):
        await message.answer("❌ Базы данных еще не созданы!")
        return
    
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
            f"📊 <b>Статистика баз данных:</b>\n\n"
            f"📌 Занятых юзернеймов: <code>{len(taken_db)}</code>\n"
            f"✅ Свободных юзернеймов: <code>{len(free_db)}</code>\n\n"
            f"Отправляю файлы...",
            parse_mode=ParseMode.HTML
        )
        
        await message.answer_document(
            types.FSInputFile(taken_file, filename=f"taken_usernames_{datetime.now().strftime('%Y%m%d')}.json"),
            caption=f"📁 Занятые юзернеймы ({len(taken_db)} шт.)"
        )
        
        await message.answer_document(
            types.FSInputFile(free_file, filename=f"free_usernames_{datetime.now().strftime('%Y%m%d')}.json"),
            caption=f"✅ Свободные юзернеймы ({len(free_db)} шт.)"
        )
        
        os.remove(taken_file)
        os.remove(free_file)
        
    except Exception as e:
        logging.error(f"Ошибка при отправке БД: {e}")
        await message.answer("❌ Ошибка при создании файлов базы данных")

# Обработчик статистики
@dp.callback_query(lambda c: c.data == "show_stats")
async def show_stats(callback_query: types.CallbackQuery):
    await callback_query.answer()
    
    taken_db = load_db(TAKEN_DB_FILE)
    free_db = load_db(FREE_DB_FILE)
    
    # Подсчет методов проверки для свободных
    methods_free = {}
    for username, data in free_db.items():
        method = data.get("method", "unknown")
        methods_free[method] = methods_free.get(method, 0) + 1
    
    # Подсчет методов проверки для занятых
    methods_taken = {}
    for username, data in taken_db.items():
        method = data.get("method", "unknown")
        methods_taken[method] = methods_taken.get(method, 0) + 1
    
    methods_free_text = "\n".join([f"  • {method}: {count}" for method, count in methods_free.items()])
    methods_taken_text = "\n".join([f"  • {method}: {count}" for method, count in methods_taken.items()])
    
    await callback_query.message.edit_text(
        f"📊 <b>Статистика работы бота</b>\n\n"
        f"✅ <b>Свободных юзернеймов:</b> {len(free_db)}\n"
        f"{methods_free_text if methods_free_text else '  (нет данных)'}\n\n"
        f"❌ <b>Занятых юзернеймов:</b> {len(taken_db)}\n"
        f"{methods_taken_text if methods_taken_text else '  (нет данных)'}\n\n"
        f"🔍 <b>Методы проверки:</b>\n"
        f"• bot_api - Telegram Bot API\n"
        f"• fragment - Fragment.com\n"
        f"• web - t.me\n"
        f"• unknown - не определено",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📥 Скачать базы", callback_data="get_db")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
            ]
        )
    )

# Обработчик открытия настроек
@dp.callback_query(lambda c: c.data == "open_settings")
async def open_settings(callback_query: types.CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    
    settings = get_user_settings(user_id)
    
    alphabet_status = "включен" if settings["use_full_alphabet"] else "выключен"
    
    examples = generate_examples(settings, 4)
    examples_text = "\n".join([f"• <code>{ex}</code>" for ex in examples])
    
    await callback_query.message.edit_text(
        f"⚙️ <b>Настройки генерации</b>\n\n"
        f"📌 <b>Текущие настройки:</b>\n"
        f"• Полный алфавит: {alphabet_status}\n"
        f"• Повторяющаяся буква: <b>{settings['letter'].upper()}</b>\n"
        f"• Кол-во повторений: <b>{settings['repeat_count']}</b>\n\n"
        f"📝 <b>Примеры генерации:</b>\n"
        f"{examples_text}\n\n"
        f"Нажми на кнопку, чтобы изменить настройку:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_settings_keyboard(user_id)
    )

# Обработчик возврата к настройкам
@dp.callback_query(lambda c: c.data == "back_to_settings")
async def back_to_settings(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await open_settings(callback_query)

# Обработчик переключения алфавита
@dp.callback_query(lambda c: c.data == "toggle_alphabet")
async def toggle_alphabet(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    
    settings = get_user_settings(user_id)
    settings["use_full_alphabet"] = not settings["use_full_alphabet"]
    
    status = "включен" if settings["use_full_alphabet"] else "выключен"
    await callback_query.answer(f"✅ Полный алфавит {status}", show_alert=False)
    
    await open_settings(callback_query)

# Обработчик смены буквы
@dp.callback_query(lambda c: c.data == "change_letter")
async def change_letter(callback_query: types.CallbackQuery):
    await callback_query.answer()
    
    await callback_query.message.edit_text(
        "🔤 <b>Выбери букву</b>, которая будет повторяться в юзернейме:\n\n"
        "Например: если выберешь <b>S</b> и 2 повторения,\n"
        "юзернеймы будут типа: <code>ssabc</code>, <code>assbc</code>, <code>abssc</code>\n\n"
        "Выбери букву:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_letter_keyboard()
    )

# Обработчик установки буквы
@dp.callback_query(lambda c: c.data.startswith("set_letter_"))
async def set_letter(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    letter = callback_query.data.replace("set_letter_", "")
    
    settings = get_user_settings(user_id)
    settings["letter"] = letter
    
    await callback_query.answer(f"✅ Выбрана буква: {letter.upper()}", show_alert=False)
    
    await open_settings(callback_query)

# Обработчик смены количества повторений
@dp.callback_query(lambda c: c.data == "change_count")
async def change_count(callback_query: types.CallbackQuery):
    await callback_query.answer()
    
    await callback_query.message.edit_text(
        "🔢 <b>Выбери количество повторений</b> буквы в юзернейме:\n\n"
        "Например:\n"
        "• 2 раза: <code>ssabc</code>, <code>assbc</code>, <code>abssc</code>\n"
        "• 3 раза: <code>sssab</code>, <code>asssb</code>, <code>absss</code>\n"
        "• 4 раза: <code>ssssa</code>, <code>assss</code>\n\n"
        "Выбери количество:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_count_keyboard()
    )

# Обработчик установки количества повторений
@dp.callback_query(lambda c: c.data.startswith("set_count_"))
async def set_count(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    count = int(callback_query.data.replace("set_count_", ""))
    
    settings = get_user_settings(user_id)
    settings["repeat_count"] = count
    
    await callback_query.answer(f"✅ Количество повторений: {count}", show_alert=False)
    
    await open_settings(callback_query)

# Обработчик сброса настроек
@dp.callback_query(lambda c: c.data == "reset_settings")
async def reset_settings(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    
    user_settings[user_id] = {
        "letter": "s",
        "repeat_count": 2,
        "use_full_alphabet": True
    }
    
    await callback_query.answer("✅ Настройки сброшены к стандартным", show_alert=False)
    
    await open_settings(callback_query)

# Обработчик генерации юзернейма
@dp.callback_query(lambda c: c.data == "generate_username")
async def process_generate_username(callback_query: types.CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    
    settings = get_user_settings(user_id)
    
    waiting_message = await callback_query.message.edit_text(
        "⏳ <b>Генерирую красивый юзернейм и проверяю его доступность...</b>\n\n"
        "<i>🔍 Проверка через Bot API, Fragment и t.me</i>\n"
        "<i>⏱ Это может занять несколько секунд</i>",
        parse_mode=ParseMode.HTML
    )
    
    # Пытаемся найти свободный юзернейм
    attempts = 0
    max_attempts = 20
    
    while attempts < max_attempts:
        username = generate_username(settings)
        
        # Обновляем прогресс
        if attempts > 0 and attempts % 5 == 0:
            try:
                await waiting_message.edit_text(
                    f"⏳ <b>Проверяю юзернеймы...</b>\n\n"
                    f"Попытка {attempts}/{max_attempts}\n"
                    f"Последний проверенный: <code>@{username}</code>\n\n"
                    f"<i>Используются все методы проверки</i>",
                    parse_mode=ParseMode.HTML
                )
            except:
                pass
        
        # Проверяем в БД
        if is_in_free_db(username):
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Забрать юзернейм", url=f"https://t.me/{username}")],
                    [InlineKeyboardButton(text="🔎 Проверить на Fragment", url=f"https://fragment.com/username/{username}")],
                    [
                        InlineKeyboardButton(text="🔄 Сгенерировать новый", callback_data="generate_username"),
                        InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")
                    ]
                ]
            )
            await waiting_message.edit_text(
                f"✅ <b>Юзернейм найден в базе как свободный! 🎉</b>\n\n"
                f"<code>@{username}</code>\n\n"
                f"<b>Нажми кнопку ниже, чтобы забрать его:</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )
            return
        
        if is_in_taken_db(username):
            attempts += 1
            continue
        
        # Проверяем через API
        is_available = await check_username(username, user_id)
        
        if is_available:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Забрать юзернейм", url=f"https://t.me/{username}")],
                    [InlineKeyboardButton(text="🔎 Проверить на Fragment", url=f"https://fragment.com/username/{username}")],
                    [
                        InlineKeyboardButton(text="🔄 Сгенерировать новый", callback_data="generate_username"),
                        InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")
                    ]
                ]
            )
            await waiting_message.edit_text(
                f"🎉 <b>Найден свободный юзернейм!</b>\n\n"
                f"✅ <code>@{username}</code>\n\n"
                f"<b>Нажми кнопку, чтобы забрать его:</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )
            return
        
        attempts += 1
    
    # Если не нашли за max_attempts
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="generate_username"),
                InlineKeyboardButton(text="⚙️ Настройки", callback_data="open_settings")
            ],
            [
                InlineKeyboardButton(text="🔍 Массовая проверка", callback_data="check_all")
            ],
            [
                InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")
            ]
        ]
    )
    await waiting_message.edit_text(
        "😔 <b>Не удалось найти свободный юзернейм</b>\n\n"
        f"Проверено <code>{attempts}</code> вариантов.\n\n"
        "💡 <b>Рекомендации:</b>\n"
        "• Измени букву или количество повторений\n"
        "• Попробуй сгенерировать снова\n"
        "• Используй функцию массовой проверки",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )

# Обработчик проверки всех комбинаций
@dp.callback_query(lambda c: c.data == "check_all")
async def check_all_combinations(callback_query: types.CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    
    settings = get_user_settings(user_id)
    
    progress_message = await callback_query.message.edit_text(
        "⏳ <b>Генерирую все возможные комбинации...</b>\n\n"
        "<i>Это может занять некоторое время...</i>",
        parse_mode=ParseMode.HTML
    )
    
    all_usernames = get_all_possible_usernames(settings)
    total = len(all_usernames)
    
    if total == 0:
        await progress_message.edit_text(
            "❌ <b>Не найдено комбинаций</b> с такими настройками.\n\n"
            "Попробуй изменить настройки!",
            parse_mode=ParseMode.HTML,
            reply_markup=get_main_keyboard()
        )
        return
    
    if total > 3000:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Да, начать проверку", callback_data="confirm_check_all"),
                    InlineKeyboardButton(text="❌ Отмена", callback_data="main_menu")
                ]
            ]
        )
        estimated_time = int((total * CHECK_DELAY) / 60)
        await progress_message.edit_text(
            f"⚠️ <b>Внимание!</b>\n\n"
            f"Найдено <code>{total}</code> комбинаций.\n"
            f"Примерное время проверки: <b>~{estimated_time} минут</b>\n\n"
            f"🔍 <b>Будут использованы все методы проверки:</b>\n"
            f"• Telegram Bot API\n"
            f"• Fragment.com\n"
            f"• t.me\n\n"
            f"Продолжить?",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
        return
    
    await perform_mass_check(progress_message, user_id, all_usernames)

@dp.callback_query(lambda c: c.data == "confirm_check_all")
async def confirm_check_all(callback_query: types.CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    settings = get_user_settings(user_id)
    
    all_usernames = get_all_possible_usernames(settings)
    await perform_mass_check(callback_query.message, user_id, all_usernames)

async def perform_mass_check(message, user_id, all_usernames):
    """Выполнение массовой проверки"""
    total = len(all_usernames)
    
    await message.edit_text(
        f"⏳ <b>Начинаю проверку {total} комбинаций...</b>\n\n"
        f"🔍 Проверка через: Bot API, Fragment, t.me\n"
        f"✅ Свободные юзернеймы будут отправляться сразу!",
        parse_mode=ParseMode.HTML
    )
    
    checked = 0
    found_free = []
    found_count = 0
    last_update = datetime.now()
    start_time = datetime.now()
    
    for username in all_usernames:
        # Пропускаем уже проверенные
        if is_in_taken_db(username):
            checked += 1
            continue
            
        if is_in_free_db(username):
            found_free.append(username)
            found_count += 1
            checked += 1
            continue
        
        # Проверяем
        is_available = await check_username(username, user_id)
        
        if is_available:
            found_free.append(username)
            found_count += 1
            
            # ОТПРАВЛЯЕМ СВОБОДНЫЙ ЮЗЕРНЕЙМ СРАЗУ!
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Забрать юзернейм", url=f"https://t.me/{username}")],
                    [InlineKeyboardButton(text="🔎 Fragment", url=f"https://fragment.com/username/{username}")]
                ]
            )
            
            try:
                await bot.send_message(
                    user_id,
                    f"🎉 <b>Найден свободный юзернейм #{found_count}!</b>\n\n"
                    f"✅ <code>@{username}</code>\n\n"
                    f"<b>Нажми кнопку, чтобы забрать его:</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard
                )
            except Exception as e:
                logging.error(f"Ошибка при отправке сообщения: {e}")
        
        checked += 1
        
        # Обновляем прогресс каждые 15 секунд
        now = datetime.now()
        if (now - last_update).total_seconds() >= 15:
            try:
                progress_percent = (checked / total) * 100
                elapsed = (now - start_time).total_seconds()
                speed = checked / elapsed if elapsed > 0 else 0
                remaining = (total - checked) / speed if speed > 0 else 0
                remaining_minutes = int(remaining / 60)
                
                await message.edit_text(
                    f"⏳ <b>Проверка комбинаций...</b>\n\n"
                    f"📊 Проверено: <code>{checked}/{total}</code> ({progress_percent:.1f}%)\n"
                    f"✅ Найдено свободных: <code>{found_count}</code>\n"
                    f"⏱ Осталось: ~{remaining_minutes} мин\n\n"
                    f"Последний: <code>@{username}</code>",
                    parse_mode=ParseMode.HTML
                )
                last_update = now
            except:
                pass
    
    # Финальное сообщение
    elapsed_total = (datetime.now() - start_time).total_seconds()
    elapsed_minutes = int(elapsed_total / 60)
    
    if found_free:
        all_free = "\n".join([f"• <code>@{u}</code>" for u in found_free[:30]])
        if len(found_free) > 30:
            all_free += f"\n... и еще {len(found_free) - 30} юзернеймов"
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="📥 Скачать базу данных", callback_data="get_db")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
            ]
        )
        
        await message.edit_text(
            f"✅ <b>Проверка завершена!</b>\n\n"
            f"📊 <b>Статистика:</b>\n"
            f"• Всего комбинаций: <code>{total}</code>\n"
            f"• Проверено: <code>{checked}</code>\n"
            f"• Найдено свободных: <code>{found_count}</code>\n"
            f"• Время: <code>{elapsed_minutes} мин</code>\n\n"
            f"📝 <b>Найденные юзернеймы (первые 30):</b>\n"
            f"{all_free}\n\n"
            f"✅ Все юзернеймы отправлены отдельными сообщениями!",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
    else:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Другие настройки", callback_data="open_settings")],
                [InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu")]
            ]
        )
        
        await message.edit_text(
            f"😔 <b>Не найдено свободных юзернеймов</b>\n\n"
            f"📊 <b>Статистика:</b>\n"
            f"• Всего комбинаций: <code>{total}</code>\n"
            f"• Проверено: <code>{checked}</code>\n"
            f"• Найдено свободных: <code>0</code>\n"
            f"• Время: <code>{elapsed_minutes} мин</code>\n\n"
            f"Попробуй изменить настройки генерации!",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )

# Обработчик получения базы данных
@dp.callback_query(lambda c: c.data == "get_db")
async def get_db_callback(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await get_db_command(callback_query.message)

# Обработчик возврата в главное меню
@dp.callback_query(lambda c: c.data == "main_menu")
async def main_menu(callback_query: types.CallbackQuery):
    await callback_query.answer()
    
    user_name = callback_query.from_user.first_name or "Пользователь"
    
    await callback_query.message.edit_text(
        f"Привет, {user_name}! 👋\n\n"
        f"🎯 <b>Я помогу найти красивый 5-значный юзернейм для Telegram!</b>\n\n"
        f"<b>Методы проверки:</b>\n"
        f"✅ Telegram Bot API\n"
        f"✅ Fragment.com\n"
        f"✅ t.me веб-интерфейс\n\n"
        f"Выбери действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard()
    )

# Запуск бота
async def main():
    logging.info("🚀 Бот запущен!")
    logging.info("✅ Проверка через: Bot API, Fragment.com, t.me")
    
    if not os.path.exists(TAKEN_DB_FILE):
        save_db(TAKEN_DB_FILE, {})
    if not os.path.exists(FREE_DB_FILE):
        save_db(FREE_DB_FILE, {})
    
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logging.error(f"Критическая ошибка: {e}")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("⛔ Бот остановлен пользователем")
