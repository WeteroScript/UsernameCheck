import logging
import os
import asyncio
import json
import re
from datetime import datetime
from typing import Dict, Optional
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from dotenv import load_dotenv

from username_bot import router as username_router, init_username_bot
from gram_bot import router as gram_router, init_gram_bot, active_clients, active_tasks, set_user_chat_id, start_gram_worker, stop_gram_bot, set_bot_instance, get_task_choice_keyboard

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# Храним активные сессии пользователей
user_sessions: Dict[int, str] = {}  # user_id -> phone
user_bot_choice: Dict[int, str] = {}  # user_id -> bot_username

# Файлы для сохранения
SESSIONS_FILE = "user_sessions.json"
BOT_CHOICE_FILE = "user_bot_choice.json"


# ============ СОХРАНЕНИЕ СЕССИЙ ============

def load_sessions() -> Dict[int, str]:
    if os.path.exists(SESSIONS_FILE):
        try:
            with open(SESSIONS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return {int(k): v for k, v in data.items()}
        except Exception as e:
            logging.error(f"Ошибка загрузки сессий: {e}")
    return {}

def save_sessions():
    try:
        with open(SESSIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(user_sessions, f, indent=2, ensure_ascii=False)
        logging.info("✅ Сессии сохранены")
    except Exception as e:
        logging.error(f"Ошибка сохранения сессий: {e}")

def load_bot_choices() -> Dict[int, str]:
    if os.path.exists(BOT_CHOICE_FILE):
        try:
            with open(BOT_CHOICE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return {int(k): v for k, v in data.items()}
        except Exception as e:
            logging.error(f"Ошибка загрузки выбора ботов: {e}")
    return {}

def save_bot_choices():
    try:
        with open(BOT_CHOICE_FILE, 'w', encoding='utf-8') as f:
            json.dump(user_bot_choice, f, indent=2, ensure_ascii=False)
        logging.info("✅ Выбор ботов сохранен")
    except Exception as e:
        logging.error(f"Ошибка сохранения выбора ботов: {e}")


# ============ КЛАВИАТУРЫ ============

def get_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Gram Бот", callback_data="gram")],
        [InlineKeyboardButton(text="👤 Юзернеймы", callback_data="users")],
        [InlineKeyboardButton(text="📱 Мои сессии", callback_data="sessions")],
    ])

def get_gram_main_keyboard(has_session: bool = False, is_running: bool = False, bot_name: str = "@gram_prbot") -> InlineKeyboardMarkup:
    buttons = []
    buttons.append([InlineKeyboardButton(text=f"🤖 {bot_name}", callback_data="no_action")])
    
    if has_session:
        if is_running:
            buttons.append([InlineKeyboardButton(text="⏹ Остановить", callback_data="gram_stop")])
        else:
            buttons.append([InlineKeyboardButton(text="▶️ Запустить", callback_data="gram_start")])
    else:
        buttons.append([InlineKeyboardButton(text="❌ Нет активной сессии", callback_data="no_session")])
    
    buttons.append([InlineKeyboardButton(text="🔄 Сменить бота", callback_data="gram_change_bot")])
    buttons.append([InlineKeyboardButton(text="📋 Выбрать задание", callback_data="gram_choose_task")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="main")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_bot_choice_keyboard(current_bot: str) -> InlineKeyboardMarkup:
    bots = [
        ("@gram_piarbot", "g_piar"),
        ("@gram_prbot", "g_pr"),
    ]
    
    buttons = []
    for name, code in bots:
        check = "✅ " if name == current_bot else ""
        buttons.append([InlineKeyboardButton(
            text=f"{check}{name}", 
            callback_data=f"bot_choice_{code}"
        )])
    
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="gram")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_sessions_keyboard(user_id: int) -> InlineKeyboardMarkup:
    buttons = []
    
    if user_id in user_sessions:
        phone = user_sessions[user_id]
        buttons.append([InlineKeyboardButton(text=f"📱 {phone}", callback_data="sess_info")])
        buttons.append([InlineKeyboardButton(text="🗑 Удалить сессию", callback_data="sess_delete")])
    else:
        buttons.append([InlineKeyboardButton(text="➕ Добавить сессию", callback_data="sess_add")])
    
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_session_actions_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚪 Выйти со всех каналов", callback_data="sess_leave_channels")],
        [InlineKeyboardButton(text="🚪 Выйти со всех групп", callback_data="sess_leave_groups")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="sessions")],
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

