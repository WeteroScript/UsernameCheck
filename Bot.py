import logging
import random
import string
import aiohttp
import os
import json
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramRetryAfter, TelegramAPIError
from datetime import datetime
import itertools
from asyncio import Semaphore
import re
from typing import Optional, Dict, List

# ============ НАСТРОЙКА ЛОГИРОВАНИЯ ============

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# ============ КОНФИГУРАЦИЯ ============

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

RATE_LIMITER = Semaphore(5)
CHECK_DELAY = 0.5
BATCH_SIZE = 5
CONNECTION_LIMIT = 50
MAX_RETRIES = 3

http_session: Optional[aiohttp.ClientSession] = None

TAKEN_DB_FILE = "taken_usernames.json"
FREE_DB_FILE = "free_usernames.json"
DEBUG_LOG_FILE = "debug_checks.log"

user_settings = {}

# Признаки забаненного/деактивированного аккаунта
BANNED_INDICATORS = [
    "deactivated",
    "user is deactivated",
    "account deleted",
    "this account was banned",
    "account was terminated",
    "this account is banned",
]

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
                f.write(f"Details:\n{details[:1000]}\n")
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

# ============ ГЕНЕРАЦИЯ ЮЗЕРНЕЙМОВ ============

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
            limit_per_host=20,
            ttl_dns_cache=300,
            enable_cleanup_closed=True
        )
        http_session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=20, connect=5)
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
                await asyncio.sleep(2 ** attempt)
            else:
                return None
        except aiohttp.ClientResponseError as e:
            if e.status == 429:
                wait_time = int(e.headers.get('Retry-After', 30))
                logging.warning(f"⏱ HTTP 429! Жду {wait_time} сек...")
                await asyncio.sleep(wait_time)
            elif e.status >= 500:
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(3)
                else:
                    return None
            else:
                logging.error(f"❌ HTTP {e.status}: {e.message}")
                return None
        except asyncio.TimeoutError:
            logging.warning(f"⏱ Timeout (попытка {attempt + 1}/{MAX_RETRIES})")
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(2)
            else:
                return None
        except Exception as e:
            logging.error(f"❌ Unexpected error: {e}")
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(2)
            else:
                return None
    return None

# ============ ПРОВЕРКА НА БАН ============

async def is_username_banned(username: str) -> bool:
    """
    Явная проверка аккаунта на бан/деактивацию через t.me
    """
    session = await get_http_session()
    try:
        async with session.get(
            f"https://t.me/{username}",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            allow_redirects=True
        ) as resp:
            if resp.status == 200:
                html = (await resp.text()).lower()
                if any(indicator in html for indicator in BANNED_INDICATORS):
                    logging.warning(f"🚫 @{username} — ЗАБАНЕН (признаки на t.me)")
                    return True
    except Exception as e:
        logging.error(f"Ban check error для @{username}: {e}")
    return False

# ============ BOT API ПРОВЕРКА (ИСПРАВЛЕНА) ============

