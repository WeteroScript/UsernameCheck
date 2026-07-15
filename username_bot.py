import logging
import random
import string
import aiohttp
import os
import json
import asyncio
from aiogram import types, Router, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramRetryAfter, TelegramAPIError
from datetime import datetime
import itertools
from asyncio import Semaphore
import re
from typing import Optional, Dict, List
from dotenv import load_dotenv

load_dotenv()

router = Router()

# ============ КОНФИГ ============

BOT_TOKEN = os.getenv("BOT_TOKEN")

RATE_LIMITER = Semaphore(10)
CHECK_DELAY = 0.2
BATCH_SIZE = 10
CONNECTION_LIMIT = 50
MAX_RETRIES = 3

TAKEN_DB_FILE = "taken_usernames.json"
FREE_DB_FILE = "free_usernames.json"
BANNED_DB_FILE = "banned_usernames.json"

BANNED_INDICATORS = [
    "deactivated", "user is deactivated", "account deleted",
    "this account was banned", "account was terminated",
]

user_settings = {}
http_session: Optional[aiohttp.ClientSession] = None
username_router_initialized = False


# ============ БАЗА ДАННЫХ ============

def load_db(file_path: str) -> Dict:
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_db(file_path: str, data: Dict):
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


# ============ НАСТРОЙКИ ============

def get_user_settings(user_id: int) -> Dict:
    if user_id not in user_settings:
        user_settings[user_id] = {
            "letter": "s",
            "repeat_count": 2,
            "use_full_alphabet": True
        }
    return user_settings[user_id]

def generate_username(settings: Dict) -> str:
    letters = string.ascii_lowercase if settings["use_full_alphabet"] else 'abcdefghijkmnopqrstuvwxyz'
    main_letter = settings["letter"]
    repeat_count = settings["repeat_count"]
    if main_letter not in letters:
        main_letter = random.choice(letters)
    other_letters = [c for c in letters if c != main_letter]
    remaining_count = max(1, min(5 - repeat_count, 5))
    chosen_others = (
        [random.choice(other_letters) for _ in range(remaining_count)]
        if len(other_letters) < remaining_count
        else random.sample(other_letters, remaining_count)
    )
    pos = random.randint(0, remaining_count)
    result = chosen_others[:pos] + [main_letter] * repeat_count + chosen_others[pos:]
    return ''.join(result)

def generate_examples(settings: Dict, count=4) -> List[str]:
    return [generate_username(settings) for _ in range(count)]

def get_all_possible_usernames(settings: Dict) -> List[str]:
    letters = string.ascii_lowercase if settings["use_full_alphabet"] else 'abcdefghijkmnopqrstuvwxyz'
    main_letter = settings["letter"]
    repeat_count = settings["repeat_count"]
    if main_letter not in letters:
        return []
    other_letters = [c for c in letters if c != main_letter]
    remaining_count = 5 - repeat_count
    if remaining_count <= 0:
        return []
    all_usernames = set()
    combinator = (
        itertools.product(other_letters, repeat=remaining_count)
        if len(other_letters) < remaining_count
        else itertools.permutations(other_letters, remaining_count)
    )
    for others in combinator:
        for pos in range(remaining_count + 1):
            result = list(others[:pos]) + [main_letter] * repeat_count + list(others[pos:])
            all_usernames.add(''.join(result))
            if len(all_usernames) >= 15000:
                return list(all_usernames)
    return list(all_usernames)


# ============ HTTP СЕССИЯ ============

async def get_http_session() -> aiohttp.ClientSession:
    global http_session
    if http_session is None or http_session.closed:
        connector = aiohttp.TCPConnector(
            limit=CONNECTION_LIMIT,
            limit_per_host=30,
            ttl_dns_cache=300
        )
        http_session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=15, connect=5)
        )
    return http_session


# ============ ПРОВЕРКИ ============

async def check_username_parallel(username: str, user_id=None) -> bool:
    # Упрощенная версия для скорости
    return True  # Заглушка, полная логика из Bot.py


# ============ КЛАВИАТУРЫ ============

def get_settings_keyboard(user_id: int) -> InlineKeyboardMarkup:
    s = get_user_settings(user_id)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{'✅' if s['use_full_alphabet'] else '❌'} Все буквы", callback_data="toggle_alphabet")],
        [InlineKeyboardButton(text=f"🔤 Буква: {s['letter'].upper()}", callback_data="change_letter")],
        [InlineKeyboardButton(text=f"🔢 Повторений: {s['repeat_count']}", callback_data="change_count")],
        [InlineKeyboardButton(text="🔄 Сбросить", callback_data="reset_settings")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="username_menu")],
    ])

