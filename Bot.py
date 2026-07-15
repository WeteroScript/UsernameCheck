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
from gram_bot import router as gram_router, init_gram_bot, active_clients, active_tasks

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
        [InlineKeyboardButton(text="📱 Сессии", callback_data="sessions")],
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

def get_session_manage_keyboard(phone: str) -> InlineKeyboardMarkup:
    """Клавиатура управления сессией"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Управление сессией", callback_data=f"sess_manage_{phone}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="sessions")],
    ])

def get_session_actions_keyboard(phone: str) -> InlineKeyboardMarkup:
    """Клавиатура действий с сессией"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚪 Выйти со всех каналов", callback_data=f"sess_leave_channels_{phone}")],
        [InlineKeyboardButton(text="🚪 Выйти со всех групп", callback_data=f"sess_leave_groups_{phone}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"sess_info_{phone}")],
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
    
    from gram_bot import send_code, set_user_chat_id
    set_user_chat_id(message.chat.id)
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
    result = await start_gram_bot(phone, code, bot_username, message.chat.id)
    
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


@dp.message(Command("continue_gram"))
async def continue_gram(message: types.Message):
    """Продолжить работу после капчи"""
    from gram_bot import continue_gram_bot, active_clients
    
    if not active_clients:
        await message.answer("❌ Нет активных сессий. Запустите бота сначала.")
        return
    
    phone = list(active_clients.keys())[0]
    result = await continue_gram_bot(phone)
    
    if result:
        await message.answer(
            "✅ Gram бот продолжен!\n\n"
            "Если капча еще активна, пройдите её вручную в Telegram и нажмите /continue_gram снова."
        )
    else:
        await message.answer("❌ Ошибка продолжения. Попробуйте перезапустить бота.")


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


# ============ CALLBACK: СЕССИИ ============

@dp.callback_query(lambda c: c.data == "sessions")
async def sessions_menu(callback: types.CallbackQuery):
    await callback.answer()
    
    if not active_clients:
        await callback.message.edit_text(
            "📱 <b>Сессии</b>\n\n"
            "Нет активных сессий.\n\n"
            "Запустите Gram бота, чтобы создать сессию.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="main")]
            ])
        )
        return
    
    # Строим список сессий
    text = "📱 <b>Активные сессии</b>\n\n"
    
    for phone, client in active_clients.items():
        # Пытаемся получить информацию об аккаунте
        try:
            me = await client.get_me()
            username = f"@{me.username}" if me.username else "Нет юзернейма"
            user_id = me.id
            first_name = me.first_name or ""
            last_name = me.last_name or ""
            name = f"{first_name} {last_name}".strip()
            
            text += f"┌ <b>{name or 'Без имени'}</b>\n"
            text += f"├ 📱 {phone}\n"
            text += f"├ 🆔 {user_id}\n"
            text += f"└ 👤 {username}\n\n"
        except Exception as e:
            text += f"┌ <b>Сессия</b>\n"
            text += f"├ 📱 {phone}\n"
            text += f"└ ❌ Ошибка получения данных\n\n"
    
    text += "\nВыбери сессию для управления:"
    
    # Создаем кнопки для каждой сессии
    buttons = []
    for phone in active_clients.keys():
        # Обрезаем номер для callback_data (максимум 64 символа)
        short_phone = phone.replace('+', '')[-12:]
        buttons.append([InlineKeyboardButton(
            text=f"📱 {phone}", 
            callback_data=f"sess_info_{short_phone}"
        )])
    
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="main")])
    
    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@dp.callback_query(lambda c: c.data.startswith("sess_info_"))
async def session_info(callback: types.CallbackQuery):
    await callback.answer()
    
    phone_short = callback.data.replace("sess_info_", "")
    
    # Находим полный номер
    phone = None
    for p in active_clients.keys():
        if p.replace('+', '')[-12:] == phone_short:
            phone = p
            break
    
    if not phone or phone not in active_clients:
        await callback.answer("❌ Сессия не найдена")
        return
    
    client = active_clients[phone]
    
    try:
        me = await client.get_me()
        username = f"@{me.username}" if me.username else "Нет юзернейма"
        user_id = me.id
        first_name = me.first_name or ""
        last_name = me.last_name or ""
        name = f"{first_name} {last_name}".strip()
        
        # Проверяем активна ли задача
        is_active = phone in active_tasks and not active_tasks[phone].done()
        
        text = f"📱 <b>Информация о сессии</b>\n\n"
        text += f"👤 <b>{name or 'Без имени'}</b>\n"
        text += f"📱 {phone}\n"
        text += f"🆔 {user_id}\n"
        text += f"👤 {username}\n"
        text += f"📊 Статус: {'🟢 Активна' if is_active else '🔴 Остановлена'}\n"
        
    except Exception as e:
        text = f"📱 <b>Информация о сессии</b>\n\n"
        text += f"📱 {phone}\n"
        text += f"❌ Ошибка получения данных: {e}\n"
    
    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_session_manage_keyboard(phone)
    )