async def check_username_bot_api_fast(username: str) -> Optional[bool]:
    """
    Проверка через Bot API.

    ИСПРАВЛЕНО:
    - error_code 400 без чёткого описания → None (не доверяем)
    - 'deactivated' → False (забанен)
    - 'forbidden' / 403 → False (чат существует, но приватный)
    """
    async def _check():
        session = await get_http_session()
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChat"
        params = {"chat_id": f"@{username}"}

        async with session.get(url, params=params) as response:
            data = await response.json()
            log_debug(username, "bot_api", f"Response: {data}")

            # Чат найден → занят
            if data.get("ok") is True:
                logging.info(f"❌ Bot API: @{username} → ЗАНЯТ (чат существует)")
                return False

            error_code = data.get("error_code")
            error_desc = data.get("description", "").lower()

            if error_code == 400:
                if "chat not found" in error_desc:
                    # Реально свободен
                    logging.info(f"✅ Bot API: @{username} → СВОБОДЕН (chat not found)")
                    return True

                elif "username not occupied" in error_desc:
                    logging.info(f"✅ Bot API: @{username} → СВОБОДЕН (not occupied)")
                    return True

                elif "username is occupied" in error_desc:
                    logging.info(f"❌ Bot API: @{username} → ЗАНЯТ (occupied)")
                    return False

                elif "deactivated" in error_desc:
                    # ИСПРАВЛЕНИЕ: забаненный/деактивированный аккаунт
                    # Ранее возвращал True — теперь False
                    logging.info(f"🚫 Bot API: @{username} → ЗАБАНЕН (deactivated)")
                    return False

                else:
                    # Неизвестная 400 ошибка — не доверяем
                    # ИСПРАВЛЕНИЕ: ранее возвращал True — теперь None
                    logging.warning(f"⚠️ Bot API: @{username} → НЕИЗВЕСТНО (400: {error_desc})")
                    return None

            elif error_code == 403:
                # Forbidden — приватный чат, но он существует
                logging.info(f"❌ Bot API: @{username} → ЗАНЯТ (private/forbidden)")
                return False

            else:
                logging.warning(f"⚠️ Bot API: @{username} → НЕИЗВЕСТНО ({error_code}: {error_desc})")
                return None

    return await safe_request_with_retry(_check)

# ============ FRAGMENT ПРОВЕРКА ============

async def check_username_fragment_fast(username: str) -> Optional[bool]:
    """
    Проверка через Fragment.
    False = на продаже/аукционе или занят.
    True  = нет признаков продажи/занятости.
    None  = неопределённо.
    """
    async def _check():
        session = await get_http_session()
        url = f"https://fragment.com/username/{username}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
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
                log_debug(username, "fragment", f"Status {status}", html[:2000])

                # Признаки продажи / аукциона
                if re.search(r'<(?:button|a)[^>]*(?:place.*bid|buy.*now|make.*offer)', html_lower):
                    logging.info(f"🛒 Fragment: @{username} → НА ПРОДАЖЕ (кнопка)")
                    return False

                if 'tm-section-bid-button' in html:
                    logging.info(f"🛒 Fragment: @{username} → НА АУКЦИОНЕ (bid button)")
                    return False

                if 'tm-section-countdown' in html or 'data-bid-ts' in html:
                    logging.info(f"🛒 Fragment: @{username} → НА АУКЦИОНЕ (countdown)")
                    return False

                if re.search(r'table-cell-value[^>]*>\s*(?:TON|USD|\$|₽)\s*[\d,]+', html):
                    logging.info(f"🛒 Fragment: @{username} → НА ПРОДАЖЕ (цена)")
                    return False

                if re.search(r'(?:highest|current|minimum|starting)\s+(?:bid|price)', html_lower):
                    logging.info(f"🛒 Fragment: @{username} → НА АУКЦИОНЕ (bid info)")
                    return False

                if re.search(r'<div[^>]*tm-status[^>]*>.*(?:auction|for sale|on sale)', html_lower):
                    logging.info(f"🛒 Fragment: @{username} → НА ПРОДАЖЕ (status)")
                    return False

                if re.search(r'(?:sold for|purchase|bought)', html_lower):
                    logging.info(f"❌ Fragment: @{username} → ПРОДАН")
                    return False

                if re.search(r'owned\s+by', html_lower):
                    logging.info(f"❌ Fragment: @{username} → ЗАНЯТ (owner)")
                    return False

                # Нет признаков занятости
                if len(html) < 5000 and 'tm-username' not in html:
                    logging.info(f"✅ Fragment: @{username} → СВОБОДЕН (мин. контент)")
                    return True

                if not any(m in html_lower for m in ['auction', 'bid', 'price', 'sold', 'owner', 'ton', 'usd', '$']):
                    logging.info(f"✅ Fragment: @{username} → СВОБОДЕН (нет признаков)")
                    return True

                logging.warning(f"⚠️ Fragment: @{username} → НЕОПРЕДЕЛЁННО")
                return None

            return None

    return await safe_request_with_retry(_check)

