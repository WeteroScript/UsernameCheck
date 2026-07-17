"""
Модуль для Gram ботов
"""

import asyncio
import random
import logging
import os
import sqlite3
from typing import Optional, Dict, Any, Tuple, List
from telethon import TelegramClient, errors
from telethon.tl.types import (
    KeyboardButtonUrl,
    KeyboardButtonCallback,
    KeyboardButton as ReplyKeyboardButton,
)
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


# ============ УТИЛИТЫ КНОПОК ============

def get_button_text(btn) -> str:
    """Текст кнопки."""
    try:
        return btn.text if hasattr(btn, 'text') else str(btn)
    except:
        return ""


def get_button_url(btn) -> Optional[str]:
    """
    URL из кнопки.
    Telethon оборачивает кнопки: btn — это MessageButton,
    внутри btn.button — реальный объект (KeyboardButtonUrl и т.д.)
    """
    try:
        # Прямой доступ
        if hasattr(btn, 'url') and btn.url:
            return btn.url
        # Через вложенный .button
        inner = getattr(btn, 'button', None)
        if inner and hasattr(inner, 'url') and inner.url:
            return inner.url
    except:
        pass
    return None


def is_url_btn(btn) -> bool:
    """Кнопка является URL-кнопкой (ссылка на канал)."""
    try:
        inner = getattr(btn, 'button', btn)
        return isinstance(inner, KeyboardButtonUrl)
    except:
        return False


def is_callback_btn(btn) -> bool:
    """Кнопка является callback (inline)."""
    try:
        inner = getattr(btn, 'button', btn)
        return isinstance(inner, KeyboardButtonCallback)
    except:
        return False


def get_msg_snapshot(msg) -> str:
    """Снимок сообщения для отслеживания edit."""
    if not msg:
        return ""
    parts = [msg.raw_text or ""]
    if msg.buttons:
        for row in msg.buttons:
            for btn in row:
                parts.append(get_button_text(btn))
    return "|".join(parts)


def log_msg_buttons(msg, prefix: str = ""):
    """Логирует все кнопки сообщения с их типами."""
    if not msg or not msg.buttons:
        logging.info(f"{prefix} Кнопок нет")
        return
    for ri, row in enumerate(msg.buttons):
        for bi, btn in enumerate(row):
            inner = getattr(btn, 'button', btn)
            btn_type = type(inner).__name__
            url = get_button_url(btn)
            text = get_button_text(btn)
            logging.info(
                f"{prefix} [{ri}][{bi}] тип={btn_type} "
                f"текст='{text}' url={url}"
            )


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
                h = link.split("joinchat/")[-1].split("/")[0]
                await client(ImportChatInviteRequest(h))
                logging.info(f"✅ Подписался (joinchat)")
                return True, "success"
            elif "+" in link:
                h = link.split("+")[-1].split("/")[0]
                await client(ImportChatInviteRequest(h))
                logging.info(f"✅ Подписался (invite)")
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

        return False, "unknown"

    except errors.FloodWaitError as e:
        logging.error(f"⏳ Flood: {e.seconds} сек")
        return False, f"flood:{e.seconds}"
    except Exception as e:
        err = str(e).lower()
        if "already" in err or "already participant" in err:
            logging.info(f"✅ Уже подписан")
            return True, "already"
        if "successfully requested" in err:
            logging.info(f"✅ Запрос отправлен")
            return True, "requested"
        logging.error(f"❌ Ошибка подписки: {e}")
        return False, str(e)


# ============ СЕССИИ ============

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
        logging.error(f"❌ cleanup: {e}")


def _cleanup_wal(session_name: str):
    for ext in ("-journal", "-wal", "-shm"):
        p = f"{session_name}.session{ext}"
        if os.path.exists(p):
            try:
                os.remove(p)
            except:
                pass


async def _wal_mode(client: TelegramClient):
    try:
        conn = (
            getattr(client.session, "_conn", None)
            or getattr(client.session, "conn", None)
        )
        if conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
    except:
        pass


