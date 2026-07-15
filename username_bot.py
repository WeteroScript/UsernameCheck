"""
Модуль для поиска свободных юзернеймов
Полная версия с Bot.py
"""

import logging
import random
import string
import aiohttp
import os
import json
import asyncio
from aiogram import Router, types
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
CHECK_DELAY = 0.3
BATCH_SIZE = 8
CONNECTION_LIMIT = 50
MAX_RETRIES = 3

TAKEN_DB_FILE = "taken_usernames.json"
FREE_DB_FILE = "free_usernames.json"
BANNED_DB_FILE = "banned_usernames.json"
DEBUG_LOG_FILE = "debug_checks.log"

BANNED_INDICATORS = [
    "deactivated",
    "user is deactivated",
    "account deleted",
    "this account was banned",
    "account was terminated",
    "this account is banned",
    "user deactivated",
]

user_settings: Dict[int, Dict] = {}
http_session: Optional[aiohttp.ClientSession] = None
username_router_initialized = False


# ============ БАЗА ДАННЫХ ============

def load_db(file_path: str) -> Dict:
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Ошибка загрузки БД {file_path}: {e}")
            return {}
    return {}

def save_db(file_path: str, data: Dict):
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.error(f"Ошибка сохранения БД {file_path}: {e}")

def add_to_taken_db(username: str, user_id=None, method="unknown", reason=""):
    db = load_db(TAKEN_DB_FILE)
    if username not in db:
        db[username] = {
            "checked_at": datetime.now().isoformat(),
            "checked_by": str(user_id) if user_id else "unknown",
            "method": method,
            "reason": reason
        }
        save_db(TAKEN_DB_FILE, db)
        return True
    return False

def add_to_banned_db(username: str, user_id=None, method="unknown", reason=""):
    db = load_db(BANNED_DB_FILE)
    if username not in db:
        db[username] = {
            "checked_at": datetime.now().isoformat(),
            "checked_by": str(user_id) if user_id else "unknown",
            "method": method,
            "reason": reason
        }
        save_db(BANNED_DB_FILE, db)
        add_to_taken_db(username, user_id, method, f"BANNED: {reason}")
        return True
    return False

def add_to_free_db(username: str, user_id=None, method="unknown"):
    db = load_db(FREE_DB_FILE)
    if username not in db:
        db[username] = {
            "found_at": datetime.now().isoformat(),
            "found_by": str(user_id) if user_id else "unknown",
            "method": method,
            "verified": True
        }
        save_db(FREE_DB_FILE, db)
        return True
    return False

def is_in_taken_db(username: str) -> bool:
    return username in load_db(TAKEN_DB_FILE)

def is_in_banned_db(username: str) -> bool:
    return username in load_db(BANNED_DB_FILE)

def is_in_free_db(username: str) -> bool:
    return username in load_db(FREE_DB_FILE)

def log_debug(username: str, method: str, status: str, details=""):
    try:
        with open(DEBUG_LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f"\n{'='*80}\n")
            f.write(f"Time: {datetime.now().isoformat()}\n")
            f.write(f"Username: @{username}\n")
            f.write(f"Method: {method}\n")
            f.write(f"Status: {status}\n")
            if details:
                f.write(f"Details:\n{details[:1500]}\n")
            f.write(f"{'='*80}\n")
    except Exception:
        pass


# ============ НАСТРОЙКИ ПОЛЬЗОВАТЕЛЯ ============

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
    max_combinations = 15000
    
    combinator = (
        itertools.product(other_letters, repeat=remaining_count)
        if len(other_letters) < remaining_count
        else itertools.permutations(other_letters, remaining_count)
    )
    
    for others in combinator:
        for pos in range(remaining_count + 1):
            result = list(others[:pos]) + [main_letter] * repeat_count + list(others[pos:])
            all_usernames.add(''.join(result))
            if len(all_usernames) >= max_combinations:
                return list(all_usernames)
    
    return list(all_usernames)


# ============ HTTP СЕССИЯ ============