# ============ T.ME ПРОВЕРКА ============

async def check_username_web_fast(username: str) -> Optional[bool]:
    """
    Проверка через t.me.
    """
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
                html_short = html[:3000]
                log_debug(username, "t.me", f"Status {status}", html_short)

                # Признаки занятости
                if any(m in html_short for m in [
                    'tgme_page_photo',
                    'tgme_page_title',
                    'tgme_page_description',
                    'tgme_page_extra'
                ]):
                    logging.info(f"❌ t.me: @{username} → ЗАНЯТ (профиль)")
                    return False

                # Признаки бана — тоже занят
                if any(ind in html_short.lower() for ind in BANNED_INDICATORS):
                    logging.info(f"🚫 t.me: @{username} → ЗАБАНЕН")
                    return False

                # Пустая страница
                if "if you have" in html_short.lower() and "telegram" in html_short.lower():
                    logging.info(f"✅ t.me: @{username} → СВОБОДЕН (пустая)")
                    return True

                logging.warning(f"⚠️ t.me: @{username} → НЕОПРЕДЕЛЁННО")
                return None

            return None

    return await safe_request_with_retry(_check)

# ============ ГЛАВНАЯ ЛОГИКА ПРОВЕРКИ (ИСПРАВЛЕНА) ============

async def check_username_parallel(username: str, user_id=None) -> bool:
    """
    ИСПРАВЛЕННАЯ ЛОГИКА:

    ❌ ЗАНЯТ если:
       - is_username_banned() → True (аккаунт забанен)
       - Хотя бы ОДИН метод говорит False
       - Все методы вернули None (нет данных)
       - Только один ненадёжный метод говорит True

    ✅ СВОБОДЕН если:
       - Минимум ДВА метода говорят True
       - Нет ни одного False
    """

    # Кэш
    if is_in_free_db(username):
        return True
    if is_in_taken_db(username):
        return False

    async with RATE_LIMITER:
        await asyncio.sleep(CHECK_DELAY)

        logging.info(f"\n{'='*60}\n🔍 ПРОВЕРЯЮ: @{username}\n{'='*60}")

        # ── Шаг 1: Явная проверка на бан ──────────────────────────
        if await is_username_banned(username):
            add_to_taken_db(username, user_id, "banned", "Аккаунт забанен/деактивирован")
            logging.info(f"🔴 ИТОГ: @{username} ЗАБАНЕН ❌")
            return False

        # ── Шаг 2: Параллельная проверка тремя методами ───────────
        raw_results = await asyncio.gather(
            check_username_bot_api_fast(username),
            check_username_fragment_fast(username),
            check_username_web_fast(username),
            return_exceptions=True
        )

        bot_api_result, fragment_result, web_result = [
            None if isinstance(r, Exception) else r
            for r in raw_results
        ]

        logging.info(
            f"📊 РЕЗУЛЬТАТЫ @{username}: "
            f"BotAPI={bot_api_result} | "
            f"Fragment={fragment_result} | "
            f"t.me={web_result}"
        )

        results_list = [bot_api_result, fragment_result, web_result]
        free_votes  = sum(1 for v in results_list if v is True)
        taken_votes = sum(1 for v in results_list if v is False)

        # ── Правило 1: Любой метод говорит ЗАНЯТ → ЗАНЯТ ──────────
        if taken_votes >= 1:
            add_to_taken_db(
                username, user_id, "any_taken",
                f"BotAPI={bot_api_result}, Fragment={fragment_result}, tme={web_result}"
            )
            logging.info(f"🔴 ИТОГ: @{username} ЗАНЯТ ❌ (taken_votes={taken_votes})")
            return False

        # ── Правило 2: Все None → нет данных → считаем ЗАНЯТЫМ ────
        if all(v is None for v in results_list):
            add_to_taken_db(username, user_id, "all_unknown", "Нет данных ни от одного метода")
            logging.warning(f"🔴 ИТОГ: @{username} ЗАНЯТ ❌ (нет данных)")
            return False

        # ── Правило 3: Только Bot API говорит True → ненадёжно ────
        if free_votes == 1 and bot_api_result is True and fragment_result is None and web_result is None:
            add_to_taken_db(
                username, user_id, "only_botapi_unreliable",
                "Только Bot API подтвердил — может быть забанен"
            )
            logging.warning(f"🔴 ИТОГ: @{username} ЗАНЯТ ❌ (только Bot API, ненадёжно)")
            return False

        # ── Правило 4: Минимум 2 подтверждения → СВОБОДЕН ─────────
        if free_votes >= 2:
            add_to_free_db(username, user_id, f"confirmed_{free_votes}")
            logging.info(f"🟢 ИТОГ: @{username} СВОБОДЕН ✅ ({free_votes} подтверждения)")
            return True

        # ── Правило 5: Ровно 1 True, но не только Bot API ─────────
        if free_votes == 1:
            # t.me + Fragment неопределены → недостаточно
            add_to_taken_db(username, user_id, "single_confirm", "Только 1 метод подтвердил")
            logging.info(f"🔴 ИТОГ: @{username} ЗАНЯТ ❌ (недостаточно подтверждений)")
            return False

        # ── Fallback ───────────────────────────────────────────────
        add_to_taken_db(username, user_id, "fallback", "Неизвестная ситуация")
        logging.warning(f"🔴 ИТОГ: @{username} ЗАНЯТ ❌ (fallback)")
        return False

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

