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
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from dotenv import load_dotenv

from telethon import TelegramClient
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
    API_ID,
    API_HASH,
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
    set_custom_session_limit,
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
from shakalizer import init_shakalizer
from gifts_feature import init_gifts_feature, setup as setup_gifts_feature

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)


@dp.errors()
async def global_error_handler(event: types.ErrorEvent):
    """Ловит любые необработанные исключения из ЛЮБОГО хендлера (сетевые
    таймауты Bot API, временные сбои и т.п.) — без этого такие ошибки
    вылетали сырым трейсбеком в логи и (в худших случаях) могли уронить
    обработку конкретного апдейта без единого понятного сообщения."""
    logging.error(
        f"❌ Необработанная ошибка в хендлере: {type(event.exception).__name__}: {event.exception}"
    )
    return True

# Храним сессии пользователей
user_sessions: Dict[int, List[str]] = {}
user_bot_choice: Dict[int, str] = {}
user_session_config: Dict[int, Dict[str, Dict[str, Any]]] = {}
user_lang: Dict[int, str] = {}  # user_id -> "ru" | "en"

LANG_FILE = "user_lang.json"

LANG_STRINGS = {
    "ru": {
        "settings_title": "⚙️ <b>Настройки</b>\n\nВыберите язык / Choose language:",
        "lang_set": "✅ Язык установлен: Русский 🇷🇺",
        "back": "⬅️ Назад",
        "lang_ru": "🇷🇺 Русский",
        "lang_en": "🇬🇧 English",
    },
    "en": {
        "settings_title": "⚙️ <b>Settings</b>\n\nSelect language / Выберите язык:",
        "lang_set": "✅ Language set: English 🇬🇧",
        "back": "⬅️ Back",
        "lang_ru": "🇷🇺 Russian",
        "lang_en": "🇬🇧 English",
    },
}


def get_user_lang(user_id: int) -> str:
    return user_lang.get(user_id, "ru")


def load_lang() -> Dict[int, str]:
    if os.path.exists(LANG_FILE):
        try:
            with open(LANG_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return {int(k): v for k, v in data.items()}
        except Exception as e:
            logging.error(f"Ошибка загрузки user_lang.json: {e}")
    return {}


def save_lang():
    try:
        with open(LANG_FILE, 'w', encoding='utf-8') as f:
            json.dump(user_lang, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Ошибка сохранения user_lang.json: {e}")

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


async def send_with_retry(message: types.Message, text: str, attempts: int = 3, **kwargs):
    """Отправляет сообщение с повторными попытками при сетевом таймауте
    Bot API. Нужно для критичных шагов флоу (например, запрос пароля 2FA) —
    раньше при единичном TelegramNetworkError сообщение просто терялось,
    и пользователь не видел, что от него что-то ждут."""
    last_error = None
    for attempt in range(attempts):
        try:
            return await message.answer(text, **kwargs)
        except TelegramNetworkError as e:
            last_error = e
            logging.warning(f"⚠️ send_with_retry: попытка {attempt + 1}/{attempts} не удалась: {e}")
            await asyncio.sleep(2)
    logging.error(f"❌ send_with_retry: не удалось отправить сообщение после {attempts} попыток: {last_error}")
    return None


# ============ КЛАВИАТУРЫ ============

def get_sessions_keyboard(user_id: int) -> InlineKeyboardMarkup:
    buttons = []
    if user_id in user_sessions and user_sessions[user_id]:
        for phone in user_sessions[user_id]:
            config = get_session_config(user_id, phone)
            status = "🟢" if config.get("enabled", False) else "🔴"
            buttons.append([InlineKeyboardButton(
                text=f"{status} {phone}",
                callback_data=f"bots_sess_{phone}"
            )])
        buttons.append([InlineKeyboardButton(text="🚀 Запустить все сессии", callback_data="sess_start_all")])
        buttons.append([InlineKeyboardButton(text="⏹ Остановить все", callback_data="sess_stop_all")])
    else:
        buttons.append([InlineKeyboardButton(text="❌ Нет аккаунтов", callback_data="no_action")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="bots")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


class SessionStates(StatesGroup):
    waiting_phone = State()
    waiting_code = State()
    waiting_2fa_password = State()
    waiting_interval = State()
    waiting_session_file = State()


def get_bot_session_item_keyboard(user_id: int, phone: str) -> InlineKeyboardMarkup:
    """Экран сессии из раздела 'Боты' — только управление заданием, без
    mute/списка чатов/получения кода (это теперь только в 'Аккаунты')."""
    config = get_session_config(user_id, phone)
    is_enabled = config.get("enabled", False)
    toggle_text = "⏹ Выключить" if is_enabled else "▶️ Включить"
    interval = config.get("custom_interval_seconds")
    interval_label = f"⏱ Настроить время ({interval} сек)" if interval else "⏱ Настроить время"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 " + phone, callback_data="no_action")],
        [InlineKeyboardButton(text=toggle_text, callback_data=f"sess_toggle_{phone}")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data=f"bots_settings_{phone}")],
        [InlineKeyboardButton(text=interval_label, callback_data=f"sess_interval_{phone}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="bot_prgramm")],
    ])


@dp.callback_query(lambda c: c.data and c.data.startswith("bots_sess_"))
async def bots_sess_item_callback(callback: types.CallbackQuery):
    await callback.answer()
    phone = callback.data.replace("bots_sess_", "")
    user_id = callback.from_user.id
    config = get_session_config(user_id, phone)
    task_names = {"channels": "📢 Подписка на каналы", "groups": "👥 Вступление в группы", "posts": "📱 Просмотр постов", "bots": "🤖 Задания с ботами"}
    # Дефолты при первом показе новой сессии: выключена, задание — выбрать в настройках
    status = "🟢 Включена" if config.get("enabled", False) else "🔴 Выключена"
    task_type = config.get("task_type", "channels")
    bot_label = user_bot_choice.get(user_id, "@gram_piarbot")
    # Если сессия только добавлена и ни разу не настраивалась, показываем подсказки
    task_display = task_names.get(task_type, "—")
    text = (
        f"📱 <b>{phone}</b>\n\n"
        f"🤖 Бот: {bot_label}\n"
        f"📋 Задание: {task_display}\n"
        f"📊 Статус: {status}\n\n"
        f"Выбери действие:"
    )
    await safe_edit_message(callback.message, text, parse_mode=ParseMode.HTML, reply_markup=get_bot_session_item_keyboard(user_id, phone))


@dp.callback_query(lambda c: c.data and c.data.startswith("sess_interval_"))
async def sess_interval_prompt(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    phone = callback.data.replace("sess_interval_", "")
    await state.update_data(interval_phone=phone)
    await state.set_state(SessionStates.waiting_interval)
    await callback.message.edit_text(
        "⏱ <b>Настройка времени</b>\n\n"
        "Введи интервал между заданиями в секундах (например, 300 — это 5 минут).\n\n"
        "Отправь <code>0</code>, чтобы вернуть автоматический интервал по умолчанию.\n\n"
        "Нажми <b>Назад</b> для отмены.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main")]
        ])
    )