async def get_http_session() -> aiohttp.ClientSession:
    global http_session
    if http_session is None or http_session.closed:
        connector = aiohttp.TCPConnector(
            limit=CONNECTION_LIMIT,
            limit_per_host=30,
            ttl_dns_cache=300,
            enable_cleanup_closed=True
        )
        http_session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=15, connect=5)
        )
    return http_session


# ============ БЕЗОПАСНЫЕ ЗАПРОСЫ ============

async def safe_request_with_retry(func, *args, **kwargs):
    for attempt in range(MAX_RETRIES):
        try:
            return await func(*args, **kwargs)
        except TelegramRetryAfter as e:
            wait_time = e.retry_after + 1
            logging.warning(f"⏱ Rate limit! Жду {wait_time} сек...")
            await asyncio.sleep(wait_time)
        except TelegramAPIError as e:
            logging.error(f"❌ Telegram API error: {e}")
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(1)
            else:
                return None
        except aiohttp.ClientResponseError as e:
            if e.status == 429:
                wait_time = int(e.headers.get('Retry-After', 20))
                logging.warning(f"⏱ HTTP 429! Жду {wait_time} сек...")
                await asyncio.sleep(wait_time)
            elif e.status >= 500:
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(2)
                else:
                    return None
            else:
                return None
        except asyncio.TimeoutError:
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(1)
            else:
                return None
        except Exception as e:
            logging.error(f"❌ Unexpected error: {e}")
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(1)
            else:
                return None
    return None


# ============ ПРОВЕРКА НА БАН ============

async def is_username_banned(username: str) -> bool:
    if is_in_banned_db(username):
        return True
    
    session = await get_http_session()
    try:
        async with session.get(
            f"https://t.me/{username}",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            allow_redirects=True,
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            if resp.status == 200:
                html = (await resp.text()).lower()
                for indicator in BANNED_INDICATORS:
                    if indicator in html:
                        logging.warning(f"🚫 @{username} — ЗАБАНЕН")
                        return True
    except Exception as e:
        logging.error(f"Ban check error для @{username}: {e}")
    
    return False


# ============ BOT API ПРОВЕРКА ============

async def check_username_bot_api_fast(username: str) -> Optional[bool]:
    async def _check():
        session = await get_http_session()
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChat"
        params = {"chat_id": f"@{username}"}
        
        async with session.get(url, params=params) as response:
            data = await response.json()
            log_debug(username, "bot_api", f"Response: {data}")
            
            if data.get("ok") is True:
                logging.info(f"❌ Bot API: @{username} → ЗАНЯТ")
                return False
            
            error_code = data.get("error_code")
            error_desc = data.get("description", "").lower()
            
            if error_code == 400:
                if "chat not found" in error_desc or "username is not occupied" in error_desc or "username not occupied" in error_desc:
                    logging.info(f"✅ Bot API: @{username} → СВОБОДЕН")
                    return True
                if "username is occupied" in error_desc or "already occupied" in error_desc:
                    logging.info(f"❌ Bot API: @{username} → ЗАНЯТ")
                    return False
                if "deactivated" in error_desc or "deleted" in error_desc:
                    logging.info(f"🚫 Bot API: @{username} → ЗАБАНЕН")
                    return False
                if error_desc == "bad request" or len(error_desc) < 15:
                    logging.info(f"✅ Bot API: @{username} → СВОБОДЕН (generic)")
                    return True
                logging.warning(f"⚠️ Bot API: @{username} → НЕИЗВЕСТНО")
                return None
            elif error_code == 403:
                logging.info(f"❌ Bot API: @{username} → ЗАНЯТ (403)")
                return False
            elif error_code == 404:
                logging.info(f"✅ Bot API: @{username} → СВОБОДЕН (404)")
                return True
            else:
                logging.warning(f"⚠️ Bot API: @{username} → НЕИЗВЕСТНО ({error_code})")
                return None
    
    return await safe_request_with_retry(_check)


# ============ FRAGMENT ПРОВЕРКА ============

async def check_username_fragment_fast(username: str) -> Optional[bool]:
    async def _check():
        session = await get_http_session()
        url = f"https://fragment.com/username/{username}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "text/html,application/xhtml+xml",
        }
        
        async with session.get(url, headers=headers, allow_redirects=True) as response:
            status = response.status
            
            if status == 404:
                logging.info(f"✅ Fragment: @{username} → СВОБОДЕН (404)")
                return True
            
            if status == 200:
                html = await response.text()
                html_lower = html.lower()
                log_debug(username, "fragment", f"Status {status}", html[:2500])
                
                sale_indicators = [
                    (r'<(?:button|a)[^>]*(?:place.*bid|buy.*now|make.*offer)', "кнопка покупки"),
                    (r'tm-section-bid-button', "кнопка ставки"),
                    (r'tm-section-countdown', "таймер аукциона"),
                    (r'data-bid-ts', "данные аукциона"),
                    (r'table-cell-value[^>]*>\s*(?:TON|USD|\$|₽)\s*[\d,]+', "цена"),
                    (r'(?:highest|current|minimum|starting)\s+(?:bid|price)', "информация о ставках"),
                    (r'<div[^>]*tm-status[^>]*>.*(?:auction|for sale|on sale)', "статус продажи"),
                    (r'(?:sold for|purchase history|bought)', "история продаж"),
                    (r'owned\s+by', "владелец указан"),
                ]
                
                for pattern, desc in sale_indicators:
                    if re.search(pattern, html_lower):
                        logging.info(f"🛒 Fragment: @{username} → НА ПРОДАЖЕ/ЗАНЯТ ({desc})")
                        return False
                
                if len(html) < 3000:
                    logging.info(f"✅ Fragment: @{username} → СВОБОДЕН (мало контента)")
                    return True
                
                if not any(m in html_lower for m in ['auction', 'bid', 'price', 'sold', 'owner', 'ton', 'usd', '$', 'tm-username', 'username-link', 'table-cell']):
                    logging.info(f"✅ Fragment: @{username} → СВОБОДЕН (нет маркеров)")
                    return True
                
                logging.warning(f"⚠️ Fragment: @{username} → НЕОПРЕДЕЛЁННО")
                return None
            
            return None
    
    return await safe_request_with_retry(_check)