def get_letter_keyboard() -> InlineKeyboardMarkup:
    rows, row = [], []
    for letter in string.ascii_lowercase:
        row.append(InlineKeyboardButton(text=letter.upper(), callback_data=f"set_letter_{letter}"))
        if len(row) == 7:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="open_settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def get_count_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="2", callback_data="set_count_2"), InlineKeyboardButton(text="3", callback_data="set_count_3"), InlineKeyboardButton(text="4", callback_data="set_count_4")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="open_settings")],
    ])


# ============ CALLBACK ============

@router.callback_query(lambda c: c.data == "gen_username")
async def generate_username_callback(callback: types.CallbackQuery):
    await callback.answer("⏳ Генерирую...")
    # Заглушка
    await callback.message.answer("✅ Найден свободный юзернейм: @testuser")

@router.callback_query(lambda c: c.data == "check_all")
async def check_all_callback(callback: types.CallbackQuery):
    await callback.answer("⏳ Проверяю...")
    await callback.message.answer("🔍 Проверка всех комбинаций...")

@router.callback_query(lambda c: c.data == "open_settings")
async def open_settings_callback(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    settings = get_user_settings(user_id)
    examples = "\n".join(f"• <code>{e}</code>" for e in generate_examples(settings, 4))
    await callback.message.edit_text(
        f"⚙️ <b>Настройки</b>\n\n"
        f"📌 Буква: <b>{settings['letter'].upper()}</b>\n"
        f"📌 Повторений: <b>{settings['repeat_count']}</b>\n\n"
        f"📝 Примеры:\n{examples}",
        parse_mode=ParseMode.HTML,
        reply_markup=get_settings_keyboard(user_id)
    )

@router.callback_query(lambda c: c.data == "show_stats")
async def show_stats_callback(callback: types.CallbackQuery):
    await callback.answer()
    taken = load_db(TAKEN_DB_FILE)
    free = load_db(FREE_DB_FILE)
    banned = load_db(BANNED_DB_FILE)
    await callback.message.edit_text(
        f"📊 <b>Статистика</b>\n\n"
        f"✅ Свободных: {len(free)}\n"
        f"❌ Занятых: {len(taken)}\n"
        f"🚫 Забаненных: {len(banned)}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="username_menu")]
        ])
    )

@router.callback_query(lambda c: c.data == "toggle_alphabet")
async def toggle_alphabet(callback: types.CallbackQuery):
    s = get_user_settings(callback.from_user.id)
    s["use_full_alphabet"] = not s["use_full_alphabet"]
    await callback.answer("✅ Изменено")
    await open_settings_callback(callback)

@router.callback_query(lambda c: c.data == "change_letter")
async def change_letter(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.edit_text("🔤 Выбери букву:", reply_markup=get_letter_keyboard())

@router.callback_query(lambda c: c.data.startswith("set_letter_"))
async def set_letter(callback: types.CallbackQuery):
    letter = callback.data.replace("set_letter_", "")
    get_user_settings(callback.from_user.id)["letter"] = letter
    await callback.answer(f"✅ Буква: {letter.upper()}")
    await open_settings_callback(callback)

@router.callback_query(lambda c: c.data == "change_count")
async def change_count(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.edit_text("🔢 Количество:", reply_markup=get_count_keyboard())

@router.callback_query(lambda c: c.data.startswith("set_count_"))
async def set_count(callback: types.CallbackQuery):
    count = int(callback.data.replace("set_count_", ""))
    get_user_settings(callback.from_user.id)["repeat_count"] = count
    await callback.answer(f"✅ Повторений: {count}")
    await open_settings_callback(callback)

@router.callback_query(lambda c: c.data == "reset_settings")
async def reset_settings(callback: types.CallbackQuery):
    user_settings[callback.from_user.id] = {"letter": "s", "repeat_count": 2, "use_full_alphabet": True}
    await callback.answer("✅ Сброшено")
    await open_settings_callback(callback)


# ============ ИНИЦИАЛИЗАЦИЯ ============

def init_username_bot(dp):
    global username_router_initialized
    if not username_router_initialized:
        dp.include_router(router)
        username_router_initialized = True
        logging.info("✅ Модуль юзернеймов инициализирован")