@dp.message(SessionStates.waiting_interval)
async def sess_interval_input(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()
    if text.lower() in ("/cancel", "отмена"):
        await state.clear()
        await message.answer("❌ Отменено", reply_markup=get_main_keyboard(message.from_user.id))
        return
    if not text.isdigit():
        await message.answer("❌ Введи число секунд (например, 300).")
        return
    
    data = await state.get_data()
    phone = data.get("interval_phone")
    user_id = message.from_user.id
    await state.clear()
    
    config = get_session_config(user_id, phone)
    value = int(text)
    if value <= 0:
        config.pop("custom_interval_seconds", None)
        await message.answer(
            "✅ Возвращён автоматический интервал по умолчанию.",
            reply_markup=get_bot_session_item_keyboard(user_id, phone)
        )
    else:
        config["custom_interval_seconds"] = value
        await message.answer(
            f"✅ Интервал установлен: {value} сек.",
            reply_markup=get_bot_session_item_keyboard(user_id, phone)
        )
    save_session_config()


def get_session_item_keyboard(user_id: int, phone: str) -> InlineKeyboardMarkup:
    config = get_session_config(user_id, phone)
    groups_muted = config.get("groups_muted", False)
    channels_muted = config.get("channels_muted", False)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 " + phone, callback_data="no_action")],
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
        [InlineKeyboardButton(text="🚪 Выйти со всех каналов", callback_data=f"sess_leaveall_channels_{phone}")],
        [InlineKeyboardButton(text="🚪 Выйти со всех групп", callback_data=f"sess_leaveall_groups_{phone}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="accounts")],
    ])


def get_session_settings_keyboard(user_id: int, phone: str, back_to: str = None) -> InlineKeyboardMarkup:
    config = get_session_config(user_id, phone)
    task_type = config.get("task_type", "channels")
    task_names = {"channels": "📢 Подписка", "groups": "👥 Группы", "posts": "📱 Посты", "bots": "🤖 Боты"}
    # back_to позволяет вернуться в нужный контекст:
    # из раздела "Боты" → bots_sess_{phone}, из "Аккаунтов" → sess_item_{phone}
    back_cb = back_to if back_to else f"sess_item_{phone}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📋 Тип: {task_names.get(task_type, task_type)}", callback_data=f"sess_task_{phone}")],
        [InlineKeyboardButton(text="🔄 Сменить бота", callback_data=f"sess_bot_{phone}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb)],
    ])


def get_main_keyboard(user_id: Optional[int] = None) -> InlineKeyboardMarkup:
    flat_buttons = [
        InlineKeyboardButton(text="🤖 Боты", callback_data="bots"),
        InlineKeyboardButton(text="👤 Юзернеймы", callback_data="users"),
        InlineKeyboardButton(text="📱 Аккаунты", callback_data="accounts"),
        InlineKeyboardButton(text="📢 Каналы", callback_data="channels_menu"),
    ]
    flat_buttons.extend(get_extra_main_buttons())
    flat_buttons.append(InlineKeyboardButton(text="📉 Шакализатор", callback_data="shakalizer_menu"))
    flat_buttons.append(InlineKeyboardButton(text="🎁 Подарки", callback_data="gifts_menu"))
    flat_buttons.append(InlineKeyboardButton(text="⚙️ Настройки", callback_data="bot_settings_menu"))
    
    # Раскладываем плоский список кнопок сеткой по 2 в ряд — компактнее и
    # приятнее одной длинной колонки. Красим только основные разделы
    # (синий) и премиум (красный, как акцент-CTA) — остальное оставляем
    # стандартным цветом, чтобы не пестрило.
    rows = [flat_buttons[i:i + 2] for i in range(0, len(flat_buttons), 2)]
    if user_id is not None and is_admin(user_id):
        rows.append([InlineKeyboardButton(text="🛠 Адм хелп", callback_data="adm_help")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_bots_list_keyboard(user_id: int = None) -> InlineKeyboardMarkup:
    premium = is_premium(user_id) if user_id else False
    if premium:
        prgramm_btn = InlineKeyboardButton(
            text=f"📢 PR GRAMM {PREMIUM_ICON}",
            callback_data="bot_prgramm",
        )
    else:
        prgramm_btn = InlineKeyboardButton(
            text=f"📢 PR GRAMM 🔒 {PREMIUM_ICON}",
            callback_data="bot_prgramm_locked",
        )
    dodeeper_btn = InlineKeyboardButton(
        text="💼 Додепер",
        callback_data="bot_dodeeper",
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        [prgramm_btn],
        [dodeeper_btn],
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
    accounts = user_sessions.get(user_id, [])
    buttons = []
    for phone in accounts:
        config = get_session_config(user_id, phone)
        status = "🟢" if config.get("enabled", False) else "🔴"
        buttons.append([InlineKeyboardButton(
            text=f"{status} {phone}", callback_data=f"sess_item_{phone}"
        )])
    buttons.append([InlineKeyboardButton(text="➕ Добавить аккаунт", callback_data="sess_add")])
    if accounts:
        buttons.append([InlineKeyboardButton(text="➖ Отвязать аккаунт", callback_data="acc_unlink_menu")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ============ СОСТОЯНИЯ ============



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
    if not user_lang:
        user_lang.update(load_lang())
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


@dp.callback_query(lambda c: c.data == "bot_settings_menu")
async def bot_settings_menu(callback: types.CallbackQuery):
    """Раздел настроек бота: смена языка."""
    await callback.answer()
    user_id = callback.from_user.id
    lang = get_user_lang(user_id)
    ls = LANG_STRINGS[lang]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=("✅ " if lang == "ru" else "") + ls["lang_ru"],
                callback_data="set_lang_ru"
            ),
            InlineKeyboardButton(
                text=("✅ " if lang == "en" else "") + ls["lang_en"],
                callback_data="set_lang_en"
            ),
        ],
        [InlineKeyboardButton(text=ls["back"], callback_data="main")],
    ])
    await safe_edit_message(
        callback.message,
        ls["settings_title"],
        parse_mode=ParseMode.HTML,
        reply_markup=kb
    )


@dp.callback_query(lambda c: c.data in ("set_lang_ru", "set_lang_en"))
async def set_lang_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    new_lang = "ru" if callback.data == "set_lang_ru" else "en"
    user_lang[user_id] = new_lang
    save_lang()
    ls = LANG_STRINGS[new_lang]
    await callback.answer(ls["lang_set"])
    # Возвращаем обновлённое меню настроек
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=("✅ " if new_lang == "ru" else "") + ls["lang_ru"],
                callback_data="set_lang_ru"
            ),
            InlineKeyboardButton(
                text=("✅ " if new_lang == "en" else "") + ls["lang_en"],
                callback_data="set_lang_en"
            ),
        ],
        [InlineKeyboardButton(text=ls["back"], callback_data="main")],
    ])
    await safe_edit_message(
        callback.message,
        ls["settings_title"],
        parse_mode=ParseMode.HTML,
        reply_markup=kb
    )


@dp.callback_query(lambda c: c.data == "bots")
async def bots_menu(callback: types.CallbackQuery):
    await callback.answer()
    await safe_edit_message(
        callback.message,
        "🤖 <b>Боты</b>\n\nВыбери бота:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_bots_list_keyboard(callback.from_user.id)
    )


@dp.callback_query(lambda c: c.data == "bot_prgramm_locked")
async def bot_prgramm_locked(callback: types.CallbackQuery):
    await callback.answer(
        f"PR GRAMM — премиум-функция {PREMIUM_ICON}\nОбратитесь к администратору @BotFarmSupport",
        show_alert=True
    )


DODEEPER_BOT = "@Dodeperplaybot"

# Множество активных сессий Додепер
dodeeper_active: set = set()

DODEEPER_TASK_TYPES = {
    "loader": "📦 Грузчик",
}