async def send_code(phone: str, bot_username: str) -> bool:
    phone = phone.strip()
    if not phone.startswith('+'):
        phone = '+' + phone
    lock = get_session_lock(phone)
    async with lock:
        try:
            os.makedirs("sessions", exist_ok=True)
            sn = f"sessions/{phone.replace('+', '')}"
            _cleanup_wal(sn)
            client = TelegramClient(
                sn, API_ID, API_HASH,
                connection_retries=5, retry_delay=1,
                auto_reconnect=True, flood_sleep_threshold=60
            )
            await client.connect()
            await _wal_mode(client)
            if not await client.is_user_authorized():
                await client.send_code_request(phone)
                logging.info(f"📱 Код отправлен: {phone}")
            else:
                logging.info(f"✅ Уже авторизован: {phone}")
            active_clients[phone] = client
            return True
        except errors.FloodWaitError as e:
            logging.error(f"⏳ Flood: {e.seconds}")
            return False
        except errors.PhoneNumberInvalidError:
            logging.error(f"❌ Неверный номер")
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
            sn = f"sessions/{phone.replace('+', '')}"
            _cleanup_wal(sn)
            client = active_clients.get(phone)
            if not client:
                client = TelegramClient(
                    sn, API_ID, API_HASH,
                    connection_retries=5, retry_delay=1,
                    auto_reconnect=True, flood_sleep_threshold=60
                )
                await client.connect()
                await _wal_mode(client)
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
            logging.error("❌ 2FA required")
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
    client: TelegramClient, bot_username: str,
    phone: str, user_id: int = None
):
    if user_id:
        set_user_chat_id(user_id)
    if not client.is_connected():
        try:
            await client.connect()
        except Exception as e:
            logging.error(f"❌ connect: {e}")
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
        for p, t in list(active_tasks.items()):
            t.cancel()
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
                logging.error(f"❌ connect: {e}")
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
            bu = (msg.chat.username if msg.chat else None) or "gram_prbot"
        except:
            bu = "gram_prbot"

        captcha_storage[chat_id] = {
            'client': client, 'bot_username': bu, 'msg_id': msg.id
        }
        captcha_url = None
        if msg.buttons:
            for row in msg.buttons:
                for btn in row:
                    u = get_button_url(btn)
                    if u:
                        captcha_url = u
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
                    text="🔄 Проверить",
                    callback_data=f"captcha_check_{chat_id}"
                )],
                [InlineKeyboardButton(
                    text="⏹ Остановить",
                    callback_data=f"captcha_stop_{chat_id}"
                )]
            ])
            await bot_instance.send_message(chat_id, "👆", reply_markup=kb)
        return True
    except Exception as e:
        logging.error(f"❌ send_captcha: {e}")
        return False