class SessionStates(StatesGroup):
    waiting_phone = State()
    waiting_code = State()


# ============ ОБРАБОТЧИКИ ============

@dp.message(Command("start"))
async def start_command(message: types.Message):
    user_id = message.from_user.id
    
    global user_sessions, user_bot_choice
    
    if not user_sessions:
        user_sessions.update(load_sessions())
    if not user_bot_choice:
        user_bot_choice.update(load_bot_choices())
    
    if user_id not in user_bot_choice:
        user_bot_choice[user_id] = "@gram_prbot"
        save_bot_choices()
    
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
    try:
        await callback.message.edit_text(
            "🏠 <b>Главное меню</b>\n\nВыбери раздел:",
            parse_mode=ParseMode.HTML,
            reply_markup=get_main_keyboard()
        )
    except Exception as e:
        logging.error(f"Ошибка main_menu: {e}")
        await callback.message.answer(
            "🏠 <b>Главное меню</b>\n\nВыбери раздел:",
            parse_mode=ParseMode.HTML,
            reply_markup=get_main_keyboard()
        )


# ============ CALLBACK: GRAM БОТ ============

@dp.callback_query(lambda c: c.data == "gram")
async def gram_menu(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    
    if user_id not in user_bot_choice:
        user_bot_choice[user_id] = "@gram_prbot"
        save_bot_choices()
    
    has_session = user_id in user_sessions
    bot_name = user_bot_choice.get(user_id, "@gram_prbot")
    
    is_running = False
    if has_session:
        phone = user_sessions[user_id]
        if phone in active_clients and phone in active_tasks:
            is_running = not active_tasks[phone].done()
    
    text = "🤖 <b>Gram Бот</b>\n\n"
    text += f"🤖 Выбран: <b>{bot_name}</b>\n\n"
    
    if has_session:
        phone = user_sessions[user_id]
        text += f"✅ Активная сессия: <b>{phone}</b>\n"
        text += f"📊 Статус: {'🟢 Запущен' if is_running else '🔴 Остановлен'}\n\n"
    else:
        text += "❌ Нет активной сессии.\n\n"
        text += "Сначала добавь сессию в разделе 'Мои сессии'."
    
    try:
        await callback.message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=get_gram_main_keyboard(has_session, is_running, bot_name)
        )
    except Exception as e:
        logging.error(f"Ошибка gram_menu: {e}")
        await callback.message.answer(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=get_gram_main_keyboard(has_session, is_running, bot_name)
        )


