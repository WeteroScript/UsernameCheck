import logging
import os
import asyncio
import json
import re
from datetime import datetime
from typing import Dict, Optional, List, Any
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest
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
    start_gram_bot as start_gram_bot_auth,
    set_session_config as set_gram_session_config,
    get_session_config as get_gram_session_config
)

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

# Храним сессии пользователей
user_sessions: Dict[int, List[str]] = {}
user_bot_choice: Dict[int, str] = {}
user_session_config: Dict[int, Dict[str, Dict[str, Any]]] = {}

SESSIONS_FILE = "user_sessions.json"
BOT_CHOICE_FILE = "user_bot_choice.json"
SESSION_CONFIG_FILE = "user_session_config.json"

MAX_SESSIONS = 5


# ============ СОХРАНЕНИЕ ============

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
    except Exception as e:
        logging.error(f"Ошибка сохранения выбора ботов: {e}")

def load_session_config() -> Dict[int, Dict[str, Dict[str, Any]]]:
    if os.path.exists(SESSION_CONFIG_FILE):
        try:
            with open(SESSION_CONFIG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return {int(k): v for k, v in data.items()}
        except Exception as e:
            logging.error(f"Ошибка загрузки конфигурации сессий: {e}")
    return {}

def save_session_config():
    try:
        with open(SESSION_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(user_session_config, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Ошибка сохранения конфигурации сессий: {e}")


def get_session_config(user_id: int, phone: str) -> Dict[str, Any]:
    if user_id not in user_session_config:
        user_session_config[user_id] = {}
    if phone not in user_session_config[user_id]:
        user_session_config[user_id][phone] = {
            "enabled": False,
            "task_type": "channels",
            "bot_category": "regular"
        }
        save_session_config()
    return user_session_config[user_id][phone]

def set_session_config(user_id: int, phone: str, key: str, value: Any):
    config = get_session_config(user_id, phone)
    config[key] = value
    save_session_config()
    set_gram_session_config(user_id, phone, key, value)


# ============ БЕЗОПАСНОЕ РЕДАКТИРОВАНИЕ ============

async def safe_edit_message(message: types.Message, text: str, **kwargs):
    """Редактирует сообщение, игнорируя 'message is not modified'"""
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            pass
        else:
            raise


# ============ КЛАВИАТУРЫ ============

def get_sessions_keyboard(user_id: int) -> InlineKeyboardMarkup:
    buttons = []
    if user_id in user_sessions and user_sessions[user_id]:
        for phone in user_sessions[user_id]:
            config = get_session_config(user_id, phone)
            status = "🟢" if config.get("enabled", False) else "🔴"
            buttons.append([InlineKeyboardButton(
                text=f"{status} {phone}",
                callback_data=f"sess_item_{phone}"
            )])
        buttons.append([InlineKeyboardButton(text="🚀 Запустить все сессии", callback_data="sess_start_all")])
        buttons.append([InlineKeyboardButton(text="⏹ Остановить все", callback_data="sess_stop_all")])
        if len(user_sessions[user_id]) < MAX_SESSIONS:
            buttons.append([InlineKeyboardButton(text="➕ Добавить сессию", callback_data="sess_add")])
    else:
        buttons.append([InlineKeyboardButton(text="❌ Нет сессий", callback_data="no_action")])
        buttons.append([InlineKeyboardButton(text="➕ Добавить сессию", callback_data="sess_add")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="bots")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_session_item_keyboard(user_id: int, phone: str) -> InlineKeyboardMarkup:
    config = get_session_config(user_id, phone)
    is_enabled = config.get("enabled", False)
    toggle_text = "⏹ Выключить" if is_enabled else "▶️ Включить"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 " + phone, callback_data="no_action")],
        [InlineKeyboardButton(text=toggle_text, callback_data=f"sess_toggle_{phone}")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data=f"sess_settings_{phone}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="bot_prgramm")],
    ])


def get_session_settings_keyboard(user_id: int, phone: str) -> InlineKeyboardMarkup:
    config = get_session_config(user_id, phone)
    task_type = config.get("task_type", "channels")
    bot_category = config.get("bot_category", "regular")
    task_names = {"channels": "📢 Подписка", "groups": "👥 Группы", "posts": "📱 Посты", "bots": "🤖 Боты"}
    cat_names = {"regular": "Обычные", "webapp": "Web App", "conditions": "С условиями"}
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📋 Тип: {task_names.get(task_type, task_type)}", callback_data=f"sess_task_{phone}")],
        [InlineKeyboardButton(text=f"🤖 Категория: {cat_names.get(bot_category, bot_category)}", callback_data=f"sess_cat_{phone}")],
        [InlineKeyboardButton(text="🔄 Сменить бота", callback_data=f"sess_bot_{phone}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"sess_item_{phone}")],
    ])


def get_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Боты", callback_data="bots")],
        [InlineKeyboardButton(text="👤 Юзернеймы", callback_data="users")],
        [InlineKeyboardButton(text="📱 Мои сессии", callback_data="sessions")],
    ])


def get_bots_list_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 PR GRAMM", callback_data="bot_prgramm")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main")],
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
        for phone in user_sessions[user_id]:
            buttons.append([InlineKeyboardButton(
                text=f"❌ {phone}",
                callback_data=f"sess_del_{phone}"
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
    global user_sessions, user_bot_choice, user_session_config
    
    if not user_sessions:
        user_sessions.update(load_sessions())
    if not user_bot_choice:
        user_bot_choice.update(load_bot_choices())
    if not user_session_config:
        user_session_config.update(load_session_config())
    
    if user_id not in user_bot_choice:
        user_bot_choice[user_id] = "@gram_prbot"
        save_bot_choices()
    if user_id not in user_sessions:
        user_sessions[user_id] = []
    if user_id not in user_session_config:
        user_session_config[user_id] = {}
    
    await message.answer(
        f"👋 Привет, {message.from_user.first_name or 'Пользователь'}!\n\n"
        f"🤖 <b>Telegram Бот-Центр</b>\n\n"
        f"📱 Максимум сессий: {MAX_SESSIONS}\n"
        f"Выбери раздел:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard()
    )


@dp.callback_query(lambda c: c.data == "main")
async def main_menu(callback: types.CallbackQuery):
    await callback.answer()
    await safe_edit_message(
        callback.message,
        "🏠 <b>Главное меню</b>\n\nВыбери раздел:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard()
    )


@dp.callback_query(lambda c: c.data == "bots")
async def bots_menu(callback: types.CallbackQuery):
    await callback.answer()
    await safe_edit_message(
        callback.message,
        "🤖 <b>Боты</b>\n\nВыбери бота:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_bots_list_keyboard()
    )


@dp.callback_query(lambda c: c.data == "bot_prgramm")
async def bot_prgramm_menu(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    
    if user_id not in user_bot_choice:
        user_bot_choice[user_id] = "@gram_prbot"
        save_bot_choices()
    
    text = "📢 <b>PR GRAMM</b>\n\n"
    text += f"🤖 Выбранный бот: <b>{user_bot_choice.get(user_id, '@gram_prbot')}</b>\n\n"
    
    if user_id in user_sessions and user_sessions[user_id]:
        text += f"📱 <b>Сессии:</b>\n"
        for phone in user_sessions[user_id]:
            config = get_session_config(user_id, phone)
            status = "🟢" if config.get("enabled", False) else "🔴"
            task_type = config.get("task_type", "channels")
            task_names = {"channels": "📢 Подписка", "groups": "👥 Группы", "posts": "📱 Посты", "bots": "🤖 Боты"}
            text += f"  {status} {phone} — {task_names.get(task_type, task_type)}\n"
        text += f"\n📊 Сессий: {len(user_sessions[user_id])}/{MAX_SESSIONS}"
    else:
        text += "❌ Нет подключенных сессий\n\n"
        text += "Добавь сессию в разделе 'Мои сессии'"
    
    await safe_edit_message(
        callback.message,
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_sessions_keyboard(user_id)
    )


@dp.callback_query(lambda c: c.data and c.data.startswith("sess_item_"))
async def sess_item_callback(callback: types.CallbackQuery):
    try:
        phone = callback.data.replace("sess_item_", "")
        user_id = callback.from_user.id
        await callback.answer()
        
        config = get_session_config(user_id, phone)
        is_enabled = config.get("enabled", False)
        task_type = config.get("task_type", "channels")
        task_names = {
            "channels": "📢 Подписка на каналы",
            "groups": "👥 Вступление в группы",
            "posts": "📱 Просмотр постов",
            "bots": "🤖 Задания с ботами"
        }
        
        text = f"📱 <b>{phone}</b>\n\n"
        text += f"📊 Статус: {'🟢 Включена' if is_enabled else '🔴 Выключена'}\n"
        text += f"📋 Задание: {task_names.get(task_type, task_type)}\n"
        text += f"🤖 Бот: {user_bot_choice.get(user_id, '@gram_prbot')}\n\n"
        text += "Выбери действие:"
        
        await safe_edit_message(
            callback.message,
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=get_session_item_keyboard(user_id, phone)
        )
    except Exception as e:
        logging.error(f"❌ sess_item_callback: {e}")
        await callback.answer("❌ Ошибка")


@dp.callback_query(lambda c: c.data and c.data.startswith("sess_toggle_"))
async def sess_toggle_callback(callback: types.CallbackQuery):
    try:
        phone = callback.data.replace("sess_toggle_", "")
        user_id = callback.from_user.id
        
        config = get_session_config(user_id, phone)
        current = config.get("enabled", False)
        config["enabled"] = not current
        save_session_config()
        
        if config["enabled"] is False and phone in active_tasks:
            await stop_gram_bot(phone)
        
        if config["enabled"] is True and phone in active_clients:
            bot_name = user_bot_choice.get(user_id, "@gram_prbot")
            client = active_clients[phone]
            if client.is_connected() and await client.is_user_authorized():
                await start_gram_worker(client, bot_name, phone, user_id)
        
        await callback.answer(f"✅ {'Включена' if config['enabled'] else 'Выключена'}")
        await bot_prgramm_menu(callback)
    except Exception as e:
        logging.error(f"❌ sess_toggle_callback: {e}")
        await callback.answer("❌ Ошибка")


@dp.callback_query(lambda c: c.data and c.data.startswith("sess_settings_"))
async def sess_settings_callback(callback: types.CallbackQuery):
    try:
        phone = callback.data.replace("sess_settings_", "")
        user_id = callback.from_user.id
        await callback.answer()
        
        await safe_edit_message(
            callback.message,
            f"⚙️ <b>Настройки — {phone}</b>\n\n"
            "Выбери настройку:",
            parse_mode=ParseMode.HTML,
            reply_markup=get_session_settings_keyboard(user_id, phone)
        )
    except Exception as e:
        logging.error(f"❌ sess_settings_callback: {e}")
        await callback.answer("❌ Ошибка")


@dp.callback_query(lambda c: c.data and c.data.startswith("sess_task_"))
async def sess_task_callback(callback: types.CallbackQuery):
    try:
        phone = callback.data.replace("sess_task_", "")
        user_id = callback.from_user.id
        await callback.answer()
        
        await safe_edit_message(
            callback.message,
            f"📋 <b>Выбор типа заданий для {phone}</b>\n\n"
            "Выбери тип заданий:",
            parse_mode=ParseMode.HTML,
            reply_markup=get_task_choice_keyboard(user_id, phone)
        )
    except Exception as e:
        logging.error(f"❌ sess_task_callback: {e}")
        await callback.answer("❌ Ошибка")


@dp.callback_query(lambda c: c.data and c.data.startswith("sess_cat_"))
async def sess_cat_callback(callback: types.CallbackQuery):
    try:
        phone = callback.data.replace("sess_cat_", "")
        user_id = callback.from_user.id
        await callback.answer()
        
        await safe_edit_message(
            callback.message,
            f"📋 <b>Выбор категории ботов для {phone}</b>\n\n"
            "Выбери категорию:",
            parse_mode=ParseMode.HTML,
            reply_markup=get_bot_category_keyboard(user_id, phone)
        )
    except Exception as e:
        logging.error(f"❌ sess_cat_callback: {e}")
        await callback.answer("❌ Ошибка")


@dp.callback_query(lambda c: c.data and c.data.startswith("sess_bot_"))
async def sess_bot_callback(callback: types.CallbackQuery):
    try:
        phone = callback.data.replace("sess_bot_", "")
        user_id = callback.from_user.id
        
        current_bot = user_bot_choice.get(user_id, "@gram_prbot")
        bots = [("@gram_piarbot", "g_piar"), ("@gram_prbot", "g_pr")]
        buttons = []
        for name, code in bots:
            check = "✅ " if name == current_bot else ""
            buttons.append([InlineKeyboardButton(
                text=f"{check}{name}",
                callback_data=f"sess_bot_choice_{code}_{phone}"
            )])
        buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"sess_item_{phone}")])
        
        await safe_edit_message(
            callback.message,
            f"🔄 <b>Смена бота для {phone}</b>\n\n"
            "Выбери бота:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )
    except Exception as e:
        logging.error(f"❌ sess_bot_callback: {e}")
        await callback.answer("❌ Ошибка")


@dp.callback_query(lambda c: c.data and c.data.startswith("sess_bot_choice_"))
async def sess_bot_choice_callback(callback: types.CallbackQuery):
    try:
        parts = callback.data.split("_")
        bot_code = parts[3]
        phone = parts[4]
        user_id = callback.from_user.id
        
        bot_name = "@gram_piarbot" if bot_code == "g_piar" else "@gram_prbot"
        user_bot_choice[user_id] = bot_name
        save_bot_choices()
        
        await callback.answer(f"✅ {bot_name}")
        await sess_item_callback(callback)
    except Exception as e:
        logging.error(f"❌ sess_bot_choice_callback: {e}")
        await callback.answer("❌ Ошибка")


@dp.callback_query(lambda c: c.data == "sess_start_all")
async def sess_start_all_callback(callback: types.CallbackQuery):
    try:
        user_id = callback.from_user.id
        await callback.answer("🚀 Запускаю...")
        
        started = 0
        failed = 0
        
        for phone in user_sessions.get(user_id, []):
            config = get_session_config(user_id, phone)
            if not config.get("enabled", False):
                continue
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
            if not await client.is_user_authorized():
                failed += 1
                continue
            if phone in active_tasks and not active_tasks[phone].done():
                started += 1
                continue
            bot_name = user_bot_choice.get(user_id, "@gram_prbot")
            await start_gram_worker(client, bot_name, phone, user_id)
            started += 1
            await asyncio.sleep(0.5)
        
        await safe_edit_message(
            callback.message,
            f"🚀 <b>Запуск завершен!</b>\n\n"
            f"✅ Запущено: {started}\n"
            f"❌ Ошибок: {failed}\n"
            f"📋 Всего сессий: {len(user_sessions.get(user_id, []))}",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="bot_prgramm")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="main")]
            ])
        )
    except Exception as e:
        logging.error(f"❌ sess_start_all_callback: {e}")
        await callback.answer("❌ Ошибка")