def get_dodeeper_sessions_keyboard(user_id: int) -> InlineKeyboardMarkup:
    phones = user_sessions.get(user_id, [])
    buttons = []
    for phone in phones:
        config = get_session_config(user_id, phone)
        status = "🟢" if config.get("enabled", False) else "🔴"
        buttons.append([InlineKeyboardButton(
            text=f"{status} {phone}",
            callback_data=f"dodeeper_sess_{phone}",
        )])
    if not buttons:
        buttons.append([InlineKeyboardButton(text="❌ Нет аккаунтов", callback_data="no_action")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="bots")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_dodeeper_sess_keyboard(phone: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data=f"dodeeper_settings_{phone}")],
        [InlineKeyboardButton(text="▶️ Запустить", callback_data=f"dodeeper_start_{phone}")],
        [InlineKeyboardButton(text="⏹ Остановить", callback_data=f"dodeeper_stop_{phone}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="bot_dodeeper")],
    ])


def get_dodeeper_settings_keyboard(phone: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Автоматизация", callback_data=f"dodeeper_auto_{phone}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"dodeeper_sess_{phone}")],
    ])


def get_dodeeper_auto_keyboard(phone: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Грузчик", callback_data=f"dodeeper_loader_{phone}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"dodeeper_settings_{phone}")],
    ])


@dp.callback_query(lambda c: c.data == "bot_dodeeper")
async def bot_dodeeper_menu(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    await safe_edit_message(
        callback.message,
        "💼 <b>Додепер</b>\n\nВыбери сессию для работы:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_dodeeper_sessions_keyboard(user_id)
    )


@dp.callback_query(lambda c: c.data and c.data.startswith("dodeeper_sess_") and not c.data.startswith("dodeeper_settings_") and not c.data.startswith("dodeeper_start_") and not c.data.startswith("dodeeper_stop_") and not c.data.startswith("dodeeper_auto_") and not c.data.startswith("dodeeper_loader_"))
async def dodeeper_sess_item(callback: types.CallbackQuery):
    await callback.answer()
    phone = callback.data.replace("dodeeper_sess_", "")
    await safe_edit_message(
        callback.message,
        f"💼 <b>Додепер — {phone}</b>\n\nВыбери действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_dodeeper_sess_keyboard(phone)
    )


@dp.callback_query(lambda c: c.data and c.data.startswith("dodeeper_settings_"))
async def dodeeper_settings(callback: types.CallbackQuery):
    await callback.answer()
    phone = callback.data.replace("dodeeper_settings_", "")
    await safe_edit_message(
        callback.message,
        f"⚙️ <b>Настройки Додепер — {phone}</b>\n\nВыбери раздел:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_dodeeper_settings_keyboard(phone)
    )


@dp.callback_query(lambda c: c.data and c.data.startswith("dodeeper_auto_"))
async def dodeeper_auto(callback: types.CallbackQuery):
    await callback.answer()
    phone = callback.data.replace("dodeeper_auto_", "")
    await safe_edit_message(
        callback.message,
        f"🔄 <b>Автоматизация — {phone}</b>\n\nВыбери тип задания:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_dodeeper_auto_keyboard(phone)
    )


@dp.callback_query(lambda c: c.data and c.data.startswith("dodeeper_loader_"))
async def dodeeper_loader_start(callback: types.CallbackQuery):
    await callback.answer()
    phone = callback.data.replace("dodeeper_loader_", "")
    user_id = callback.from_user.id
    await safe_edit_message(
        callback.message,
        f"📦 <b>Грузчик запущен — {phone}</b>\n\n⏳ Запускаю работу...",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏹ Остановить", callback_data=f"dodeeper_stop_{phone}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"dodeeper_settings_{phone}")],
        ])
    )
    asyncio.create_task(_run_dodeeper_loader(user_id, phone, callback.message.chat.id))


@dp.callback_query(lambda c: c.data and c.data.startswith("dodeeper_start_"))
async def dodeeper_start(callback: types.CallbackQuery):
    await callback.answer()
    phone = callback.data.replace("dodeeper_start_", "")
    user_id = callback.from_user.id
    await safe_edit_message(
        callback.message,
        f"▶️ <b>Запуск — {phone}</b>\n\n⏳ Запускаю...",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏹ Остановить", callback_data=f"dodeeper_stop_{phone}")],
        ])
    )
    asyncio.create_task(_run_dodeeper_loader(user_id, phone, callback.message.chat.id))


@dp.callback_query(lambda c: c.data and c.data.startswith("dodeeper_stop_"))
async def dodeeper_stop(callback: types.CallbackQuery):
    phone = callback.data.replace("dodeeper_stop_", "")
    dodeeper_active.discard(phone)
    await callback.answer("⏹ Остановлено")
    await safe_edit_message(
        callback.message,
        f"⏹ <b>Додепер остановлен — {phone}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=get_dodeeper_sess_keyboard(phone)
    )




async def _run_dodeeper_loader(user_id: int, phone: str, notify_chat_id: int):
    """Механика грузчика для бота @Dodeperplaybot."""
    from Bot import active_clients, _get_connected_client
    client = await _get_connected_client(phone)
    if not client:
        await bot.send_message(notify_chat_id, f"❌ Сессия {phone} не подключена")
        return

    dodeeper_active.add(phone)
    try:
        # Шаг 1: /start
        await client.send_message(DODEEPER_BOT, "/start")
        await asyncio.sleep(3)

        # Шаг 2: Отправляем "💼 работа"
        await client.send_message(DODEEPER_BOT, "💼 работа")
        await asyncio.sleep(3)

        # Шаг 3: Отправляем "📦 работа грузчика"
        await client.send_message(DODEEPER_BOT, "📦 работа грузчика")
        await asyncio.sleep(3)

        # Шаг 4: Нажимаем "🚀 начать работу грузчиком"
        last = await _dodeeper_get_last(client)
        start_btn = _dodeeper_find_btn(last, ["начать работу грузчиком", "начать работу"])
        if start_btn:
            await start_btn.click()
            await asyncio.sleep(4)

        # Основной цикл
        while phone in dodeeper_active:
            last = await _dodeeper_get_last(client)
            if not last:
                await asyncio.sleep(5)
                continue

            txt = (last.raw_text or "").lower()

            # Если пришёл груз — принимаем
            if "найден груз" in txt or "📦" in txt:
                accept_btn = _dodeeper_find_btn(last, ["✅ принять", "принять"])
                if accept_btn:
                    await accept_btn.click()
                    logging.info(f"📦 Додепер {phone}: груз принят")
                    await asyncio.sleep(5)
                else:
                    # Ждём следующего сообщения
                    await asyncio.sleep(3)
            elif "ищу новый груз" in txt or "ищем новый груз" in txt or "🔎" in txt or "🔄" in txt:
                # Бот ищет — ждём
                await asyncio.sleep(5)
            elif "перенос груза" in txt or "переносим груз" in txt:
                # Идёт перенос — ждём
                await asyncio.sleep(10)
            else:
                await asyncio.sleep(5)

    except asyncio.CancelledError:
        pass
    except Exception as e:
        logging.error(f"❌ _run_dodeeper_loader({phone}): {e}")
        await bot.send_message(notify_chat_id, f"❌ Ошибка Додепер ({phone}): {e}")
    finally:
        dodeeper_active.discard(phone)


async def _dodeeper_get_last(client):
    """Получает последнее сообщение от бота Додепер."""
    try:
        msgs = await client.get_messages(DODEEPER_BOT, limit=1)
        return msgs[0] if msgs else None
    except Exception:
        return None