@dp.callback_query(lambda c: c.data == "gram_choose_task")
async def gram_choose_task(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    
    try:
        await callback.message.edit_text(
            "📋 <b>Выбор типа заданий</b>\n\n"
            "Выбери тип заданий, которые будет выполнять бот:\n\n"
            "✅ - текущий выбранный тип",
            parse_mode=ParseMode.HTML,
            reply_markup=get_task_choice_keyboard(user_id)
        )
    except Exception as e:
        logging.error(f"Ошибка gram_choose_task: {e}")
        await callback.message.answer(
            "📋 <b>Выбор типа заданий</b>\n\n"
            "Выбери тип заданий, которые будет выполнять бот:\n\n"
            "✅ - текущий выбранный тип",
            parse_mode=ParseMode.HTML,
            reply_markup=get_task_choice_keyboard(user_id)
        )


@dp.callback_query(lambda c: c.data == "gram_change_bot")
async def gram_change_bot(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    current_bot = user_bot_choice.get(user_id, "@gram_prbot")
    
    try:
        await callback.message.edit_text(
            "🔄 <b>Выбор Gram бота</b>\n\n"
            "Выбери бота для работы:",
            parse_mode=ParseMode.HTML,
            reply_markup=get_bot_choice_keyboard(current_bot)
        )
    except Exception as e:
        logging.error(f"Ошибка gram_change_bot: {e}")
        await callback.message.answer(
            "🔄 <b>Выбор Gram бота</b>\n\n"
            "Выбери бота для работы:",
            parse_mode=ParseMode.HTML,
            reply_markup=get_bot_choice_keyboard(current_bot)
        )


@dp.callback_query(lambda c: c.data.startswith("bot_choice_"))
async def bot_choice(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    bot_code = callback.data.replace("bot_choice_", "")
    
    if bot_code == "g_piar":
        bot_name = "@gram_piarbot"
    else:
        bot_name = "@gram_prbot"
    
    user_bot_choice[user_id] = bot_name
    save_bot_choices()
    
    if user_id in user_sessions:
        phone = user_sessions[user_id]
        
        if phone in active_tasks and not active_tasks[phone].done():
            await stop_gram_bot(phone)
            
            if phone in active_clients:
                from gram_bot import set_user_chat_id
                set_user_chat_id(user_id)
                client = active_clients[phone]
                await start_gram_worker(client, bot_name, phone)
    
    try:
        await callback.message.edit_text(
            f"✅ <b>Бот изменен!</b>\n\n"
            f"🤖 Выбран: <b>{bot_name}</b>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logging.error(f"Ошибка bot_choice: {e}")
        await callback.message.answer(
            f"✅ <b>Бот изменен!</b>\n\n"
            f"🤖 Выбран: <b>{bot_name}</b>",
            parse_mode=ParseMode.HTML
        )
    
    await gram_menu(callback)


@dp.callback_query(lambda c: c.data == "gram_start")
async def gram_start(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    
    if user_id not in user_sessions:
        await callback.answer("❌ Нет активной сессии")
        return
    
    phone = user_sessions[user_id]
    
    if phone not in active_clients:
        await callback.answer("❌ Сессия не активна. Пересоздайте.")
        return
    
    bot_name = user_bot_choice.get(user_id, "@gram_prbot")
    
    from gram_bot import set_user_chat_id
    set_user_chat_id(user_id)
    client = active_clients[phone]
    
    await start_gram_worker(client, bot_name, phone, user_id)
    
    await gram_menu(callback)


@dp.callback_query(lambda c: c.data == "gram_stop")
async def gram_stop(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    
    if user_id not in user_sessions:
        await callback.answer("❌ Нет активной сессии")
        return
    
    phone = user_sessions[user_id]
    
    await stop_gram_bot(phone)
    
    await gram_menu(callback)


@dp.callback_query(lambda c: c.data == "no_session")
async def no_session(callback: types.CallbackQuery):
    await callback.answer("Сначала добавь сессию в разделе 'Мои сессии'", show_alert=True)


@dp.callback_query(lambda c: c.data == "no_action")
async def no_action(callback: types.CallbackQuery):
    await callback.answer()


@dp.message(Command("stop_gram"))
async def stop_gram_command(message: types.Message):
    user_id = message.from_user.id
    
    if user_id not in user_sessions:
        await message.answer("❌ Нет активной сессии")
        return
    
    phone = user_sessions[user_id]
    
    result = await stop_gram_bot(phone)
    
    if result:
        await message.answer(f"✅ Gram бот остановлен для {phone}\n\nДля возобновления нажмите 'Запустить' в меню Gram бота.")
    else:
        await message.answer("❌ Gram бот не был запущен")


@dp.message(Command("continue_gram"))
async def continue_gram(message: types.Message):
    user_id = message.from_user.id
    
    if user_id not in user_sessions:
        await message.answer("❌ Нет активной сессии")
        return
    
    phone = user_sessions[user_id]
    
    from gram_bot import continue_gram_bot
    result = await continue_gram_bot(phone)
    
    if result:
        await message.answer(
            "✅ Gram бот продолжен!\n\n"
            "Если капча еще активна, пройдите её вручную в Telegram и нажмите /continue_gram снова."
        )
    else:
        await message.answer("❌ Ошибка продолжения. Попробуйте перезапустить бота.")


# ============ CALLBACK: СЕССИИ ============

@dp.callback_query(lambda c: c.data == "sessions")
async def sessions_menu(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    
    text = "📱 <b>Мои сессии</b>\n\n"
    
    if user_id in user_sessions:
        phone = user_sessions[user_id]
        text += f"✅ Активная сессия:\n"
        text += f"📱 <b>{phone}</b>\n\n"
        
        if phone in active_clients:
            try:
                me = await active_clients[phone].get_me()
                text += "🟢 Сессия активна\n"
            except Exception:
                text += "🟡 Сессия требует переподключения\n"
        else:
            text += "🔴 Сессия не подключена\n"
        
        text += "\n<i>Нажми на номер для управления</i>"
    else:
        text += "❌ Нет активных сессий\n\n"
        text += "Нажми 'Добавить сессию' для авторизации"
    
    try:
        await callback.message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=get_sessions_keyboard(user_id)
        )
    except Exception as e:
        logging.error(f"Ошибка sessions_menu: {e}")
        await callback.message.answer(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=get_sessions_keyboard(user_id)
        )


@dp.callback_query(lambda c: c.data == "sess_add")
async def session_add(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(SessionStates.waiting_phone)
    
    try:
        await callback.message.edit_text(
            "📱 <b>Добавление сессии</b>\n\n"
            "Введите номер телефона в формате:\n"
            "<code>+79172993848</code>\n\n"
            "или отправьте /cancel для отмены",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logging.error(f"Ошибка session_add: {e}")
        await callback.message.answer(
            "📱 <b>Добавление сессии</b>\n\n"
            "Введите номер телефона в формате:\n"
            "<code>+79172993848</code>\n\n"
            "или отправьте /cancel для отмены",
            parse_mode=ParseMode.HTML
        )


@dp.message(SessionStates.waiting_phone)
async def session_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    
    if not re.match(r'^\+?\d{10,15}$', phone):
        await message.answer(
            "❌ Неверный формат номера.\n"
            "Используйте формат: <code>+79172993848</code>",
            parse_mode=ParseMode.HTML
        )
        return
    
    await state.update_data(phone=phone)
    await state.set_state(SessionStates.waiting_code)
    
    from gram_bot import send_code, set_user_chat_id
    set_user_chat_id(message.chat.id)
    result = await send_code(phone, "gram_prbot")
    
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


@dp.message(SessionStates.waiting_code)
async def session_code(message: types.Message, state: FSMContext):
    code = message.text.strip()
    user_id = message.from_user.id
    
    data = await state.get_data()
    phone = data.get("phone")
    
    from gram_bot import start_gram_bot
    result = await start_gram_bot(phone, code, "gram_prbot", message.chat.id)
    
    await state.clear()
    
    if result:
        user_sessions[user_id] = phone
        save_sessions()
        
        bot_name = user_bot_choice.get(user_id, "@gram_prbot")
        
        await message.answer(
            f"✅ <b>Сессия добавлена!</b>\n\n"
            f"📱 {phone}\n"
            f"🤖 Выбранный бот: {bot_name}\n\n"
            f"Теперь перейди в раздел 'Gram Бот' и нажми 'Запустить'",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🤖 Перейти в Gram", callback_data="gram")],
                [InlineKeyboardButton(text="📱 Мои сессии", callback_data="sessions")],
            ])
        )
    else:
        await message.answer(
            "❌ Ошибка авторизации.\n"
            "Проверьте код и попробуйте снова.\n\n"
            "Отправьте /start для возврата в меню"
        )


@dp.callback_query(lambda c: c.data == "sess_info")
async def session_info(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    
    if user_id not in user_sessions:
        await callback.answer("❌ Сессия не найдена")
        return
    
    phone = user_sessions[user_id]
    
    if phone not in active_clients:
        await callback.answer("❌ Сессия не активна")
        return
    
    client = active_clients[phone]
    
    try:
        me = await client.get_me()
        username = f"@{me.username}" if me.username else "Нет юзернейма"
        user_id_telegram = me.id
        first_name = me.first_name or ""
        last_name = me.last_name or ""
        name = f"{first_name} {last_name}".strip()
        
        is_active = phone in active_tasks and not active_tasks[phone].done()
        
        text = f"📱 <b>Информация о сессии</b>\n\n"
        text += f"👤 <b>{name or 'Без имени'}</b>\n"
        text += f"📱 {phone}\n"
        text += f"🆔 {user_id_telegram}\n"
        text += f"👤 {username}\n"
        text += f"📊 Статус: {'🟢 Активна' if is_active else '🟡 Остановлена'}\n"
        
    except Exception as e:
        text = f"📱 <b>Информация о сессии</b>\n\n"
        text += f"📱 {phone}\n"
        text += f"❌ Ошибка получения данных: {e}\n"
    
    try:
        await callback.message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=get_session_actions_keyboard()
        )
    except Exception as e:
        logging.error(f"Ошибка session_info: {e}")
        await callback.message.answer(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=get_session_actions_keyboard()
        )


@dp.callback_query(lambda c: c.data == "sess_delete")
async def session_delete(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    
    if user_id not in user_sessions:
        await callback.answer("❌ Сессия не найдена")
        return
    
    phone = user_sessions[user_id]
    
    await stop_gram_bot(phone)
    
    if phone in active_clients:
        try:
            await active_clients[phone].disconnect()
        except:
            pass
        del active_clients[phone]
    
    if phone in active_tasks:
        del active_tasks[phone]
    
    del user_sessions[user_id]
    save_sessions()
    
    try:
        await callback.message.edit_text(
            f"🗑 <b>Сессия удалена</b>\n\n"
            f"📱 {phone}\n\n"
            f"Сессия успешно удалена.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📱 Мои сессии", callback_data="sessions")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="main")]
            ])
        )
    except Exception as e:
        logging.error(f"Ошибка session_delete: {e}")
        await callback.message.answer(
            f"🗑 <b>Сессия удалена</b>\n\n"
            f"📱 {phone}\n\n"
            f"Сессия успешно удалена.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📱 Мои сессии", callback_data="sessions")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="main")]
            ])
        )


