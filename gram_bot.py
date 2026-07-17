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
        return ""


def get_button_url(btn) -> Optional[str]:
    try:
        if hasattr(btn, "url") and btn.url:
            return btn.url
        if hasattr(btn, "button") and hasattr(btn.button, "url"):
            return btn.button.url
        return None
    except:
        return None


def get_msg_snapshot(msg) -> str:
    """Снимок сообщения для отслеживания изменений (edit)."""
    if not msg:
        return ""
    parts = [msg.raw_text or ""]
    if msg.buttons:
        for row in msg.buttons:
            for btn in row:
                parts.append(get_button_text(btn))
    return "|".join(parts)


# ============ КЛАВИАТУРЫ aiogram ============

def get_task_choice_keyboard(user_id: int) -> InlineKeyboardMarkup:
    buttons = []
    task_types = {
        "channels": "📢 Подписка на каналы",
        "posts": "📱 Просмотр постов",
    }
    for task_key, task_name in task_types.items():
        is_selected = user_task_choice.get(user_id) == task_key
        text = f"{'✅ ' if is_selected else ''}{task_name}"
        buttons.append([InlineKeyboardButton(
            text=text, callback_data=f"task_choose_{task_key}"
        )])
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
                    logging.info(f"✅ Подписался: @{username}")
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
                    logging.info(f"🗑 Удалён: {file}")
                except:
                    pass
    except Exception as e:
        logging.error(f"❌ cleanup_session_files: {e}")


def _cleanup_wal_files(session_name: str):
    for ext in ("-journal", "-wal", "-shm"):
        path = f"{session_name}.session{ext}"
        if os.path.exists(path):
            try:
                os.remove(path)
            except:
                pass


async def _enable_wal_mode(client: TelegramClient):
    try:
        conn = (
            getattr(client.session, "_conn", None)
            or getattr(client.session, "conn", None)
        )
        if conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
    except Exception as e:
        logging.debug(f"WAL: {e}")


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
                logging.info(f"📱 Код отправлен: {phone}")
            else:
                logging.info(f"✅ Уже авторизован: {phone}")
            active_clients[phone] = client
            return True
        except errors.FloodWaitError as e:
            logging.error(f"⏳ Flood: {e.seconds} сек")
            return False
        except errors.PhoneNumberInvalidError:
            logging.error(f"❌ Неверный номер: {phone}")
            return False
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e):
                cleanup_session_files(phone)
                await asyncio.sleep(2)
            else:
                return False
        except Exception as e:
            logging.error(f"❌ send_code: {e}")
            return False
    return await send_code(phone, bot_username)


async def start_gram_bot(
    phone: str, code: str, bot_username: str, chat_id: int = None
) -> bool:
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
                cleanup_session_files(phone)
                await asyncio.sleep(2)
            else:
                return False
        except Exception as e:
            logging.error(f"❌ start_gram_bot: {e}")
            return False
    return await start_gram_bot(phone, code, bot_username, chat_id)


async def start_gram_worker(
    client: TelegramClient, bot_username: str, phone: str, user_id: int = None
):
    if user_id:
        set_user_chat_id(user_id)
    if not client.is_connected():
        try:
            await client.connect()
        except Exception as e:
            logging.error(f"❌ Подключение: {e}")
            return None
    bot_username_for_task[phone] = bot_username
    task = asyncio.create_task(run_gram_worker(client, bot_username, phone))
    active_tasks[phone] = task
    logging.info(f"✅ Воркер запущен: {phone}")
    return task


async def stop_gram_bot(phone: Optional[str] = None) -> bool:
    if phone and phone in active_tasks:
        active_tasks[phone].cancel()
        del active_tasks[phone]
        return True
    elif active_tasks:
        for p, task in list(active_tasks.items()):
            task.cancel()
        active_tasks.clear()
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
                logging.error(f"❌ Подключение: {e}")
                return False
        task = asyncio.create_task(run_gram_worker(client, bot_username, phone))
        active_tasks[phone] = task
        return True
    return False


