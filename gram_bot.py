"""
Модуль для Gram ботов - с фиксом database is locked (Telethon)
"""

import asyncio
import re
import random
import logging
import os
import sqlite3
from typing import Optional, Dict, Any, Tuple, List
from telethon import TelegramClient, errors
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from aiogram import Router, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode

router = Router()

# ============ КОНФИГ ============

API_ID = 2040
API_HASH = "b18441a1ff607e10a989891a5462e627"

active_clients: Dict[str, TelegramClient] = {}
active_tasks: Dict[str, asyncio.Task] = {}
gram_bot_initialized = False
user_chat_id: Optional[int] = None
bot_username_for_task: Dict[str, str] = {}
bot_instance = None

captcha_storage: Dict[int, Dict[str, Any]] = {}
AUTO_SUBSCRIBE = True
user_task_choice: Dict[int, str] = {}

SUBSCRIBE_DELAY = 60

# Локи для доступа к файлам сессии (фикс "database is locked")
session_locks: Dict[str, asyncio.Lock] = {}


def get_session_lock(phone: str) -> asyncio.Lock:
    if phone not in session_locks:
        session_locks[phone] = asyncio.Lock()
    return session_locks[phone]


# ============ УСТАНОВКА ============

def set_bot_instance(bot):
    global bot_instance
    bot_instance = bot

def set_user_chat_id(chat_id: int):
    global user_chat_id
    user_chat_id = chat_id


# ============ КНОПКИ ============

def get_button_text(btn) -> str:
    try:
        return btn.text if hasattr(btn, 'text') else str(btn)
    except:
        return "Неизвестно"

def get_button_url(btn) -> Optional[str]:
    try:
        if hasattr(btn, "url") and btn.url:
            return btn.url
        if hasattr(btn, "button") and hasattr(btn.button, "url"):
            return btn.button.url
        return None
    except:
        return None

def is_url_button(btn) -> bool:
    try:
        if hasattr(btn, "url"):
            return btn.url is not None
        if hasattr(btn, "button"):
            return hasattr(btn.button, "url") and btn.button.url is not None
        return False
    except:
        return False

def get_button_data(btn) -> dict:
    return {
        "text": get_button_text(btn),
        "is_url": is_url_button(btn),
        "url": get_button_url(btn) if is_url_button(btn) else None
    }


# ============ КЛАВИАТУРЫ ============

def get_task_choice_keyboard(user_id: int) -> InlineKeyboardMarkup:
    buttons = []
    task_types = {
        "channels": "📢 Подписка на каналы",
        "posts": "📱 Просмотр постов",
    }

    for task_key, task_name in task_types.items():
        is_selected = user_task_choice.get(user_id) == task_key
        text = f"{'✅ ' if is_selected else ''}{task_name}"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"task_choose_{task_key}")])

    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="gram")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ============ ПОДПИСКА ============

async def subscribe_to_channel(client: TelegramClient, link: str) -> Tuple[bool, str]:
    try:
        logging.info(f"📢 Подписываюсь: {link}")

        if not client.is_connected():
            logging.warning("⚠️ Клиент отключен, переподключаю...")
            await client.connect()

        if "?" in link:
            link = link.split("?")[0]

        if "t.me/" in link:
            if "joinchat/" in link:
                invite_hash = link.split("joinchat/")[-1].split("/")[0]
                await client(ImportChatInviteRequest(invite_hash))
                logging.info(f"✅ Подписался (invite/joinchat): {link}")
                return True, "success"
            elif "+" in link:
                invite_hash = link.split("+")[-1]
                if "/" in invite_hash:
                    invite_hash = invite_hash.split("/")[0]
                await client(ImportChatInviteRequest(invite_hash))
                logging.info(f"✅ Подписался (invite): {link}")
                return True, "success"
            else:
                username = link.split("t.me/")[-1].split("/")[0]
                if username:
                    entity = await client.get_entity(f"@{username}")
                    await client(JoinChannelRequest(entity))
                    logging.info(f"✅ Подписался (channel): {link}")
                    return True, "success"
        else:
            entity = await client.get_entity(f"@{link}")
            await client(JoinChannelRequest(entity))
            logging.info(f"✅ Подписался: {link}")
            return True, "success"

        return False, "unknown_link_format"

    except errors.FloodWaitError as e:
        wait_time = e.seconds
        logging.error(f"⏳ Flood wait: {wait_time} сек")
        return False, f"flood:{wait_time}"
    except Exception as e:
        error_msg = str(e).lower()
        if "already" in error_msg or "already participant" in error_msg:
            logging.info(f"✅ Уже подписан: {link}")
            return True, "already"
        if "successfully requested" in error_msg:
            logging.info(f"✅ Запрос на вступление отправлен: {link}")
            return True, "requested"
        logging.error(f"❌ Ошибка подписки {link}: {e}")
        return False, str(e)