# ============ ВЫХОД ИЗ КАНАЛОВ ============

@dp.callback_query(lambda c: c.data == "sess_leave_channels")
async def session_leave_channels(callback: types.CallbackQuery):
    await callback.answer("⏳ Выхожу из каналов...")
    user_id = callback.from_user.id
    
    if user_id not in user_sessions:
        await callback.answer("❌ Нет активной сессии")
        return
    
    phone = user_sessions[user_id]
    
    if phone not in active_clients:
        await callback.answer("❌ Сессия не активна")
        return
    
    client = active_clients[phone]
    
    try:
        from telethon.tl.functions.channels import LeaveChannelRequest
        from telethon.tl.types import InputChannel
        
        left_count = 0
        error_count = 0
        skipped_count = 0
        
        await callback.message.edit_text(
            f"⏳ <b>Выход из каналов...</b>\n\n"
            f"📱 {phone}\n"
            f"Это может занять некоторое время",
            parse_mode=ParseMode.HTML
        )
        
        async for dialog in client.iter_dialogs():
            try:
                if dialog.is_channel:
                    if dialog.entity.username == "me":
                        skipped_count += 1
                        continue
                    
                    try:
                        input_channel = InputChannel(
                            channel_id=dialog.entity.id,
                            access_hash=dialog.entity.access_hash
                        )
                        await client(LeaveChannelRequest(input_channel))
                        left_count += 1
                        logging.info(f"🚪 Вышел из канала: {dialog.name}")
                        await asyncio.sleep(1.5)
                    except Exception as e:
                        error_msg = str(e).lower()
                        if "not a member" in error_msg or "already left" in error_msg or "user not found" in error_msg:
                            skipped_count += 1
                        elif "admin" in error_msg or "creator" in error_msg:
                            skipped_count += 1
                        else:
                            error_count += 1
                            logging.error(f"❌ Ошибка выхода из {dialog.name}: {e}")
                                
            except Exception as e:
                error_count += 1
                continue
        
        result_text = (
            f"✅ <b>Выход из каналов завершен!</b>\n\n"
            f"📱 {phone}\n"
            f"🚪 Вышел из: {left_count} каналов\n"
            f"⏭ Пропущено: {skipped_count}\n"
            f"❌ Ошибок: {error_count}\n\n"
            f"<i>Личные чаты не были затронуты</i>"
        )
        
        try:
            await callback.message.edit_text(
                result_text,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад к сессии", callback_data="sess_info")],
                    [InlineKeyboardButton(text="🏠 Меню", callback_data="main")]
                ])
            )
        except Exception as e:
            logging.error(f"Ошибка session_leave_channels: {e}")
            await callback.message.answer(
                result_text,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад к сессии", callback_data="sess_info")],
                    [InlineKeyboardButton(text="🏠 Меню", callback_data="main")]
                ])
            )
        
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Критическая ошибка: {e}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="sessions")]
            ])
        )


