import logging
import random
import string
import aiohttp
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import asyncio
import json

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
    waiting_for_settings = State()

# Глобальные настройки (будут храниться в памяти)
user_settings = {}

def get_user_settings(user_id):
    """Получить настройки пользователя"""
    if user_id not in user_settings:
        user_settings[user_id] = {
            "use_full_alphabet": True,  # Использовать все буквы английского алфавита
            "allow_repeat_letters": True,  # Разрешить повторение букв
            "allow_repeat_digits": True,  # Разрешить повторение цифр
            "generation_mode": "mixed"  # mixed, letters_only, digits_only
        }
    return user_settings[user_id]

def generate_username(settings):
    """Генерация юзернейма с учетом настроек"""
    
    # Определяем доступные символы
    if settings["use_full_alphabet"]:
        letters = string.ascii_lowercase  # Все 26 букв
    else:
        # Только популярные буквы (исключаем похожие на цифры)
        letters = 'abcdefghijkmnopqrstuvwxyz'  # Без l, o
    
    digits = string.digits
    
    # Если повторение запрещено, используем random.sample
    def get_chars(source, count, allow_repeat):
        if allow_repeat:
            return ''.join(random.choices(source, k=count))
        else:
            if len(source) < count:
                # Если символов меньше чем нужно, разрешаем повтор
                return ''.join(random.choices(source, k=count))
            return ''.join(random.sample(source, count))
    
    # Выбираем режим генерации
    mode = settings["generation_mode"]
    
    if mode == "letters_only":
        # Только буквы
        return get_chars(letters, 5, settings["allow_repeat_letters"])
    
    elif mode == "digits_only":
        # Только цифры
        return get_chars(digits, 5, settings["allow_repeat_digits"])
    
    else:  # mixed - смешанный режим
        patterns = [
            # Паттерн 1: Случайные буквы и цифры
            lambda: get_chars(letters + digits, 5, settings["allow_repeat_letters"]),
            
            # Паттерн 2: Буква-цифра-буква-цифра-буква
            lambda: get_chars(letters, 1, True) + 
                    get_chars(digits, 1, True) + 
                    get_chars(letters, 1, True) + 
                    get_chars(digits, 1, True) + 
                    get_chars(letters, 1, True),
            
            # Паттерн 3: Цифра-буква-цифра-буква-цифра
            lambda: get_chars(digits, 1, True) + 
                    get_chars(letters, 1, True) + 
                    get_chars(digits, 1, True) + 
                    get_chars(letters, 1, True) + 
                    get_chars(digits, 1, True),
            
            # Паттерн 4: Только буквы с повторениями (aabbb, baabb, bbaab, bbbaa)
            lambda: generate_repeating_pattern(letters, settings["allow_repeat_letters"])
        ]
        
        return random.choice(patterns)()
    
    return get_chars(letters + digits, 5, settings["allow_repeat_letters"])

def generate_repeating_pattern(letters, allow_repeat):
    """Генерация паттерна с повторяющимися буквами (aabbb, baabb, bbaab, bbbaa)"""
    
    if not allow_repeat:
        # Если повтор запрещен, используем обычную генерацию
        return ''.join(random.choices(letters, k=5))
    
    # Выбираем 2 разные буквы
    if len(letters) < 2:
        letter1 = random.choice(letters)
        letter2 = random.choice(letters)
    else:
        selected = random.sample(letters, 2)
        letter1, letter2 = selected[0], selected[1]
    
    # Определяем количество первой буквы (от 1 до 4)
    count_first = random.randint(1, 4)
    count_second = 5 - count_first
    
    # Создаем шаблон и перемешиваем
    pattern = [letter1] * count_first + [letter2] * count_second
    random.shuffle(pattern)
    
    return ''.join(pattern)

# Проверка юзернейма через Fragment API
async def check_username_fragment(username):
    try:
        url = f"https://api.fragment.com/api/username/{username}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("available", False)
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
            async with session.get(url) as response:
                return response.status == 404
    except Exception as e:
        logging.error(f"Ошибка при проверке через Telegram: {e}")
        return None

# Клавиатура настроек
def get_settings_keyboard(user_id):
    settings = get_user_settings(user_id)
    
    # Определяем статусы
    alphabet_status = "✅" if settings["use_full_alphabet"] else "❌"
    repeat_status = "✅" if settings["allow_repeat_letters"] else "❌"
    
    # Определяем текущий режим
    mode_text = {
        "mixed": "🔀 Смешанный",
        "letters_only": "🔤 Только буквы",
        "digits_only": "🔢 Только цифры"
    }.get(settings["generation_mode"], "🔀 Смешанный")
    
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
                    text=f"{repeat_status} Повторение букв", 
                    callback_data="toggle_repeat"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"📝 Режим: {mode_text}", 
                    callback_data="change_mode"
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