async def click_button(client: TelegramClient, bot_username: str, msg, keywords: list, wait: float = 2):
    if not msg or not msg.buttons:
        return None

    if not client.is_connected():
        logging.warning("⚠️ Клиент отключен, переподключаю...")
        await client.connect()

    for row in msg.buttons:
        for btn in row:
            btn_text = get_button_text(btn).lower()
            for kw in keywords:
                if kw.lower() in btn_text:
                    logging.info(f"✅ Нажимаю: '{get_button_text(btn)}'")
                    try:
                        await btn.click()
                        await asyncio.sleep(wait)
                        return await get_last_message(client, bot_username)
                    except Exception as e:
                        logging.error(f"❌ Ошибка клика: {e}")
                        return None
    return None


# ============ ФУНКЦИИ СЕССИЙ ============

def cleanup_session_files(phone: str):
    """Очистка битых файлов сессии"""
    try:
        phone_clean = phone.replace('+', '')
        session_dir = "sessions"

        if not os.path.isdir(session_dir):
            return

        for file in os.listdir(session_dir):
            if file.startswith(phone_clean):
                file_path = os.path.join(session_dir, file)
                try:
                    os.remove(file_path)
                    logging.info(f"🗑 Удален файл: {file}")
                except:
                    pass
    except Exception as e:
        logging.error(f"❌ Ошибка очистки сессии: {e}")


def _cleanup_wal_files(session_name: str):
    """Удаляет journal/wal/shm файлы, которые могут вызывать блокировку"""
    for ext in ("-journal", "-wal", "-shm"):
        path = f"{session_name}.session{ext}"
        if os.path.exists(path):
            try:
                os.remove(path)
                logging.info(f"🗑 Удален файл: {path}")
            except:
                pass


async def _enable_wal_mode(client: TelegramClient):
    """Включает WAL режим sqlite для снижения вероятности блокировок"""
    try:
        conn = getattr(client.session, "_conn", None) or getattr(client.session, "conn", None)
        if conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
    except Exception as e:
        logging.debug(f"Не удалось включить WAL: {e}")


async def send_code(phone: str, bot_username: str) -> bool:
    phone = phone.strip()
    if not phone.startswith('+'):
        phone = '+' + phone

    lock = get_session_lock(phone)
    async with lock:
        try:
            os.makedirs("sessions", exist_ok=True)

            session_name = f"sessions/{phone.replace('+', '')}"
            _cleanup_wal_files(session_name)

            client = TelegramClient(
                session_name,
                API_ID,
                API_HASH,
                connection_retries=5,
                retry_delay=1,
                auto_reconnect=True,
                flood_sleep_threshold=60
            )

            await client.connect()
            await _enable_wal_mode(client)

            if not await client.is_user_authorized():
                await client.send_code_request(phone)
                logging.info(f"📱 Код отправлен на {phone}")
                active_clients[phone] = client
                return True
            else:
                logging.info(f"✅ Уже авторизован: {phone}")
                active_clients[phone] = client
                return True

        except errors.FloodWaitError as e:
            logging.error(f"⏳ Flood wait: {e.seconds} секунд")
            return False
        except errors.PhoneNumberInvalidError:
            logging.error(f"❌ Неверный номер: {phone}")
            return False
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e):
                logging.error(f"❌ База данных заблокирована, очищаю сессию...")
                cleanup_session_files(phone)
                await asyncio.sleep(2)
            else:
                logging.error(f"❌ Ошибка SQLite: {e}")
                return False
        except Exception as e:
            logging.error(f"❌ Ошибка отправки кода: {e}")
            return False

    return await send_code(phone, bot_username)