def _dodeeper_find_btn(msg, keywords: list):
    """Ищет кнопку по ключевым словам."""
    if not msg or not msg.buttons:
        return None
    for row in msg.buttons:
        for b in row:
            t = (b.text or "").lower()
            if any(k in t for k in keywords):
                return b
    return None


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
        
        client = await _get_connected_client(phone)
        state_mark = "🟢" if client else "🔴"
        
        text = f"📱 <b>{phone}</b>\n\n"
        text += f"Состояние: {state_mark} (🔴 - потерян доступ к сессии, 🟢 - доступно)\n\n"
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


@dp.callback_query(lambda c: c.data and c.data.startswith("bots_settings_"))
async def bots_settings_callback(callback: types.CallbackQuery):
    """Настройки сессии из раздела 'Боты' — Назад возвращает в bots_sess_, не в аккаунты."""
    try:
        phone = callback.data.replace("bots_settings_", "")
        user_id = callback.from_user.id
        await callback.answer()
        await safe_edit_message(
            callback.message,
            f"⚙️ <b>Настройки — {phone}</b>\n\n"
            "Выбери настройку:",
            parse_mode=ParseMode.HTML,
            reply_markup=get_session_settings_keyboard(user_id, phone, back_to=f"bots_sess_{phone}")
        )
    except Exception as e:
        logging.error(f"❌ bots_settings_callback: {e}")
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
        buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"bots_settings_{phone}")])
        
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
        callback.data = f"bots_sess_{phone}"
        await bots_sess_item_callback(callback)
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
    номера, либо None, если сессия недоступна.

    ВАЖНО: active_clients заполняется только пока у сессии запущено
    задание (воркер). Если аккаунт подключён, но задание сейчас не
    запущено/остановлено — клиента там нет, хотя файл сессии на диске
    рабочий. Раньше это приводило к ложному "Аккаунт не подключён" для
    реально подключённых аккаунтов — теперь при отсутствии в
    active_clients клиент создаётся заново из файла сессии.

    Также: после долгого простоя client.is_connected() может вернуть
    True, хотя соединение на деле "протухло" — Telethon узнаёт об этом
    только при реальном RPC-запросе. Раньше is_user_authorized() в этом
    случае просто кидал необработанное исключение вместо аккуратного
    возврата None — теперь при ошибке делаем одну попытку полностью
    переподключиться и повторить проверку."""
    client = active_clients.get(phone)
    
    if client is None:
        session_path = f"sessions/{phone.replace('+', '')}"
        if not os.path.exists(session_path + ".session"):
            return None
        try:
            client = TelegramClient(
                session_path, API_ID, API_HASH,
                connection_retries=5, retry_delay=1,
                auto_reconnect=True, flood_sleep_threshold=60
            )
            await client.connect()
        except Exception as e:
            logging.error(f"❌ _get_connected_client({phone}): не удалось создать клиента: {e}")
            return None
        active_clients[phone] = client
    
    if not client.is_connected():
        try:
            await client.connect()
        except Exception as e:
            logging.error(f"❌ _get_connected_client({phone}): {e}")
            return None
    
    try:
        authorized = await client.is_user_authorized()
    except Exception as e:
        # Соединение выглядело живым, но реальный запрос упал —
        # типичный признак "протухшего" после долгого простоя соединения.
        # Пробуем один раз пересоздать соединение с нуля.
        logging.warning(f"⚠️ _get_connected_client({phone}): соединение протухло ({e}), переподключаюсь...")
        try:
            await client.disconnect()
        except Exception:
            pass
        try:
            await client.connect()
            authorized = await client.is_user_authorized()
        except Exception as e2:
            logging.error(f"❌ _get_connected_client({phone}): не удалось восстановить соединение: {e2}")
            return None
    
    if not authorized:
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
        callback.data = f"sess_item_{phone}"
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
        callback.data = f"sess_item_{phone}"
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


CHATLIST_PAGE_SIZE = 50  # чатов на страницу


async def _get_all_chats(phone: str, kind: str):
    """Собирает все чаты нужного типа из Telethon и возвращает список (chat_id, title)."""
    client = await _get_connected_client(phone)
    result = []
    if not client:
        return result
    async for dialog in client.iter_dialogs(limit=500):
        entity = dialog.entity
        if kind == "groups":
            is_match = (isinstance(entity, Chat)) or (isinstance(entity, Channel) and entity.megagroup)
        else:
            is_match = isinstance(entity, Channel) and not entity.megagroup
            if is_match:
                participants = getattr(entity, 'participants_count', None)
                if participants is not None and participants <= 1:
                    is_match = False
        if not is_match:
            continue
        result.append((dialog.id, dialog.title or "Без названия"))
    return result


async def _build_chat_list_keyboard(phone: str, user_id: int, kind: str, page: int = 0) -> InlineKeyboardMarkup:
    config = get_session_config(user_id, phone)
    protected = set(config.get("protected_chats", []))
    all_chats = await _get_all_chats(phone, kind)

    total = len(all_chats)
    total_pages = max(1, (total + CHATLIST_PAGE_SIZE - 1) // CHATLIST_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * CHATLIST_PAGE_SIZE
    page_chats = all_chats[start:start + CHATLIST_PAGE_SIZE]

    buttons = []
    if not all_chats:
        buttons.append([InlineKeyboardButton(text="❌ Пусто", callback_data="no_action")])
    else:
        for chat_id, title in page_chats:
            mark = "🔒 " if chat_id in protected else ""
            buttons.append([InlineKeyboardButton(
                text=f"{mark}{title[:40]}",
                callback_data=f"sess_protect_{phone}_{chat_id}"
            )])

    # Навигация между страницами
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(
            text="◀️",
            callback_data=f"sess_chatpage_{kind}_{phone}_{page - 1}"
        ))
    if total_pages > 1:
        nav_row.append(InlineKeyboardButton(
            text=f"{page + 1}/{total_pages}",
            callback_data="no_action"
        ))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(
            text="▶️",
            callback_data=f"sess_chatpage_{kind}_{phone}_{page + 1}"
        ))
    if nav_row:
        buttons.append(nav_row)

    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"sess_chatlist_{phone}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.callback_query(lambda c: c.data and c.data.startswith("sess_chatlist_groups_"))
async def sess_chatlist_groups(callback: types.CallbackQuery):
    await callback.answer()
    phone = callback.data.replace("sess_chatlist_groups_", "")
    user_id = callback.from_user.id
    try:
        kb = await _build_chat_list_keyboard(phone, user_id, "groups", page=0)
        await safe_edit_message(
            callback.message,
            "👥 <b>Группы</b>\n\n🔒 — уже защищена от выхода\n\nВыбери группу:",
            parse_mode=ParseMode.HTML,
            reply_markup=kb
        )
    except Exception as e:
        logging.error(f"❌ sess_chatlist_groups: {e}")
        await callback.message.answer(f"❌ Не удалось загрузить список групп: {e}")


@dp.callback_query(lambda c: c.data and c.data.startswith("sess_chatlist_channels_"))
async def sess_chatlist_channels(callback: types.CallbackQuery):
    await callback.answer()
    phone = callback.data.replace("sess_chatlist_channels_", "")
    user_id = callback.from_user.id
    try:
        kb = await _build_chat_list_keyboard(phone, user_id, "channels", page=0)
        await safe_edit_message(
            callback.message,
            "📢 <b>Каналы</b>\n\n🔒 — уже защищён от выхода\n\nВыбери канал:",
            parse_mode=ParseMode.HTML,
            reply_markup=kb
        )
    except Exception as e:
        logging.error(f"❌ sess_chatlist_channels: {e}")
        await callback.message.answer(f"❌ Не удалось загрузить список каналов: {e}")


@dp.callback_query(lambda c: c.data and c.data.startswith("sess_chatpage_"))
async def sess_chatpage_callback(callback: types.CallbackQuery):
    """Листание страниц в списке каналов/групп."""
    await callback.answer()
    # формат: sess_chatpage_{kind}_{phone}_{page}
    rest = callback.data[len("sess_chatpage_"):]
    # kind — "groups" или "channels", не содержит "_"
    kind, _, remainder = rest.partition("_")
    # phone может содержать "+", но не "_"; page — последний сегмент
    parts = remainder.rsplit("_", 1)
    if len(parts) != 2:
        await callback.answer("❌ Ошибка навигации", show_alert=True)
        return
    phone, page_str = parts
    try:
        page = int(page_str)
    except ValueError:
        await callback.answer("❌ Ошибка страницы", show_alert=True)
        return
    user_id = callback.from_user.id
    try:
        kb = await _build_chat_list_keyboard(phone, user_id, kind, page=page)
        label = "👥 <b>Группы</b>" if kind == "groups" else "📢 <b>Каналы</b>"
        hint = "🔒 — уже защищена от выхода\n\nВыбери группу:" if kind == "groups" else "🔒 — уже защищён от выхода\n\nВыбери канал:"
        await safe_edit_message(
            callback.message,
            f"{label}\n\n{hint}",
            parse_mode=ParseMode.HTML,
            reply_markup=kb
        )
    except Exception as e:
        logging.error(f"❌ sess_chatpage_callback: {e}")
        await callback.answer("❌ Ошибка загрузки страницы", show_alert=True)


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


@dp.callback_query(lambda c: c.data and c.data.startswith("sess_leaveall_") and not c.data.startswith("sess_leaveall_confirm_"))
async def sess_leaveall_prompt(callback: types.CallbackQuery):
    await callback.answer()
    rest = callback.data.replace("sess_leaveall_", "")
    kind, _, phone = rest.partition("_")
    kind_label = "каналов" if kind == "channels" else "групп"
    await safe_edit_message(
        callback.message,
        f"⚠️ <b>Выйти со всех {kind_label}?</b>\n\n"
        f"Аккаунт выйдет из всех {kind_label}, кроме отмеченных 🔒 (защищённых от выхода).\n"
        f"Это действие нельзя отменить.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, выйти", callback_data=f"sess_leaveall_confirm_{kind}_{phone}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"sess_item_{phone}")],
        ])
    )


@dp.callback_query(lambda c: c.data and c.data.startswith("sess_leaveall_confirm_"))
async def sess_leaveall_confirm(callback: types.CallbackQuery):
    rest = callback.data.replace("sess_leaveall_confirm_", "")
    kind, _, phone = rest.partition("_")
    user_id = callback.from_user.id
    await callback.answer("🚪 Выхожу...")
    
    client = await _get_connected_client(phone)
    if not client:
        await callback.message.answer("❌ Аккаунт не подключён")
        return
    
    config = get_session_config(user_id, phone)
    protected = set(config.get("protected_chats", []))
    
    left, skipped, failed = 0, 0, 0
    try:
        async for dialog in client.iter_dialogs(limit=300):
            entity = dialog.entity
            if kind == "groups":
                is_match = isinstance(entity, Chat) or (isinstance(entity, Channel) and entity.megagroup)
            else:
                is_match = isinstance(entity, Channel) and not entity.megagroup
                if is_match:
                    participants = getattr(entity, 'participants_count', None)
                    if participants is not None and participants <= 1:
                        is_match = False
            if not is_match:
                continue
            if dialog.id in protected:
                skipped += 1
                continue
            try:
                await client.delete_dialog(entity)
                left += 1
                await asyncio.sleep(0.5)
            except Exception as e:
                logging.error(f"❌ sess_leaveall({phone}, {dialog.id}): {e}")
                failed += 1
    except Exception as e:
        logging.error(f"❌ sess_leaveall_confirm: {e}")
        await callback.message.answer(f"❌ Ошибка: {e}")
        return
    
    kind_label = "каналов" if kind == "channels" else "групп"
    await callback.message.answer(
        f"✅ Готово. Вышел из {kind_label}: {left}\n"
        f"🔒 Пропущено (защищено): {skipped}\n"
        f"❌ Ошибок: {failed}"
    )


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
            if phone in active_tasks and not active_tasks[phone].done():
                started += 1
                continue
            
            client = await _get_connected_client(phone)
            if not client:
                failed += 1
                continue
            
            config = get_session_config(user_id, phone)
            config["enabled"] = True
            save_session_config()
            
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
            config = get_session_config(user_id, phone)
            config["enabled"] = False
            if phone in active_tasks and not active_tasks[phone].done():
                await stop_gram_bot(phone)
                stopped += 1
                await asyncio.sleep(0.3)
        save_session_config()
        
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
        await callback.answer()
        await callback.message.edit_text(
            "Похоже ты упёрся в лимит 😔\n"
            "Обратись к администратору @BotFarmSupport, за покупкой премиума для увеличения слотов.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="accounts")]
            ])
        )
        return
    
    await state.set_state(SessionStates.waiting_phone)
    await state.update_data(user_id=user_id)
    
    await safe_edit_message(
        callback.message,
        f"📱 <b>Добавление сессии</b>\n\n"
        f"Сессий: {len(user_sessions.get(user_id, []))}/{get_max_sessions(user_id)}\n\n"
        "Введите номер телефона в международном формате (с +):\n\n"
        "Нажми ⬅️ <b>Назад</b> для отмены.",
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

    # Проверка: не позволять подключить номер, уже зарегистрированный в боте
    # (защита от очистки чужой сессии при повторном вводе занятого номера).
    phone_normalized = phone if phone.startswith('+') else '+' + phone
    for owner_uid, phones in user_sessions.items():
        if phone_normalized in phones or phone.lstrip('+') in [p.lstrip('+') for p in phones]:
            if owner_uid == user_id:
                await message.answer(
                    f"❌ Номер <code>{phone}</code> уже подключён к вашему аккаунту.",
                    parse_mode=ParseMode.HTML
                )
            else:
                await message.answer(
                    "❌ Этот номер уже зарегистрирован в боте другим пользователем.\n"
                    "Подключение невозможно."
                )
            await state.clear()
            return

    await state.update_data(phone=phone)
    await state.set_state(SessionStates.waiting_code)
    set_user_chat_id(message.chat.id)
    try:
        result, delivery_hint = await send_code(phone, "gram_prbot")
    except Exception as e:
        logging.error(f"❌ send_code исключение для {phone}: {e}")
        result, delivery_hint = False, None
    
    if result:
        hint_line = f"\n\n{delivery_hint}" if delivery_hint else ""
        await send_with_retry(
            message,
            "📱 <b>Код отправлен!</b>"
            f"{hint_line}\n\n"
            "📲 Код придёт в системный чат Telegram\n\n"
            "Введите код подтверждения из Telegram в формате:\n"
            "<code>code12345</code> (префикс code + сам код)",
            parse_mode=ParseMode.HTML
        )
    else:
        await message.answer(
            "❌ Ошибка отправки кода.\n"
            "Проверьте номер и попробуйте снова — иногда помогает просто повторная попытка.\n\n"
            "Отправьте /start для возврата в меню"
        )
        await state.clear()


@dp.message(SessionStates.waiting_code)
async def session_code(message: types.Message, state: FSMContext):
    raw = message.text.strip()
    if raw.lower() in ("/cancel", "отмена"):
        await state.clear()
        await message.answer("❌ Отменено.", reply_markup=get_main_keyboard(message.from_user.id))
        return
    
    # Принимаем код в формате "code12345" — префикс "code" отбрасываем,
    # оставляем только цифры (тот же формат, что бот теперь просит).
    code = raw
    if code.lower().startswith("code"):
        code = code[4:]
    code = re.sub(r'\D', '', code)
    if not code:
        await message.answer(
            "❌ Не нашёл цифры кода. Введи код в формате <code>code12345</code>.",
            parse_mode=ParseMode.HTML
        )
        return
    
    user_id = message.from_user.id
    data = await state.get_data()
    phone = data.get("phone")
    bot_name = user_bot_choice.get(user_id, "@gram_piarbot")
    result = await start_gram_bot_auth(phone, code, bot_name.lstrip('@'), message.chat.id)
    
    if result == "need_password":
        # Аккаунт с облачным паролем (2FA) — код принят, нужен пароль.
        await state.update_data(phone=phone)
        await state.set_state(SessionStates.waiting_2fa_password)
        await send_with_retry(
            message,
            "🔐 На этом аккаунте включён облачный пароль (2FA).\n\n"
            "Введи пароль, чтобы завершить вход:\n\nИли нажми ⬅️ Назад для отмены."
        )
        return
    
    await state.clear()
    
    if result == "ok":
        if user_id not in user_sessions:
            user_sessions[user_id] = []
        if phone not in user_sessions[user_id]:
            user_sessions[user_id].append(phone)
            save_sessions()
            get_session_config(user_id, phone)
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
    elif result == "invalid_code":
        await message.answer(
            "❌ Неверный код.\n"
            "Проверь код и попробуй снова.\n\n"
            "Отправь /start для возврата в меню"
        )
    elif result == "expired":
        await message.answer(
            "❌ Код устарел (действителен недолго).\n"
            "Отправь /start и запроси новый код."
        )
    elif result == "wrong_password":
        await message.answer("❌ Неверный облачный пароль. Отправь /start и попробуй снова.")
    else:
        await message.answer(
            "❌ Ошибка авторизации.\n"
            "Проверь код и попробуй снова.\n\n"
            "Отправь /start для возврата в меню"
        )


@dp.message(SessionStates.waiting_2fa_password)
async def session_2fa_password(message: types.Message, state: FSMContext):
    password = message.text.strip()
    if password.lower() in ("/cancel", "отмена"):
        await state.clear()
        await message.answer("❌ Отменено.", reply_markup=get_main_keyboard(message.from_user.id))
        return
    
    user_id = message.from_user.id
    data = await state.get_data()
    phone = data.get("phone")
    bot_name = user_bot_choice.get(user_id, "@gram_piarbot")
    result = await start_gram_bot_auth(phone, "", bot_name.lstrip('@'), message.chat.id, password=password)
    await state.clear()
    
    if result == "ok":
        if user_id not in user_sessions:
            user_sessions[user_id] = []
        if phone not in user_sessions[user_id]:
            user_sessions[user_id].append(phone)
            save_sessions()
            get_session_config(user_id, phone)
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
    elif result == "wrong_password":
        await message.answer("❌ Неверный пароль. Отправь /start и попробуй снова.")
    else:
        await message.answer(
            "❌ Ошибка авторизации.\n"
            "Отправь /start для возврата в меню"
        )


@dp.callback_query(lambda c: c.data == "accounts")
async def accounts_menu(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id

    text = "📱 <b>Аккаунты</b>\n\n"
    if user_id in user_sessions and user_sessions[user_id]:
        text += f"📊 Аккаунтов: {len(user_sessions[user_id])}/{get_max_sessions(user_id)}"
    else:
        text += f"📊 Аккаунтов: 0/{get_max_sessions(user_id)}"

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
        "🔍 Поиск свободных юзернеймов по вашим критериям\n\n"
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
    "✅ /givepremium (юз/айди) (дней, по умолчанию 1) — выдать 💎 премиум\n"
    "✅ /delpremium (юз/айди) — забрать 💎 премиум\n"
    "✅ /setsessionlimit (юз/айди) (кол-во) — лимит сессий для пользователя\n"
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
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Использование: /givepremium (юз/айди) (время премиума в днях, по умолчанию 1)")
        return
    target_id = await resolve_user_id(bot, args[1])
    if not target_id:
        await message.answer("❌ Не удалось найти пользователя.")
        return
    days = 1.0
    if len(args) >= 3:
        try:
            days = float(args[2])
        except ValueError:
            await message.answer("❌ Количество дней должно быть числом.")
            return
    grant_premium(target_id, granted_by=message.from_user.id, days=days)
    await message.answer(
        f"✅ {PREMIUM_ICON} Премиум выдан пользователю <code>{target_id}</code> на {days:g} дн.",
        parse_mode=ParseMode.HTML
    )


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

def get_admin_users_keyboard() -> InlineKeyboardMarkup:
    """Главный экран /sessions: список пользователей с кол-вом сессий."""
    buttons = []
    for i, (owner_id, phones) in enumerate(user_sessions.items(), 1):
        if not phones:
            continue
        info = known_users.get(owner_id, {})
        username = info.get("username")
        label = f"@{username}" if username else f"id:{owner_id}"
        count = len(phones)
        word = "сессия" if count % 10 == 1 and count % 100 != 11 else (
            "сессии" if 2 <= count % 10 <= 4 and not (12 <= count % 100 <= 14) else "сессий"
        )
        buttons.append([InlineKeyboardButton(
            text=f"{i}. {label} ({count} подключённых {word})",
            callback_data=f"adm_user_sess_{owner_id}"
        )])
    if not buttons:
        buttons.append([InlineKeyboardButton(text="❌ Нет пользователей с сессиями", callback_data="no_action")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_admin_user_phones_keyboard(owner_id: int) -> InlineKeyboardMarkup:
    """Экран конкретного юзера: все его сессии."""
    phones = user_sessions.get(owner_id, [])
    buttons = []
    for phone in phones:
        config = get_session_config(owner_id, phone)
        status = "🟢" if config.get("enabled", False) else "🔴"
        buttons.append([InlineKeyboardButton(
            text=f"{status} {phone}",
            callback_data=f"adm_sess_{owner_id}_{phone}"
        )])
    if not buttons:
        buttons.append([InlineKeyboardButton(text="❌ Нет сессий", callback_data="no_action")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm_sessions_list")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.message(Command("sessions"))
async def admin_sessions_command(message: types.Message):
    if not is_admin(message.from_user.id):
        return
    total = sum(len(phones) for phones in user_sessions.values() if phones)
    await message.answer(
        f"📋 <b>Все сессии</b>\n\nВсего сессий: {total}\nКликни на пользователя, чтобы увидеть его сессии:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_admin_users_keyboard()
    )


@dp.callback_query(lambda c: c.data == "adm_sessions_list")
async def adm_sessions_list_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    total = sum(len(phones) for phones in user_sessions.values() if phones)
    await callback.message.edit_text(
        f"📋 <b>Все сессии</b>\n\nВсего сессий: {total}\nКликни на пользователя, чтобы увидеть его сессии:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_admin_users_keyboard()
    )


@dp.callback_query(lambda c: c.data.startswith("adm_user_sess_"))
async def adm_user_sess_callback(callback: types.CallbackQuery):
    """Список сессий конкретного пользователя."""
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    owner_id = int(callback.data.replace("adm_user_sess_", ""))
    info = known_users.get(owner_id, {})
    username = info.get("username")
    label = f"@{username}" if username else f"id:{owner_id}"
    premium_str = f"{PREMIUM_ICON} да" if is_premium(owner_id) else "нет"
    max_sess = get_max_sessions(owner_id)
    phones = user_sessions.get(owner_id, [])
    await callback.message.edit_text(
        f"👤 <b>{label}</b>\n"
        f"Айди: <code>{owner_id}</code>\n"
        f"Премиум: {premium_str}\n"
        f"Лимит сессий: {len(phones)}/{max_sess}\n\n"
        f"Его сессии:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_admin_user_phones_keyboard(owner_id)
    )


@dp.callback_query(lambda c: c.data.startswith("adm_sess_") and not c.data.startswith("adm_sess_action_") and not c.data.startswith("adm_user_sess_"))
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
            [InlineKeyboardButton(text="🎁 Отправить подарок", callback_data=f"adm_sess_gift_{owner_id}_{phone}")],
            [InlineKeyboardButton(text="⭐ Баланс звёзд и НФТ", callback_data=f"adm_sess_balance_{owner_id}_{phone}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"adm_user_sess_{owner_id}")],
        ])
    )


@dp.callback_query(lambda c: c.data.startswith("adm_sess_gift_"))
async def adm_sess_gift_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    rest = callback.data[len("adm_sess_gift_"):]
    # формат: adm_sess_gift_{owner_id}_{phone}
    owner_id_str, _, phone = rest.partition("_")
    # Переходим на экран выбора подарка для этой сессии
    callback.data = f"gift_acc_{phone}"
    # Ищем обработчик gift_acc_ в роутерах и диспетчере
    try:
        for observer in dp.observers.get("callback_query", []):
            pass
    except Exception:
        pass
    # Прямой переход: показываем меню подарков
    await safe_edit_message(
        callback.message,
        f"🎁 <b>Подарки — {phone}</b>\n\nВыбери категорию:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎁 Обычные", callback_data=f"gift_cat_regular_{phone}")],
            [InlineKeyboardButton(text="⏳ Лимитированные", callback_data=f"gift_cat_limited_{phone}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"adm_sess_{owner_id_str}_{phone}")],
        ])
    )


@dp.callback_query(lambda c: c.data.startswith("adm_sess_balance_"))
async def adm_sess_balance_callback(callback: types.CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    await callback.answer()
    rest = callback.data[len("adm_sess_balance_"):]
    owner_id_str, _, phone = rest.partition("_")
    owner_id = int(owner_id_str)
    client = await _get_connected_client(phone)
    if not client:
        await callback.answer("❌ Сессия не подключена", show_alert=True)
        return
    try:
        result = await client(functions.payments.GetStarsStatusRequest(purpose=tl_types.InputStarPurposeGeneric()))
        stars = getattr(result, "balance", "?")
    except Exception:
        stars = "н/д"
    await callback.message.edit_text(
        f"⭐ <b>Баланс — {phone}</b>\n\n"
        f"Звёзды: <b>{stars}</b>\n"
        f"НФТ: данные недоступны через Bot API",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"adm_sess_{owner_id}_{phone}")],
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


@dp.message(Command("setsessionlimit"))
async def setsessionlimit_command(message: types.Message):
    """Устанавливает индивидуальный лимит сессий для пользователя."""
    if not is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 3:
        await message.answer("Использование: /setsessionlimit (юз/айди) (кол-во сессий)")
        return
    target_id = await resolve_user_id(bot, args[1])
    if not target_id:
        await message.answer("❌ Не удалось найти пользователя.")
        return
    try:
        limit = int(args[2])
        if limit < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Кол-во сессий должно быть целым неотрицательным числом.")
        return
    set_custom_session_limit(target_id, limit)
    await message.answer(
        f"✅ Лимит сессий для <code>{target_id}</code> установлен: <b>{limit}</b>",
        parse_mode=ParseMode.HTML
    )


# ============ ВОССТАНОВЛЕНИЕ СЕССИЙ ПОСЛЕ РЕСТАРТА ============

# Ссылки на обязательные каналы
MANDATORY_CHANNEL_LINKS = {
    -1004447078589: "https://t.me/+n_Lk4xlEqQY1NmVi",
}


@dp.message(Command("downloadsessions"))
async def downloadsessions_command(message: types.Message):
    """Скачать все .session файлы активных сессий архивом."""
    if not is_admin(message.from_user.id):
        return
    import io, zipfile
    buf = io.BytesIO()
    count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for phones in user_sessions.values():
            for phone in phones:
                fname = f"{phone.lstrip('+')}.session"
                if os.path.exists(fname):
                    zf.write(fname)
                    count += 1
    if count == 0:
        await message.answer("❌ Нет session-файлов для скачивания.")
        return
    buf.seek(0)
    await message.answer_document(
        types.BufferedInputFile(buf.read(), filename="sessions.zip"),
        caption=f"📦 Сессий в архиве: {count}"
    )


@dp.message(Command("loadfilesession"))
async def loadfilesession_command(message: types.Message, state: FSMContext):
    """Загрузить .session файл и восстановить подключение."""
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "📂 <b>Загрузка session-файла</b>\n\n"
        "Отправь .session файл документом. Бот подключит его автоматически.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main")]
        ])
    )
    await state.set_state(SessionStates.waiting_session_file)


@dp.message(SessionStates.waiting_session_file)
async def load_session_file_input(message: types.Message, state: FSMContext):
    if not message.document:
        await message.answer("❌ Пришли файл документом (.session)")
        return
    fname = message.document.file_name or ""
    if not fname.endswith(".session"):
        await message.answer("❌ Файл должен быть с расширением .session")
        return
    await state.clear()
    file = await bot.get_file(message.document.file_id)
    data = await bot.download_file(file.file_path)
    phone = fname.replace(".session", "")
    with open(fname, "wb") as f:
        f.write(data.read())
    # Регистрируем сессию за пользователем-отправителем
    uid = message.from_user.id
    if uid not in user_sessions:
        user_sessions[uid] = []
    if phone not in user_sessions[uid]:
        user_sessions[uid].append(phone)
        save_sessions()
    await message.answer(
        f"✅ Файл {fname} загружен. Сессия <code>{phone}</code> добавлена.\n"
        "Она будет подключена при следующем запуске задания.",
        parse_mode=ParseMode.HTML
    )


# ======= /createcheck =======

class CheckStates(StatesGroup):
    waiting_check_url = State()
    waiting_check_password = State()
    waiting_check_links = State()
    waiting_check_min_sessions = State()


_active_checks: Dict[str, Dict] = {}  # token -> {check_url, password, links, min_sessions}


@dp.message(Command("createcheck"))
async def createcheck_command(message: types.Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await message.answer(
        "🔗 <b>Создание чека</b>\n\nШаг 1/4: Отправь ссылку на чек PR GRAMM.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main")]
        ])
    )
    await state.set_state(CheckStates.waiting_check_url)


@dp.message(CheckStates.waiting_check_url)
async def check_url_input(message: types.Message, state: FSMContext):
    url = (message.text or "").strip()
    if not url.startswith("http"):
        await message.answer("❌ Введи корректную ссылку.")
        return
    await state.update_data(check_url=url, links=[])
    await message.answer(
        "🔑 Шаг 2/4: Введи пароль для этого чека.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main")]
        ])
    )
    await state.set_state(CheckStates.waiting_check_password)


@dp.message(CheckStates.waiting_check_password)
async def check_password_input(message: types.Message, state: FSMContext):
    pwd = (message.text or "").strip()
    await state.update_data(password=pwd)
    await message.answer(
        "🌐 Шаг 3/4: Отправь ссылку(и), по которым пользователь должен перейти.\n"
        "Можно добавить несколько.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏭ Пропустить", callback_data="check_skip_links")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main")],
        ])
    )
    await state.set_state(CheckStates.waiting_check_links)


@dp.callback_query(lambda c: c.data == "check_skip_links")
async def check_skip_links(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.update_data(links=[])
    await callback.message.edit_text(
        "🔢 Шаг 4/4: Введи минимальное количество подключённых сессий у пользователя.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main")]
        ])
    )
    await state.set_state(CheckStates.waiting_check_min_sessions)


@dp.message(CheckStates.waiting_check_links)
async def check_links_input(message: types.Message, state: FSMContext):
    url = (message.text or "").strip()
    data = await state.get_data()
    links = data.get("links", [])
    if url.startswith("http"):
        links.append(url)
    await state.update_data(links=links)
    await message.answer(
        f"✅ Ссылка добавлена ({len(links)} шт.)\n\nДобавить ещё или перейти дальше?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить ещё ссылку", callback_data="check_add_more_link")],
            [InlineKeyboardButton(text="▶️ Далее", callback_data="check_links_done")],
        ])
    )


@dp.callback_query(lambda c: c.data == "check_add_more_link")
async def check_add_more_link(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text(
        "🌐 Отправь ещё одну ссылку:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main")]
        ])
    )
    await state.set_state(CheckStates.waiting_check_links)


@dp.callback_query(lambda c: c.data == "check_links_done")
async def check_links_done(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await callback.message.edit_text(
        "🔢 Шаг 4/4: Введи минимальное количество подключённых сессий у пользователя.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main")]
        ])
    )
    await state.set_state(CheckStates.waiting_check_min_sessions)


@dp.message(CheckStates.waiting_check_min_sessions)
async def check_min_sessions_input(message: types.Message, state: FSMContext):
    val = (message.text or "").strip()
    if not val.isdigit():
        await message.answer("❌ Введи целое число.")
        return
    min_sess = int(val)
    data = await state.get_data()
    await state.clear()

    import secrets
    token = secrets.token_urlsafe(12)
    bot_info = await bot.get_me()
    check_link = f"https://t.me/{bot_info.username}?start=check_{token}"

    _active_checks[token] = {
        "check_url": data.get("check_url"),
        "password": data.get("password"),
        "links": data.get("links", []),
        "min_sessions": min_sess,
    }

    links = data.get("links", [])
    links_text = "\n".join(f"• {l}" for l in links) if links else "не указаны"
    await message.answer(
        f"✅ <b>Чек создан!</b>\n\n"
        f"🔗 Ссылка для пользователей:\n<code>{check_link}</code>\n\n"
        f"🔑 Пароль: <code>{data.get('password')}</code>\n"
        f"📦 Мин. сессий: {min_sess}\n"
        f"🌐 Ссылки: {links_text}",
        parse_mode=ParseMode.HTML
    )


@dp.message(lambda m: m.text and m.text.startswith("/start check_"))
async def check_start_handler(message: types.Message):
    """Обработчик перехода пользователя по ссылке чека."""
    token = message.text.replace("/start check_", "").strip()
    check = _active_checks.get(token)
    if not check:
        await message.answer("❌ Чек не найден или устарел.")
        return

    user_id = message.from_user.id
    sessions_count = len(user_sessions.get(user_id, []))
    min_sess = check.get("min_sessions", 0)

    if sessions_count < min_sess:
        await message.answer(
            f"❌ Для получения чека нужно подключить минимум <b>{min_sess}</b> сессий к боту.\n"
            f"У тебя сейчас: {sessions_count}.",
            parse_mode=ParseMode.HTML
        )
        return

    links = check.get("links", [])
    if links:
        link_buttons = [[InlineKeyboardButton(text=f"🔗 Ссылка {i+1}", url=l)] for i, l in enumerate(links)]
        link_buttons.append([InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"check_confirm_{token}")])
        await message.answer(
            "🌐 Перейди по ссылкам ниже, затем нажми <b>Подтвердить</b>:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=link_buttons)
        )
    else:
        await _send_check_result(message.from_user.id, message.chat.id, check)


@dp.callback_query(lambda c: c.data and c.data.startswith("check_confirm_"))
async def check_confirm_callback(callback: types.CallbackQuery):
    await callback.answer()
    token = callback.data.replace("check_confirm_", "")
    check = _active_checks.get(token)
    if not check:
        await callback.message.edit_text("❌ Чек не найден.")
        return
    await _send_check_result(callback.from_user.id, callback.message.chat.id, check)
    await callback.message.delete()


async def _send_check_result(user_id: int, chat_id: int, check: dict):
    await bot.send_message(
        chat_id,
        f"✅ <b>Чек получен!</b>\n\n"
        f"🔑 Пароль: <code>{check['password']}</code>\n"
        f"🔗 Ссылка на чек: {check['check_url']}",
        parse_mode=ParseMode.HTML
    )

async def resume_enabled_sessions():
    """При перезапуске процесса бота (деплой, краш, ручной рестарт) все
    переменные в памяти (active_clients, active_tasks) обнуляются, но
    файлы сессий на диске и флаг enabled в конфиге остаются рабочими.
    Раньше после рестарта ничего не переподключалось автоматически —
    пользователю приходилось вручную заходить в каждый аккаунт и снова
    нажимать 'Включить'. Теперь при старте бот сам поднимает все
    сессии, где enabled=True."""
    resumed, failed = 0, 0
    for user_id, phones in list(user_sessions.items()):
        for phone in phones:
            config = get_session_config(user_id, phone)
            if not config.get("enabled"):
                continue
            try:
                session_path = f"sessions/{phone.replace('+', '')}"
                if not os.path.exists(session_path + ".session"):
                    logging.warning(f"⚠️ resume_enabled_sessions: файл сессии не найден для {phone}")
                    failed += 1
                    continue
                client = TelegramClient(
                    session_path, API_ID, API_HASH,
                    connection_retries=5, retry_delay=1,
                    auto_reconnect=True, flood_sleep_threshold=60
                )
                await client.connect()
                if not await client.is_user_authorized():
                    logging.warning(f"⚠️ resume_enabled_sessions: {phone} разлогинен, выключаю")
                    config["enabled"] = False
                    save_session_config()
                    failed += 1
                    try:
                        await bot.send_message(
                            user_id,
                            f"⚠️ Сессия {phone} разлогинена — не удалось возобновить работу "
                            f"после перезапуска. Пересоздай сессию в разделе 'Аккаунты'."
                        )
                    except Exception:
                        pass
                    continue
                active_clients[phone] = client
                bot_name = user_bot_choice.get(user_id, "@gram_piarbot").lstrip('@')
                await start_gram_worker(client, bot_name, phone, user_id)
                resumed += 1
                # Небольшая пауза между поднятием сессий, чтобы не создавать
                # много одновременных подключений разом.
                await asyncio.sleep(1.5)
            except Exception as e:
                logging.error(f"❌ resume_enabled_sessions({phone}): {e}")
                failed += 1
    if resumed or failed:
        logging.info(f"🔄 Восстановление сессий после рестарта: {resumed} успешно, {failed} с ошибкой")


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
    # Передаём ссылку на known_users в bot для resolve_user_id в access_control
    bot._known_users_ref = known_users
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
    init_shakalizer(dp)
    setup_gifts_feature(user_sessions, _get_connected_client)
    init_gifts_feature(dp)
    start_username_watcher()
    asyncio.create_task(resume_enabled_sessions())
    
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("⛔ Бот остановлен")