# Главное меню с кнопкой настроек
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
    
    # Инициализируем настройки пользователя
    get_user_settings(user_id)
    
    await message.answer(
        f"Привет 👋 {user_name}\n"
        f"Это бот по созданию юзернеймов, нажми на кнопку ниже!",
        reply_markup=get_main_keyboard()
    )

# Обработчик открытия настроек
@dp.callback_query(lambda c: c.data == "open_settings")
async def open_settings(callback_query: types.CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    
    settings = get_user_settings(user_id)
    
    # Информация о текущих настройках
    alphabet_status = "включен" if settings["use_full_alphabet"] else "выключен"
    repeat_status = "включен" if settings["allow_repeat_letters"] else "выключен"
    
    mode_names = {
        "mixed": "Смешанный (буквы + цифры)",
        "letters_only": "Только буквы",
        "digits_only": "Только цифры"
    }
    
    await callback_query.message.edit_text(
        f"⚙️ <b>Настройки генерации</b>\n\n"
        f"📌 Текущие настройки:\n"
        f"• Полный алфавит: {alphabet_status}\n"
        f"• Повторение букв: {repeat_status}\n"
        f"• Режим: {mode_names[settings['generation_mode']]}\n\n"
        f"Нажми на кнопку, чтобы изменить настройку:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_settings_keyboard(user_id)
    )

# Обработчик переключения алфавита
@dp.callback_query(lambda c: c.data == "toggle_alphabet")
async def toggle_alphabet(callback_query: types.CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    
    settings = get_user_settings(user_id)
    settings["use_full_alphabet"] = not settings["use_full_alphabet"]
    
    await callback_query.message.edit_reply_markup(
        reply_markup=get_settings_keyboard(user_id)
    )
    
    # Подтверждение
    status = "включен" if settings["use_full_alphabet"] else "выключен"
    await callback_query.message.answer(
        f"✅ Полный алфавит {status}",
        show_alert=False
    )

# Обработчик переключения повторения
@dp.callback_query(lambda c: c.data == "toggle_repeat")
async def toggle_repeat(callback_query: types.CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    
    settings = get_user_settings(user_id)
    settings["allow_repeat_letters"] = not settings["allow_repeat_letters"]
    settings["allow_repeat_digits"] = settings["allow_repeat_letters"]  # Синхронизируем
    
    await callback_query.message.edit_reply_markup(
        reply_markup=get_settings_keyboard(user_id)
    )
    
    status = "включено" if settings["allow_repeat_letters"] else "выключено"
    await callback_query.message.answer(
        f"✅ Повторение букв {status}",
        show_alert=False
    )

# Обработчик смены режима
@dp.callback_query(lambda c: c.data == "change_mode")
async def change_mode(callback_query: types.CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    
    settings = get_user_settings(user_id)
    
    # Циклически меняем режим
    modes = ["mixed", "letters_only", "digits_only"]
    current_index = modes.index(settings["generation_mode"])
    next_index = (current_index + 1) % len(modes)
    settings["generation_mode"] = modes[next_index]
    
    await callback_query.message.edit_reply_markup(
        reply_markup=get_settings_keyboard(user_id)
    )
    
    mode_names = {
        "mixed": "Смешанный",
        "letters_only": "Только буквы",
        "digits_only": "Только цифры"
    }
    await callback_query.message.answer(
        f"✅ Режим изменен на: {mode_names[settings['generation_mode']]}",
        show_alert=False
    )

# Обработчик сброса настроек
@dp.callback_query(lambda c: c.data == "reset_settings")
async def reset_settings(callback_query: types.CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    
    user_settings[user_id] = {
        "use_full_alphabet": True,
        "allow_repeat_letters": True,
        "allow_repeat_digits": True,
        "generation_mode": "mixed"
    }
    
    await callback_query.message.edit_reply_markup(
        reply_markup=get_settings_keyboard(user_id)
    )
    
    await callback_query.message.answer(
        "✅ Настройки сброшены к стандартным",
        show_alert=False
    )

# Обработчик генерации юзернейма
@dp.callback_query(lambda c: c.data == "generate_username")
async def process_generate_username(callback_query: types.CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    
    # Получаем настройки пользователя
    settings = get_user_settings(user_id)
    
    waiting_message = await callback_query.message.edit_text(
        "⏳ Генерирую красивый юзернейм и проверяю его доступность..."
    )
    
    # Генерируем с учетом настроек
    username = generate_username(settings)
    
    # Проверяем через Fragment API
    fragment_result = await check_username_fragment(username)
    
    if fragment_result is None:
        telegram_result = await check_username_telegram(username)
        is_available = telegram_result
    else:
        is_available = fragment_result
    
    if is_available:
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
        attempts = 0
        while attempts < 10:
            new_username = generate_username(settings)
            
            if fragment_result is None:
                new_result = await check_username_telegram(new_username)
            else:
                new_result = await check_username_fragment(new_username)
            
            if new_result:
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
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