async def start_gram_bot(phone: str, code: str, bot_username: str, chat_id: int = None) -> bool:
    if chat_id:
        set_user_chat_id(chat_id)

    phone = phone.strip()
    if not phone.startswith('+'):
        phone = '+' + phone

    lock = get_session_lock(phone)
    async with lock:
        try:
            session_name = f"sessions/{phone.replace('+', '')}"
            _cleanup_wal_files(session_name)

            client = active_clients.get(phone)
            if not client:
                client = TelegramClient(
                    session_name,
                    API_ID,
                    API_HASH,
                    connection_retries=5,
                    retry_delay=1,
                    auto_reconnect=True,
                    flood_sleep_threshold=60
                )
                await client.connect()
                await _enable_wal_mode(client)
                active_clients[phone] = client

            if not client.is_connected():
                await client.connect()

            if await client.is_user_authorized():
                logging.info(f"✅ Уже авторизован: {phone}")
                return True

            await client.sign_in(phone, code)
            logging.info(f"✅ Авторизован: {phone}")
            return True

        except errors.SessionPasswordNeededError:
            logging.error("❌ Требуется 2FA пароль")
            return False
        except errors.PhoneCodeInvalidError:
            logging.error("❌ Неверный код")
            return False
        except errors.PhoneCodeExpiredError:
            logging.error("❌ Код истек")
            return False
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e):
                logging.error(f"❌ База данных заблокирована, очищаю сессию...")
                cleanup_session_files(phone)
                await asyncio.sleep(2)
            else:
                logging.error(f"❌ Ошибка SQLite: {e}")
                return False
        except Exception as e:
            logging.error(f"❌ Ошибка авторизации: {e}")
            return False

    return await start_gram_bot(phone, code, bot_username, chat_id)


async def start_gram_worker(client: TelegramClient, bot_username: str, phone: str, user_id: int = None):
    if user_id:
        set_user_chat_id(user_id)

    if not client.is_connected():
        logging.warning("⚠️ Клиент отключен, подключаю...")
        try:
            await client.connect()
        except Exception as e:
            logging.error(f"❌ Ошибка подключения: {e}")
            return None

    bot_username_for_task[phone] = bot_username
    task = asyncio.create_task(run_gram_worker(client, bot_username, phone))
    active_tasks[phone] = task
    logging.info(f"✅ Воркер запущен для {phone}")
    return task


async def stop_gram_bot(phone: Optional[str] = None) -> bool:
    if phone and phone in active_tasks:
        active_tasks[phone].cancel()
        del active_tasks[phone]
        logging.info(f"⏹ Остановлен: {phone}")
        return True
    elif active_tasks:
        for phone, task in list(active_tasks.items()):
            task.cancel()
        active_tasks.clear()
        logging.info("⏹ Все остановлены")
        return True
    return False


async def continue_gram_bot(phone: str) -> bool:
    if phone in active_clients and phone in bot_username_for_task:
        client = active_clients[phone]
        bot_username = bot_username_for_task[phone]

        if not client.is_connected():
            logging.warning("⚠️ Клиент отключен, подключаю...")
            try:
                await client.connect()
            except Exception as e:
                logging.error(f"❌ Ошибка подключения: {e}")
                return False

        task = asyncio.create_task(run_gram_worker(client, bot_username, phone))
        active_tasks[phone] = task
        logging.info(f"✅ Gram бот продолжен: {phone}")
        return True

    return False