# ============ БЕЗОПАСНАЯ ОТПРАВКА ============

async def safe_send_message(chat_id: int, text: str, **kwargs):
    try:
        return await bot.send_message(chat_id, text, **kwargs)
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
        return await bot.send_message(chat_id, text, **kwargs)
    except Exception as e:
        logging.error(f"❌ Ошибка отправки: {e}")
        return None

async def safe_edit_message(message: types.Message, text: str, **kwargs):
    try:
        return await message.edit_text(text, **kwargs)
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
        return await message.edit_text(text, **kwargs)
    except Exception as e:
        logging.error(f"❌ Ошибка редактирования: {e}")
        return None

# ============ КЛАВИАТУРЫ ============

def get_settings_keyboard(user_id: int) -> InlineKeyboardMarkup:
    s = get_user_settings(user_id)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{'✅' if s['use_full_alphabet'] else '❌'} Все буквы",
                              callback_data="toggle_alphabet")],
        [InlineKeyboardButton(text=f"🔤 Буква: {s['letter'].upper()}", callback_data="change_letter")],
        [InlineKeyboardButton(text=f"🔢 Повторений: {s['repeat_count']}", callback_data="change_count")],
        [InlineKeyboardButton(text="🔄 Сбросить", callback_data="reset_settings")],
        [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")],
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
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def get_count_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="2", callback_data="set_count_2"),
            InlineKeyboardButton(text="3", callback_data="set_count_3"),
            InlineKeyboardButton(text="4", callback_data="set_count_4"),
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_settings")],
    ])

def get_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✨ Генерировать", callback_data="generate_username")],
        [InlineKeyboardButton(text="🔍 Проверить все", callback_data="check_all")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="open_settings")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="show_stats")],
    ])

# ============ КОМАНДЫ ============

@dp.message(Command("start"))
async def start_command(message: types.Message):
    user_id = message.from_user.id
    get_user_settings(user_id)

    for f in (TAKEN_DB_FILE, FREE_DB_FILE):
        if not os.path.exists(f):
            save_db(f, {})

    await safe_send_message(
        message.chat.id,
        f"Привет, {message.from_user.first_name or 'Пользователь'}! 👋\n\n"
        f"🎯 <b>Поиск СВОБОДНЫХ юзернеймов</b>\n\n"
        f"🛡 <b>Защита от забаненных:</b>\n"
        f"✅ Проверка на бан/деактивацию\n"
        f"✅ Требует 2+ подтверждения свободности\n"
        f"✅ 3 метода: Bot API, Fragment, t.me\n"
        f"✅ Защита от rate limit\n\n"
        f"Выбери действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard()
    )

