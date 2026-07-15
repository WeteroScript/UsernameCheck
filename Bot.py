import logging
import os
import asyncio
import json
import re
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from dotenv import load_dotenv

from username_bot import router as username_router, init_username_bot
from gram_bot import router as gram_router, init_gram_bot

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)


# ============ КЛАВИАТУРЫ ============

def get_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Боты", callback_data="bots")],
        [InlineKeyboardButton(text="👤 Юзернеймы", callback_data="users")],
    ])

def get_bots_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Gram", callback_data="gram")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main")],
    ])

def get_gram_bots_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="@gram_piarbot", callback_data="g_piar")],
        [InlineKeyboardButton(text="@gram_prbot", callback_data="g_pr")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="bots")],
    ])

def get_gram_action_keyboard(bot_type: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Авто-Просмотры", callback_data=f"gstart_{bot_type}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="gram")],
    ])

def get_username_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✨ Генерировать", callback_data="gen")],
        [InlineKeyboardButton(text="🔍 Проверить все", callback_data="check")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main")],
    ])


# ============ СОСТОЯНИЯ ============

class GramStates(StatesGroup):
    waiting_phone = State()
    waiting_code = State()


# ============ ОБРАБОТЧИКИ ============

@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        f"👋 Привет, {message.from_user.first_name or 'Пользователь'}!\n\n"
        f"🤖 <b>Telegram Бот-Центр</b>\n\n"
        f"Выбери раздел:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard()
    )


# ============ CALLBACK: ГЛАВНОЕ МЕНЮ ============

@dp.callback_query(lambda c: c.data == "main")
async def main_menu(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🏠 <b>Главное меню</b>\n\nВыбери раздел:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard()
    )


# ============ CALLBACK: БОТЫ ============

@dp.callback_query(lambda c: c.data == "bots")
async def bots_menu(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🤖 <b>Раздел Ботов</b>\n\nВыбери категорию:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_bots_keyboard()
    )


# ============ CALLBACK: GRAM БОТЫ ============

@dp.callback_query(lambda c: c.data == "gram")
async def gram_bots(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "📱 <b>Gram Боты</b>\n\n"
        "Выбери бота для заработка:\n\n"
        "• <b>@gram_piarbot</b> - основной бот\n"
        "• <b>@gram_prbot</b> - резервный бот",
        parse_mode=ParseMode.HTML,
        reply_markup=get_gram_bots_keyboard()
    )


# ============ CALLBACK: ВЫБОР GRAM БОТА ============

@dp.callback_query(lambda c: c.data in ["g_piar", "g_pr"])
async def select_gram_bot(callback: types.CallbackQuery):
    bot_type = callback.data
    await callback.answer()
    
    # Преобразуем короткий код в полное имя
    if bot_type == "g_piar":
        bot_name = "@gram_piarbot"
    else:
        bot_name = "@gram_prbot"
    
    await callback.message.edit_text(
        f"📱 <b>{bot_name}</b>\n\n"
        f"Выбери действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_gram_action_keyboard(bot_type)
    )


# ============ CALLBACK: ЗАПУСК GRAM АВТО-ПРОСМОТРОВ ============

@dp.callback_query(lambda c: c.data.startswith("gstart_"))
async def gram_start(callback: types.CallbackQuery, state: FSMContext):
    bot_type = callback.data.replace("gstart_", "")
    await callback.answer()
    
    # Преобразуем короткий код в полное имя
    if bot_type == "g_piar":
        bot_username = "@gram_piarbot"
    else:
        bot_username = "@gram_prbot"
    
    await state.update_data(bot_username=bot_username, bot_type=bot_type)
    await state.set_state(GramStates.waiting_phone)
    
    await callback.message.edit_text(
        f"📱 <b>Настройка {bot_username}</b>\n\n"
        f"Введите номер телефона в формате:\n"
        f"<code>+79172993848</code>\n\n"
        f"или отправьте /cancel для отмены",
        parse_mode=ParseMode.HTML
    )


@dp.message(GramStates.waiting_phone)
async def gram_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    
    if not re.match(r'^\+?\d{10,15}$', phone):
        await message.answer(
            "❌ Неверный формат номера.\n"
            "Используйте формат: <code>+79172993848</code>",
            parse_mode=ParseMode.HTML
        )
        return
    
    await state.update_data(phone=phone)
    await state.set_state(GramStates.waiting_code)
    
    data = await state.get_data()
    bot_username = data.get("bot_username")
    
    from gram_bot import send_code
    result = await send_code(phone, bot_username)
    
    if result:
        await message.answer(
            "📱 <b>Код отправлен!</b>\n\n"
            "Введите код подтверждения из Telegram:",
            parse_mode=ParseMode.HTML
        )
    else:
        await message.answer(
            "❌ Ошибка отправки кода.\n"
            "Проверьте номер и попробуйте снова.\n\n"
            "Отправьте /start для возврата в меню"
        )
        await state.clear()


@dp.message(GramStates.waiting_code)
async def gram_code(message: types.Message, state: FSMContext):
    code = message.text.strip()
    
    data = await state.get_data()
    phone = data.get("phone")
    bot_username = data.get("bot_username")
    
    from gram_bot import start_gram_bot
    result = await start_gram_bot(phone, code, bot_username)
    
    await state.clear()
    
    if result:
        await message.answer(
            f"✅ <b>Бот запущен!</b>\n\n"
            f"🤖 {bot_username}\n"
            f"📱 {phone}\n\n"
            f"Авто-просмотры работают в фоне.\n"
            f"Для остановки используйте /stop_gram",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ В меню", callback_data="main")]
            ])
        )
    else:
        await message.answer(
            "❌ Ошибка авторизации.\n"
            "Проверьте код и попробуйте снова.\n\n"
            "Отправьте /start для возврата в меню"
        )


@dp.message(Command("cancel"))
async def cancel_command(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "❌ Отменено.",
        reply_markup=get_main_keyboard()
    )


@dp.message(Command("stop_gram"))
async def stop_gram(message: types.Message):
    from gram_bot import stop_gram_bot
    result = await stop_gram_bot()
    if result:
        await message.answer("✅ Gram бот остановлен.")
    else:
        await message.answer("❌ Gram бот не был запущен.")


# ============ CALLBACK: ЮЗЕРНЕЙМЫ ============

@dp.callback_query(lambda c: c.data == "users")
async def username_menu(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "👤 <b>Раздел Юзернеймы</b>\n\n"
        "🔍 Поиск свободных 5-значных юзернеймов\n\n"
        "Выбери действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_username_keyboard()
    )


# ============ ИНИЦИАЛИЗАЦИЯ ============

async def main():
    # Инициализируем модули
    init_username_bot(dp)
    init_gram_bot(dp)
    
    # Запускаем бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("⛔ Бот остановлен")