# ============ ПРОВЕРКА КАПЧИ ============

def is_captcha_message(msg) -> bool:
    if not msg:
        return False

    text = (msg.raw_text or "").lower()

    captcha_keywords = [
        "подтвердите, что вы человек",
        "captcha",
        "verify you are human",
    ]

    for keyword in captcha_keywords:
        if keyword in text:
            logging.info(f"🔍 Капча: '{keyword}'")
            return True

    return False


# ============ ОТПРАВКА КАПЧИ ============

async def send_captcha_to_user(msg, chat_id: int, client: TelegramClient) -> bool:
    if not chat_id or not bot_instance:
        return False

    try:
        bot = bot_instance

        try:
            if msg.chat:
                bot_username = msg.chat.username or "gram_prbot"
            else:
                bot_entity = await client.get_entity(msg.peer_id)
                bot_username = bot_entity.username or "gram_prbot"
        except:
            bot_username = "gram_prbot"

        captcha_storage[chat_id] = {
            'client': client,
            'bot_username': bot_username,
            'msg_id': msg.id
        }

        captcha_url = None
        if msg.buttons:
            for row in msg.buttons:
                for btn in row:
                    if hasattr(btn, "url") and btn.url:
                        captcha_url = btn.url
                        break
                if captcha_url:
                    break

        if captcha_url:
            await bot.send_message(
                chat_id,
                f"🔗 <b>Капча:</b>\n<code>{captcha_url}</code>\n\nПройди и отправь /continue_gram",
                parse_mode=ParseMode.HTML
            )

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🌐 Открыть", url=captcha_url)],
                [InlineKeyboardButton(text="🔄 Проверить", callback_data=f"captcha_check_{chat_id}")],
                [InlineKeyboardButton(text="⏹ Остановить", callback_data=f"captcha_stop_{chat_id}")]
            ])
            await bot.send_message(chat_id, "👆", reply_markup=keyboard)

        return True

    except Exception as e:
        logging.error(f"❌ Ошибка отправки капчи: {e}")
        return False


# ============ CALLBACK ============

@router.callback_query(lambda c: c.data and c.data.startswith("captcha_check_"))
async def captcha_check_callback(callback: types.CallbackQuery):
    try:
        chat_id = int(callback.data.split("_")[2])
        await callback.answer("🔄")

        if chat_id not in captcha_storage:
            await callback.message.edit_text("✅ Капча пройдена!")
            return

        captcha_data = captcha_storage[chat_id]
        client = captcha_data['client']
        bot_username = captcha_data['bot_username']

        new_msg = await get_last_message(client, bot_username)

        if new_msg and not is_captcha_message(new_msg):
            await callback.message.edit_text("✅ Капча пройдена!", parse_mode=ParseMode.HTML)
            del captcha_storage[chat_id]

            phone = None
            for p, c in active_clients.items():
                if c == client:
                    phone = p
                    break
            if phone:
                await continue_gram_bot(phone)
        else:
            await callback.message.edit_text("⏳ Капча еще активна", parse_mode=ParseMode.HTML)

    except Exception as e:
        logging.error(f"❌ Ошибка проверки: {e}")
        await callback.answer(f"❌ Ошибка: {e}")


@router.callback_query(lambda c: c.data and c.data.startswith("captcha_stop_"))
async def captcha_stop_callback(callback: types.CallbackQuery):
    try:
        chat_id = int(callback.data.split("_")[2])

        if chat_id in captcha_storage:
            del captcha_storage[chat_id]

        await callback.answer("⏹ Остановлен")
        await callback.message.edit_text("⏹ Бот остановлен. Отправьте /continue_gram", parse_mode=ParseMode.HTML)
    except Exception as e:
        logging.error(f"❌ Ошибка остановки: {e}")


