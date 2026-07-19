import logging
import os
import asyncio
import json
import re
from datetime import datetime
from typing import Dict, Optional, List
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from dotenv import load_dotenv

from username_bot import router as username_router, init_username_bot
from gram_bot import (
    router as gram_router, 
    init_gram_bot, 
    active_clients, 
    active_tasks, 
    set_user_chat_id, 
    start_gram_worker, 
    stop_gram_bot, 
    set_bot_instance as set_gram_bot_instance,
    get_task_choice_keyboard,
    get_bot_category_keyboard,
    get_bot_settings_keyboard,
    continue_gram_bot,
    send_code,
    start_gram_bot as start_gram_bot_auth
)

# Импорты для капчи
from captcha_solver import (
    set_captcha_bot,
    set_captcha_clients,
    set_captcha_continue_callback,
    set_auto_click_timeout,
    set_ai_solver,
    handle_captcha_answer,
    check_captcha_status,
    stop_captcha
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# Храним активные сессии пользователей (максимум 5)
user_sessions: Dict[int, List[str]] = {}  # user_id -> [phone1, phone2, ...]
user_active_session: Dict[int, int] = {}  # user_id -> индекс активной сессии
user_bot_choice: Dict[int, str] = {}  # user_id -> bot_username
user_work_sessions: Dict[int, List[str]] = {}  # user_id -> [phone1, phone2, ...] - сессии для работы

# Файлы для сохранения
SESSIONS_FILE = "user_sessions.json"
BOT_CHOICE_FILE = "user_bot_choice.json"
ACTIVE_SESSION_FILE = "user_active_session.json"
WORK_SESSIONS_FILE = "user_work_sessions.json"

MAX_SESSIONS = 5


# ============ СОХРАНЕНИЕ ДАННЫХ ============

def load_sessions() -> Dict[int, List[str]]:
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

def load_active_session() -> Dict[int, int]:
    if os.path.exists(ACTIVE_SESSION_FILE):
        try:
            with open(ACTIVE_SESSION_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return {int(k): v for k, v in data.items()}
        except Exception as e:
            logging.error(f"Ошибка загрузки активной сессии: {e}")
    return {}

def save_active_session():
    try:
        with open(ACTIVE_SESSION_FILE, 'w', encoding='utf-8') as f:
            json.dump(user_active_session, f, indent=2, ensure_ascii=False)
        logging.info("✅ Активная сессия сохранена")
    except Exception as e:
        logging.error(f"Ошибка сохранения активной сессии: {e}")

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

def load_work_sessions() -> Dict[int, List[str]]:
    if os.path.exists(WORK_SESSIONS_FILE):
        try:
            with open(WORK_SESSIONS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return {int(k): v for k, v in data.items()}
        except Exception as e:
            logging.error(f"Ошибка загрузки рабочих сессий: {e}")
    return {}

def save_work_sessions():
    try:
        with open(WORK_SESSIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(user_work_sessions, f, indent=2, ensure_ascii=False)
        logging.info("✅ Рабочие сессии сохранены")
    except Exception as e:
        logging.error(f"Ошибка сохранения рабочих сессий: {e}")


def get_user_active_phone(user_id: int) -> Optional[str]:
    """Получить активную сессию пользователя"""
    if user_id in user_sessions and user_id in user_active_session:
        idx = user_active_session[user_id]
        if idx < len(user_sessions[user_id]):
            return user_sessions[user_id][idx]
    return None

def set_user_active_phone(user_id: int, phone: str):
    """Установить активную сессию"""
    if user_id in user_sessions:
        if phone in user_sessions[user_id]:
            idx = user_sessions[user_id].index(phone)
            user_active_session[user_id] = idx
            save_active_session()
            return True
    return False


# ============ КЛАВИАТУРЫ ============

def get_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Боты", callback_data="bots")],
        [InlineKeyboardButton(text="👤 Юзернеймы", callback_data="users")],
        [InlineKeyboardButton(text="📱 Мои сессии", callback_data="sessions")],
    ])

def get_bots_list_keyboard() -> InlineKeyboardMarkup:
    """Список доступных ботов для работы"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 PR GRAMM", callback_data="bot_prgramm")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main")],
    ])

def get_bot_detail_keyboard(has_session: bool = False, is_running: bool = False) -> InlineKeyboardMarkup:
    """Меню конкретного бота: Включить / Настройки / Назад"""
    buttons = []
    
    if has_session:
        if is_running:
            buttons.append([InlineKeyboardButton(text="⏹ Выключить", callback_data="bot_prgramm_stop")])
        else:
            buttons.append([InlineKeyboardButton(text="▶️ Включить", callback_data="bot_prgramm_start")])
    else:
        buttons.append([InlineKeyboardButton(text="❌ Нет активной сессии", callback_data="no_session")])
    
    buttons.append([InlineKeyboardButton(text="⚙️ Настройки", callback_data="bot_prgramm_settings")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="bots")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_work_sessions_keyboard(user_id: int) -> InlineKeyboardMarkup:
    buttons = []
    
    if user_id not in user_sessions or not user_sessions[user_id]:
        buttons.append([InlineKeyboardButton(text="❌ Нет сессий", callback_data="no_action")])
        buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="bot_prgramm_settings")])
        return InlineKeyboardMarkup(inline_keyboard=buttons)
    
    work_sessions = user_work_sessions.get(user_id, [])
    
    for phone in user_sessions[user_id]:
        is_selected = phone in work_sessions
        is_online = phone in active_clients and active_clients[phone].is_connected()
        
        status = "✅" if is_selected else "⬜"
        online = "🟢" if is_online else "🔴"
        
        text = f"{status} {online} {phone}"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"work_toggle_{phone}")])
    
    buttons.append([InlineKeyboardButton(text="✅ Выбрать все", callback_data="work_select_all")])
    buttons.append([InlineKeyboardButton(text="⬜ Снять все", callback_data="work_select_none")])
    buttons.append([InlineKeyboardButton(text="🚀 Запустить задания", callback_data="work_start_all")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="bot_prgramm_settings")])
    
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
    
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="bot_prgramm_settings")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_sessions_keyboard(user_id: int) -> InlineKeyboardMarkup:
    buttons = []
    
    if user_id in user_sessions and user_sessions[user_id]:
        phones = user_sessions[user_id]
        active_idx = user_active_session.get(user_id, 0)
        
        for i, phone in enumerate(phones):
            is_active = (i == active_idx)
            text = f"{'✅ ' if is_active else ''}📱 {phone}"
            buttons.append([InlineKeyboardButton(
                text=text, 
                callback_data=f"sess_switch_{i}"
            )])
        
        if len(phones) < MAX_SESSIONS:
            buttons.append([InlineKeyboardButton(text="➕ Добавить сессию", callback_data="sess_add")])
        
        buttons.append([InlineKeyboardButton(text="🗑 Удалить сессию", callback_data="sess_delete_choose")])
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

def get_session_delete_keyboard(user_id: int) -> InlineKeyboardMarkup:
    buttons = []
    if user_id in user_sessions:
        for i, phone in enumerate(user_sessions[user_id]):
            buttons.append([InlineKeyboardButton(
                text=f"❌ {phone}", 
                callback_data=f"sess_del_{i}"
            )])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="sessions")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ============ СОСТОЯНИЯ ============

class SessionStates(StatesGroup):
    waiting_phone = State()
    waiting_code = State()


# ============ ОБРАБОТЧИКИ ============

@dp.message(Command("start"))
async def start_command(message: types.Message):
    user_id = message.from_user.id
    
    global user_sessions, user_bot_choice, user_active_session, user_work_sessions
    
    if not user_sessions:
        user_sessions.update(load_sessions())
    if not user_bot_choice:
        user_bot_choice.update(load_bot_choices())
    if not user_active_session:
        user_active_session.update(load_active_session())
    if not user_work_sessions:
        user_work_sessions.update(load_work_sessions())
    
    if user_id not in user_bot_choice:
        user_bot_choice[user_id] = "@gram_prbot"
        save_bot_choices()
    
    if user_id not in user_sessions:
        user_sessions[user_id] = []
    
    if user_id not in user_work_sessions:
        user_work_sessions[user_id] = []
    
    await message.answer(
        f"👋 Привет, {message.from_user.first_name or 'Пользователь'}!\n\n"
        f"🤖 <b>Telegram Бот-Центр</b>\n\n"
        f"📱 Максимум сессий: {MAX_SESSIONS}\n"
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


# ============ CALLBACK: БОТЫ ============

@dp.callback_query(lambda c: c.data == "bots")
async def bots_menu(callback: types.CallbackQuery):
    """Список доступных ботов для работы"""
    await callback.answer()
    
    text = "🤖 <b>Боты</b>\n\n"
    text += "Доступные боты для работы:\n\n"
    text += "Выбери бота, чтобы настроить его или запустить:"
    
    try:
        await callback.message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=get_bots_list_keyboard()
        )
    except Exception as e:
        logging.error(f"Ошибка bots_menu: {e}")
        await callback.message.answer(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=get_bots_list_keyboard()
        )


@dp.callback_query(lambda c: c.data == "bot_prgramm")
async def bot_prgramm_menu(callback: types.CallbackQuery):
    """Меню бота PR GRAMM: Включить / Настройки / Назад"""
    await callback.answer()
    user_id = callback.from_user.id
    
    if user_id not in user_bot_choice:
        user_bot_choice[user_id] = "@gram_prbot"
        save_bot_choices()
    
    phone = get_user_active_phone(user_id)
    has_session = phone is not None
    bot_name = user_bot_choice.get(user_id, "@gram_prbot")
    
    is_running = False
    if has_session and phone in active_tasks:
        is_running = not active_tasks[phone].done()
    
    text = "📢 <b>PR GRAMM</b>\n\n"
    text += f"🤖 Рабочий бот: <b>{bot_name}</b>\n\n"
    
    if has_session:
        text += f"✅ Активная сессия: <b>{phone}</b>\n"
        text += f"📊 Статус: {'🟢 Запущен' if is_running else '🔴 Остановлен'}\n\n"
        text += f"📋 Рабочих сессий: {len(user_work_sessions.get(user_id, []))}\n"
    else:
        text += "❌ Нет активной сессии.\n\n"
        text += "Сначала добавь сессию в разделе 'Мои сессии'."
    
    try:
        await callback.message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=get_bot_detail_keyboard(has_session, is_running)
        )
    except Exception as e:
        logging.error(f"Ошибка bot_prgramm_menu: {e}")
        await callback.message.answer(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=get_bot_detail_keyboard(has_session, is_running)
        )


@dp.callback_query(lambda c: c.data == "bot_prgramm_settings")
async def bot_prgramm_settings(callback: types.CallbackQuery):
    """Настройки бота"""
    await callback.answer()
    
    text = "⚙️ <b>Настройки — PR GRAMM</b>\n\n"
    text += "Выбери раздел настроек:"
    
    try:
        await callback.message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=get_bot_settings_keyboard()
        )
    except Exception as e:
        logging.error(f"Ошибка bot_prgramm_settings: {e}")
        await callback.message.answer(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=get_bot_settings_keyboard()
        )


# ============ СЕССИИ ДЛЯ РАБОТЫ ============

@dp.callback_query(lambda c: c.data == "gram_sessions")
async def gram_sessions(callback: types.CallbackQuery):
    """Выбор сессий для работы"""
    await callback.answer()
    user_id = callback.from_user.id
    
    text = "📋 <b>Сессии для работы</b>\n\n"
    text += "Выбери сессии, на которых будут выполняться задания:\n"
    text += "✅ - выбрана для работы\n"
    text += "🟢 - сессия онлайн\n"
    text += "🔴 - сессия офлайн\n\n"
    
    if user_id in user_sessions and user_sessions[user_id]:
        work_sessions = user_work_sessions.get(user_id, [])
        text += f"📊 Выбрано: {len(work_sessions)}/{len(user_sessions[user_id])}\n"
    else:
        text += "❌ Нет сессий"
    
    try:
        await callback.message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=get_work_sessions_keyboard(user_id)
        )
    except Exception as e:
        logging.error(f"Ошибка gram_sessions: {e}")
        await callback.message.answer(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=get_work_sessions_keyboard(user_id)
        )


@dp.callback_query(lambda c: c.data and c.data.startswith("work_toggle_"))
async def work_toggle_callback(callback: types.CallbackQuery):
    """Переключение сессии для работы"""
    try:
        phone = callback.data.replace("work_toggle_", "")
        user_id = callback.from_user.id
        
        if user_id not in user_work_sessions:
            user_work_sessions[user_id] = []
        
        if phone in user_work_sessions[user_id]:
            user_work_sessions[user_id].remove(phone)
        else:
            user_work_sessions[user_id].append(phone)
        
        save_work_sessions()
        await gram_sessions(callback)
        
    except Exception as e:
        logging.error(f"Ошибка work_toggle: {e}")
        await callback.answer(f"❌ Ошибка: {e}")


@dp.callback_query(lambda c: c.data == "work_select_all")
async def work_select_all(callback: types.CallbackQuery):
    """Выбрать все сессии"""
    try:
        user_id = callback.from_user.id
        
        if user_id in user_sessions:
            user_work_sessions[user_id] = user_sessions[user_id].copy()
            save_work_sessions()
        
        await gram_sessions(callback)
        
    except Exception as e:
        logging.error(f"Ошибка work_select_all: {e}")
        await callback.answer(f"❌ Ошибка: {e}")


@dp.callback_query(lambda c: c.data == "work_select_none")
async def work_select_none(callback: types.CallbackQuery):
    """Снять все сессии"""
    try:
        user_id = callback.from_user.id
        
        if user_id in user_work_sessions:
            user_work_sessions[user_id] = []
            save_work_sessions()
        
        await gram_sessions(callback)
        
    except Exception as e:
        logging.error(f"Ошибка work_select_none: {e}")
        await callback.answer(f"❌ Ошибка: {e}")


@dp.callback_query(lambda c: c.data == "work_start_all")
async def work_start_all(callback: types.CallbackQuery):
    """Запустить задания на всех выбранных сессиях ПАРАЛЛЕЛЬНО"""
    try:
        user_id = callback.from_user.id
        work_sessions = user_work_sessions.get(user_id, [])
        
        if not work_sessions:
            await callback.answer("❌ Нет выбранных сессий", show_alert=True)
            return
        
        await callback.answer(f"🚀 Запускаю {len(work_sessions)} сессий параллельно...")
        
        bot_name = user_bot_choice.get(user_id, "@gram_prbot")
        
        started = 0
        failed = 0
        start_tasks = []
        
        for phone in work_sessions:
            if phone not in active_clients:
                failed += 1
                continue
            
            client = active_clients[phone]
            
            if not client.is_connected():
                try:
                    await client.connect()
                except:
                    failed += 1
                    continue
            
            try:
                if not await client.is_user_authorized():
                    failed += 1
                    continue
            except:
                failed += 1
                continue
            
            if phone in active_tasks and not active_tasks[phone].done():
                started += 1
                continue
            
            # Создаем задачу для параллельного запуска
            task = start_gram_worker(client, bot_name, phone, user_id)
            start_tasks.append(task)
            started += 1
        
        # Запускаем ВСЕ задачи параллельно (не ждём завершения)
        if start_tasks:
            for task in start_tasks:
                asyncio.create_task(task)
            await asyncio.sleep(0.5)  # Даём время на старт
        
        await callback.message.edit_text(
            f"🚀 <b>Запуск завершен!</b>\n\n"
            f"✅ Запущено: {started}\n"
            f"❌ Ошибок: {failed}\n"
            f"📋 Всего сессий: {len(work_sessions)}\n\n"
            f"⚡ Все сессии работают ПАРАЛЛЕЛЬНО",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="gram_sessions")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="main")]
            ])
        )
        
    except Exception as e:
        logging.error(f"Ошибка work_start_all: {e}")
        await callback.answer(f"❌ Ошибка: {e}")


# ============ ВЫБОР ТИПА ЗАДАНИЙ ============

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


# ============ СМЕНА БОТА ============

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
    
    work_sessions = user_work_sessions.get(user_id, [])
    for phone in work_sessions:
        if phone in active_tasks and not active_tasks[phone].done():
            await stop_gram_bot(phone)
            if phone in active_clients:
                client = active_clients[phone]
                if client.is_connected():
                    await start_gram_worker(client, bot_name, phone)
    
    try:
        await callback.message.edit_text(
            f"✅ <b>Бот изменен!</b>\n\n"
            f"🤖 Выбран: <b>{bot_name}</b>\n\n"
            f"🔄 Обновлено для всех рабочих сессий",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logging.error(f"Ошибка bot_choice: {e}")
        await callback.message.answer(
            f"✅ <b>Бот изменен!</b>\n\n"
            f"🤖 Выбран: <b>{bot_name}</b>",
            parse_mode=ParseMode.HTML
        )
    
    await bot_prgramm_menu(callback)


# ============ ВКЛЮЧЕНИЕ/ВЫКЛЮЧЕНИЕ БОТА ============

@dp.callback_query(lambda c: c.data == "bot_prgramm_start")
async def gram_start(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    
    phone = get_user_active_phone(user_id)
    if not phone:
        await callback.answer("❌ Нет активной сессии")
        return
    
    if phone not in active_clients:
        await callback.answer("❌ Сессия не активна. Пересоздайте.")
        return
    
    bot_name = user_bot_choice.get(user_id, "@gram_prbot")
    
    set_user_chat_id(user_id)
    client = active_clients[phone]
    
    if not client.is_connected():
        try:
            await client.connect()
        except Exception as e:
            logging.error(f"❌ Ошибка подключения: {e}")
            await callback.answer("❌ Ошибка подключения к сессии")
            return
    
    try:
        if not await client.is_user_authorized():
            await callback.answer("❌ Сессия не авторизована. Пересоздайте.")
            return
    except Exception as e:
        logging.error(f"❌ Ошибка проверки авторизации: {e}")
        await callback.answer("❌ Ошибка сессии")
        return
    
    await start_gram_worker(client, bot_name, phone, user_id)
    
    await bot_prgramm_menu(callback)


@dp.callback_query(lambda c: c.data == "bot_prgramm_stop")
async def gram_stop(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    
    phone = get_user_active_phone(user_id)
    if not phone:
        await callback.answer("❌ Нет активной сессии")
        return
    
    result = await stop_gram_bot(phone)
    
    if result:
        await callback.answer("✅ Остановлен")
    else:
        await callback.answer("❌ Ошибка остановки")
    
    await bot_prgramm_menu(callback)


@dp.callback_query(lambda c: c.data == "no_session")
async def no_session(callback: types.CallbackQuery):
    await callback.answer("Сначала добавь сессию в разделе 'Мои сессии'", show_alert=True)


@dp.callback_query(lambda c: c.data == "no_action")
async def no_action(callback: types.CallbackQuery):
    await callback.answer()


@dp.message(Command("stop_gram"))
async def stop_gram_command(message: types.Message):
    user_id = message.from_user.id
    
    phone = get_user_active_phone(user_id)
    if not phone:
        await message.answer("❌ Нет активной сессии")
        return
    
    result = await stop_gram_bot(phone)
    
    if result:
        await message.answer(f"✅ Gram бот остановлен для {phone}\n\nДля возобновления нажмите 'Запустить' в меню Gram бота.")
    else:
        await message.answer("❌ Gram бот не был запущен")


@dp.message(Command("continue_gram"))
async def continue_gram_command(message: types.Message):
    user_id = message.from_user.id
    
    phone = get_user_active_phone(user_id)
    if not phone:
        await message.answer("❌ Нет активной сессии")
        return
    
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
    
    if user_id in user_sessions and user_sessions[user_id]:
        phones = user_sessions[user_id]
        active_idx = user_active_session.get(user_id, 0)
        
        text += f"📊 Сессий: {len(phones)}/{MAX_SESSIONS}\n\n"
        
        for i, phone in enumerate(phones):
            is_active = (i == active_idx)
            status = "🟢 активна" if is_active else "⚪"
            text += f"{'✅ ' if is_active else '   '}📱 {phone} - {status}\n"
        
        text += "\n<i>Нажми на номер для переключения</i>"
    else:
        text += "❌ Нет активных сессий\n\n"
        text += f"Максимум: {MAX_SESSIONS} сессий\n"
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


@dp.callback_query(lambda c: c.data and c.data.startswith("sess_switch_"))
async def session_switch(callback: types.CallbackQuery):
    try:
        idx = int(callback.data.replace("sess_switch_", ""))
        user_id = callback.from_user.id
        
        if user_id not in user_sessions or idx >= len(user_sessions[user_id]):
            await callback.answer("❌ Сессия не найдена")
            return
        
        phone = user_sessions[user_id][idx]
        
        current_phone = get_user_active_phone(user_id)
        if current_phone and current_phone in active_tasks:
            await stop_gram_bot(current_phone)
        
        user_active_session[user_id] = idx
        save_active_session()
        
        await callback.answer(f"✅ Переключено на {phone}")
        await sessions_menu(callback)
        
    except Exception as e:
        logging.error(f"Ошибка session_switch: {e}")
        await callback.answer(f"❌ Ошибка: {e}")


@dp.callback_query(lambda c: c.data == "sess_add")
async def session_add(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    user_id = callback.from_user.id
    
    if user_id in user_sessions and len(user_sessions[user_id]) >= MAX_SESSIONS:
        await callback.answer(f"❌ Достигнут лимит сессий ({MAX_SESSIONS})", show_alert=True)
        return
    
    await state.set_state(SessionStates.waiting_phone)
    await state.update_data(user_id=user_id)
    
    try:
        await callback.message.edit_text(
            f"📱 <b>Добавление сессии</b>\n\n"
            f"Сессий: {len(user_sessions.get(user_id, []))}/{MAX_SESSIONS}\n\n"
            "Введите номер телефона в формате:\n"
            "<code>+79172993848</code>\n\n"
            "или отправьте /cancel для отмены",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logging.error(f"Ошибка session_add: {e}")
        await callback.message.answer(
            f"📱 <b>Добавление сессии</b>\n\n"
            f"Сессий: {len(user_sessions.get(user_id, []))}/{MAX_SESSIONS}\n\n"
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
    
    result = await start_gram_bot_auth(phone, code, "gram_prbot", message.chat.id)
    
    await state.clear()
    
    if result:
        if user_id not in user_sessions:
            user_sessions[user_id] = []
        
        if phone not in user_sessions[user_id]:
            user_sessions[user_id].append(phone)
            save_sessions()
            
            user_active_session[user_id] = len(user_sessions[user_id]) - 1
            save_active_session()
            
            if user_id not in user_work_sessions:
                user_work_sessions[user_id] = []
            if phone not in user_work_sessions[user_id]:
                user_work_sessions[user_id].append(phone)
                save_work_sessions()
        
        bot_name = user_bot_choice.get(user_id, "@gram_prbot")
        
        await message.answer(
            f"✅ <b>Сессия добавлена!</b>\n\n"
            f"📱 {phone}\n"
            f"🤖 Выбранный бот: {bot_name}\n"
            f"📊 Всего сессий: {len(user_sessions[user_id])}/{MAX_SESSIONS}\n\n"
            f"✅ Сессия добавлена в рабочие",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🤖 Перейти в Боты", callback_data="bot_prgramm")],
                [InlineKeyboardButton(text="📱 Мои сессии", callback_data="sessions")],
            ])
        )
    else:
        await message.answer(
            "❌ Ошибка авторизации.\n"
            "Проверьте код и попробуйте снова.\n\n"
            "Отправьте /start для возврата в меню"
        )


@dp.callback_query(lambda c: c.data == "sess_delete_choose")
async def session_delete_choose(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    
    if user_id not in user_sessions or not user_sessions[user_id]:
        await callback.answer("❌ Нет сессий для удаления")
        return
    
    try:
        await callback.message.edit_text(
            "🗑 <b>Удаление сессии</b>\n\n"
            "Выбери сессию для удаления:",
            parse_mode=ParseMode.HTML,
            reply_markup=get_session_delete_keyboard(user_id)
        )
    except Exception as e:
        logging.error(f"Ошибка session_delete_choose: {e}")
        await callback.message.answer(
            "🗑 <b>Удаление сессии</b>\n\n"
            "Выбери сессию для удаления:",
            parse_mode=ParseMode.HTML,
            reply_markup=get_session_delete_keyboard(user_id)
        )


@dp.callback_query(lambda c: c.data and c.data.startswith("sess_del_"))
async def session_delete_execute(callback: types.CallbackQuery):
    try:
        idx = int(callback.data.replace("sess_del_", ""))
        user_id = callback.from_user.id
        
        if user_id not in user_sessions or idx >= len(user_sessions[user_id]):
            await callback.answer("❌ Сессия не найдена")
            return
        
        phone = user_sessions[user_id][idx]
        
        if phone in active_tasks:
            await stop_gram_bot(phone)
        
        if phone in active_clients:
            try:
                await active_clients[phone].disconnect()
            except:
                pass
            del active_clients[phone]
        
        del user_sessions[user_id][idx]
        save_sessions()
        
        if user_id in user_work_sessions and phone in user_work_sessions[user_id]:
            user_work_sessions[user_id].remove(phone)
            save_work_sessions()
        
        if user_id in user_active_session:
            if user_active_session[user_id] >= len(user_sessions[user_id]):
                user_active_session[user_id] = max(0, len(user_sessions[user_id]) - 1)
            save_active_session()
        
        await callback.answer(f"✅ Сессия {phone} удалена")
        await sessions_menu(callback)
        
    except Exception as e:
        logging.error(f"Ошибка session_delete_execute: {e}")
        await callback.answer(f"❌ Ошибка: {e}")


@dp.callback_query(lambda c: c.data == "sess_info")
async def session_info(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    
    phone = get_user_active_phone(user_id)
    if not phone:
        await callback.answer("❌ Сессия не найдена")
        return
    
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
        
        total_sessions = len(user_sessions.get(user_id, []))
        work_sessions = len(user_work_sessions.get(user_id, []))
        
        text = f"📱 <b>Информация о сессии</b>\n\n"
        text += f"👤 <b>{name or 'Без имени'}</b>\n"
        text += f"📱 {phone}\n"
        text += f"🆔 {user_id_telegram}\n"
        text += f"👤 {username}\n"
        text += f"📊 Статус: {'🟢 Активна' if is_active else '🟡 Остановлена'}\n"
        text += f"📊 Всего сессий: {total_sessions}/{MAX_SESSIONS}\n"
        text += f"📋 В работе: {work_sessions}\n"
        
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


# ============ ВЫХОД ИЗ КАНАЛОВ ============

@dp.callback_query(lambda c: c.data == "sess_leave_channels")
async def session_leave_channels(callback: types.CallbackQuery):
    await callback.answer("⏳ Выхожу из каналов...")
    user_id = callback.from_user.id
    
    phone = get_user_active_phone(user_id)
    if not phone:
        await callback.answer("❌ Нет активной сессии")
        return
    
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
    
    phone = get_user_active_phone(user_id)
    if not phone:
        await callback.answer("❌ Нет активной сессии")
        return
    
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


# ============ CALLBACK: ОТВЕТЫ НА КАПЧУ ============

@dp.callback_query(lambda c: c.data and c.data.startswith("captcha_answer_"))
async def captcha_answer_callback(callback: types.CallbackQuery):
    try:
        parts = callback.data.split("_")
        chat_id = int(parts[2])
        number = parts[3]
        
        await callback.answer(f"✅ Выбрано: {number}")
        
        success, msg = await handle_captcha_answer(chat_id, number)
        
        if success:
            await callback.message.edit_text(f"✅ {msg}", parse_mode=ParseMode.HTML)
        else:
            await callback.message.edit_text(f"⏳ {msg}", parse_mode=ParseMode.HTML)
            
    except Exception as e:
        logging.error(f"❌ Ошибка captcha_answer: {e}")
        await callback.answer(f"❌ Ошибка: {e}")


@dp.callback_query(lambda c: c.data and c.data.startswith("captcha_check_"))
async def captcha_check_callback(callback: types.CallbackQuery):
    try:
        chat_id = int(callback.data.split("_")[2])
        await callback.answer("🔄 Проверяю...")
        
        success, msg = await check_captcha_status(chat_id)
        
        if success:
            await callback.message.edit_text(f"✅ {msg}", parse_mode=ParseMode.HTML)
        else:
            await callback.message.edit_text(f"⏳ {msg}", parse_mode=ParseMode.HTML)
            
    except Exception as e:
        logging.error(f"❌ Ошибка captcha_check: {e}")
        await callback.answer(f"❌ Ошибка: {e}")


@dp.callback_query(lambda c: c.data and c.data.startswith("captcha_stop_"))
async def captcha_stop_callback(callback: types.CallbackQuery):
    try:
        chat_id = int(callback.data.split("_")[2])
        
        stop_captcha(chat_id)
        
        await callback.answer("⏹ Остановлен")
        await callback.message.edit_text("⏹ Капча остановлена", parse_mode=ParseMode.HTML)
    except Exception as e:
        logging.error(f"❌ Ошибка captcha_stop: {e}")
        await callback.answer(f"❌ Ошибка: {e}")


# ============ ИНИЦИАЛИЗАЦИЯ ============

async def main():
    global user_sessions, user_bot_choice, user_active_session, user_work_sessions
    
    user_sessions.update(load_sessions())
    user_bot_choice.update(load_bot_choices())
    user_active_session.update(load_active_session())
    user_work_sessions.update(load_work_sessions())
    
    # Устанавливаем экземпляры ботов
    set_gram_bot_instance(bot)
    set_captcha_bot(bot)
    set_captcha_clients(active_clients)
    set_captcha_continue_callback(continue_gram_bot)
    set_auto_click_timeout(30)
    set_ai_solver(True)  # Включаем авто-решение капчи через AI
    
    logging.info("✅ Экземпляр бота передан в gram_bot и captcha_solver")
    logging.info("✅ Работает")
    logging.info(f"📱 Максимум сессий: {MAX_SESSIONS}")
    
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
