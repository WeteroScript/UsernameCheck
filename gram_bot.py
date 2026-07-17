"""
Модуль для Gram ботов - с фиксом database is locked (Telethon)
"""

import asyncio
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
user_task_choice: Dict[int, str] = {}

SUBSCRIBE_DELAY = 60

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
            await client.connect()

        if "?" in link:
            link = link.split("?")[0]

        if "t.me/" in link:
            if "joinchat/" in link:
                invite_hash = link.split("joinchat/")[-1].split("/")[0]
                await client(ImportChatInviteRequest(invite_hash))
                logging.info(f"✅ Подписался (joinchat): {link}")
                return True, "success"
            elif "+" in link:
                invite_hash = link.split("+")[-1].split("/")[0]
                await client(ImportChatInviteRequest(invite_hash))
                logging.info(f"✅ Подписался (invite): {link}")
                return True, "success"
            else:
                username = link.split("t.me/")[-1].split("/")[0]
                if username:
                    entity = await client.get_entity(f"@{username}")
                    await client(JoinChannelRequest(entity))
                    logging.info(f"✅ Подписался (channel): @{username}")
                    return True, "success"
        else:
            entity = await client.get_entity(f"@{link}")
            await client(JoinChannelRequest(entity))
            logging.info(f"✅ Подписался: @{link}")
            return True, "success"

        return False, "unknown_link_format"

    except errors.FloodWaitError as e:
        logging.error(f"⏳ Flood wait: {e.seconds} сек")
        return False, f"flood:{e.seconds}"
    except Exception as e:
        err = str(e).lower()
        if "already" in err or "already participant" in err:
            logging.info(f"✅ Уже подписан: {link}")
            return True, "already"
        if "successfully requested" in err:
            logging.info(f"✅ Запрос отправлен: {link}")
            return True, "requested"
        logging.error(f"❌ Ошибка подписки {link}: {e}")
        return False, str(e)


# ============ ФУНКЦИИ СЕССИЙ ============

def cleanup_session_files(phone: str):
    try:
        phone_clean = phone.replace('+', '')
        session_dir = "sessions"
        if not os.path.isdir(session_dir):
            return
        for file in os.listdir(session_dir):
            if file.startswith(phone_clean):
                try:
                    os.remove(os.path.join(session_dir, file))
                    logging.info(f"🗑 Удален: {file}")
                except:
                    pass
    except Exception as e:
        logging.error(f"❌ Ошибка очистки сессии: {e}")


def _cleanup_wal_files(session_name: str):
    for ext in ("-journal", "-wal", "-shm"):
        path = f"{session_name}.session{ext}"
        if os.path.exists(path):
            try:
                os.remove(path)
                logging.info(f"🗑 Удален: {path}")
            except:
                pass


async def _enable_wal_mode(client: TelegramClient):
    try:
        conn = getattr(client.session, "_conn", None) or getattr(client.session, "conn", None)
        if conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
    except Exception as e:
        logging.debug(f"WAL не включен: {e}")


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
                session_name, API_ID, API_HASH,
                connection_retries=5, retry_delay=1,
                auto_reconnect=True, flood_sleep_threshold=60
            )
            await client.connect()
            await _enable_wal_mode(client)

            if not await client.is_user_authorized():
                await client.send_code_request(phone)
                logging.info(f"📱 Код отправлен на {phone}")
            else:
                logging.info(f"✅ Уже авторизован: {phone}")

            active_clients[phone] = client
            return True

        except errors.FloodWaitError as e:
            logging.error(f"⏳ Flood wait: {e.seconds} сек")
            return False
        except errors.PhoneNumberInvalidError:
            logging.error(f"❌ Неверный номер: {phone}")
            return False
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e):
                logging.error("❌ БД заблокирована, очищаю...")
                cleanup_session_files(phone)
                await asyncio.sleep(2)
            else:
                logging.error(f"❌ SQLite: {e}")
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
                    session_name, API_ID, API_HASH,
                    connection_retries=5, retry_delay=1,
                    auto_reconnect=True, flood_sleep_threshold=60
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
            logging.error("❌ Требуется 2FA")
            return False
        except errors.PhoneCodeInvalidError:
            logging.error("❌ Неверный код")
            return False
        except errors.PhoneCodeExpiredError:
            logging.error("❌ Код истёк")
            return False
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e):
                logging.error("❌ БД заблокирована, очищаю...")
                cleanup_session_files(phone)
                await asyncio.sleep(2)
            else:
                logging.error(f"❌ SQLite: {e}")
                return False
        except Exception as e:
            logging.error(f"❌ Ошибка авторизации: {e}")
            return False

    return await start_gram_bot(phone, code, bot_username, chat_id)