@router.callback_query(lambda c: c.data and c.data.startswith("task_choose_"))
async def task_choose_callback(callback: types.CallbackQuery):
    try:
        task_type = callback.data.replace("task_choose_", "")
        user_id = callback.from_user.id

        task_names = {
            "channels": "📢 Подписка на каналы",
            "posts": "📱 Просмотр постов",
        }

        if task_type in task_names:
            user_task_choice[user_id] = task_type
            await callback.answer(f"✅ {task_names[task_type]}")

            await callback.message.edit_text(
                f"✅ <b>Выбран тип:</b>\n{task_names[task_type]}",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="gram")]
                ])
            )
        else:
            await callback.answer("❌ Неверный тип")

    except Exception as e:
        logging.error(f"❌ Ошибка выбора: {e}")
        await callback.answer(f"❌ Ошибка: {e}")


# ============ ВСПОМОГАТЕЛЬНЫЕ ============

async def send_text(client: TelegramClient, bot_username: str, text: str, delay: float = 2):
    try:
        if not client.is_connected():
            logging.warning("⚠️ Клиент отключен, переподключаю...")
            await client.connect()

        await client.send_message(bot_username, text)
        logging.info(f"📤 {bot_username}: {text[:50]}")
        await asyncio.sleep(delay)
    except Exception as e:
        logging.error(f"❌ Ошибка отправки: {e}")

async def get_last_message(client: TelegramClient, bot_username: str):
    try:
        if not client.is_connected():
            logging.warning("⚠️ Клиент отключен, переподключаю...")
            await client.connect()

        msgs = await client.get_messages(bot_username, limit=1)
        return msgs[0] if msgs else None
    except Exception as e:
        logging.error(f"❌ Ошибка получения: {e}")
        return None

async def go_to_earn_menu(client: TelegramClient, bot_username: str):
    logging.info("🔄 Возврат в меню...")
    await send_text(client, bot_username, "◀️ Назад", 1)
    await asyncio.sleep(1)
    await send_text(client, bot_username, "👨‍💻 Заработать", 2)
    return await get_last_message(client, bot_username)


# ============ ОСНОВНАЯ ЛОГИКА ПОДПИСКИ ============

def _find_next_page_button(msg):
    if not msg or not msg.buttons:
        return None
    for row in msg.buttons:
        for btn in row:
            t = get_button_text(btn).strip()
            if t == ">" or "след" in t.lower() or t == "»":
                return btn
    return None


def build_task_list(msg) -> Tuple[List[Tuple[Any, Any]], bool]:
    """
    Универсальный парсер заданий подписки.
    Возвращает (список пар (url_btn, check_btn), is_batch_mode)

    is_batch_mode=True  -> общий список ссылок + ОДНА общая кнопка "Проверить"
                            (нужно подписаться на все, потом жать одну кнопку)
    is_batch_mode=False -> у каждой подписки СВОЯ кнопка "Проверить" в том же ряду
                            (обрабатываем поштучно)
    """
    if not msg or not msg.buttons:
        return [], False

    rows_info = []
    for row in msg.buttons:
        row_urls = []
        row_checks = []
        for btn in row:
            url = get_button_url(btn)
            text = get_button_text(btn).lower()
            if url and "t.me/" in url:
                row_urls.append(btn)
            elif "провер" in text or "✅" in text:
                row_checks.append(btn)
        rows_info.append((row_urls, row_checks))

    has_paired_row = any(r[0] and r[1] for r in rows_info)

    if has_paired_row:
        # Построчный режим: своя проверка на каждую подписку
        tasks = []
        pending_check = None
        for row_urls, row_checks in rows_info:
            if row_checks:
                pending_check = row_checks[0]
            for url_btn in row_urls:
                check_btn = row_checks[0] if row_checks else pending_check
                if check_btn:
                    tasks.append((url_btn, check_btn))
        return tasks, False

    # Общий (пакетный) режим: все ссылки + одна общая кнопка проверки
    all_urls = [btn for row_urls, _ in rows_info for btn in row_urls]
    all_checks = [btn for _, row_checks in rows_info for btn in row_checks]

    if all_urls and all_checks:
        common_check = all_checks[-1]
        tasks = [(url_btn, common_check) for url_btn in all_urls]
        return tasks, True

    return [], False