# ============ T.ME ПРОВЕРКА ============

async def check_username_web_fast(username: str) -> Optional[bool]:
    async def _check():
        session = await get_http_session()
        url = f"https://t.me/{username}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "text/html",
        }
        
        async with session.get(url, headers=headers, allow_redirects=True) as response:
            status = response.status
            
            if status == 404:
                logging.info(f"✅ t.me: @{username} → СВОБОДЕН (404)")
                return True
            
            if status == 200:
                html = await response.text()
                html_lower = html.lower()
                log_debug(username, "t.me", f"Status {status}", html[:3000])
                
                occupied_markers = ['tgme_page_photo', 'tgme_page_title', 'tgme_page_description', 'tgme_page_extra', 'tgme_page_action']
                
                if any(marker in html for marker in occupied_markers):
                    logging.info(f"❌ t.me: @{username} → ЗАНЯТ")
                    return False
                
                if any(ind in html_lower for ind in BANNED_INDICATORS):
                    logging.info(f"🚫 t.me: @{username} → ЗАБАНЕН")
                    return False
                
                free_indicators = ["if you have telegram, you can contact", "view and join", "preview channel"]
                
                if any(ind in html_lower for ind in free_indicators) and 'tgme_page_photo' not in html:
                    logging.info(f"✅ t.me: @{username} → СВОБОДЕН")
                    return True
                
                if len(html) < 2000:
                    logging.info(f"✅ t.me: @{username} → СВОБОДЕН (короткая)")
                    return True
                
                logging.warning(f"⚠️ t.me: @{username} → НЕОПРЕДЕЛЁННО")
                return None
            
            return None
    
    return await safe_request_with_retry(_check)


# ============ ГЛАВНАЯ ЛОГИКА ПРОВЕРКИ ============