# ============ КАПЧА ============

def is_captcha_message(msg) -> bool:
    if not msg:
        return False
    text = (msg.raw_text or "").lower()
    for kw in ["подтвердите, что вы человек", "captcha", "verify you are human"]:
        if kw in text:
            return True
    return False


async def send_captcha_to_user(msg, chat_id: int, client: TelegramClient) -> bool:
    if not chat_id or not bot_instance:
        return False
    try:
        try:
            if msg.chat:
                bu = msg.chat.username or "gram_prbot"
            else:
                ent = await client.get_entity(msg.peer_id)
                bu = ent.username or "gram_prbot"
        except:
            bu = "gram_prbot"

        captcha_storage[chat_id] = {
            'client': client, 'bot_username': bu, 'msg_id': msg.id
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
            await bot_instance.send_message(
                chat_id,
                f"🔗 <b>Капча:</b>\n<code>{captcha_url}</code>\n\nПройди и отправь /continue_gram",
                parse_mode=ParseMode.HTML
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🌐 Открыть", url=captcha_url)],
                [InlineKeyboardButton(
                    text="🔄 Проверить", callback_data=f"captcha_check_{chat_id}"
                )],
                [InlineKeyboardButton(
                    text="⏹ Остановить", callback_data=f"captcha_stop_{chat_id}"
                )]
            ])
            await bot_instance.send_message(chat_id, "👆", reply_markup=kb)
        return True
    except Exception as e:
        logging.error(f"❌ send_captcha_to_user: {e}")
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
            phone = next(
                (p for p, c in active_clients.items() if c == client), None
            )
            if phone:
                await continue_gram_bot(phone)
        else:
            await callback.message.edit_text("⏳ Капча ещё активна")
    except Exception as e:
        logging.error(f"❌ captcha_check: {e}")


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
        task_names = {
            "channels": "📢 Подписка на каналы",
            "posts": "📱 Просмотр постов"
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
        logging.error(f"❌ task_choose: {e}")


# ============ БАЗОВЫЕ ФУНКЦИИ ============

async def get_last_message(client: TelegramClient, bot_username: str):
    try:
        if not client.is_connected():
            await client.connect()
        msgs = await client.get_messages(bot_username, limit=1)
        return msgs[0] if msgs else None
    except Exception as e:
        logging.error(f"❌ get_last_message: {e}")
        return None


async def wait_for_change(
    client: TelegramClient,
    bot_username: str,
    before_id: int,
    before_snap: str,
    timeout: float = 15.0
):
    """
    Ждёт пока последнее сообщение изменится:
    - новый id (новое сообщение) ИЛИ
    - тот же id но другой контент (edit)
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.8)
        msg = await get_last_message(client, bot_username)
        if not msg:
            continue
        if msg.id != before_id:
            logging.info("📨 Получено новое сообщение")
            return msg
        if get_msg_snapshot(msg) != before_snap:
            logging.info("✏️ Сообщение отредактировано")
            return msg
    logging.warning(f"⚠️ Нет ответа за {timeout} сек")
    return await get_last_message(client, bot_username)


async def click_btn_wait(
    client: TelegramClient,
    bot_username: str,
    btn,
    timeout: float = 15.0
):
    """
    Нажимает inline-кнопку через .click() и ждёт изменения сообщения.
    """
    if not client.is_connected():
        await client.connect()

    before = await get_last_message(client, bot_username)
    before_id = before.id if before else 0
    before_snap = get_msg_snapshot(before)

    btn_text = get_button_text(btn)
    logging.info(f"🖱 Нажимаю кнопку: '{btn_text}'")

    try:
        await btn.click()
    except Exception as e:
        logging.error(f"❌ Ошибка клика '{btn_text}': {e}")
        return None

    return await wait_for_change(client, bot_username, before_id, before_snap, timeout)


async def send_text_wait(
    client: TelegramClient,
    bot_username: str,
    text: str,
    timeout: float = 15.0
):
    """
    Отправляет текстовое сообщение и ждёт ответа.
    Используется ТОЛЬКО для текстовых команд (например '👨‍💻 Заработать').
    """
    if not client.is_connected():
        await client.connect()

    before = await get_last_message(client, bot_username)
    before_id = before.id if before else 0
    before_snap = get_msg_snapshot(before)

    logging.info(f"📤 Отправляю текст: '{text}'")
    await client.send_message(bot_username, text)

    return await wait_for_change(client, bot_username, before_id, before_snap, timeout)


# ============ ПАРСИНГ КНОПОК ============

def find_row_pairs(msg) -> List[Tuple[Any, Any]]:
    """
    Ищет пары (кнопка-ссылка-на-канал, кнопка-Проверить) в одном ряду.
    Левая кнопка: URL содержит t.me/
    Правая кнопка: текст содержит 'провер'
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
    return len(find_row_pairs(msg)) > 0


def is_earn_type_menu(msg) -> bool:
    """Меню выбора типа заданий."""
    if not msg or not msg.buttons:
        return False
    earn_keywords = [
        "подписаться на канал", "вступить в группу",
        "просмотр постов", "перейти в бота",
        "поставить реакци", "премиум буст"
    ]
    for row in msg.buttons:
        for btn in row:
            t = get_button_text(btn).lower()
            if any(kw in t for kw in earn_keywords):
                return True
    return False


def _find_next_page_btn(msg):
    if not msg or not msg.buttons:
        return None
    for row in msg.buttons:
        for btn in row:
            t = get_button_text(btn).strip()
            if t in (">", "»") or ("след" in t.lower() and len(t) < 10):
                return btn
    return None


# ============ НАВИГАЦИЯ ============

async def open_earn_menu(
    client: TelegramClient, bot_username: str
) -> Optional[Any]:
    """
    Отправляет текст '👨‍💻 Заработать' и ждёт меню выбора типов.
    Это единственное место где используется send_text_wait.
    """
    logging.info("📋 Открываю меню 'Заработать'...")
    msg = await send_text_wait(client, bot_username, "👨‍💻 Заработать", timeout=15)
    if msg:
        if msg.buttons:
            btns = [f"'{get_button_text(b)}'" for row in msg.buttons for b in row]
            logging.info(f"📋 Кнопки меню: {', '.join(btns[:10])}")
    return msg


async def select_task_type_btn(
    client: TelegramClient,
    bot_username: str,
    earn_menu_msg,
    task_type: str
) -> Optional[Any]:
    """
    Нажимает inline-кнопку типа задания в меню 'Заработать'.
    Все кнопки в меню — inline, нажимаем через .click().
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
        logging.warning(f"⚠️ Кнопка типа не найдена: {keywords}")
        all_btns = [f"'{get_button_text(b)}'" for row in earn_menu_msg.buttons for b in row]
        logging.info(f"Все кнопки меню: {', '.join(all_btns)}")
        return None

    logging.info(f"✅ Нажимаю тип задания: '{get_button_text(target_btn)}'")
    return await click_btn_wait(client, bot_username, target_btn, timeout=15)


# ============ ОБРАБОТКА ЗАДАНИЙ ============

async def process_one_task(
    client: TelegramClient,
    bot_username: str,
    sub_btn,
    check_btn,
    idx: int,
    total: int
) -> Optional[Any]:
    """
    Одно задание подписки:
    1. Подписываемся на канал по URL из кнопки
    2. Ждём SUBSCRIBE_DELAY секунд
    3. Нажимаем inline-кнопку 'Проверить' через .click()
    4. Ждём изменения сообщения
    """
    url = get_button_url(sub_btn)
    btn_text = get_button_text(sub_btn)
    logging.info(f"📢 [{idx}/{total}] '{btn_text}' -> {url}")

    # Подписываемся
    success, result = await subscribe_to_channel(client, url)
    if not success and result.startswith("flood:"):
        wait_sec = int(result.split(":")[1])
        logging.warning(f"⏳ Flood {wait_sec} сек...")
        await asyncio.sleep(min(wait_sec, 300))

    await asyncio.sleep(random.uniform(2, 4))

    logging.info(f"⏳ Жду {SUBSCRIBE_DELAY} сек перед проверкой...")
    await asyncio.sleep(SUBSCRIBE_DELAY)

    current_check = check_btn
    updated_msg = None

    for attempt in range(1, 4):
        logging.info(f"🔄 Нажимаю Проверить '{btn_text}' (попытка {attempt}/3)")

        # Нажимаем inline-кнопку Проверить
        updated_msg = await click_btn_wait(
            client, bot_username, current_check, timeout=15
        )

        if not updated_msg:
            logging.warning("⚠️ Нет ответа после Проверить, читаю последнее...")
            updated_msg = await get_last_message(client, bot_username)

        if not updated_msg:
            continue

        if is_captcha_message(updated_msg):
            return updated_msg

        resp = (updated_msg.raw_text or "").lower()
        logging.info(f"📝 Ответ: {resp[:200]}")

        if "начислено" in resp or "успешно" in resp or "подписались" in resp:
            logging.info(f"💰 Начислено за '{btn_text}'!")
            return updated_msg

        if "не подписан" in resp:
            logging.warning("⚠️ Не засчитано, повторяю подписку...")
            await subscribe_to_channel(client, url)
            await asyncio.sleep(random.uniform(2, 4))
            await asyncio.sleep(SUBSCRIBE_DELAY)
            # Ищем актуальную кнопку Проверить
            new_pairs = find_row_pairs(updated_msg)
            matched = next(
                (nc for nu, nc in new_pairs if get_button_url(nu) == url),
                None
            )
            if matched:
                current_check = matched
            continue

        return updated_msg

    return updated_msg


async def process_channel_list(
    client: TelegramClient, bot_username: str, msg, phone: str
):
    """Обрабатывает список заданий постранично."""
    logging.info("📋 Обрабатываю задания на подписку...")

    page = 0
    while True:
        page += 1
        if not client.is_connected():
            await client.connect()

        if not msg or not msg.buttons:
            logging.warning("⚠️ Нет кнопок в сообщении")
            return msg

        if is_captcha_message(msg):
            return msg

        task_pairs = find_row_pairs(msg)

        if not task_pairs:
            logging.warning(f"⚠️ Страница {page}: пары не найдены")
            logging.info(f"Текст: {(msg.raw_text or '')[:200]}")
            if msg.buttons:
                btns = [f"'{get_button_text(b)}'" for row in msg.buttons for b in row]
                logging.info(f"Кнопки: {', '.join(btns)}")
            return msg

        logging.info(f"📄 Страница {page}: заданий {len(task_pairs)}")

        for i, (sub_btn, check_btn) in enumerate(task_pairs, 1):
            result_msg = await process_one_task(
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

        # Пагинация
        next_btn = _find_next_page_btn(msg)
        if next_btn:
            logging.info("➡️ Следующая страница...")
            result = await click_btn_wait(client, bot_username, next_btn, timeout=10)
            if result:
                msg = result
                continue

        logging.info("✅ Все страницы обработаны")
        return msg


# ============ ОСНОВНОЙ ЦИКЛ ============

async def do_one_cycle(
    client: TelegramClient,
    bot_username: str,
    user_id: int = None,
    phone: str = None
):
    """
    Один цикл работы:
    1. Текстом отправляем '👨‍💻 Заработать' → получаем меню типов (inline-кнопки)
    2. Нажимаем inline-кнопку типа задания → получаем список каналов
    3. Для каждого задания: подписываемся, ждём, нажимаем inline 'Проверить'
    """
    task_type = user_task_choice.get(user_id, "channels")
    logging.info(f"📋 Тип задания: {task_type}")

    # Капча?
    cur = await get_last_message(client, bot_username)
    if cur and is_captcha_message(cur):
        await send_captcha_to_user(cur, user_chat_id, client)
        return

    # ШАГ 1: Открываем меню Заработать (текстом)
    earn_menu = await open_earn_menu(client, bot_username)

    if not earn_menu:
        logging.warning("⚠️ Не получили меню Заработать")
        return

    if is_captcha_message(earn_menu):
        await send_captcha_to_user(earn_menu, user_chat_id, client)
        return

    # Если не то меню — пробуем ещё раз
    if not is_earn_type_menu(earn_menu):
        logging.warning("⚠️ Получили не меню типов, пробую снова...")
        # Иногда бот присылает что-то лишнее — читаем следующее сообщение
        await asyncio.sleep(2)
        earn_menu = await get_last_message(client, bot_username)
        if not earn_menu or not is_earn_type_menu(earn_menu):
            # Последняя попытка: Назад → Заработать
            logging.warning("⚠️ Пробую Назад → Заработать...")
            await send_text_wait(client, bot_username, "◀️ Назад", timeout=5)
            await asyncio.sleep(1)
            earn_menu = await open_earn_menu(client, bot_username)
            if not earn_menu or not is_earn_type_menu(earn_menu):
                logging.error("❌ Не удалось получить меню типов заданий")
                return

    # ШАГ 2: Нажимаем inline-кнопку типа задания
    task_list_msg = await select_task_type_btn(
        client, bot_username, earn_menu, task_type
    )

    if not task_list_msg:
        logging.warning(f"⚠️ Нет ответа после выбора типа '{task_type}'")
        return

    if is_captcha_message(task_list_msg):
        await send_captcha_to_user(task_list_msg, user_chat_id, client)
        return

    if task_list_msg.buttons:
        btns = [f"'{get_button_text(b)}'" for row in task_list_msg.buttons for b in row]
        logging.info(f"📝 После выбора типа, кнопки: {', '.join(btns[:10])}")

    # ШАГ 3: Обрабатываем
    if task_type == "channels":
        if is_task_list_message(task_list_msg):
            logging.info("📢 Список заданий получен!")
            result = await process_channel_list(
                client, bot_username, task_list_msg, phone
            )
            if result and is_captcha_message(result):
                await send_captcha_to_user(result, user_chat_id, client)
        else:
            logging.warning(
                f"⚠️ Список заданий не найден. "
                f"Текст: {(task_list_msg.raw_text or '')[:150]}"
            )
    else:
        # Просмотр постов
        wait_time = random.randint(8, 15)
        logging.info(f"👀 Читаю пост {wait_time} сек...")
        await asyncio.sleep(wait_time)

        if task_list_msg.buttons:
            confirm_kw = ["просмотрел", "готово", "получить"]
            for row in task_list_msg.buttons:
                for btn in row:
                    t = get_button_text(btn).lower()
                    if any(kw in t for kw in confirm_kw):
                        logging.info(f"✅ Подтверждаю: '{get_button_text(btn)}'")
                        result = await click_btn_wait(
                            client, bot_username, btn, timeout=10
                        )
                        if result and is_captcha_message(result):
                            await send_captcha_to_user(result, user_chat_id, client)
                        return

        logging.info("✅ Просмотр завершён")


# ============ ВОРКЕР ============

async def run_gram_worker(client: TelegramClient, bot_username: str, phone: str):
    try:
        logging.info(f"🚀 Воркер: {bot_username} | задержка: {SUBSCRIBE_DELAY} сек")

        # /start текстом
        await send_text_wait(client, bot_username, "/start", timeout=8)
        await asyncio.sleep(2)

        cycle_count = 0
        while True:
            cycle_count += 1
            logging.info(f"\n{'#'*50}\n🔁 ЦИКЛ #{cycle_count}\n{'#'*50}")
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
        logging.info(f"⏹ Воркер остановлен: {bot_username}")
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


__all__ = [
    'router', 'init_gram_bot', 'send_code', 'start_gram_bot',
    'start_gram_worker', 'stop_gram_bot', 'continue_gram_bot',
    'set_user_chat_id', 'set_bot_instance', 'get_task_choice_keyboard',
    'active_clients', 'active_tasks'
        ]