async def _process_batch_tasks(client: TelegramClient, bot_username: str, msg, tasks: List[Tuple[Any, Any]]):
    """Пакетный режим: подписываемся на все ссылки, потом жмём одну общую кнопку Проверить"""
    urls = [get_button_url(t[0]) for t in tasks]
    common_check_btn = tasks[0][1]

    logging.info(f"🔗 Найдено каналов для подписки (пакет): {len(urls)}")

    for i, url in enumerate(urls, 1):
        logging.info(f"📢 [{i}/{len(urls)}] {url}")
        success, result = await subscribe_to_channel(client, url)

        if not success and result.startswith("flood:"):
            wait_sec = int(result.split(":")[1])
            logging.warning(f"⏳ Flood wait {wait_sec} сек, жду...")
            await asyncio.sleep(min(wait_sec, 300))

        await asyncio.sleep(random.uniform(2, 4))

    logging.info(f"⏳ Жду {SUBSCRIBE_DELAY} секунд перед проверкой...")
    await asyncio.sleep(SUBSCRIBE_DELAY)

    max_attempts = 3
    updated_msg = None
    check_btn = common_check_btn

    for attempt in range(1, max_attempts + 1):
        logging.info(f"✅ Нажимаю Проверить (попытка {attempt}/{max_attempts})")
        try:
            await check_btn.click()
        except Exception as e:
            logging.error(f"❌ Ошибка клика Проверить: {e}")
            break

        await asyncio.sleep(3)
        updated_msg = await get_last_message(client, bot_username)

        if not updated_msg:
            continue

        if is_captcha_message(updated_msg):
            return updated_msg

        text = (updated_msg.raw_text or "").lower()
        logging.info(f"📝 Ответ: {text[:200]}")

        if "начислено" in text or "успешно" in text or "подписались" in text:
            logging.info("💰 Подписка подтверждена, начисление получено!")
            return updated_msg

        if "не подписан" in text:
            logging.warning("⚠️ Бот считает, что подписка не пройдена, повторяю...")

            for url in urls:
                await subscribe_to_channel(client, url)
                await asyncio.sleep(random.uniform(2, 4))

            new_tasks, _ = build_task_list(updated_msg)
            if new_tasks:
                check_btn = new_tasks[0][1]

            await asyncio.sleep(SUBSCRIBE_DELAY)
            continue

        return updated_msg

    return updated_msg or msg