async def check_username_parallel(username: str, user_id=None) -> bool:
    if is_in_free_db(username):
        return True
    if is_in_taken_db(username) or is_in_banned_db(username):
        return False
    
    async with RATE_LIMITER:
        await asyncio.sleep(CHECK_DELAY)
        
        logging.info(f"\n{'='*60}\n🔍 ПРОВЕРЯЮ: @{username}\n{'='*60}")
        
        if await is_username_banned(username):
            add_to_banned_db(username, user_id, "t.me_ban_check", "Обнаружены признаки бана")
            return False
        
        raw_results = await asyncio.gather(
            check_username_bot_api_fast(username),
            check_username_fragment_fast(username),
            check_username_web_fast(username),
            return_exceptions=True
        )
        
        bot_api_result, fragment_result, web_result = [
            None if isinstance(r, Exception) else r for r in raw_results
        ]
        
        logging.info(
            f"📊 РЕЗУЛЬТАТЫ @{username}:\n"
            f"   Bot API  = {bot_api_result}\n"
            f"   Fragment = {fragment_result}\n"
            f"   t.me     = {web_result}"
        )
        
        results_list = [bot_api_result, fragment_result, web_result]
        free_votes = sum(1 for v in results_list if v is True)
        taken_votes = sum(1 for v in results_list if v is False)
        
        # ОПТИМИСТИЧНАЯ ЛОГИКА
        
        if fragment_result is False and bot_api_result is False:
            add_to_taken_db(username, user_id, "fragment_and_botapi", "Оба подтверждают занятость")
            return False
        
        if taken_votes >= 2:
            add_to_taken_db(username, user_id, "majority_taken", f"{taken_votes} методов подтвердили")
            return False
        
        if bot_api_result is True or web_result is True:
            method = "bot_api" if bot_api_result is True else "web"
            add_to_free_db(username, user_id, f"{method}_confirmed")
            return True
        
        if fragment_result is True and bot_api_result is None and web_result is None:
            add_to_free_db(username, user_id, "fragment_optimistic")
            return True
        
        if fragment_result is False and bot_api_result is None and web_result is None:
            add_to_free_db(username, user_id, "ignore_fragment_false")
            return True
        
        if all(v is None for v in results_list):
            retry_result = await check_username_bot_api_fast(username)
            if retry_result is True:
                add_to_free_db(username, user_id, "bot_api_retry")
                return True
            elif retry_result is False:
                add_to_taken_db(username, user_id, "bot_api_retry", "Повторная проверка подтвердила")
                return False
            else:
                add_to_free_db(username, user_id, "all_unknown_optimistic")
                return True
        
        add_to_free_db(username, user_id, "fallback_optimistic")
        return True


# ============ БАТЧ-ПРОВЕРКА ============

async def check_usernames_batch(usernames: List[str], user_id=None) -> Dict[str, bool]:
    results = await asyncio.gather(
        *[check_username_parallel(u, user_id) for u in usernames],
        return_exceptions=True
    )
    return {
        u: (False if isinstance(r, Exception) else r)
        for u, r in zip(usernames, results)
    }


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


# ============ CALLBACK ОБРАБОТЧИКИ ============

