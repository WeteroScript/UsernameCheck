import logging
import random
import string
import aiohttp
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
import asyncio

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Токен бота из переменных окружения
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# Функция для генерации красивого 5-значного юзернейма
def generate_username():
    patterns = [
        lambda: ''.join(random.choices(string.ascii_lowercase + string.digits, k=5)),
        lambda: random.choice(string.ascii_lowercase) + 
                random.choice(string.digits) + 
                random.choice(string.ascii_lowercase) + 
                random.choice(string.digits) + 
                random.choice(string.ascii_lowercase),
        lambda: random.choice(string.digits) + 
                random.choice(string.ascii_lowercase) + 
                random.choice(string.digits) + 
                random.choice(string.ascii_lowercase) + 
                random.choice(string.digits)
    ]
    return random.choice(patterns)()

# Функция для проверки юзернейма через Fragment API
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

# Функция для проверки юзернейма через Telegram API
async def check_username_telegram(username):
    try:
        url = f"https://t.me/{username}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                return response.status == 404
    except Exception as e:
        logging.error(f"Ошибка при проверке через Telegram: {e}")
        return None

# Команда /start
@dp.message(Command("start"))
async def start_command(message: types.Message):
    user_name = message.from_user.first_name or "Пользователь"
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="✨ Создать 5-значный юзернейм", 
                callback_data="generate_username"
            )]
        ]
    )
    
    await message.answer(
        f"Привет 👋 {user_name}\n"
        f"Это бот по созданию юзернеймов, нажми на кнопку ниже!",
        reply_markup=keyboard
    )

# Обработчик нажатия на кнопку
@dp.callback_query(lambda c: c.data == "generate_username")
async def process_generate_username(callback_query: types.CallbackQuery):
    await callback_query.answer()
    
    waiting_message = await callback_query.message.edit_text(
        "⏳ Генерирую красивый юзернейм и проверяю его доступность..."
    )
    
    username = generate_username()
    
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
            new_username = generate_username()
            
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
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="✨ Создать 5-значный юзернейм", 
                callback_data="generate_username"
            )]
        ]
    )
    
    await callback_query.message.edit_text(
        f"Привет 👋 {user_name}\n"
        f"Это бот по созданию юзернеймов, нажми на кнопку ниже!",
        reply_markup=keyboard
    )

# Запуск бота
async def main():
    logging.info("Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
