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

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Токен бота из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# Инициализация бота и диспетчера
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# Класс для состояний
class SettingsStates(StatesGroup):
    waiting_for_letter = State()
    waiting_for_count = State()

# Пути к файлам базы данных
TAKEN_DB_FILE = "taken_usernames.json"
FREE_DB_FILE = "free_usernames.json"

# Глобальные настройки (будут храниться в памяти)
user_settings = {}

# Функции для работы с базой данных
def load_db(file_path):
    """Загрузить базу данных из JSON файла"""
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_db(file_path, data):
    """Сохранить базу данных в JSON файл"""
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def add_to_taken_db(username, user_id=None):
    """Добавить юзернейм в базу занятых"""
    db = load_db(TAKEN_DB_FILE)
    if username not in db:
        db[username] = {
            "checked_at": datetime.now().isoformat(),
            "checked_by": str(user_id) if user_id else "unknown"
        }
        save_db(TAKEN_DB_FILE, db)
        return True
    return False

def add_to_free_db(username, user_id=None):
    """Добавить юзернейм в базу свободных"""
    db = load_db(FREE_DB_FILE)
    if username not in db:
        db[username] = {
            "found_at": datetime.now().isoformat(),
            "found_by": str(user_id) if user_id else "unknown"
        }
        save_db(FREE_DB_FILE, db)
        return True
    return False

def is_in_taken_db(username):
    """Проверить, есть ли юзернейм в базе занятых"""
    db = load_db(TAKEN_DB_FILE)
    return username in db

def is_in_free_db(username):
    """Проверить, есть ли юзернейм в базе свободных"""
    db = load_db(FREE_DB_FILE)
    return username in db

def get_user_settings(user_id):
    """Получить настройки пользователя"""
    if user_id not in user_settings:
        user_settings[user_id] = {
            "letter": "s",  # Буква для повторения (по умолчанию s)
            "repeat_count": 2,  # Количество повторений (1-4)
            "use_full_alphabet": True  # Использовать все буквы
        }
    return user_settings[user_id]

def generate_username(settings):
    """
    Генерация юзернейма:
    - Основная буква (например s) повторяется repeat_count раз в разных местах
    - Остальные буквы - рандомные (разные)
    """
    
    # Определяем доступные буквы
    if settings["use_full_alphabet"]:
        letters = string.ascii_lowercase  # Все 26 букв
    else:
        letters = 'abcdefghijkmnopqrstuvwxyz'  # Без l, o
    
    main_letter = settings["letter"]
    repeat_count = settings["repeat_count"]
    
    # Если выбранная буква не в алфавите, берем случайную
    if main_letter not in letters:
        main_letter = random.choice(letters)
    
    # Получаем все буквы, кроме main_letter
    other_letters = [c for c in letters if c != main_letter]
    
    # Выбираем рандомные буквы для остальных позиций (уникальные)
    remaining_count = 5 - repeat_count
    if len(other_letters) < remaining_count:
        # Если не хватает уникальных букв, разрешаем повтор
        chosen_others = [random.choice(other_letters) for _ in range(remaining_count)]
    else:
        # Выбираем уникальные буквы
        chosen_others = random.sample(other_letters, remaining_count)
    
    # Создаем список: main_letter повторяется repeat_count раз + остальные буквы
    pattern = [main_letter] * repeat_count + chosen_others
    
    # Перемешиваем (чтобы main_letter была в разных местах)
    random.shuffle(pattern)
    
    return ''.join(pattern)

def generate_examples(settings, count=4):
    """Генерирует примеры юзернеймов с текущими настройками"""
    examples = []
    for _ in range(count):
        examples.append(generate_username(settings))
    return examples

def get_all_possible_usernames(settings):
    """Генерирует ВСЕ возможные комбинации юзернеймов по заданным настройкам"""
    
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
    
    all_usernames = []
    
    # Если остальных букв меньше чем нужно, используем все возможные комбинации
    if len(other_letters) < remaining_count:
        # Используем itertools.product для всех комбинаций
        for others in itertools.product(other_letters, repeat=remaining_count):
            pattern = [main_letter] * repeat_count + list(others)
            for perm in set(itertools.permutations(pattern)):
                all_usernames.append(''.join(perm))
    else:
        # Используем комбинации без повторений
        for others in itertools.combinations(other_letters, remaining_count):
            for perm_others in set(itertools.permutations(others)):
                pattern = [main_letter] * repeat_count + list(perm_others)
                for perm in set(itertools.permutations(pattern)):
                    all_usernames.append(''.join(perm))
    
    return list(set(all_usernames))  # Убираем дубликаты