@router.callback_query(lambda c: c.data == "gen_username")
async def generate_username_callback(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    settings = get_user_settings(user_id)
    
    msg = await callback.message.edit_text(
        "✨ <b>Оптимистичный поиск...</b>\n\n<i>Игнорирую ложные срабатывания</i>",
        parse_mode=ParseMode.HTML
    )
    
    max_attempts = 40
    found = False
    
    for i in range(0, max_attempts, BATCH_SIZE):
        batch = []
        for _ in range(min(BATCH_SIZE, max_attempts - i)):
            u = generate_username(settings)
            if not is_in_taken_db(u) and not is_in_free_db(u):
                batch.append(u)
        
        if not batch:
            continue
        
        if i > 0 and i % 16 == 0:
            await msg.edit_text(f"✨ Проверяю... {i}/{max_attempts}", parse_mode=ParseMode.HTML)
        
        results = await check_usernames_batch(batch, user_id)
        
        for username, is_free in results.items():
            if is_free:
                await msg.edit_text(
                    f"🎉 <b>НАЙДЕН!</b>\n\n✅ <code>@{username}</code>\n\n<i>Оптимистичная проверка</i>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="✅ Забрать", url=f"https://t.me/{username}")],
                        [InlineKeyboardButton(text="🔎 Fragment", url=f"https://fragment.com/username/{username}")],
                        [InlineKeyboardButton(text="🔄 Ещё", callback_data="gen_username"), InlineKeyboardButton(text="⬅️ Назад", callback_data="username_menu")],
                    ])
                )
                found = True
                break
        if found:
            break
    
    if not found:
        await msg.edit_text(
            f"😔 Не найдено за {max_attempts} попыток\n\n<i>Попробуй изменить настройки</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Снова", callback_data="gen_username")],
                [InlineKeyboardButton(text="⚙️ Настройки", callback_data="open_settings")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="username_menu")],
            ])
        )

@router.callback_query(lambda c: c.data == "check_all")
async def check_all_callback(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    settings = get_user_settings(user_id)
    all_usernames = get_all_possible_usernames(settings)
    total = len(all_usernames)
    
    if total == 0:
        await callback.message.answer("❌ Нет комбинаций")
        return
    
    estimated_minutes = max(1, int((total * CHECK_DELAY / BATCH_SIZE) / 60))
    
    await callback.message.edit_text(
        f"⚡️ <b>МАССОВАЯ ПРОВЕРКА</b>\n\n"
        f"Комбинаций: {total}\n"
        f"Примерное время: ~{estimated_minutes} мин\n\n"
        f"✨ Оптимистичный режим\n"
        f"🚀 Быстрая проверка (0.3 сек)\n\n"
        f"Продолжить?",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Начать", callback_data="confirm_check_all"), InlineKeyboardButton(text="❌ Отмена", callback_data="username_menu")],
        ])
    )

@router.callback_query(lambda c: c.data == "confirm_check_all")
async def confirm_check_all_callback(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    settings = get_user_settings(user_id)
    all_usernames = get_all_possible_usernames(settings)
    await perform_mass_check(callback.message, user_id, all_usernames)

async def perform_mass_check(message: types.Message, user_id: int, all_usernames: List[str]):
    total = len(all_usernames)
    await message.edit_text(f"⚡️ <b>СТАРТ!</b>\n\nВсего: {total}", parse_mode=ParseMode.HTML)
    
    checked = 0
    found_free: List[str] = []
    last_update = datetime.now()
    start_time = datetime.now()
    
    for i in range(0, total, BATCH_SIZE):
        batch = all_usernames[i:i + BATCH_SIZE]
        to_check = []
        
        for username in batch:
            if is_in_taken_db(username):
                checked += 1
                continue
            if is_in_free_db(username):
                found_free.append(username)
                checked += 1
                continue
            to_check.append(username)
        
        if to_check:
            results = await check_usernames_batch(to_check, user_id)
            for username, is_free in results.items():
                if is_free:
                    found_free.append(username)
                    try:
                        await message.bot.send_message(
                            user_id,
                            f"🎉 <b>#{len(found_free)}!</b>\n\n✅ <code>@{username}</code>",
                            parse_mode=ParseMode.HTML,
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="✅ Забрать", url=f"https://t.me/{username}")],
                                [InlineKeyboardButton(text="🔎 Fragment", url=f"https://fragment.com/username/{username}")],
                            ])
                        )
                    except Exception as e:
                        logging.error(f"Ошибка отправки уведомления: {e}")
                checked += 1
        
        if (datetime.now() - last_update).total_seconds() >= 20:
            await message.edit_text(
                f"⚡️ {checked}/{total} ({checked/total*100:.1f}%)\n"
                f"✅ Найдено: {len(found_free)}",
                parse_mode=ParseMode.HTML
            )
            last_update = datetime.now()
    
    elapsed = int((datetime.now() - start_time).total_seconds() / 60)
    
    if found_free:
        samples = "\n".join(f"• <code>@{u}</code>" for u in found_free[:25])
        if len(found_free) > 25:
            samples += f"\n... +{len(found_free) - 25}"
        text = (
            f"✅ <b>ГОТОВО!</b>\n\n"
            f"Проверено: {checked}\n"
            f"Найдено: {len(found_free)}\n"
            f"Время: {elapsed} мин\n\n"
            f"{samples}"
        )
    else:
        text = f"😔 Не найдено\n\nПроверено: {checked}\nВремя: {elapsed} мин"
    
    await message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📥 Скачать БД", callback_data="get_db")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="username_menu")]
        ])
    )

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
    
    methods: Dict[str, int] = {}
    for data in free.values():
        m = data.get("method", "unknown")
        methods[m] = methods.get(m, 0) + 1
    
    methods_text = "\n".join(f"  • {m}: {c}" for m, c in methods.items()) or "  (нет)"
    
    await callback.message.edit_text(
        f"📊 <b>Статистика</b>\n\n"
        f"✅ Свободных: {len(free)}\n{methods_text}\n\n"
        f"❌ Занятых: {len(taken)}\n"
        f"🚫 Забаненных: {len(banned)}\n\n"
        f"✨ Оптимистичный режим",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📥 Скачать БД", callback_data="get_db")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="username_menu")],
        ])
    )