async def start_gram_worker(client: TelegramClient, bot_username: str, phone: str, user_id: int = None):
    if user_id:
        set_user_chat_id(user_id)

    if not client.is_connected():
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
        for p, task in list(active_tasks.items()):
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
            try:
                await client.connect()
            except Exception as e:
                logging.error(f"❌ Ошибка подключения: {e}")
                return False

        task = asyncio.create_task(run_gram_worker(client, bot_username, phone))
        active_tasks[phone] = task
        logging.info(f"✅ Продолжен: {phone}")
        return True
    return False


# ============ ПРОВЕРКА КАПЧИ ============

def is_captcha_message(msg) -> bool:
    if not msg:
        return False
    text = (msg.raw_text or "").lower()
    for kw in ["подтвердите, что вы человек", "captcha", "verify you are human"]:
        if kw in text:
            logging.info(f"🔍 Капча: '{kw}'")
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
                bu = msg.chat.username or "gram_prbot"
            else:
                ent = await client.get_entity(msg.peer_id)
                bu = ent.username or "gram_prbot"
        except:
            bu = "gram_prbot"

        captcha_storage[chat_id] = {'client': client, 'bot_username': bu, 'msg_id': msg.id}

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
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🌐 Открыть", url=captcha_url)],
                [InlineKeyboardButton(text="🔄 Проверить", callback_data=f"captcha_check_{chat_id}")],
                [InlineKeyboardButton(text="⏹ Остановить", callback_data=f"captcha_stop_{chat_id}")]
            ])
            await bot.send_message(chat_id, "👆", reply_markup=kb)
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
        data = captcha_storage[chat_id]
        client = data['client']
        bot_username = data['bot_username']
        new_msg = await get_last_message(client, bot_username)
        if new_msg and not is_captcha_message(new_msg):
            await callback.message.edit_text("✅ Капча пройдена!")
            del captcha_storage[chat_id]
            phone = next((p for p, c in active_clients.items() if c == client), None)
            if phone:
                await continue_gram_bot(phone)
        else:
            await callback.message.edit_text("⏳ Капча ещё активна")
    except Exception as e:
        logging.error(f"❌ captcha_check: {e}")
        await callback.answer(f"❌ {e}")


@router.callback_query(lambda c: c.data and c.data.startswith("captcha_stop_"))
async def captcha_stop_callback(callback: types.CallbackQuery):
    try:
        chat_id = int(callback.data.split("_")[2])
        captcha_storage.pop(chat_id, None)
        await callback.answer("⏹")
        await callback.message.edit_text("⏹ Остановлен. Отправьте /continue_gram")
    except Exception as e:
        logging.error(f"❌ captcha_stop: {e}")


@router.callback_query(lambda c: c.data and c.data.startswith("task_choose_"))
async def task_choose_callback(callback: types.CallbackQuery):
    try:
        task_type = callback.data.replace("task_choose_", "")
        user_id = callback.from_user.id
        task_names = {"channels": "📢 Подписка на каналы", "posts": "📱 Просмотр постов"}
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
        logging.error(f"❌ task_choose: {e}")
        await callback.answer(f"❌ {e}")


# ============ ВСПОМОГАТЕЛЬНЫЕ ============