# Новая функция проверки через Fragment
async def check_username_fragment(username):
    try:
        url = f"https://fragment.com/api/username/{username}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://fragment.com/",
            "Origin": "https://fragment.com"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as response:
                if response.status == 200:
                    try:
                        data = await response.json()
                        if isinstance(data, dict):
                            return data.get("available", False)
                        return False
                    except:
                        html = await response.text()
                        if "available" in html.lower() or "free" in html.lower():
                            return True
                        elif "taken" in html.lower() or "occupied" in html.lower():
                            return False
                        return None
                elif response.status == 404:
                    return True
                else:
                    return None
    except Exception as e:
        logging.error(f"Ошибка при проверке через Fragment: {e}")
        return None

# Проверка юзернейма через Telegram API
async def check_username_telegram(username):
    try:
        url = f"https://t.me/{username}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as response:
                return response.status == 404
    except Exception as e:
        logging.error(f"Ошибка при проверке через Telegram: {e}")
        return None

# Клавиатура настроек
def get_settings_keyboard(user_id):
    settings = get_user_settings(user_id)
    
    alphabet_status = "✅" if settings["use_full_alphabet"] else "❌"
    letter_info = f"Буква: {settings['letter'].upper()}"
    count_info = f"Повторений: {settings['repeat_count']}"
    
    examples = generate_examples(settings, 4)
    examples_text = "\n".join([f"• {ex}" for ex in examples])
    
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
                InlineKeyboardButton(text="1 раз", callback_data="set_count_1"),
                InlineKeyboardButton(text="2 раза", callback_data="set_count_2"),
                InlineKeyboardButton(text="3 раза", callback_data="set_count_3")
            ],
            [
                InlineKeyboardButton(text="4 раза", callback_data="set_count_4"),
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
        f"Привет 👋 {user_name}\n"
        f"Это бот по созданию юзернеймов, нажми на кнопку ниже!",
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
    
    taken_file = f"taken_{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    free_file = f"free_{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    
    with open(taken_file, 'w', encoding='utf-8') as f:
        json.dump(taken_db, f, indent=2, ensure_ascii=False)
    
    with open(free_file, 'w', encoding='utf-8') as f:
        json.dump(free_db, f, indent=2, ensure_ascii=False)
    
    await message.answer(
        f"📊 Статистика баз данных:\n\n"
        f"📌 Занятых юзернеймов: {len(taken_db)}\n"
        f"✅ Свободных юзернеймов: {len(free_db)}\n\n"
        f"Отправляю файлы..."
    )
    
    with open(taken_file, 'rb') as f:
        await message.answer_document(
            types.FSInputFile(taken_file, filename=f"taken_usernames_{datetime.now().strftime('%Y%m%d')}.json"),
            caption=f"📁 Занятые юзернеймы ({len(taken_db)} шт.)"
        )
    
    with open(free_file, 'rb') as f:
        await message.answer_document(
            types.FSInputFile(free_file, filename=f"free_usernames_{datetime.now().strftime('%Y%m%d')}.json"),
            caption=f"✅ Свободные юзернеймы ({len(free_db)} шт.)"
        )
    
    os.remove(taken_file)
    os.remove(free_file)

# Обработчик открытия настроек
@dp.callback_query(lambda c: c.data == "open_settings")
async def open_settings(callback_query: types.CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    
    settings = get_user_settings(user_id)
    
    alphabet_status = "включен" if settings["use_full_alphabet"] else "выключен"
    
    examples = generate_examples(settings, 4)
    examples_text = "\n".join([f"• {ex}" for ex in examples])
    
    await callback_query.message.edit_text(
        f"⚙️ <b>Настройки генерации</b>\n\n"
        f"📌 Текущие настройки:\n"
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
    await callback_query.answer()
    user_id = callback_query.from_user.id
    
    settings = get_user_settings(user_id)
    settings["use_full_alphabet"] = not settings["use_full_alphabet"]
    
    await open_settings(callback_query)
    
    status = "включен" if settings["use_full_alphabet"] else "выключен"
    await callback_query.message.answer(
        f"✅ Полный алфавит {status}",
        show_alert=False
    )

# Обработчик смены буквы
@dp.callback_query(lambda c: c.data == "change_letter")
async def change_letter(callback_query: types.CallbackQuery):
    await callback_query.answer()
    
    await callback_query.message.edit_text(
        "🔤 <b>Выбери букву</b>, которая будет повторяться в юзернейме:\n\n"
        "Например: если выберешь <b>S</b> и 2 повторения,\n"
        "юзернеймы будут типа: <b>ssabc</b>, <b>assbc</b>, <b>abssc</b>, <b>abcss</b>\n\n"
        "Выбери букву:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_letter_keyboard()
    )

# Обработчик установки буквы
@dp.callback_query(lambda c: c.data.startswith("set_letter_"))
async def set_letter(callback_query: types.CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    letter = callback_query.data.replace("set_letter_", "")
    
    settings = get_user_settings(user_id)
    settings["letter"] = letter
    
    await callback_query.message.answer(
        f"✅ Выбрана буква: <b>{letter.upper()}</b>",
        parse_mode=ParseMode.HTML,
        show_alert=False
    )
    
    await open_settings(callback_query)

# Обработчик смены количества повторений
@dp.callback_query(lambda c: c.data == "change_count")
async def change_count(callback_query: types.CallbackQuery):
    await callback_query.answer()
    
    await callback_query.message.edit_text(
        "🔢 <b>Выбери количество повторений</b> буквы в юзернейме:\n\n"
        "Например:\n"
        "• 2 раза: <b>ssabc</b>, <b>assbc</b>, <b>abssc</b>, <b>abcss</b>\n"
        "• 3 раза: <b>sssab</b>, <b>ssasb</b>, <b>ssabs</b>, <b>sassb</b>\n"
        "• 1 раз: <b>sabcd</b>, <b>asbcd</b>, <b>absсd</b>, <b>abcsd</b>\n"
        "• 4 раза: <b>ssssa</b>, <b>sssas</b>, <b>ssass</b>, <b>sasss</b>\n\n"
        "Выбери количество:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_count_keyboard()
    )

# Обработчик установки количества повторений
@dp.callback_query(lambda c: c.data.startswith("set_count_"))
async def set_count(callback_query: types.CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    count = int(callback_query.data.replace("set_count_", ""))
    
    settings = get_user_settings(user_id)
    settings["repeat_count"] = count
    
    await callback_query.message.answer(
        f"✅ Количество повторений: <b>{count}</b>",
        parse_mode=ParseMode.HTML,
        show_alert=False
    )
    
    await open_settings(callback_query)

# Обработчик сброса настроек
@dp.callback_query(lambda c: c.data == "reset_settings")
async def reset_settings(callback_query: types.CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    
    user_settings[user_id] = {
        "letter": "s",
        "repeat_count": 2,
        "use_full_alphabet": True
    }
    
    await callback_query.message.answer(
        "✅ Настройки сброшены к стандартным",
        show_alert=False
    )
    
    await open_settings(callback_query)

# Обработчик генерации юзернейма
@dp.callback_query(lambda c: c.data == "generate_username")
async def process_generate_username(callback_query: types.CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    
    settings = get_user_settings(user_id)
    
    waiting_message = await callback_query.message.edit_text(
        "⏳ Генерирую красивый юзернейм и проверяю его доступность..."
    )
    
    username = generate_username(settings)
    
    if is_in_free_db(username):
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Забрать юзернейм", 
                        url=f"https://t.me/{username}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔄 Сгенерировать новый", 
                        callback_data="generate_username"
                    ),
                    InlineKeyboardButton(
                        text="🏠 Главное меню", 
                        callback_data="main_menu"
                    )
                ]
            ]
        )
        
        await waiting_message.edit_text(
            f"✅ Юзернейм <b>@{username}</b> уже найден как свободный! 🎉\n\n"
            f"Ты можешь забрать его по ссылке ниже:",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
        return
    
    if is_in_taken_db(username):
        await waiting_message.edit_text("⏳ Юзернейм занят, генерирую новый...")
        return await process_generate_username(callback_query)
    
    fragment_result = await check_username_fragment(username)
    
    if fragment_result is None:
        telegram_result = await check_username_telegram(username)
        is_available = telegram_result
    else:
        is_available = fragment_result
    
    if is_available:
        add_to_free_db(username, user_id)
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Забрать юзернейм", 
                        url=f"https://t.me/{username}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔄 Сгенерировать новый", 
                        callback_data="generate_username"
                    ),
                    InlineKeyboardButton(
                        text="🏠 Главное меню", 
                        callback_data="main_menu"
                    )
                ]
            ]
        )
        
        await waiting_message.edit_text(
            f"✅ Юзернейм <b>@{username}</b> свободен! 🎉\n\n"
            f"Ты можешь забрать его по ссылке ниже:",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
    else:
        add_to_taken_db(username, user_id)
        
        attempts = 0
        while attempts < 20:
            new_username = generate_username(settings)
            
            if is_in_free_db(new_username):
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="✅ Забрать юзернейм", 
                                url=f"https://t.me/{new_username}"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="🔄 Сгенерировать новый", 
                                callback_data="generate_username"
                            ),
                            InlineKeyboardButton(
                                text="🏠 Главное меню", 
                                callback_data="main_menu"
                            )
                        ]
                    ]
                )
                
                await waiting_message.edit_text(
                    f"✅ Юзернейм <b>@{new_username}</b> свободен! 🎉\n\n"
                    f"Ты можешь забрать его по ссылке ниже:",
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard
                )
                return
            
            if is_in_taken_db(new_username):
                attempts += 1
                continue
            
            if fragment_result is None:
                new_result = await check_username_telegram(new_username)
            else:
                new_result = await check_username_fragment(new_username)
            
            if new_result:
                add_to_free_db(new_username, user_id)
                
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="✅ Забрать юзернейм", 
                                url=f"https://t.me/{new_username}"
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="🔄 Сгенерировать новый", 
                                callback_data="generate_username"
                            ),
                            InlineKeyboardButton(
                                text="🏠 Главное меню", 
                                callback_data="main_menu"
                            )
                        ]
                    ]
                )
                
                await waiting_message.edit_text(
                    f"✅ Юзернейм <b>@{new_username}</b> свободен! 🎉\n\n"
                    f"Ты можешь забрать его по ссылке ниже:",
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard
                )
                return
            
            add_to_taken_db(new_username, user_id)
            attempts += 1
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔄 Попробовать снова", 
                        callback_data="generate_username"
                    ),
                    InlineKeyboardButton(
                        text="🏠 Главное меню", 
                        callback_data="main_menu"
                    )
                ]
            ]
        )
        
        await waiting_message.edit_text(
            "😔 Не удалось найти свободный юзернейм.\n"
            "Попробуй сгенерировать новый!",
            reply_markup=keyboard
        )