@dp.callback_query(lambda c: c.data.startswith("sess_manage_"))
async def session_manage(callback: types.CallbackQuery):
    await callback.answer()
    phone = callback.data.replace("sess_manage_", "")
    
    # Проверяем существование сессии
    if phone not in active_clients:
        await callback.answer("❌ Сессия не найдена")
        return
    
    await callback.message.edit_text(
        f"📱 <b>Управление сессией</b>\n\n"
        f"Выбери действие для {phone}:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_session_actions_keyboard(phone)
    )


# ============ ВЫХОД ИЗ КАНАЛОВ/ГРУПП ============

@dp.callback_query(lambda c: c.data.startswith("sess_leave_channels_"))
async def session_leave_channels(callback: types.CallbackQuery):
    await callback.answer("⏳ Выхожу из каналов...")
    
    phone = callback.data.replace("sess_leave_channels_", "")
    
    if phone not in active_clients:
        await callback.answer("❌ Сессия не найдена")
        return
    
    client = active_clients[phone]
    
    try:
        left_count = 0
        error_count = 0
        
        # Получаем все диалоги
        async for dialog in client.iter_dialogs():
            # Проверяем что это канал (не личный чат и не группа)
            if dialog.is_channel:
                try:
                    # Проверяем что это не личный чат
                    if dialog.entity.username and dialog.entity.username.lower() != "me":
                        # Выходим из канала
                        await client.leave_channel(dialog.entity)
                        left_count += 1
                        logging.info(f"🚪 Вышел из канала: {dialog.name}")
                        # Небольшая задержка чтобы не спамить
                        await asyncio.sleep(0.5)
                except Exception as e:
                    error_count += 1
                    logging.error(f"❌ Ошибка выхода из канала {dialog.name}: {e}")
        
        await callback.message.edit_text(
            f"✅ <b>Выход из каналов завершен!</b>\n\n"
            f"📱 {phone}\n"
            f"🚪 Вышел из: {left_count} каналов\n"
            f"❌ Ошибок: {error_count}\n\n"
            f"<i>Личные чаты не были затронуты</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад к сессии", callback_data=f"sess_info_{phone.replace('+', '')[-12:]}")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="main")]
            ])
        )
        
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Ошибка: {e}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"sess_manage_{phone}")]
            ])
        )


@dp.callback_query(lambda c: c.data.startswith("sess_leave_groups_"))
async def session_leave_groups(callback: types.CallbackQuery):
    await callback.answer("⏳ Выхожу из групп...")
    
    phone = callback.data.replace("sess_leave_groups_", "")
    
    if phone not in active_clients:
        await callback.answer("❌ Сессия не найдена")
        return
    
    client = active_clients[phone]
    
    try:
        left_count = 0
        error_count = 0
        
        # Получаем все диалоги
        async for dialog in client.iter_dialogs():
            # Проверяем что это группа (не канал и не личный чат)
            if dialog.is_group:
                try:
                    # Выходим из группы
                    await client.leave_group(dialog.entity)
                    left_count += 1
                    logging.info(f"🚪 Вышел из группы: {dialog.name}")
                    # Небольшая задержка чтобы не спамить
                    await asyncio.sleep(0.5)
                except Exception as e:
                    error_count += 1
                    logging.error(f"❌ Ошибка выхода из группы {dialog.name}: {e}")
        
        await callback.message.edit_text(
            f"✅ <b>Выход из групп завершен!</b>\n\n"
            f"📱 {phone}\n"
            f"🚪 Вышел из: {left_count} групп\n"
            f"❌ Ошибок: {error_count}\n\n"
            f"<i>Личные чаты не были затронуты</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад к сессии", callback_data=f"sess_info_{phone.replace('+', '')[-12:]}")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="main")]
            ])
        )
        
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Ошибка: {e}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"sess_manage_{phone}")]
            ])
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