async def send_text(client: TelegramClient, bot_username: str, text: str, delay: float = 2):
    try:
        if not client.is_connected():
            await client.connect()
        await client.send_message(bot_username, text)
        logging.info(f"📤 → {bot_username}: {text[:60]}")
        await asyncio.sleep(delay)
    except Exception as e:
        logging.error(f"❌ send_text: {e}")


async def get_last_message(client: TelegramClient, bot_username: str):
    try:
        if not client.is_connected():
            await client.connect()
        msgs = await client.get_messages(bot_username, limit=1)
        return msgs[0] if msgs else None
    except Exception as e:
        logging.error(f"❌ get_last_message: {e}")
        return None


async def wait_for_new_message(client: TelegramClient, bot_username: str,
                                old_msg_id: int, timeout: float = 15.0) -> Optional[Any]:
    """
    Ждёт НОВОЕ сообщение от бота (с id > old_msg_id).
    Гарантирует, что мы читаем именно ответ на наше действие,
    а не старое сообщение.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        msg = await get_last_message(client, bot_username)
        if msg and msg.id != old_msg_id:
            return msg
        await asyncio.sleep(1.0)
    # Вернём последнее что есть
    return await get_last_message(client, bot_username)


async def click_btn_in_msg(btn, client: TelegramClient, bot_username: str,
                            wait: float = 2.0, old_msg_id: int = None):
    """
    Кликает кнопку и ждёт новое сообщение.
    """
    try:
        if not client.is_connected():
            await client.connect()
        cur_msg = await get_last_message(client, bot_username)
        cur_id = cur_msg.id if cur_msg else (old_msg_id or 0)
        await btn.click()
        await asyncio.sleep(wait)
        return await wait_for_new_message(client, bot_username, cur_id, timeout=10)
    except Exception as e:
        logging.error(f"❌ click_btn_in_msg: {e}")
        return None


def _find_next_page_button(msg):
    if not msg or not msg.buttons:
        return None
    for row in msg.buttons:
        for btn in row:
            t = get_button_text(btn).strip()
            if t in (">", "»") or "след" in t.lower():
                return btn
    return None


def find_row_pairs(msg) -> List[Tuple[Any, Any]]:
    """
    Ищет пары (url_кнопка_подписки, кнопка_Проверить) в одном ряду.
    Формат PR GRAM: слева "+N💰 | Подписаться" (URL t.me/...),
    справа "🔄 Проверить" — обе в одном row.
    """
    pairs = []
    if not msg or not msg.buttons:
        return pairs
    for row in msg.buttons:
        sub_btn = None
        check_btn = None
        for btn in row:
            url = get_button_url(btn)
            text = get_button_text(btn).lower()
            if url and "t.me/" in url:
                sub_btn = btn
            elif "провер" in text:
                check_btn = btn
        if sub_btn and check_btn:
            pairs.append((sub_btn, check_btn))
    return pairs


def is_task_list_message(msg) -> bool:
    """
    Проверяет, что сообщение содержит список заданий на подписку
    (есть хотя бы одна пара url+проверить в рядах).
    """
    return len(find_row_pairs(msg)) > 0


def is_earn_type_menu(msg) -> bool:
    """
    Проверяет, что сообщение — это меню выбора типа заданий
    (содержит кнопки типа "Подписаться на канал", "Просмотр постов" и т.д.
    но НЕ содержит пар подписка+проверить).
    """
    if not msg or not msg.buttons:
        return False
    earn_keywords = [
        "подписаться на канал", "вступить в группу", "просмотр постов",
        "перейти в бота", "поставить реакци", "премиум буст", "заряд"
    ]
    found_earn_btn = False
    for row in msg.buttons:
        for btn in row:
            t = get_button_text(btn).lower()
            if any(kw in t for kw in earn_keywords):
                found_earn_btn = True
                break
        if found_earn_btn:
            break
    return found_earn_btn and not is_task_list_message(msg)


# ============ НАВИГАЦИЯ ============

async def navigate_to_earn_type_menu(client: TelegramClient, bot_username: str) -> Optional[Any]:
    """
    Отправляет '👨‍💻 Заработать' и ждёт меню выбора типа заданий.
    Возвращает сообщение с меню или None.
    """
    logging.info("📋 Открываю меню 'Заработать'...")

    cur = await get_last_message(client, bot_username)
    cur_id = cur.id if cur else 0

    await send_text(client, bot_username, "👨‍💻 Заработать", delay=0)

    # Ждём новое сообщение от бота
    for _ in range(10):
        await asyncio.sleep(1.5)
        msg = await get_last_message(client, bot_username)
        if msg and msg.id != cur_id and msg.buttons:
            logging.info("✅ Меню 'Заработать' получено")
            return msg

    logging.warning("⚠️ Меню 'Заработать' не получено")
    return await get_last_message(client, bot_username)


async def go_back_and_earn(client: TelegramClient, bot_username: str) -> Optional[Any]:
    """
    Возврат: Назад → Заработать → меню типов заданий.
    """
    logging.info("🔄 Возврат в меню...")

    cur = await get_last_message(client, bot_username)
    cur_id = cur.id if cur else 0

    await send_text(client, bot_username, "◀️ Назад", delay=1)
    await asyncio.sleep(1)

    return await navigate_to_earn_type_menu(client, bot_username)


async def click_task_type_button(client: TelegramClient, bot_username: str,
                                  earn_menu_msg, task_type: str) -> Optional[Any]:
    """
    В меню выбора типа заданий нажимает нужную кнопку
    ("Подписаться на канал" или "Просмотр постов").
    Возвращает сообщение со списком заданий или None.
    """
    if task_type == "channels":
        keywords = ["подписаться на канал"]
    else:
        keywords = ["просмотр постов"]

    if not earn_menu_msg or not earn_menu_msg.buttons:
        return None

    target_btn = None
    for row in earn_menu_msg.buttons:
        for btn in row:
            t = get_button_text(btn).lower()
            if any(kw in t for kw in keywords):
                target_btn = btn
                break
        if target_btn:
            break

    if not target_btn:
        logging.warning(f"⚠️ Кнопка типа задания не найдена: {keywords}")
        return None

    logging.info(f"✅ Нажимаю тип задания: '{get_button_text(target_btn)}'")

    result = await click_btn_in_msg(target_btn, client, bot_username, wait=2)
    return result


# ============ ОБРАБОТКА ЗАДАНИЙ ПОДПИСКИ ============

async def process_one_subscription_task(
    client: TelegramClient,
    bot_username: str,
    sub_btn,
    check_btn,
    task_index: int,
    total: int
) -> Optional[Any]:
    """
    Обрабатывает одно задание: подписывается и нажимает свою кнопку Проверить.
    Возвращает последнее полученное сообщение.
    """
    url = get_button_url(sub_btn)
    btn_text = get_button_text(sub_btn)
    logging.info(f"📢 [{task_index}/{total}] '{btn_text}' -> {url}")

    # Подписываемся
    success, result = await subscribe_to_channel(client, url)

    if not success and result.startswith("flood:"):
        wait_sec = int(result.split(":")[1])
        logging.warning(f"⏳ Flood wait {wait_sec} сек...")
        await asyncio.sleep(min(wait_sec, 300))

    await asyncio.sleep(random.uniform(2, 4))

    # Ждём перед проверкой
    logging.info(f"⏳ Жду {SUBSCRIBE_DELAY} сек перед проверкой...")
    await asyncio.sleep(SUBSCRIBE_DELAY)

    current_check = check_btn
    updated_msg = None

    for attempt in range(1, 4):
        logging.info(f"🔄 Проверить '{btn_text}' (попытка {attempt}/3)")
        try:
            updated_msg = await click_btn_in_msg(current_check, client, bot_username, wait=3)
        except Exception as e:
            logging.error(f"❌ Клик Проверить: {e}")
            break

        if not updated_msg:
            continue

        if is_captcha_message(updated_msg):
            return updated_msg

        resp = (updated_msg.raw_text or "").lower()
        logging.info(f"📝 Ответ: {resp[:150]}")

        if "начислено" in resp or "успешно" in resp or "подписались" in resp:
            logging.info(f"💰 Начислено за '{btn_text}'!")
            return updated_msg

        if "не подписан" in resp:
            logging.warning(f"⚠️ Не засчитано, повторяю подписку...")
            await subscribe_to_channel(client, url)
            await asyncio.sleep(random.uniform(2, 4))
            await asyncio.sleep(SUBSCRIBE_DELAY)

            # Ищем актуальную кнопку Проверить по URL
            new_pairs = find_row_pairs(updated_msg)
            matched_check = next(
                (nc for nu, nc in new_pairs if get_button_url(nu) == url),
                None
            )
            if matched_check:
                current_check = matched_check
            continue

        # Другой ответ — прерываем
        return updated_msg

    return updated_msg


async def process_channel_list(client: TelegramClient, bot_username: str, msg, phone: str):
    """
    Обрабатывает список заданий подписки постранично.
    Каждый ряд: (URL-кнопка Подписаться, кнопка Проверить) — своя пара.
    """
    logging.info("📋 Обрабатываю задания на подписку...")

    while True:
        if not client.is_connected():
            await client.connect()

        if not msg or not msg.buttons:
            logging.warning("⚠️ Нет кнопок")
            return msg

        if is_captcha_message(msg):
            return msg

        # Проверяем, не вернулись ли мы случайно в меню типов заданий
        if is_earn_type_menu(msg):
            logging.warning("⚠️ Попали в меню типов вместо списка заданий")
            return msg

        task_pairs = find_row_pairs(msg)

        if not task_pairs:
            logging.warning("⚠️ Пары (подписка+проверить) не найдены")
            return msg

        logging.info(f"🔗 Заданий на странице: {len(task_pairs)}")

        for i, (sub_btn, check_btn) in enumerate(task_pairs, 1):
            result_msg = await process_one_subscription_task(
                client, bot_username, sub_btn, check_btn, i, len(task_pairs)
            )
            if result_msg:
                if is_captcha_message(result_msg):
                    return result_msg
                msg = result_msg

        # Обновляем сообщение
        fresh = await get_last_message(client, bot_username)
        if fresh:
            msg = fresh

        if is_captcha_message(msg):
            return msg

        # Проверяем пагинацию
        next_btn = _find_next_page_button(msg)
        if next_btn:
            logging.info("➡️ Следующая страница...")
            try:
                result = await click_btn_in_msg(next_btn, client, bot_username, wait=2)
                if result:
                    msg = result
                    continue
            except Exception as e:
                logging.error(f"❌ Ошибка пагинации: {e}")
            return msg

        logging.info("✅ Все страницы обработаны")
        return msg


# ============ ОСНОВНОЙ ЦИКЛ ============

async def do_one_cycle(client: TelegramClient, bot_username: str,
                        user_id: int = None, phone: str = None):
    """
    Один полный цикл:
    1. Открываем меню 'Заработать' → получаем список типов заданий
    2. Нажимаем нужный тип ("Подписаться на канал" или "Просмотр постов")
    3. Обрабатываем полученный список заданий
    """
    task_type = user_task_choice.get(user_id, "channels")
    logging.info(f"📋 Тип задания: {task_type}")

    # Проверяем капчу
    current_msg = await get_last_message(client, bot_username)
    if current_msg and is_captcha_message(current_msg):
        await send_captcha_to_user(current_msg, user_chat_id, client)
        return

    # ШАГ 1: Открываем меню "Заработать" и получаем меню типов заданий
    earn_menu = await navigate_to_earn_type_menu(client, bot_username)

    if not earn_menu:
        logging.warning("⚠️ Не удалось открыть меню Заработать")
        return

    if is_captcha_message(earn_menu):
        await send_captcha_to_user(earn_menu, user_chat_id, client)
        return

    # Логируем что видим в меню
    if earn_menu.buttons:
        btns_text = []
        for row in earn_menu.buttons:
            for btn in row:
                btns_text.append(f"'{get_button_text(btn)}'")
        logging.info(f"📋 Кнопки в меню: {', '.join(btns_text[:8])}")

    # Проверяем что это действительно меню выбора типа
    if not is_earn_type_menu(earn_menu):
        logging.warning("⚠️ Неожиданное сообщение вместо меню типов заданий")
        logging.info(f"📝 Текст: {(earn_menu.raw_text or '')[:200]}")
        # Попробуем ещё раз через назад
        earn_menu = await go_back_and_earn(client, bot_username)
        if not earn_menu or not is_earn_type_menu(earn_menu):
            logging.error("❌ Не удалось получить меню типов заданий")
            return

    # ШАГ 2: Нажимаем нужный тип задания
    task_list_msg = await click_task_type_button(client, bot_username, earn_menu, task_type)

    if not task_list_msg:
        logging.warning(f"⚠️ Не удалось нажать кнопку типа '{task_type}'")
        return

    if is_captcha_message(task_list_msg):
        await send_captcha_to_user(task_list_msg, user_chat_id, client)
        return

    # Логируем ответ
    logging.info(f"📝 После выбора типа: {(task_list_msg.raw_text or '')[:150]}")

    # ШАГ 3: Обрабатываем задания
    if task_type == "channels":
        # Проверяем есть ли задания
        if is_task_list_message(task_list_msg):
            logging.info("📢 Список заданий на подписку получен!")
            result = await process_channel_list(client, bot_username, task_list_msg, phone)

            if result and is_captcha_message(result):
                await send_captcha_to_user(result, user_chat_id, client)
                return

            logging.info("✅ Цикл подписок завершён")
            return

        # Заданий нет или ответ неожиданный
        resp_text = (task_list_msg.raw_text or "").lower()
        logging.info(f"⚠️ Список заданий не распознан. Ответ: {resp_text[:200]}")
        return

    else:
        # task_type == "posts"
        wait_time = random.randint(8, 15)
        logging.info(f"👀 Читаю пост {wait_time} сек...")
        await asyncio.sleep(wait_time)

        # Ищем кнопку подтверждения
        if task_list_msg.buttons:
            confirm_btn = None
            confirm_kw = ["✅", "просмотрел", "готово", "получить"]
            for row in task_list_msg.buttons:
                for btn in row:
                    t = get_button_text(btn).lower()
                    if any(kw in t for kw in confirm_kw):
                        confirm_btn = btn
                        break
                if confirm_btn:
                    break

            if confirm_btn:
                logging.info(f"✅ Нажимаю: '{get_button_text(confirm_btn)}'")
                confirm_msg = await click_btn_in_msg(confirm_btn, client, bot_username, wait=2)
                if confirm_msg and is_captcha_message(confirm_msg):
                    await send_captcha_to_user(confirm_msg, user_chat_id, client)
                    return

        logging.info("✅ Просмотр поста завершён")


# ============ ВОРКЕР ============

async def run_gram_worker(client: TelegramClient, bot_username: str, phone: str):
    try:
        logging.info(f"🚀 Запуск воркера для {bot_username}")
        logging.info(f"⏳ Задержка подписки: {SUBSCRIBE_DELAY} сек")

        # Стартуем бота
        cur = await get_last_message(client, bot_username)
        cur_id = cur.id if cur else 0
        await send_text(client, bot_username, "/start", delay=0)
        await wait_for_new_message(client, bot_username, cur_id, timeout=10)
        await asyncio.sleep(2)

        cycle_count = 0

        while True:
            cycle_count += 1
            logging.info(f"\n{'#'*50}")
            logging.info(f"🔁 ЦИКЛ #{cycle_count}")
            logging.info(f"{'#'*50}")

            try:
                await do_one_cycle(client, bot_username, user_chat_id, phone)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logging.error(f"❌ Ошибка в цикле: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(5)

            pause = random.randint(5, 10)
            logging.info(f"⏸️ Пауза {pause} сек...")
            await asyncio.sleep(pause)

    except asyncio.CancelledError:
        logging.info(f"⏹ Воркер {bot_username} остановлен")
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