@dp.message(Command("check"))
async def check_command(message: types.Message):
    try:
        username = message.text.split()[1].replace("@", "")
    except IndexError:
        await safe_send_message(message.chat.id, "Использование: /check username")
        return

    msg = await safe_send_message(message.chat.id, f"🔍 Проверяю @{username}...")
    is_free = await check_username_parallel(username, message.from_user.id)

    if is_free:
        await safe_edit_message(
            msg,
            f"✅ <b>@{username} СВОБОДЕН!</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Забрать", url=f"https://t.me/{username}")],
                [InlineKeyboardButton(text="🔎 Fragment", url=f"https://fragment.com/username/{username}")],
            ])
        )
    else:
        await safe_edit_message(
            msg,
            f"❌ <b>@{username} ЗАНЯТ или ЗАБАНЕН</b>",
            parse_mode=ParseMode.HTML
        )

@dp.message(Command("getdb"))
async def get_db_command(message: types.Message):
    taken_db = load_db(TAKEN_DB_FILE)
    free_db  = load_db(FREE_DB_FILE)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    taken_file = f"taken_{message.from_user.id}_{ts}.json"
    free_file  = f"free_{message.from_user.id}_{ts}.json"

    try:
        for path, data in ((taken_file, taken_db), (free_file, free_db)):
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

        await safe_send_message(
            message.chat.id,
            f"📊 Занятых: {len(taken_db)}, Свободных: {len(free_db)}"
        )
        await message.answer_document(types.FSInputFile(taken_file), caption=f"📁 Занятые ({len(taken_db)})")
        await message.answer_document(types.FSInputFile(free_file),  caption=f"✅ Свободные ({len(free_db)})")

        if os.path.exists(DEBUG_LOG_FILE):
            await message.answer_document(types.FSInputFile(DEBUG_LOG_FILE), caption="🔍 Debug log")
    except Exception as e:
        await safe_send_message(message.chat.id, f"❌ Ошибка: {e}")
    finally:
        for p in (taken_file, free_file):
            if os.path.exists(p):
                os.remove(p)

# ============ CALLBACK HANDLERS ============