async def _process_individual_tasks(client: TelegramClient, bot_username: str, msg, tasks: List[Tuple[Any, Any]]):
    """Построчный режим: у каждой подписки своя кнопка Проверить"""
    logging.info(f"🔗 Заданий на этой странице: {len(tasks)}")
    last_msg = msg

    for i, (url_btn, check_btn) in enumerate(tasks, 1):
        url = get_button_url(url_btn)
        btn_text = get_button_text(url_btn)
        logging.info(f"📢 [{i}/{len(tasks)}] '{btn_text}' -> {url}")

        success, result = await subscribe_to_channel(client, url)

        if not success and result.startswith("flood:"):
            wait_sec = int(result.split(":")[1])
            logging.warning(f"⏳ Flood wait {wait_sec} сек, жду...")
            await asyncio.sleep(min(wait_sec, 300))

        await asyncio.sleep(random.uniform(2, 4))
        await asyncio.sleep(SUBSCRIBE_DELAY)

        current_check_btn = check_btn
        max_attempts = 3
        updated_msg = None

        for attempt in range(1, max_attempts + 1):
            logging.info(f"✅ Нажимаю Проверить для '{btn_text}' (попытка {attempt}/{max_attempts})")
            try:
                await current_check_btn.click()
            except Exception as e:
                logging.error(f"❌ Ошибка клика Проверить: {e}")
                break

            await asyncio.sleep(3)
            updated_msg = await get_last_message(client, bot_username)

            if not updated_msg:
                continue

            if is_captcha_message(updated_msg):
                return updated_msg

            resp_text = (updated_msg.raw_text or "").lower()
            logging.info(f"📝 Ответ: {resp_text[:200]}")

            if "начислено" in resp_text or "успешно" in resp_text or "подписались" in resp_text:
                logging.info(f"💰 '{btn_text}' — начисление получено!")
                last_msg = updated_msg
                break

            if "не подписан" in resp_text:
                logging.warning(f"⚠️ '{btn_text}' — подписка не засчитана, повторяю...")
                await subscribe_to_channel(client, url)
                await asyncio.sleep(random.uniform(2, 4))
                await asyncio.sleep(SUBSCRIBE_DELAY)

                new_tasks, _ = build_task_list(updated_msg)
                if len(new_tasks) >= i:
                    current_check_btn = new_tasks[i - 1][1]
                last_msg = updated_msg
                continue

            last_msg = updated_msg
            break

    return last_msg


async def process_channel_list(client: TelegramClient, bot_username: str, msg, phone: str):
    """
    Обрабатывает список заданий на подписку постранично.
    Поддерживает 2 формата:
      - общий список ссылок + одна общая кнопка "Проверить"
      - у каждой ссылки своя кнопка "Проверить" в том же ряду
    """
    try:
        logging.info("📋 Обрабатываю задание(я) на подписку...")

        while True:
            if not client.is_connected():
                logging.warning("⚠️ Клиент отключен, переподключаю...")
                await client.connect()

            if not msg or not msg.buttons:
                logging.warning("⚠️ Нет кнопок в сообщении")
                return msg

            if is_captcha_message(msg):
                return msg

            tasks, is_batch = build_task_list(msg)

            if not tasks:
                logging.warning("⚠️ Не найдены задания на подписку в текущем сообщении")
                return msg

            if is_batch:
                msg = await _process_batch_tasks(client, bot_username, msg, tasks)
            else:
                msg = await _process_individual_tasks(client, bot_username, msg, tasks)

            if msg and is_captcha_message(msg):
                return msg

            # обновляем сообщение перед проверкой пагинации
            fresh_msg = await get_last_message(client, bot_username)
            if fresh_msg:
                msg = fresh_msg

            if is_captcha_message(msg):
                return msg

            next_btn = _find_next_page_button(msg)

            if next_btn:
                logging.info("➡️ Переход на следующую страницу заданий...")
                try:
                    await next_btn.click()
                    await asyncio.sleep(2)
                    new_msg = await get_last_message(client, bot_username)
                    if new_msg:
                        msg = new_msg
                        continue
                    else:
                        return msg
                except Exception as e:
                    logging.error(f"❌ Ошибка перехода на следующую страницу: {e}")
                    return msg
            else:
                logging.info("✅ Все страницы заданий обработаны")
                return msg

    except Exception as e:
        logging.error(f"❌ Ошибка обработки задания подписки: {e}")
        return msg


# ============ ОСНОВНОЙ ЦИКЛ ============

