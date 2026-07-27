import logging
import os
import asyncio
import json
import re
from datetime import datetime
from typing import Dict, Optional, List, Any
from aiogram import Bot, Dispatcher, types, BaseMiddleware
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest
from dotenv import load_dotenv

from telethon import functions, types as tl_types
from telethon.tl.types import Channel, Chat

from username_bot import (
    router as username_router,
    init_username_bot,
    set_username_bot,
    start_username_watcher,
)
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
    get_session_config as get_gram_session_config,
    webapp_captcha_pending,
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

from access_control import (
    is_admin,
    is_premium,
    add_admin,
    remove_admin,
    grant_premium,
    revoke_premium,
    get_all_admins,
    get_all_premium,
    resolve_user_id,
    get_max_sessions,
    is_phone_allowed,
    PREMIUM_ICON,
    FREE_ALLOWED_COUNTRY_CODES,
    add_mandatory_channel,
    remove_mandatory_channel,
    get_mandatory_channels,
    ban_user,
    unban_user,
    is_banned,
    get_ban_reason,
    set_technical_mode,
    is_technical_mode,
    block_tech_support,
    unblock_tech_support,
    SUPER_ADMIN_ID,
)

from extra_features import (
    router as extra_router,
    init_extra_features,
    set_bot as set_extra_bot,
    get_extra_main_buttons,
)
from video_download import router as video_router, init_video_download
from channels_feature import init_channels_feature

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
    # Единый источник правды — словарь gram_bot.py. Раньше здесь читался
    # отдельный локальный словарь user_session_config, который не обновлялся,
    # когда пользователь менял тип заданий через кнопки (это меняло только
    # словарь в gram_bot.py) — из-за этого меню показывало старое значение
    # ("Подписки"), хотя реально был выбран другой тип ("Боты").
    config = get_gram_session_config(user_id, phone)
    if user_id not in user_session_config:
        user_session_config[user_id] = {}
    # Держим одну и ту же ссылку на dict в обоих модулях, чтобы дальнейшие
    # изменения из любого места сразу были видны везде.
    user_session_config[user_id][phone] = config
    return config


# ============ РЕЕСТР ПОЛЬЗОВАТЕЛЕЙ (для /mail и даты регистрации) ============

KNOWN_USERS_FILE = "known_users.json"
known_users: Dict[int, Dict[str, Any]] = {}  # user_id -> {"username":, "registered_at":}


def load_known_users() -> Dict[int, Dict[str, Any]]:
    if os.path.exists(KNOWN_USERS_FILE):
        try:
            with open(KNOWN_USERS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return {int(k): v for k, v in data.items()}
        except Exception as e:
            logging.error(f"Ошибка загрузки known_users.json: {e}")
    return {}


def save_known_users():
    try:
        with open(KNOWN_USERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(known_users, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Ошибка сохранения known_users.json: {e}")


def register_known_user(user: types.User):
    is_new = user.id not in known_users
    known_users[user.id] = {
        "username": user.username,
        "registered_at": known_users.get(user.id, {}).get("registered_at") or datetime.now().isoformat(),
    }
    if is_new:
        save_known_users()

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
    else:
        buttons.append([InlineKeyboardButton(text="❌ Нет аккаунтов", callback_data="no_action")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="bots")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_session_item_keyboard(user_id: int, phone: str) -> InlineKeyboardMarkup:
    config = get_session_config(user_id, phone)
    is_enabled = config.get("enabled", False)
    toggle_text = "⏹ Выключить" if is_enabled else "▶️ Включить"
    groups_muted = config.get("groups_muted", False)
    channels_muted = config.get("channels_muted", False)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 " + phone, callback_data="no_action")],
        [InlineKeyboardButton(text=toggle_text, callback_data=f"sess_toggle_{phone}")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data=f"sess_settings_{phone}")],
        [InlineKeyboardButton(
            text=f"{'🔇' if groups_muted else '🔊'} Выкл звук в группах",
            callback_data=f"sess_mute_groups_{phone}"
        )],
        [InlineKeyboardButton(
            text=f"{'🔇' if channels_muted else '🔊'} Выкл звук в каналах",
            callback_data=f"sess_mute_channels_{phone}"
        )],
        [InlineKeyboardButton(text="📋 Список каналов и групп", callback_data=f"sess_chatlist_{phone}")],
        [InlineKeyboardButton(text="🔑 Получить код", callback_data=f"sess_getcode_{phone}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="bot_prgramm")],
    ])


def get_session_settings_keyboard(user_id: int, phone: str) -> InlineKeyboardMarkup:
    config = get_session_config(user_id, phone)
    task_type = config.get("task_type", "channels")
    task_names = {"channels": "📢 Подписка", "groups": "👥 Группы", "posts": "📱 Посты", "bots": "🤖 Боты"}
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📋 Тип: {task_names.get(task_type, task_type)}", callback_data=f"sess_task_{phone}")],
        [InlineKeyboardButton(text="🔄 Сменить бота", callback_data=f"sess_bot_{phone}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"sess_item_{phone}")],
    ])


def get_main_keyboard(user_id: Optional[int] = None) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="🤖 Боты", callback_data="bots")],
        [InlineKeyboardButton(text="👤 Юзернеймы", callback_data="users")],
        [InlineKeyboardButton(text="📱 Аккаунты", callback_data="accounts")],
        [InlineKeyboardButton(text="📢 Каналы", callback_data="channels_menu")],
    ]
    buttons.extend(get_extra_main_buttons())
    if user_id is not None and is_admin(user_id):
        buttons.append([InlineKeyboardButton(text="🛠 Адм хелп", callback_data="adm_help")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_bots_list_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 PR GRAMM", callback_data="bot_prgramm")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main")],
    ])