@dp.callback_query(lambda c: c.data == "sess_stop_all")
async def sess_stop_all_callback(callback: types.CallbackQuery):
    try:
        user_id = callback.from_user.id
        await callback.answer("⏹ Останавливаю...")
        stopped = 0
        for phone in user_sessions.get(user_id, []):
            if phone in active_tasks and not active_tasks[phone].done():
                await stop_gram_bot(phone)
                stopped += 1
                await asyncio.sleep(0.3)
        
        await safe_edit_message(
            callback.message,
            f"⏹ <b>Остановка завершена!</b>\n\n"
            f"✅ Остановлено: {stopped} сессий",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="bot_prgramm")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="main")]
            ])
        )
    except Exception as e:
        logging.error(f"❌ sess_stop_all_callback: {e}")
        await callback.answer("❌ Ошибка")


@dp.callback_query(lambda c: c.data == "sess_add")
async def session_add(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    user_id = callback.from_user.id
    
    if user_id in user_sessions and len(user_sessions[user_id]) >= MAX_SESSIONS:
        await callback.answer(f"❌ Достигнут лимит сессий ({MAX_SESSIONS})", show_alert=True)
        return
    
    await state.set_state(SessionStates.waiting_phone)
    await state.update_data(user_id=user_id)
    
    await safe_edit_message(
        callback.message,
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
            get_session_config(user_id, phone)
        bot_name = user_bot_choice.get(user_id, "@gram_prbot")
        await message.answer(
            f"✅ <b>Сессия добавлена!</b>\n\n"
            f"📱 {phone}\n"
            f"🤖 Выбранный бот: {bot_name}\n"
            f"📊 Всего сессий: {len(user_sessions[user_id])}/{MAX_SESSIONS}\n\n"
            f"Теперь перейди в раздел 'Боты' → 'PR GRAMM' для настройки",
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


@dp.callback_query(lambda c: c.data == "sessions")
async def sessions_menu(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    
    text = "📱 <b>Мои сессии</b>\n\n"
    if user_id in user_sessions and user_sessions[user_id]:
        text += f"📊 Сессий: {len(user_sessions[user_id])}/{MAX_SESSIONS}\n\n"
        for phone in user_sessions[user_id]:
            config = get_session_config(user_id, phone)
            status = "🟢 Вкл" if config.get("enabled", False) else "🔴 Выкл"
            text += f"  {status} 📱 {phone}\n"
        text += "\n<i>Нажми на номер для удаления</i>"
    else:
        text += "❌ Нет активных сессий\n\n"
        text += f"Максимум: {MAX_SESSIONS} сессий"
    
    await safe_edit_message(
        callback.message,
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_session_delete_keyboard(user_id)
    )


@dp.callback_query(lambda c: c.data and c.data.startswith("sess_del_"))
async def session_delete_execute(callback: types.CallbackQuery):
    try:
        phone = callback.data.replace("sess_del_", "")
        user_id = callback.from_user.id
        
        if user_id not in user_sessions or phone not in user_sessions[user_id]:
            await callback.answer("❌ Сессия не найдена")
            return
        
        if phone in active_tasks:
            await stop_gram_bot(phone)
        if phone in active_clients:
            try:
                await active_clients[phone].disconnect()
            except:
                pass
            del active_clients[phone]
        
        user_sessions[user_id].remove(phone)
        save_sessions()
        if user_id in user_session_config and phone in user_session_config[user_id]:
            del user_session_config[user_id][phone]
            save_session_config()
        
        await callback.answer(f"✅ Сессия {phone} удалена")
        await sessions_menu(callback)
    except Exception as e:
        logging.error(f"❌ session_delete_execute: {e}")
        await callback.answer(f"❌ Ошибка: {e}")


@dp.callback_query(lambda c: c.data == "users")
async def username_menu(callback: types.CallbackQuery):
    await callback.answer()
    await safe_edit_message(
        callback.message,
        "👤 <b>Раздел Юзернеймы</b>\n\n"
        "🔍 Поиск свободных 5-значных юзернеймов\n\n"
        "Выбери действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_username_keyboard()
    )


@dp.message(Command("cancel"))
async def cancel_command(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Отменено.", reply_markup=get_main_keyboard())


# ============ КАПЧА ============

@dp.callback_query(lambda c: c.data and c.data.startswith("captcha_answer_"))
async def captcha_answer_callback(callback: types.CallbackQuery):
    try:
        parts = callback.data.split("_")
        chat_id = int(parts[2])
        number = parts[3]
        await callback.answer(f"✅ Выбрано: {number}")
        success, msg = await handle_captcha_answer(chat_id, number)
        if success:
            await safe_edit_message(callback.message, f"✅ {msg}", parse_mode=ParseMode.HTML)
        else:
            await safe_edit_message(callback.message, f"⏳ {msg}", parse_mode=ParseMode.HTML)
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
            await safe_edit_message(callback.message, f"✅ {msg}", parse_mode=ParseMode.HTML)
        else:
            await safe_edit_message(callback.message, f"⏳ {msg}", parse_mode=ParseMode.HTML)
    except Exception as e:
        logging.error(f"❌ Ошибка captcha_check: {e}")
        await callback.answer(f"❌ Ошибка: {e}")


@dp.callback_query(lambda c: c.data and c.data.startswith("captcha_stop_"))
async def captcha_stop_callback(callback: types.CallbackQuery):
    try:
        chat_id = int(callback.data.split("_")[2])
        stop_captcha(chat_id)
        await callback.answer("⏹ Остановлен")
        await safe_edit_message(callback.message, "⏹ Капча остановлена", parse_mode=ParseMode.HTML)
    except Exception as e:
        logging.error(f"❌ Ошибка captcha_stop: {e}")
        await callback.answer(f"❌ Ошибка: {e}")


# ============ ОБРАБОТЧИКИ ИЗ gram_bot.py ============

@dp.callback_query(lambda c: c.data == "gram_choose_task")
async def gram_choose_task(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    await safe_edit_message(
        callback.message,
        "📋 <b>Выбор типа заданий</b>\n\n"
        "Выбери тип заданий:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_task_choice_keyboard(user_id)
    )


@dp.callback_query(lambda c: c.data == "gram_change_bot")
async def gram_change_bot(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    current_bot = user_bot_choice.get(user_id, "@gram_prbot")
    bots = [("@gram_piarbot", "g_piar"), ("@gram_prbot", "g_pr")]
    buttons = []
    for name, code in bots:
        check = "✅ " if name == current_bot else ""
        buttons.append([InlineKeyboardButton(
            text=f"{check}{name}",
            callback_data=f"bot_choice_{code}"
        )])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="bot_prgramm")])
    await safe_edit_message(
        callback.message,
        "🔄 <b>Выбор Gram бота</b>\n\n"
        "Выбери бота для работы:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@dp.callback_query(lambda c: c.data.startswith("bot_choice_"))
async def bot_choice_callback(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    bot_code = callback.data.replace("bot_choice_", "")
    bot_name = "@gram_piarbot" if bot_code == "g_piar" else "@gram_prbot"
    user_bot_choice[user_id] = bot_name
    save_bot_choices()
    await safe_edit_message(
        callback.message,
        f"✅ <b>Бот изменен!</b>\n\n"
        f"🤖 Выбран: <b>{bot_name}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="bot_prgramm")]
        ])
    )
    await bot_prgramm_menu(callback)


# ============ ИНИЦИАЛИЗАЦИЯ ============

async def main():
    global user_sessions, user_bot_choice, user_session_config
    
    os.makedirs("sessions", exist_ok=True)
    os.makedirs("fonts", exist_ok=True)
    
    user_sessions.update(load_sessions())
    user_bot_choice.update(load_bot_choices())
    user_session_config.update(load_session_config())
    
    set_gram_bot_instance(bot)
    set_captcha_bot(bot)
    set_captcha_clients(active_clients)
    set_captcha_continue_callback(continue_gram_bot)
    set_auto_click_timeout(30)
    set_ai_solver(True)
    
    logging.info("✅ Экземпляр бота передан в gram_bot и captcha_solver")
    logging.info(f"📱 Максимум сессий: {MAX_SESSIONS}")
    
    dp.include_router(username_router)
    dp.include_router(gram_router)

    init_username_bot(dp)
    init_gram_bot(dp)
    
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("⛔ Бот остановлен")