# ============ CALLBACKS ============

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
    """Ждёт новое сообщение ИЛИ edit существующего."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.8)
        msg = await get_last_message(client, bot_username)
        if not msg:
            continue
        if msg.id != before_id:
            logging.info("📨 Новое сообщение")
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
    """Нажимает кнопку (.click()) и ждёт изменения сообщения."""
    if not client.is_connected():
        await client.connect()

    before = await get_last_message(client, bot_username)
    before_id = before.id if before else 0
    before_snap = get_msg_snapshot(before)

    logging.info(f"🖱 Нажимаю: '{get_button_text(btn)}'")
    try:
        await btn.click()
    except Exception as e:
        logging.error(f"❌ click error: {e}")
        return None

    return await wait_for_change(client, bot_username, before_id, before_snap, timeout)


async def send_text_wait(
    client: TelegramClient,
    bot_username: str,
    text: str,
    timeout: float = 15.0
):
    """Отправляет ТЕКСТ и ждёт ответа. Только для /start и Заработать."""
    if not client.is_connected():
        await client.connect()

    before = await get_last_message(client, bot_username)
    before_id = before.id if before else 0
    before_snap = get_msg_snapshot(before)

    logging.info(f"📤 Текст: '{text}'")
    await client.send_message(bot_username, text)

    return await wait_for_change(client, bot_username, before_id, before_snap, timeout)


# ============ ПАРСИНГ КНОПОК ЗАДАНИЙ ============

def find_row_pairs(msg) -> List[Tuple[Any, Any]]:
    """
    Ищет пары (кнопка-подписки, кнопка-Проверить) в одном ряду.

    Кнопка подписки: KeyboardButtonUrl с t.me/ в url
    Кнопка Проверить: KeyboardButtonCallback с текстом 'провер'

    ВАЖНО: в Telethon MessageButton обёртка над реальным типом.
    Реальный тип в btn.button
    """
    pairs = []
    if not msg or not msg.buttons:
        return pairs

    for row in msg.buttons:
        sub_btn = None
        check_btn = None

        for btn in row:
            text = get_button_text(btn).lower()
            url = get_button_url(btn)

            # Кнопка подписки — URL с t.me/
            if url and "t.me/" in url:
                sub_btn = btn
                continue

            # Кнопка проверки — callback с текстом "провер"
            if "провер" in text:
                check_btn = btn
                continue

        if sub_btn and check_btn:
            pairs.append((sub_btn, check_btn))

    return pairs


def is_task_list_message(msg) -> bool:
    """Сообщение содержит список заданий."""
    if not msg or not msg.buttons:
        return False

    # Проверяем наличие пар
    pairs = find_row_pairs(msg)
    if pairs:
        return True

    # Дополнительная проверка: есть URL t.me/ И текст "провер" хоть где-то
    has_tme_url = False
    has_check = False
    for row in msg.buttons:
        for btn in row:
            url = get_button_url(btn)
            text = get_button_text(btn).lower()
            if url and "t.me/" in url:
                has_tme_url = True
            if "провер" in text:
                has_check = True
    return has_tme_url and has_check


def is_earn_type_menu(msg) -> bool:
    """Меню выбора типа заданий."""
    if not msg or not msg.buttons:
        return False
    earn_kw = [
        "подписаться на канал", "вступить в группу",
        "просмотр постов", "перейти в бота",
        "поставить реакци", "премиум буст"
    ]
    for row in msg.buttons:
        for btn in row:
            t = get_button_text(btn).lower()
            if any(kw in t for kw in earn_kw):
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

async def open_earn_menu(client: TelegramClient, bot_username: str):
    """Отправляет текст 'Заработать', возвращает меню типов."""
    logging.info("📋 Открываю 'Заработать'...")
    msg = await send_text_wait(client, bot_username, "👨‍💻 Заработать", timeout=15)
    if msg:
        log_msg_buttons(msg, prefix="[earn_menu]")
    return msg


async def select_task_type(
    client: TelegramClient,
    bot_username: str,
    earn_menu_msg,
    task_type: str
):
    """Нажимает кнопку типа задания (inline .click())."""
    keywords = (
        ["подписаться на канал"]
        if task_type == "channels"
        else ["просмотр постов"]
    )

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
        log_msg_buttons(earn_menu_msg, "[earn_menu_all]")
        return None

    logging.info(f"✅ Нажимаю тип: '{get_button_text(target_btn)}'")
    result = await click_btn_wait(client, bot_username, target_btn, timeout=15)
    if result:
        log_msg_buttons(result, "[after_type_select]")
    return result


# ============ ОБРАБОТКА ОДНОГО ЗАДАНИЯ ============

async def process_one_task(
    client: TelegramClient,
    bot_username: str,
    sub_btn,
    check_btn,
    idx: int,
    total: int
) -> Optional[Any]:
    """
    Подписка + Проверить для одного задания.
    sub_btn  — URL-кнопка (ссылка на канал)
    check_btn — callback-кнопка "Проверить"
    """
    url = get_button_url(sub_btn)
    btn_text = get_button_text(sub_btn)
    logging.info(f"📢 [{idx}/{total}] '{btn_text}' url={url}")

    if not url:
        logging.error(f"❌ URL не найден в кнопке '{btn_text}'")
        log_msg_buttons(
            type('M', (), {'buttons': [[sub_btn]]})(),
            "[bad_btn]"
        )
        return None

    # Подписываемся
    success, result = await subscribe_to_channel(client, url)
    if not success and result.startswith("flood:"):
        wait_sec = int(result.split(":")[1])
        logging.warning(f"⏳ Flood {wait_sec} сек...")
        await asyncio.sleep(min(wait_sec, 300))

    await asyncio.sleep(random.uniform(2, 4))
    logging.info(f"⏳ Жду {SUBSCRIBE_DELAY} сек...")
    await asyncio.sleep(SUBSCRIBE_DELAY)

    current_check = check_btn
    updated_msg = None

    for attempt in range(1, 4):
        logging.info(f"🔄 Проверить (попытка {attempt}/3)")
        updated_msg = await click_btn_wait(
            client, bot_username, current_check, timeout=15
        )

        if not updated_msg:
            updated_msg = await get_last_message(client, bot_username)

        if not updated_msg:
            continue

        if is_captcha_message(updated_msg):
            return updated_msg

        resp = (updated_msg.raw_text or "").lower()
        logging.info(f"📝 Ответ: {resp[:200]}")

        if any(w in resp for w in ["начислено", "успешно", "подписались"]):
            logging.info(f"💰 Начислено!")
            return updated_msg

        if "не подписан" in resp:
            logging.warning("⚠️ Не засчитано, повторяю...")
            await subscribe_to_channel(client, url)
            await asyncio.sleep(random.uniform(2, 4))
            await asyncio.sleep(SUBSCRIBE_DELAY)
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


# ============ ОБРАБОТКА СПИСКА ЗАДАНИЙ ============

async def process_channel_list(
    client: TelegramClient, bot_username: str, msg, phone: str
):
    """Обрабатывает список заданий постранично."""
    logging.info("📋 Обрабатываю задания...")
    page = 0

    while True:
        page += 1
        if not client.is_connected():
            await client.connect()

        if not msg or not msg.buttons:
            logging.warning("⚠️ Нет кнопок")
            return msg

        if is_captcha_message(msg):
            return msg

        task_pairs = find_row_pairs(msg)

        if not task_pairs:
            logging.warning(f"⚠️ Стр.{page}: пары не найдены")
            log_msg_buttons(msg, f"[page{page}]")
            return msg

        logging.info(f"📄 Стр.{page}: заданий {len(task_pairs)}")

        for i, (sub_btn, check_btn) in enumerate(task_pairs, 1):
            result_msg = await process_one_task(
                client, bot_username, sub_btn, check_btn, i, len(task_pairs)
            )
            if result_msg:
                if is_captcha_message(result_msg):
                    return result_msg
                msg = result_msg

        fresh = await get_last_message(client, bot_username)
        if fresh:
            msg = fresh

        if is_captcha_message(msg):
            return msg

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
    task_type = user_task_choice.get(user_id, "channels")
    logging.info(f"📋 Тип: {task_type}")

    cur = await get_last_message(client, bot_username)
    if cur and is_captcha_message(cur):
        await send_captcha_to_user(cur, user_chat_id, client)
        return

    # Шаг 1: открываем меню Заработать (текстом)
    earn_menu = await open_earn_menu(client, bot_username)
    if not earn_menu:
        logging.warning("⚠️ Нет меню Заработать")
        return
    if is_captcha_message(earn_menu):
        await send_captcha_to_user(earn_menu, user_chat_id, client)
        return

    if not is_earn_type_menu(earn_menu):
        logging.warning("⚠️ Не то меню, жду ещё...")
        await asyncio.sleep(2)
        earn_menu = await get_last_message(client, bot_username)
        if not earn_menu or not is_earn_type_menu(earn_menu):
            await send_text_wait(client, bot_username, "◀️ Назад", timeout=5)
            await asyncio.sleep(1)
            earn_menu = await open_earn_menu(client, bot_username)
            if not earn_menu or not is_earn_type_menu(earn_menu):
                logging.error("❌ Не удалось получить меню типов")
                return

    # Шаг 2: нажимаем кнопку типа задания (inline .click())
    task_list_msg = await select_task_type(
        client, bot_username, earn_menu, task_type
    )
    if not task_list_msg:
        logging.warning("⚠️ Нет ответа после выбора типа")
        return
    if is_captcha_message(task_list_msg):
        await send_captcha_to_user(task_list_msg, user_chat_id, client)
        return

    # Шаг 3: обрабатываем
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
                f"⚠️ Список не найден. "
                f"Текст: '{(task_list_msg.raw_text or '')[:100]}'"
            )
            log_msg_buttons(task_list_msg, "[no_task_list]")
    else:
        wait_time = random.randint(8, 15)
        logging.info(f"👀 Читаю пост {wait_time} сек...")
        await asyncio.sleep(wait_time)
        if task_list_msg.buttons:
            for row in task_list_msg.buttons:
                for btn in row:
                    t = get_button_text(btn).lower()
                    if any(kw in t for kw in ["просмотрел", "готово", "получить"]):
                        result = await click_btn_wait(
                            client, bot_username, btn, timeout=10
                        )
                        if result and is_captcha_message(result):
                            await send_captcha_to_user(result, user_chat_id, client)
                        return


# ============ ВОРКЕР ============

async def run_gram_worker(client: TelegramClient, bot_username: str, phone: str):
    try:
        logging.info(f"🚀 Старт: {bot_username}")
        await send_text_wait(client, bot_username, "/start", timeout=8)
        await asyncio.sleep(2)

        cycle = 0
        while True:
            cycle += 1
            logging.info(f"\n{'#'*50}\n🔁 ЦИКЛ #{cycle}\n{'#'*50}")
            try:
                await do_one_cycle(client, bot_username, user_chat_id, phone)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logging.error(f"❌ Цикл: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(5)

            p = random.randint(5, 10)
            logging.info(f"⏸️ Пауза {p} сек...")
            await asyncio.sleep(p)

    except asyncio.CancelledError:
        logging.info(f"⏹ Остановлен: {bot_username}")
    except Exception as e:
        logging.error(f"❌ Критично: {e}")
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
        logging.info("✅ Gram бот инициализирован")


__all__ = [
    'router', 'init_gram_bot', 'send_code', 'start_gram_bot',
    'start_gram_worker', 'stop_gram_bot', 'continue_gram_bot',
    'set_user_chat_id', 'set_bot_instance', 'get_task_choice_keyboard',
    'active_clients', 'active_tasks'
]