# ============ ВЫХОД ИЗ ГРУПП ============

@dp.callback_query(lambda c: c.data == "sess_leave_groups")
async def session_leave_groups(callback: types.CallbackQuery):
    await callback.answer("⏳ Выхожу из групп...")
    user_id = callback.from_user.id
    
    if user_id not in user_sessions:
        await callback.answer("❌ Нет активной сессии")
        return
    
    phone = user_sessions[user_id]
    
    if phone not in active_clients:
        await callback.answer("❌ Сессия не активна")
        return
    
    client = active_clients[phone]
    
    try:
        from telethon.tl.functions.channels import LeaveChannelRequest
        from telethon.tl.types import InputChannel
        
        left_count = 0
        error_count = 0
        skipped_count = 0
        
        await callback.message.edit_text(
            f"⏳ <b>Выход из групп...</b>\n\n"
            f"📱 {phone}\n"
            f"Это может занять некоторое время",
            parse_mode=ParseMode.HTML
        )
        
        async for dialog in client.iter_dialogs():
            try:
                if dialog.is_group:
                    try:
                        if hasattr(dialog.entity, 'access_hash') and dialog.entity.access_hash:
                            input_channel = InputChannel(
                                channel_id=dialog.entity.id,
                                access_hash=dialog.entity.access_hash
                            )
                            await client(LeaveChannelRequest(input_channel))
                            left_count += 1
                            logging.info(f"🚪 Вышел из группы: {dialog.name}")
                        else:
                            from telethon.tl.functions.messages import DeleteChatUserRequest
                            from telethon.tl.types import InputUserSelf
                            await client(DeleteChatUserRequest(
                                chat_id=dialog.id,
                                user_id=InputUserSelf()
                            ))
                            left_count += 1
                            logging.info(f"🚪 Вышел из группы (обычная): {dialog.name}")
                        
                        await asyncio.sleep(1.5)
                    except Exception as e:
                        error_msg = str(e).lower()
                        if "not a member" in error_msg or "already left" in error_msg or "user not found" in error_msg:
                            skipped_count += 1
                        elif "admin" in error_msg or "creator" in error_msg:
                            skipped_count += 1
                        else:
                            error_count += 1
                            logging.error(f"❌ Ошибка выхода из группы {dialog.name}: {e}")
                                
            except Exception as e:
                error_count += 1
                continue
        
        result_text = (
            f"✅ <b>Выход из групп завершен!</b>\n\n"
            f"📱 {phone}\n"
            f"🚪 Вышел из: {left_count} групп\n"
            f"⏭ Пропущено: {skipped_count}\n"
            f"❌ Ошибок: {error_count}\n\n"
            f"<i>Личные чаты не были затронуты</i>"
        )
        
        try:
            await callback.message.edit_text(
                result_text,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад к сессии", callback_data="sess_info")],
                    [InlineKeyboardButton(text="🏠 Меню", callback_data="main")]
                ])
            )
        except Exception as e:
            logging.error(f"Ошибка session_leave_groups: {e}")
            await callback.message.answer(
                result_text,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад к сессии", callback_data="sess_info")],
                    [InlineKeyboardButton(text="🏠 Меню", callback_data="main")]
                ])
            )
        
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Критическая ошибка: {e}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="sessions")]
            ])
        )