@dp.callback_query(lambda c: c.data == "main_menu")
async def main_menu(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await safe_edit_message(
        callback_query.message,
        "🏠 <b>Главное меню</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard()
    )

@dp.callback_query(lambda c: c.data == "open_settings")
async def open_settings(callback_query: types.CallbackQuery):
    await callback_query.answer()
    user_id = callback_query.from_user.id
    settings = get_user_settings(user_id)
    examples = "\n".join(f"• <code>{e}</code>" for e in generate_examples(settings, 4))

    await safe_edit_message(
        callback_query.message,
        f"⚙️ <b>Настройки</b>\n\n"
        f"📌 Буква: <b>{settings['letter'].upper()}</b>\n"
        f"📌 Повторений: <b>{settings['repeat_count']}</b>\n\n"
        f"📝 Примеры:\n{examples}",
        parse_mode=ParseMode.HTML,
        reply_markup=get_settings_keyboard(user_id)
    )

@dp.callback_query(lambda c: c.data == "back_to_settings")
async def back_to_settings(callback_query: types.CallbackQuery):
    await open_settings(callback_query)

@dp.callback_query(lambda c: c.data == "toggle_alphabet")
async def toggle_alphabet(callback_query: types.CallbackQuery):
    s = get_user_settings(callback_query.from_user.id)
    s["use_full_alphabet"] = not s["use_full_alphabet"]
    await callback_query.answer("✅ Изменено")
    await open_settings(callback_query)

@dp.callback_query(lambda c: c.data == "change_letter")
async def change_letter(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await safe_edit_message(callback_query.message, "🔤 Выбери букву:", reply_markup=get_letter_keyboard())

@dp.callback_query(lambda c: c.data.startswith("set_letter_"))
async def set_letter(callback_query: types.CallbackQuery):
    letter = callback_query.data.replace("set_letter_", "")
    get_user_settings(callback_query.from_user.id)["letter"] = letter
    await callback_query.answer(f"✅ Буква: {letter.upper()}")
    await open_settings(callback_query)

@dp.callback_query(lambda c: c.data == "change_count")
async def change_count(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await safe_edit_message(callback_query.message, "🔢 Количество:", reply_markup=get_count_keyboard())

@dp.callback_query(lambda c: c.data.startswith("set_count_"))
async def set_count(callback_query: types.CallbackQuery):
    count = int(callback_query.data.replace("set_count_", ""))
    get_user_settings(callback_query.from_user.id)["repeat_count"] = count
    await callback_query.answer(f"✅ Повторений: {count}")
    await open_settings(callback_query)

@dp.callback_query(lambda c: c.data == "reset_settings")
async def reset_settings(callback_query: types.CallbackQuery):
    user_settings[callback_query.from_user.id] = {
        "letter": "s", "repeat_count": 2, "use_full_alphabet": True
    }
    await callback_query.answer("✅ Сброшено")
    await open_settings(callback_query)

@dp.callback_query(lambda c: c.data == "show_stats")
async def show_stats(callback_query: types.CallbackQuery):
    await callback_query.answer()
    taken_db = load_db(TAKEN_DB_FILE)
    free_db  = load_db(FREE_DB_FILE)

    methods: Dict[str, int] = {}
    for data in free_db.values():
        m = data.get("method", "unknown")
        methods[m] = methods.get(m, 0) + 1

    methods_text = "\n".join(f"  • {m}: {c}" for m, c in methods.items()) or "  (нет)"

    await safe_edit_message(
        callback_query.message,
        f"📊 <b>Статистика</b>\n\n"
        f"✅ Свободных: {len(free_db)}\n{methods_text}\n\n"
        f"❌ Занятых/забаненных: {len(taken_db)}\n\n"
        f"🛡 Защита от бана активна!",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📥 Скачать БД", callback_data="get_db")],
            [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")],
        ])
    )

@dp.callback_query(lambda c: c.data == "get_db")
async def get_db_callback(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await get_db_command(callback_query.message)

@dp.callback_query(lambda c: c.data == "generate_username")
async def process_generate_username(callback_query: types.CallbackQuery):
    await callback_query.answer()
    user_id  = callback_query.from_user.id
    settings = get_user_settings(user_id)

    waiting_msg = await safe_edit_message(
        callback_query.message,
        "🔍 <b>Ищу свободный юзернейм...</b>\n\n<i>Проверка на бан включена</i>",
        parse_mode=ParseMode.HTML
    )

    max_attempts = 30
    found = False

    for i in range(0, max_attempts, BATCH_SIZE):
        batch = []
        for _ in range(min(BATCH_SIZE, max_attempts - i)):
            u = generate_username(settings)
            if not is_in_taken_db(u) and not is_in_free_db(u):
                batch.append(u)

        if not batch:
            continue

        if i > 0 and i % 10 == 0:
            await safe_edit_message(
                waiting_msg,
                f"🔍 Проверяю... {i}/{max_attempts}",
                parse_mode=ParseMode.HTML
            )

        results = await check_usernames_batch(batch, user_id)

        for username, is_free in results.items():
            if is_free:
                await safe_edit_message(
                    waiting_msg,
                    f"🎉 <b>НАЙДЕН!</b>\n\n✅ <code>@{username}</code>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="✅ Забрать", url=f"https://t.me/{username}")],
                        [InlineKeyboardButton(text="🔎 Fragment", url=f"https://fragment.com/username/{username}")],
                        [
                            InlineKeyboardButton(text="🔄 Ещё", callback_data="generate_username"),
                            InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu"),
                        ],
                    ])
                )
                found = True
                break

        if found:
            break

    if not found:
        await safe_edit_message(
            waiting_msg,
            f"😔 Не найдено за {max_attempts} попыток",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Снова", callback_data="generate_username")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")],
            ])
        )