async def do_one_cycle(client: TelegramClient, bot_username: str, user_id: int = None, phone: str = None):
    task_type = user_task_choice.get(user_id, "channels")
    logging.info(f"📋 Тип: {task_type}")

    msg = await get_last_message(client, bot_username)

    if msg and is_captcha_message(msg):
        await send_captcha_to_user(msg, user_chat_id, client)
        return

    if task_type == "channels":
        target_button = "Подписаться на канал"
    else:
        target_button = "Просмотр постов"

    logging.info(f"🎯 Нажимаю: {target_button}")

    updated = await click_button(client, bot_username, msg, [target_button], wait=2)

    if not updated:
        if task_type == "channels":
            updated = await click_button(client, bot_username, msg, ["Каналы", "📢"], wait=2)
        else:
            updated = await click_button(client, bot_username, msg, ["Посты", "👁"], wait=2)

    if not updated:
        logging.info(f"❌ Кнопка '{target_button}' не найдена")
        await go_to_earn_menu(client, bot_username)
        return

    if is_captcha_message(updated):
        await send_captcha_to_user(updated, user_chat_id, client)
        return

    if task_type == "channels":
        has_check = False
        if updated.buttons:
            for row in updated.buttons:
                for btn in row:
                    btn_text = get_button_text(btn).lower()
                    if "провер" in btn_text or "✅" in btn_text:
                        has_check = True
                        break
                if has_check:
                    break

        if has_check:
            logging.info("📢 Обнаружено задание подписки (есть кнопка Проверить)")
            updated = await process_channel_list(client, bot_username, updated, phone)

            if updated and is_captcha_message(updated):
                await send_captcha_to_user(updated, user_chat_id, client)
                return

            await asyncio.sleep(2)
            await go_to_earn_menu(client, bot_username)
            return

        logging.info("⚠️ Задание подписки не найдено, возврат в меню")
        await go_to_earn_menu(client, bot_username)
        return

    wait_time = random.randint(8, 15)
    logging.info(f"👀 Читаю пост {wait_time} сек...")
    await asyncio.sleep(wait_time)

    confirm_msg = await click_button(client, bot_username, updated, ["✅", "Просмотрел", "Готово", "Получить"], wait=2)

    if confirm_msg and is_captcha_message(confirm_msg):
        await send_captcha_to_user(confirm_msg, user_chat_id, client)
        return

    await go_to_earn_menu(client, bot_username)


# ============ ВОРКЕР ============

async def run_gram_worker(client: TelegramClient, bot_username: str, phone: str):
    try:
        logging.info(f"🚀 Запуск {bot_username}...")
        logging.info(f"⏳ Пауза между подписками: {SUBSCRIBE_DELAY} секунд")

        await send_text(client, bot_username, "/start", 3)
        await send_text(client, bot_username, "👨‍💻 Заработать", 2)

        cycle_count = 0

        while True:
            cycle_count += 1
            logging.info(f"\n\n{'#'*60}")
            logging.info(f"🔁 ЦИКЛ #{cycle_count}")
            logging.info(f"{'#'*60}")

            try:
                await do_one_cycle(client, bot_username, user_chat_id, phone)
            except Exception as e:
                logging.error(f"❌ Ошибка: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(5)

            pause = random.randint(5, 10)
            logging.info(f"⏸️ Пауза {pause} сек...")
            await asyncio.sleep(pause)

    except asyncio.CancelledError:
        logging.info(f"⏹ {bot_username} остановлен")
    except Exception as e:
        logging.error(f"❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
    finally:
        try:
            await client.disconnect()
        except:
            pass


# ============ ИНИЦИАЛИЗАЦИЯ ============

def init_gram_bot(dp):
    global gram_bot_initialized
    if not gram_bot_initialized:
        dp.include_router(router)
        gram_bot_initialized = True
        logging.info("✅ Модуль Gram ботов инициализирован")


# ============ ЭКСПОРТ ============

__all__ = [
    'router',
    'init_gram_bot',
    'send_code',
    'start_gram_bot',
    'start_gram_worker',
    'stop_gram_bot',
    'continue_gram_bot',
    'set_user_chat_id',
    'set_bot_instance',
    'get_task_choice_keyboard',
    'active_clients',
    'active_tasks'
    ]