# Обработчик проверки всех комбинаций
@dp.callback_query(lambda c: c.data == "check_all")
async def check_all_combinations(callback_query: types.CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    
    settings = get_user_settings(user_id)
    
    waiting_message = await callback_query.message.edit_text(
        "⏳ Генерирую все возможные комбинации по вашим настройкам...\n"
        "Это может занять некоторое время..."
    )
    
    all_usernames = get_all_possible_usernames(settings)
    total = len(all_usernames)
    
    if total == 0:
        await waiting_message.edit_text(
            "❌ Не найдено комбинаций с такими настройками.\n"
            "Попробуй изменить настройки!",
            reply_markup=get_main_keyboard()
        )
        return
    
    await waiting_message.edit_text(
        f"⏳ Найдено {total} комбинаций.\n"
        f"Начинаю проверку каждой...\n"
        f"Это может занять несколько минут."
    )
    
    checked = 0
    found_free = []
    
    for username in all_usernames:
        if is_in_taken_db(username) or is_in_free_db(username):
            checked += 1
            continue
        
        fragment_result = await check_username_fragment(username)
        
        if fragment_result is None:
            is_available = await check_username_telegram(username)
        else:
            is_available = fragment_result
        
        if is_available:
            add_to_free_db(username, user_id)
            found_free.append(username)
        else:
            add_to_taken_db(username, user_id)
        
        checked += 1
        
        if checked % 10 == 0:
            try:
                await waiting_message.edit_text(
                    f"⏳ Проверка комбинаций...\n"
                    f"Проверено: {checked}/{total}\n"
                    f"Найдено свободных: {len(found_free)}\n\n"
                    f"Последний найденный: @{found_free[-1] if found_free else 'пока нет'}"
                )
            except:
                pass
    
    if found_free:
        free_list = "\n".join([f"• @{u}" for u in found_free[:10]])
        if len(found_free) > 10:
            free_list += f"\n... и еще {len(found_free) - 10} шт."
        
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📥 Получить все свободные", 
                        callback_data=f"get_free_{user_id}"
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
        
        await waiting_message.edit_text(
            f"✅ Проверка завершена!\n\n"
            f"📊 Статистика:\n"
            f"• Всего комбинаций: {total}\n"
            f"• Проверено: {checked}\n"
            f"• Найдено свободных: {len(found_free)}\n\n"
            f"📝 Первые 10 свободных:\n{free_list}",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
    else:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔄 Попробовать другие настройки", 
                        callback_data="open_settings"
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
        
        await waiting_message.edit_text(
            f"😔 Не найдено свободных юзернеймов.\n\n"
            f"📊 Статистика:\n"
            f"• Всего комбинаций: {total}\n"
            f"• Проверено: {checked}\n"
            f"• Найдено свободных: 0\n\n"
            f"Попробуй изменить настройки генерации!",
            reply_markup=keyboard
        )

# Обработчик возврата в главное меню
@dp.callback_query(lambda c: c.data == "main_menu")
async def main_menu(callback_query: types.CallbackQuery):
    await callback_query.answer()
    
    user_name = callback_query.from_user.first_name or "Пользователь"
    
    await callback_query.message.edit_text(
        f"Привет 👋 {user_name}\n"
        f"Это бот по созданию юзернеймов, нажми на кнопку ниже!",
        reply_markup=get_main_keyboard()
    )

# Запуск бота
async def main():
    logging.info("Бот запущен!")
    
    if not os.path.exists(TAKEN_DB_FILE):
        save_db(TAKEN_DB_FILE, {})
    if not os.path.exists(FREE_DB_FILE):
        save_db(FREE_DB_FILE, {})
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