@dp.callback_query(lambda c: c.data == "check_all")
async def check_all_combinations(callback_query: types.CallbackQuery):
    await callback_query.answer()
    user_id  = callback_query.from_user.id
    settings = get_user_settings(user_id)
    all_usernames = get_all_possible_usernames(settings)
    total = len(all_usernames)

    if total == 0:
        await safe_send_message(callback_query.message.chat.id, "❌ Нет комбинаций")
        return

    estimated_minutes = max(1, int((total * CHECK_DELAY / BATCH_SIZE) / 60))

    await safe_edit_message(
        callback_query.message,
        f"⚡️ <b>МАССОВАЯ ПРОВЕРКА</b>\n\n"
        f"Комбинаций: {total}\n"
        f"Примерное время: ~{estimated_minutes} мин\n\n"
        f"🛡 Проверка на бан включена\n\n"
        f"Продолжить?",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Начать", callback_data="confirm_check_all"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="main_menu"),
            ]
        ])
    )

@dp.callback_query(lambda c: c.data == "confirm_check_all")
async def confirm_check_all(callback_query: types.CallbackQuery):
    await callback_query.answer()
    user_id  = callback_query.from_user.id
    settings = get_user_settings(user_id)
    all_usernames = get_all_possible_usernames(settings)
    await perform_mass_check(callback_query.message, user_id, all_usernames)

async def perform_mass_check(message: types.Message, user_id: int, all_usernames: List[str]):
    total = len(all_usernames)
    await safe_edit_message(message, f"⚡️ <b>СТАРТ!</b>\n\nВсего: {total}", parse_mode=ParseMode.HTML)

    checked    = 0
    found_free: List[str] = []
    last_update = datetime.now()
    start_time  = datetime.now()

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
                    await safe_send_message(
                        user_id,
                        f"🎉 <b>#{len(found_free)}!</b>\n\n✅ <code>@{username}</code>",
                        parse_mode=ParseMode.HTML,
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="✅ Забрать", url=f"https://t.me/{username}")],
                            [InlineKeyboardButton(text="🔎 Fragment", url=f"https://fragment.com/username/{username}")],
                        ])
                    )
                checked += 1

        if (datetime.now() - last_update).total_seconds() >= 15:
            await safe_edit_message(
                message,
                f"⚡️ {checked}/{total} ({checked/total*100:.1f}%)\n"
                f"✅ Найдено: {len(found_free)}",
                parse_mode=ParseMode.HTML
            )
            last_update = datetime.now()

    elapsed = int((datetime.now() - start_time).total_seconds() / 60)

    if found_free:
        samples = "\n".join(f"• <code>@{u}</code>" for u in found_free[:20])
        if len(found_free) > 20:
            samples += f"\n... +{len(found_free) - 20}"
        text = (
            f"✅ <b>ГОТОВО!</b>\n\n"
            f"Проверено: {checked}\n"
            f"Найдено: {len(found_free)}\n"
            f"Время: {elapsed} мин\n\n"
            f"{samples}"
        )
    else:
        text = f"😔 Не найдено\n\nПроверено: {checked}\nВремя: {elapsed} мин"

    await safe_edit_message(
        message, text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🏠 Меню", callback_data="main_menu")]
        ])
    )

# ============ ЗАПУСК ============

async def on_startup():
    for f in (TAKEN_DB_FILE, FREE_DB_FILE):
        if not os.path.exists(f):
            save_db(f, {})
    logging.info("🚀 Бот запущен!")
    logging.info("🛡 Защита от забаненных юзернеймов активна")
    logging.info("📋 Требуется 2+ подтверждения для признания юзернейма свободным")

async def on_shutdown():
    global http_session
    if http_session and not http_session.closed:
        await http_session.close()
    logging.info("⛔ Бот остановлен")

async def main():
    await on_startup()
    try:
        await dp.start_polling(bot)
    finally:
        await on_shutdown()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("⛔ Остановлено")