def get_username_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✨ Генерировать", callback_data="gen")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main")],
    ])


def get_account_unlink_keyboard(user_id: int) -> InlineKeyboardMarkup:
    buttons = []
    if user_id in user_sessions:
        for phone in user_sessions[user_id]:
            buttons.append([InlineKeyboardButton(
                text=f"❌ {phone}",
                callback_data=f"sess_del_{phone}"
            )])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="accounts")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_accounts_keyboard(user_id: int) -> InlineKeyboardMarkup:
    has_accounts = bool(user_sessions.get(user_id))
    buttons = [
        [InlineKeyboardButton(text="➕ Добавить аккаунт", callback_data="sess_add")],
    ]
    if has_accounts:
        buttons.append([InlineKeyboardButton(text="➖ Отвязать аккаунт", callback_data="acc_unlink_menu")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ============ СОСТОЯНИЯ ============

class SessionStates(StatesGroup):
    waiting_phone = State()
    waiting_code = State()


# ============ ОБРАБОТЧИКИ ============

@dp.message(Command("start"))
async def start_command(message: types.Message):
    user_id = message.from_user.id
    global user_sessions, user_bot_choice, user_session_config, known_users
    
    if not user_sessions:
        user_sessions.update(load_sessions())
    if not user_bot_choice:
        user_bot_choice.update(load_bot_choices())
    if not user_session_config:
        user_session_config.update(load_session_config())
    if not known_users:
        known_users.update(load_known_users())
    register_known_user(message.from_user)
    
    if user_id not in user_bot_choice:
        user_bot_choice[user_id] = "@gram_piarbot"
        save_bot_choices()
    if user_id not in user_sessions:
        user_sessions[user_id] = []
    if user_id not in user_session_config:
        user_session_config[user_id] = {}
    
    await message.answer(
        f"👋 Привет, {message.from_user.first_name or 'Пользователь'}!\n\n"
        f"🤖 <b>Ты попал в Telegram-Центр</b>,\n"
        f"Тут есть огромное количество различных функций.\n\n"
        f"Премиум:\n\n"
        f"Выберите нужный раздел:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard(message.from_user.id)
    )


@dp.callback_query(lambda c: c.data == "main")
async def main_menu(callback: types.CallbackQuery):
    await callback.answer()
    await safe_edit_message(
        callback.message,
        "🏠 <b>Главное меню</b>\n\nВыбери раздел:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard(callback.from_user.id)
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
        user_bot_choice[user_id] = "@gram_piarbot"
        save_bot_choices()
    
    text = "📢 <b>PR GRAMM</b>\n\n"
    text += f"🤖 Выбранный бот: <b>{user_bot_choice.get(user_id, '@gram_piarbot')}</b>\n\n"
    
    if user_id in user_sessions and user_sessions[user_id]:
        text += f"📱 <b>Сессии:</b>\n"
        for phone in user_sessions[user_id]:
            config = get_session_config(user_id, phone)
            status = "🟢" if config.get("enabled", False) else "🔴"
            task_type = config.get("task_type", "channels")
            task_names = {"channels": "📢 Подписка", "groups": "👥 Группы", "posts": "📱 Посты", "bots": "🤖 Боты"}
            text += f"  {status} {phone} — {task_names.get(task_type, task_type)}\n"
        text += f"\n📊 Сессий: {len(user_sessions[user_id])}/{get_max_sessions(user_id)}"
    else:
        text += "❌ Нет подключенных сессий\n\n"
        text += "Добавь аккаунт в разделе 'Аккаунты'"
    
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
        text += f"🤖 Бот: {user_bot_choice.get(user_id, '@gram_piarbot')}\n\n"
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
            bot_name = user_bot_choice.get(user_id, "@gram_piarbot")
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


@dp.callback_query(lambda c: c.data and c.data.startswith("sess_bot_") and not c.data.startswith("sess_bot_choice_"))
async def sess_bot_callback(callback: types.CallbackQuery):
    try:
        phone = callback.data.replace("sess_bot_", "")
        user_id = callback.from_user.id
        
        current_bot = user_bot_choice.get(user_id, "@gram_piarbot")
        bots = [("@gram_piarbot", "gpiar"), ("@gram_prbot", "gpr")]
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
        # code больше не содержит "_" (gpiar/gpr), поэтому remainder всегда
        # делится ровно на "код_телефон" одним split(maxsplit=1) — раньше
        # использовался callback.data.split("_") целиком, и старые коды
        # "g_piar"/"g_pr" (сами содержащие "_") ломали индексы, из-за чего
        # телефон и код бота съезжали и превращались в мусор.
        remainder = callback.data[len("sess_bot_choice_"):]
        bot_code, _, phone = remainder.partition("_")
        user_id = callback.from_user.id
        
        bot_name = "@gram_piarbot" if bot_code == "gpiar" else "@gram_prbot"
        user_bot_choice[user_id] = bot_name
        save_bot_choices()
        
        await callback.answer(f"✅ {bot_name}")
        callback.data = f"sess_item_{phone}"
        await sess_item_callback(callback)
    except Exception as e:
        logging.error(f"❌ sess_bot_choice_callback: {e}")
        await callback.answer("❌ Ошибка")


# ============ УПРАВЛЕНИЕ АККАУНТОМ: доп. функции ============

RUSSIAN_MONTHS = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


def format_ru_date(dt: datetime) -> str:
    return f"{dt.day} {RUSSIAN_MONTHS[dt.month]} {dt.year} года"


async def _get_connected_client(phone: str):
    """Возвращает подключённого и авторизованного Telethon-клиента для
    номера, либо None, если сессия недоступна."""
    if phone not in active_clients:
        return None
    client = active_clients[phone]
    if not client.is_connected():
        try:
            await client.connect()
        except Exception as e:
            logging.error(f"❌ _get_connected_client({phone}): {e}")
            return None
    if not await client.is_user_authorized():
        return None
    return client


@dp.callback_query(lambda c: c.data and c.data.startswith("sess_mute_groups_"))
async def sess_mute_groups_callback(callback: types.CallbackQuery):
    try:
        phone = callback.data.replace("sess_mute_groups_", "")
        user_id = callback.from_user.id
        config = get_session_config(user_id, phone)
        new_state = not config.get("groups_muted", False)
        
        client = await _get_connected_client(phone)
        if not client:
            await callback.answer("❌ Аккаунт не подключён", show_alert=True)
            return
        
        await client(functions.account.UpdateNotifySettingsRequest(
            peer=tl_types.InputNotifyChats(),
            settings=tl_types.InputPeerNotifySettings(
                mute_until=(2**31 - 1) if new_state else 0
            )
        ))
        config["groups_muted"] = new_state
        save_session_config()
        await callback.answer(f"{'🔇 Звук в группах выключен' if new_state else '🔊 Звук в группах включён'}")
        await sess_item_callback(callback)
    except Exception as e:
        logging.error(f"❌ sess_mute_groups_callback: {e}")
        await callback.answer("❌ Ошибка")


@dp.callback_query(lambda c: c.data and c.data.startswith("sess_mute_channels_"))
async def sess_mute_channels_callback(callback: types.CallbackQuery):
    try:
        phone = callback.data.replace("sess_mute_channels_", "")
        user_id = callback.from_user.id
        config = get_session_config(user_id, phone)
        new_state = not config.get("channels_muted", False)
        
        client = await _get_connected_client(phone)
        if not client:
            await callback.answer("❌ Аккаунт не подключён", show_alert=True)
            return
        
        await client(functions.account.UpdateNotifySettingsRequest(
            peer=tl_types.InputNotifyBroadcasts(),
            settings=tl_types.InputPeerNotifySettings(
                mute_until=(2**31 - 1) if new_state else 0
            )
        ))
        config["channels_muted"] = new_state
        save_session_config()
        await callback.answer(f"{'🔇 Звук в каналах выключен' if new_state else '🔊 Звук в каналах включён'}")
        await sess_item_callback(callback)
    except Exception as e:
        logging.error(f"❌ sess_mute_channels_callback: {e}")
        await callback.answer("❌ Ошибка")


@dp.callback_query(lambda c: c.data and c.data.startswith("sess_chatlist_") and not c.data.startswith("sess_chatlist_groups_") and not c.data.startswith("sess_chatlist_channels_"))
async def sess_chatlist_menu(callback: types.CallbackQuery):
    await callback.answer()
    phone = callback.data.replace("sess_chatlist_", "")
    await safe_edit_message(
        callback.message,
        f"📋 <b>Список каналов и групп</b>\n\n"
        f"📱 {phone}\n\n"
        f"Выбери, чтобы отметить: этот чат нельзя будет покидать.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👥 Группы", callback_data=f"sess_chatlist_groups_{phone}")],
            [InlineKeyboardButton(text="📢 Каналы", callback_data=f"sess_chatlist_channels_{phone}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"sess_item_{phone}")],
        ])
    )


async def _build_chat_list_keyboard(phone: str, user_id: int, kind: str) -> InlineKeyboardMarkup:
    config = get_session_config(user_id, phone)
    protected = set(config.get("protected_chats", []))
    client = await _get_connected_client(phone)
    buttons = []
    if client:
        async for dialog in client.iter_dialogs(limit=200):
            entity = dialog.entity
            if kind == "groups":
                is_match = (isinstance(entity, Chat)) or (isinstance(entity, Channel) and entity.megagroup)
            else:
                # Каналы: широковещательные (не супергруппы), исключаем
                # "личные" — каналы, где кроме самого аккаунта никого нет
                # (обычно используются как личный блокнот/архив).
                is_match = isinstance(entity, Channel) and not entity.megagroup
                if is_match:
                    participants = getattr(entity, 'participants_count', None)
                    if participants is not None and participants <= 1:
                        is_match = False
            if not is_match:
                continue
            chat_id = dialog.id
            mark = "🔒 " if chat_id in protected else ""
            title = (dialog.title or "Без названия")[:40]
            buttons.append([InlineKeyboardButton(
                text=f"{mark}{title}",
                callback_data=f"sess_protect_{phone}_{chat_id}"
            )])
    if not buttons:
        buttons.append([InlineKeyboardButton(text="❌ Пусто", callback_data="no_action")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"sess_chatlist_{phone}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.callback_query(lambda c: c.data and c.data.startswith("sess_chatlist_groups_"))
async def sess_chatlist_groups(callback: types.CallbackQuery):
    await callback.answer()
    phone = callback.data.replace("sess_chatlist_groups_", "")
    user_id = callback.from_user.id
    kb = await _build_chat_list_keyboard(phone, user_id, "groups")
    await safe_edit_message(
        callback.message,
        "👥 <b>Группы</b>\n\n🔒 — уже защищена от выхода\n\nВыбери группу:",
        parse_mode=ParseMode.HTML,
        reply_markup=kb
    )


@dp.callback_query(lambda c: c.data and c.data.startswith("sess_chatlist_channels_"))
async def sess_chatlist_channels(callback: types.CallbackQuery):
    await callback.answer()
    phone = callback.data.replace("sess_chatlist_channels_", "")
    user_id = callback.from_user.id
    kb = await _build_chat_list_keyboard(phone, user_id, "channels")
    await safe_edit_message(
        callback.message,
        "📢 <b>Каналы</b>\n\n🔒 — уже защищён от выхода\n\nВыбери канал:",
        parse_mode=ParseMode.HTML,
        reply_markup=kb
    )


@dp.callback_query(lambda c: c.data and c.data.startswith("sess_protect_"))
async def sess_protect_callback(callback: types.CallbackQuery):
    try:
        rest = callback.data.replace("sess_protect_", "")
        phone, _, chat_id_str = rest.rpartition("_")
        chat_id = int(chat_id_str)
        user_id = callback.from_user.id
        config = get_session_config(user_id, phone)
        protected = set(config.get("protected_chats", []))
        if chat_id in protected:
            protected.discard(chat_id)
            await callback.answer("🔓 Снята защита от выхода")
        else:
            protected.add(chat_id)
            await callback.answer("🔒 Теперь бот никогда не выйдет из этого чата")
        config["protected_chats"] = list(protected)
        save_session_config()
        # Возвращаемся к тому же списку (группы/каналы определяем по callback заново невозможно,
        # поэтому просто открываем меню выбора Группы/Каналы).
        callback.data = f"sess_chatlist_{phone}"
        await sess_chatlist_menu(callback)
    except Exception as e:
        logging.error(f"❌ sess_protect_callback: {e}")
        await callback.answer("❌ Ошибка")


@dp.callback_query(lambda c: c.data and c.data.startswith("sess_getcode_"))
async def sess_getcode_callback(callback: types.CallbackQuery):
    try:
        phone = callback.data.replace("sess_getcode_", "")
        await callback.answer("🔎 Ищу коды...")
        
        client = await _get_connected_client(phone)
        if not client:
            await callback.message.answer("❌ Аккаунт не подключён")
            return
        
        codes = []
        # Официальный аккаунт Telegram (777000) присылает коды входа —
        # они стабильно состоят из 5 цифр во всех локалях. Более широкий
        # захват (4-7 цифр) ловил бы даты вида "24.07.2026" в сообщениях
        # о новых входах в аккаунт как ложные "коды".
        code_pattern = re.compile(r'(?<!\d)(\d{5})(?!\d)')
        async for msg in client.iter_messages(777000, limit=200):
            if not msg.text:
                continue
            m = code_pattern.search(msg.text)
            if m:
                codes.append((msg.date, m.group(1)))
        
        if not codes:
            await callback.message.answer(f"📱 {phone}\n\n❌ Коды не найдены")
            return
        
        codes.sort(key=lambda x: x[0], reverse=True)
        lines = [f"🔑 <b>Коды для {phone}</b>\n"]
        for i, (dt, code) in enumerate(codes[:50], 1):
            lines.append(f"{i}. <code>{code}</code> — {format_ru_date(dt)}")
        text = "\n".join(lines)
        
        if len(text) > 4096:
            text = text[:4090] + "\n..."
        
        await callback.message.answer(text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logging.error(f"❌ sess_getcode_callback: {e}")
        await callback.message.answer(f"❌ Ошибка получения кодов: {e}")


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
            bot_name = user_bot_choice.get(user_id, "@gram_piarbot")
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
    
    if user_id in user_sessions and len(user_sessions[user_id]) >= get_max_sessions(user_id):
        await callback.answer(f"❌ Достигнут лимит аккаунтов ({get_max_sessions(user_id)})", show_alert=True)
        return
    
    await state.set_state(SessionStates.waiting_phone)
    await state.update_data(user_id=user_id)
    
    await safe_edit_message(
        callback.message,
        f"📱 <b>Добавление сессии</b>\n\n"
        f"Сессий: {len(user_sessions.get(user_id, []))}/{get_max_sessions(user_id)}\n\n"
        "Введите номер телефона в международном формате (с +):\n\n"
        "или отправьте /cancel для отмены",
        parse_mode=ParseMode.HTML
    )


@dp.message(SessionStates.waiting_phone)
async def session_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    user_id = message.from_user.id
    if phone.lower() in ("/cancel", "отмена"):
        await state.clear()
        await message.answer("❌ Отменено.", reply_markup=get_main_keyboard(message.from_user.id))
        return
    if not re.match(r'^\+?\d{10,15}$', phone):
        await message.answer(
            "❌ Неверный формат номера.\n"
            "Введите номер в международном формате (с +).",
            parse_mode=ParseMode.HTML
        )
        return
    if not is_phone_allowed(user_id, phone):
        await message.answer(
            f"❌ Без {PREMIUM_ICON} премиум-подписки доступны только номера "
            f"РФ, Украины, Беларуси, Молдовы, Грузии, Армении, Азербайджана, "
            f"Кыргызстана, Туркменистана, Узбекистана и Таджикистана.\n\n"
            f"Оформи {PREMIUM_ICON} премиум, чтобы подключать номера любых стран.",
            parse_mode=ParseMode.HTML
        )
        return
    
    await state.update_data(phone=phone)
    await state.set_state(SessionStates.waiting_code)
    set_user_chat_id(message.chat.id)
    try:
        result = await send_code(phone, "gram_prbot")
    except Exception as e:
        logging.error(f"❌ send_code исключение для {phone}: {e}")
        result = False
    
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
    if code.lower() in ("/cancel", "отмена"):
        await state.clear()
        await message.answer("❌ Отменено.", reply_markup=get_main_keyboard(message.from_user.id))
        return
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
        bot_name = user_bot_choice.get(user_id, "@gram_piarbot")
        await message.answer(
            f"✅ <b>Сессия добавлена!</b>\n\n"
            f"📱 {phone}\n"
            f"🤖 Выбранный бот: {bot_name}\n"
            f"📊 Всего сессий: {len(user_sessions[user_id])}/{get_max_sessions(user_id)}\n\n"
            f"Теперь перейди в раздел 'Боты' → 'PR GRAMM' для настройки",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🤖 Перейти в Боты", callback_data="bot_prgramm")],
                [InlineKeyboardButton(text="📱 Аккаунты", callback_data="accounts")],
            ])
        )
    else:
        await message.answer(
            "❌ Ошибка авторизации.\n"
            "Проверьте код и попробуйте снова.\n\n"
            "Отправьте /start для возврата в меню"
        )


@dp.callback_query(lambda c: c.data == "accounts")
async def accounts_menu(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    
    text = "📱 <b>Аккаунты</b>\n\n"
    if user_id in user_sessions and user_sessions[user_id]:
        text += f"📊 Аккаунтов: {len(user_sessions[user_id])}/{get_max_sessions(user_id)}\n\n"
        for phone in user_sessions[user_id]:
            config = get_session_config(user_id, phone)
            status = "🟢 Вкл" if config.get("enabled", False) else "🔴 Выкл"
            text += f"  {status} 📱 {phone}\n"
    else:
        text += "❌ Нет привязанных аккаунтов\n\n"
        text += f"Максимум: {get_max_sessions(user_id)} аккаунтов"
    
    await safe_edit_message(
        callback.message,
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_accounts_keyboard(user_id)
    )


@dp.callback_query(lambda c: c.data == "acc_unlink_menu")
async def acc_unlink_menu(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    await safe_edit_message(
        callback.message,
        "➖ <b>Отвязать аккаунт</b>\n\nВыбери номер для отвязки:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_account_unlink_keyboard(user_id)
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
        
        await callback.answer(f"✅ Аккаунт {phone} отвязан")
        await accounts_menu(callback)
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
    await message.answer("❌ Отменено.", reply_markup=get_main_keyboard(message.from_user.id))


@dp.message(Command("continue_gram"))
async def continue_gram_command(message: types.Message):
    user_id = message.from_user.id
    pending = webapp_captcha_pending.pop(user_id, [])
    if not pending:
        await message.answer("ℹ️ Нет заданий, ожидающих прохождения капчи.")
        return
    resumed = 0
    for phone in pending:
        try:
            if await continue_gram_bot(phone):
                resumed += 1
        except Exception as e:
            logging.error(f"❌ continue_gram_command({phone}): {e}")
    await message.answer(f"✅ Продолжаю выполнение заданий ({resumed}/{len(pending)} сессий).")


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
    current_bot = user_bot_choice.get(user_id, "@gram_piarbot")
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


# ============ АДМИН-КОМАНДЫ (Этап 0: премиум/админ-система) ============

ADMIN_COMMANDS_HELP = (
    "🛠 <b>Команды администратора</b>\n\n"
    "✅ /addadmin (юз/айди) — выдать права админа\n"
    "✅ /deladmin (юз/айди) — снять права админа\n"
    "✅ /givepremium (юз/айди) — выдать 💎 премиум\n"
    "✅ /delpremium (юз/айди) — забрать 💎 премиум\n"
    "✅ /ban (юз/айди) (причина)\n"
    "✅ /unban (юз/айди) (причина)\n"
    "✅ /mail (текст) — рассылка всем\n"
    "✅ /mailuser (юз/айди) (текст)\n"
    "✅ /addchannel (айди) (часы) — обязательная подписка\n"
    "✅ /delchannel (айди)\n"
    "✅ /technical (on/off)\n"
    "✅ /blockTechPod (юз/айди)\n"
    "✅ /unblockTechPod (юз/айди)\n"
    "✅ /sessions — управление всеми сессиями\n"
    "✅ /offallsession\n"
    "✅ /stopalltasks"
)


@dp.callback_query(lambda c: c.data == "adm_help")
async def adm_help_callback(callback: types.CallbackQuery):
    await callback.answer()
    if not is_admin(callback.from_user.id):
        return
    await safe_edit_message(
        callback.message,
        ADMIN_COMMANDS_HELP,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main")]
        ])
    )


@dp.message(Command("addadmin"))
async def addadmin_command(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /addadmin (юз/айди)")
        return
    target_id = await resolve_user_id(bot, args[1])
    if not target_id:
        await message.answer("❌ Не удалось найти пользователя.")
        return
    if add_admin(target_id):
        await message.answer(f"✅ Пользователь <code>{target_id}</code> назначен админом.", parse_mode=ParseMode.HTML)
    else:
        await message.answer(f"ℹ️ Пользователь <code>{target_id}</code> уже админ.", parse_mode=ParseMode.HTML)


@dp.message(Command("deladmin"))
async def deladmin_command(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /deladmin (юз/айди)")
        return
    target_id = await resolve_user_id(bot, args[1])
    if not target_id:
        await message.answer("❌ Не удалось найти пользователя.")
        return
    if remove_admin(target_id):
        await message.answer(f"✅ Права админа сняты с <code>{target_id}</code>.", parse_mode=ParseMode.HTML)
    else:
        await message.answer("❌ Не удалось снять права (не админ или это суперадмин).")


@dp.message(Command("givepremium"))
async def givepremium_command(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /givepremium (юз/айди)")
        return
    target_id = await resolve_user_id(bot, args[1])
    if not target_id:
        await message.answer("❌ Не удалось найти пользователя.")
        return
    grant_premium(target_id, granted_by=message.from_user.id)
    await message.answer(f"✅ {PREMIUM_ICON} Премиум выдан пользователю <code>{target_id}</code>.", parse_mode=ParseMode.HTML)


@dp.message(Command("delpremium"))
async def delpremium_command(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /delpremium (юз/айди)")
        return
    target_id = await resolve_user_id(bot, args[1])
    if not target_id:
        await message.answer("❌ Не удалось найти пользователя.")
        return
    if revoke_premium(target_id):
        await message.answer(f"✅ {PREMIUM_ICON} Премиум снят с пользователя <code>{target_id}</code>.", parse_mode=ParseMode.HTML)
    else:
        await message.answer(f"ℹ️ У пользователя <code>{target_id}</code> не было премиума.", parse_mode=ParseMode.HTML)


# ============ MIDDLEWARE: БАН / ТЕХРАБОТЫ / ОБЯЗАТЕЛЬНАЯ ПОДПИСКА ============

class AccessMiddleware(BaseMiddleware):
    def __init__(self, bot_ref):
        self.bot_ref = bot_ref
        super().__init__()

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        user_id = user.id

        if is_banned(user_id) and user_id != SUPER_ADMIN_ID:
            reason = get_ban_reason(user_id)
            text = f"⛔ Вы заблокированы в этом боте.\nПричина: {reason}"
            try:
                if isinstance(event, types.CallbackQuery):
                    await event.answer(text, show_alert=True)
                else:
                    await event.answer(text)
            except Exception:
                pass
            return

        if is_admin(user_id):
            return await handler(event, data)

        if is_technical_mode():
            text = "🔧 Ведутся технические работы. Попробуйте позже."
            try:
                if isinstance(event, types.CallbackQuery):
                    await event.answer(text, show_alert=True)
                else:
                    await event.answer(text)
            except Exception:
                pass
            return

        cb_data = getattr(event, "data", None)
        if cb_data == "check_subscription":
            return await handler(event, data)

        mandatory = get_mandatory_channels()
        if mandatory:
            not_subscribed = []
            for chat_id in mandatory:
                try:
                    member = await self.bot_ref.get_chat_member(chat_id, user_id)
                    if member.status in ("left", "kicked"):
                        not_subscribed.append(chat_id)
                except Exception as e:
                    logging.error(f"❌ AccessMiddleware подписка {chat_id}: {e}")

            if not_subscribed:
                buttons = []
                for chat_id in not_subscribed:
                    try:
                        chat = await self.bot_ref.get_chat(chat_id)
                        title = chat.title or "Канал"
                        url = f"https://t.me/{chat.username}" if chat.username else None
                    except Exception:
                        title, url = "Канал", None
                    if url:
                        buttons.append([InlineKeyboardButton(text=f"📢 {title}", url=url)])
                buttons.append([InlineKeyboardButton(text="✅ Я подписался", callback_data="check_subscription")])
                text = "🔒 Для использования бота подпишись на канал(ы):"
                markup = InlineKeyboardMarkup(inline_keyboard=buttons)
                try:
                    if isinstance(event, types.CallbackQuery):
                        await event.answer()
                        await event.message.answer(text, reply_markup=markup)
                    else:
                        await event.answer(text, reply_markup=markup)
                except Exception as e:
                    logging.error(f"❌ AccessMiddleware notify: {e}")
                return

        return await handler(event, data)


@dp.callback_query(lambda c: c.data == "check_subscription")
async def check_subscription_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    mandatory = get_mandatory_channels()
    not_subscribed = []
    for chat_id in mandatory:
        try:
            member = await bot.get_chat_member(chat_id, user_id)
            if member.status in ("left", "kicked"):
                not_subscribed.append(chat_id)
        except Exception:
            not_subscribed.append(chat_id)

    if not_subscribed:
        await callback.answer("❌ Ты всё ещё не подписан на все каналы", show_alert=True)
        return

    await callback.answer("✅ Подписка подтверждена!")
    await callback.message.edit_text(
        f"👋 Привет, {callback.from_user.first_name or 'Пользователь'}!\n\n"
        f"🤖 <b>Ты попал в Telegram-Центр</b>,\n"
        f"Тут есть огромное количество различных функций.\n\n"
        f"Премиум:\n\n"
        f"Выберите нужный раздел:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard(user_id)
    )


# ============ АДМИН-КОМАНДЫ: продолжение (Этап 6) ============

@dp.message(Command("ban"))
async def ban_command(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split(maxsplit=2)
    if len(args) < 2:
        await message.answer("Использование: /ban (юз/айди) (причина)")
        return
    target_id = await resolve_user_id(bot, args[1])
    if not target_id:
        await message.answer("❌ Не удалось найти пользователя.")
        return
    reason = args[2] if len(args) > 2 else ""
    if ban_user(target_id, reason):
        await message.answer(f"✅ Пользователь <code>{target_id}</code> забанен. Причина: {reason or '—'}", parse_mode=ParseMode.HTML)
    else:
        await message.answer("❌ Нельзя забанить суперадмина.")


@dp.message(Command("unban"))
async def unban_command(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split(maxsplit=2)
    if len(args) < 2:
        await message.answer("Использование: /unban (юз/айди) (причина)")
        return
    target_id = await resolve_user_id(bot, args[1])
    if not target_id:
        await message.answer("❌ Не удалось найти пользователя.")
        return
    if unban_user(target_id):
        await message.answer(f"✅ Пользователь <code>{target_id}</code> разбанен.", parse_mode=ParseMode.HTML)
    else:
        await message.answer(f"ℹ️ Пользователь <code>{target_id}</code> не был забанен.", parse_mode=ParseMode.HTML)


@dp.message(Command("mail"))
async def mail_command(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /mail (текст)")
        return
    text = args[1]
    sent, failed = 0, 0
    for uid in list(known_users.keys()):
        try:
            await bot.send_message(uid, text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    await message.answer(f"✅ Рассылка завершена: {sent} успешно, {failed} не доставлено.")


@dp.message(Command("mailuser"))
async def mailuser_command(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer("Использование: /mailuser (юз/айди) (текст)")
        return
    target_id = await resolve_user_id(bot, args[1])
    if not target_id:
        await message.answer("❌ Не удалось найти пользователя.")
        return
    try:
        await bot.send_message(target_id, args[2])
        await message.answer("✅ Отправлено.")
    except Exception as e:
        await message.answer(f"❌ Не удалось отправить: {e}")


@dp.message(Command("addchannel"))
async def addchannel_command(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /addchannel (айди канала/группы) (время в часах, необязательно)")
        return
    try:
        chat_id = int(args[1])
    except ValueError:
        await message.answer("❌ ID должен быть числом (например, -1001234567890).")
        return
    hours = None
    if len(args) >= 3:
        try:
            hours = float(args[2])
        except ValueError:
            await message.answer("❌ Часы должны быть числом.")
            return
    add_mandatory_channel(chat_id, hours)
    period = f"{hours} ч." if hours else "бессрочно"
    await message.answer(
        f"✅ Канал/группа <code>{chat_id}</code> добавлен(а) в обязательную подписку ({period}).",
        parse_mode=ParseMode.HTML
    )


@dp.message(Command("delchannel"))
async def delchannel_command(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /delchannel (айди канала/группы)")
        return
    try:
        chat_id = int(args[1])
    except ValueError:
        await message.answer("❌ ID должен быть числом.")
        return
    if remove_mandatory_channel(chat_id):
        await message.answer(f"✅ Канал/группа <code>{chat_id}</code> убран(а) из обязательной подписки.", parse_mode=ParseMode.HTML)
    else:
        await message.answer("ℹ️ Такого канала/группы не было в списке.")


@dp.message(Command("technical"))
async def technical_command(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        await message.answer("Использование: /technical (on/off)")
        return
    enabled = args[1].lower() == "on"
    set_technical_mode(enabled)
    await message.answer(f"✅ Технические работы: {'включены' if enabled else 'выключены'}")


@dp.message(Command("blockTechPod"))
async def blocktechpod_command(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /blockTechPod (юз/айди)")
        return
    target_id = await resolve_user_id(bot, args[1])
    if not target_id:
        await message.answer("❌ Не удалось найти пользователя.")
        return
    if block_tech_support(target_id):
        await message.answer(
            f"✅ Пользователю <code>{target_id}</code> закрыт доступ к разделу 'Сообщить о баге'.",
            parse_mode=ParseMode.HTML
        )
    else:
        await message.answer("ℹ️ Уже заблокирован.")


@dp.message(Command("unblockTechPod"))
async def unblocktechpod_command(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /unblockTechPod (юз/айди)")
        return
    target_id = await resolve_user_id(bot, args[1])
    if not target_id:
        await message.answer("❌ Не удалось найти пользователя.")
        return
    if unblock_tech_support(target_id):
        await message.answer(
            f"✅ Доступ к разделу 'Сообщить о баге' возвращён пользователю <code>{target_id}</code>.",
            parse_mode=ParseMode.HTML
        )
    else:
        await message.answer("ℹ️ Не был заблокирован.")


# ============ /sessions — АДМИНСКАЯ ПАНЕЛЬ ВСЕХ СЕССИЙ ============

def get_admin_sessions_keyboard() -> InlineKeyboardMarkup:
    buttons = []
    for owner_id, phones in user_sessions.items():
        for phone in phones:
            buttons.append([InlineKeyboardButton(
                text=f"📱 {phone}", callback_data=f"adm_sess_{owner_id}_{phone}"
            )])
    if not buttons:
        buttons.append([InlineKeyboardButton(text="❌ Нет сессий", callback_data="no_action")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_sessions_list")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.message(Command("sessions"))
async def admin_sessions_command(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "📋 <b>Все активные сессии</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=get_admin_sessions_keyboard()
    )


@dp.callback_query(lambda c: c.data == "adm_sessions_list")
async def adm_sessions_list_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    await callback.message.edit_text(
        "📋 <b>Все активные сессии</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=get_admin_sessions_keyboard()
    )


@dp.callback_query(lambda c: c.data.startswith("adm_sess_") and not c.data.startswith("adm_sess_action_"))
async def adm_sess_item_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    rest = callback.data[len("adm_sess_"):]
    owner_id_str, _, phone = rest.partition("_")
    owner_id = int(owner_id_str)

    info = known_users.get(owner_id, {})
    username = info.get("username")
    reg_date = info.get("registered_at")
    reg_date_str = reg_date[:10] if reg_date else "неизвестно"
    sessions_count = len(user_sessions.get(owner_id, []))
    premium_str = f"{PREMIUM_ICON} да" if is_premium(owner_id) else "нет"

    text = (
        f"📱 <b>{phone}</b>\n\n"
        f"Юз: @{username if username else '—'}\n"
        f"Айди: <code>{owner_id}</code>\n"
        f"Кол-во подключенных сессий: {sessions_count}\n"
        f"Премиум: {premium_str}\n"
        f"Дата регистрации в боте: {reg_date_str}"
    )
    await callback.message.edit_text(
        text, parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Удалить сессию", callback_data=f"adm_sess_action_del_{owner_id}_{phone}")],
            [InlineKeyboardButton(text="⏹ Остановить задание", callback_data=f"adm_sess_action_stop_{owner_id}_{phone}")],
            [InlineKeyboardButton(text="🔑 Получить код", callback_data=f"adm_sess_action_code_{owner_id}_{phone}")],
            [InlineKeyboardButton(text="📄 Получить session файл", callback_data=f"adm_sess_action_file_{owner_id}_{phone}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_sessions_list")],
        ])
    )


@dp.callback_query(lambda c: c.data.startswith("adm_sess_action_"))
async def adm_sess_action_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    rest = callback.data[len("adm_sess_action_"):]
    action, _, remainder = rest.partition("_")
    owner_id_str, _, phone = remainder.partition("_")
    owner_id = int(owner_id_str)

    if action == "del":
        await callback.answer("🗑 Удаляю...")
        if phone in active_tasks:
            await stop_gram_bot(phone)
        if phone in active_clients:
            try:
                await active_clients[phone].disconnect()
            except Exception:
                pass
            del active_clients[phone]
        if owner_id in user_sessions and phone in user_sessions[owner_id]:
            user_sessions[owner_id].remove(phone)
            save_sessions()
        if owner_id in user_session_config and phone in user_session_config[owner_id]:
            del user_session_config[owner_id][phone]
            save_session_config()
        await callback.message.edit_text(f"✅ Сессия {phone} удалена.", reply_markup=get_admin_sessions_keyboard())

    elif action == "stop":
        await callback.answer("⏹ Останавливаю...")
        if phone in active_tasks:
            await stop_gram_bot(phone)
            await callback.message.edit_text(f"⏹ Задание для {phone} остановлено (сессия не тронута).")
        else:
            await callback.message.edit_text(f"ℹ️ У {phone} нет активного задания.")

    elif action == "code":
        await callback.answer("🔎 Ищу коды...")
        client = await _get_connected_client(phone)
        if not client:
            await callback.message.answer("❌ Аккаунт не подключён")
            return
        codes = []
        code_pattern = re.compile(r'(?<!\d)(\d{5})(?!\d)')
        async for msg in client.iter_messages(777000, limit=200):
            if not msg.text:
                continue
            m = code_pattern.search(msg.text)
            if m:
                codes.append((msg.date, m.group(1)))
        if not codes:
            await callback.message.answer(f"📱 {phone}\n\n❌ Коды не найдены")
            return
        codes.sort(key=lambda x: x[0], reverse=True)
        lines = [f"🔑 <b>Коды для {phone}</b>\n"]
        for i, (dt, code) in enumerate(codes[:50], 1):
            lines.append(f"{i}. <code>{code}</code> — {format_ru_date(dt)}")
        text = "\n".join(lines)
        if len(text) > 4096:
            text = text[:4090] + "\n..."
        await callback.message.answer(text, parse_mode=ParseMode.HTML)

    elif action == "file":
        await callback.answer("📄 Отправляю файл...")
        session_path = f"sessions/{phone.replace('+', '')}.session"
        if not os.path.exists(session_path):
            await callback.message.answer("❌ Файл сессии не найден.")
            return
        try:
            await callback.message.answer_document(FSInputFile(session_path), caption=f"📄 {phone}")
        except Exception as e:
            await callback.message.answer(f"❌ Ошибка отправки файла: {e}")


@dp.message(Command("offallsession"))
async def offallsession_command(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    count = 0
    for phone in list(active_clients.keys()):
        try:
            if phone in active_tasks:
                await stop_gram_bot(phone)
            await active_clients[phone].disconnect()
            del active_clients[phone]
            count += 1
        except Exception as e:
            logging.error(f"❌ offallsession({phone}): {e}")
    await message.answer(f"✅ Отключено сессий: {count}")


@dp.message(Command("stopalltasks"))
async def stopalltasks_command(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    count = 0
    for phone in list(active_tasks.keys()):
        try:
            await stop_gram_bot(phone)
            count += 1
        except Exception as e:
            logging.error(f"❌ stopalltasks({phone}): {e}")
    await message.answer(f"✅ Остановлено заданий: {count} (сессии остались подключены)")


# ============ ИНИЦИАЛИЗАЦИЯ ============

async def main():
    global user_sessions, user_bot_choice, user_session_config, known_users
    
    os.makedirs("sessions", exist_ok=True)
    os.makedirs("fonts", exist_ok=True)
    
    user_sessions.update(load_sessions())
    user_bot_choice.update(load_bot_choices())
    user_session_config.update(load_session_config())
    known_users.update(load_known_users())
    
    access_middleware = AccessMiddleware(bot)
    dp.message.middleware(access_middleware)
    dp.callback_query.middleware(access_middleware)
    
    set_gram_bot_instance(bot)
    set_username_bot(bot)
    set_extra_bot(bot)
    set_captcha_bot(bot)
    set_captcha_clients(active_clients)
    set_captcha_continue_callback(continue_gram_bot)
    set_auto_click_timeout(30)
    set_ai_solver(True)
    
    logging.info("✅ Экземпляр бота передан в gram_bot и captcha_solver")
    logging.info("📱 Лимиты сессий: 3 (обычные), 10 (💎 премиум)")
    
    init_username_bot(dp)
    init_gram_bot(dp)
    init_extra_features(dp)
    init_video_download(dp)
    init_channels_feature(dp)
    start_username_watcher()
    
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("⛔ Бот остановлен")