@router.callback_query(lambda c: c.data == "get_db")
async def get_db_callback(callback: types.CallbackQuery):
    await callback.answer()
    await get_db_command(callback.message)

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
    user_settings[callback.from_user.id] = {
        "letter": "s", "repeat_count": 2, "use_full_alphabet": True
    }
    await callback.answer("✅ Сброшено")
    await open_settings_callback(callback)


# ============ КОМАНДЫ ============

@router.message(Command("getdb"))
async def get_db_command(message: types.Message):
    taken_db = load_db(TAKEN_DB_FILE)
    free_db = load_db(FREE_DB_FILE)
    banned_db = load_db(BANNED_DB_FILE)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    taken_file = f"taken_{message.from_user.id}_{ts}.json"
    free_file = f"free_{message.from_user.id}_{ts}.json"
    banned_file = f"banned_{message.from_user.id}_{ts}.json"
    
    try:
        for path, data in ((taken_file, taken_db), (free_file, free_db), (banned_file, banned_db)):
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        
        await message.answer(
            f"📊 Занятых: {len(taken_db)}, Свободных: {len(free_db)}, Забаненных: {len(banned_db)}"
        )
        await message.answer_document(types.FSInputFile(taken_file), caption=f"📁 Занятые ({len(taken_db)})")
        await message.answer_document(types.FSInputFile(free_file), caption=f"✅ Свободные ({len(free_db)})")
        await message.answer_document(types.FSInputFile(banned_file), caption=f"🚫 Забаненные ({len(banned_db)})")
        
        if os.path.exists(DEBUG_LOG_FILE):
            await message.answer_document(types.FSInputFile(DEBUG_LOG_FILE), caption="🔍 Debug log")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
    finally:
        for p in (taken_file, free_file, banned_file):
            if os.path.exists(p):
                os.remove(p)


# ============ ИНИЦИАЛИЗАЦИЯ ============

def init_username_bot(dp):
    global username_router_initialized
    if not username_router_initialized:
        dp.include_router(router)
        username_router_initialized = True
        logging.info("✅ Модуль юзернеймов инициализирован")


# ============ ЭКСПОРТ ============

__all__ = [
    'router',
    'init_username_bot',
    'check_username_parallel',
    'check_usernames_batch',
    'get_user_settings',
    'generate_username',
    'generate_examples',
    'get_all_possible_usernames',
    'load_db',
    'save_db',
    'add_to_free_db',
    'add_to_taken_db',
    'add_to_banned_db',
    'is_in_free_db',
    'is_in_taken_db',
    'is_in_banned_db',
    'TAKEN_DB_FILE',
    'FREE_DB_FILE',
    'BANNED_DB_FILE'
            ]