# ============ CALLBACK: ЮЗЕРНЕЙМЫ ============

@dp.callback_query(lambda c: c.data == "users")
async def username_menu(callback: types.CallbackQuery):
    await callback.answer()
    try:
        await callback.message.edit_text(
            "👤 <b>Раздел Юзернеймы</b>\n\n"
            "🔍 Поиск свободных 5-значных юзернеймов\n\n"
            "Выбери действие:",
            parse_mode=ParseMode.HTML,
            reply_markup=get_username_keyboard()
        )
    except Exception as e:
        logging.error(f"Ошибка username_menu: {e}")
        await callback.message.answer(
            "👤 <b>Раздел Юзернеймы</b>\n\n"
            "🔍 Поиск свободных 5-значных юзернеймов\n\n"
            "Выбери действие:",
            parse_mode=ParseMode.HTML,
            reply_markup=get_username_keyboard()
        )


@dp.message(Command("cancel"))
async def cancel_command(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "❌ Отменено.",
        reply_markup=get_main_keyboard()
    )


# ============ ИНИЦИАЛИЗАЦИЯ ============

async def main():
    global user_sessions, user_bot_choice
    
    user_sessions.update(load_sessions())
    user_bot_choice.update(load_bot_choices())
    
    set_bot_instance(bot)
    logging.info("✅ Экземпляр бота передан в gram_bot")
    
    init_username_bot(dp)
    init_gram_bot(dp)
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("⛔ Бот остановлен")
