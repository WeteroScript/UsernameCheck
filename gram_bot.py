"""
Модуль для Gram ботов (PR GRAM | DRAGON)
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
session_locks: Dict[str, asyncio.Lock] = {}

SUBSCRIBE_DELAY = 60


def get_session_lock(phone: str) -> asyncio.Lock:
    if phone not in session_locks:
        session_locks[phone] = asyncio.Lock()
    return session_locks[phone]


def set_bot_instance(bot):
    global bot_instance
    bot_instance = bot


def set_user_chat_id(chat_id: int):
    global user_chat_id
    user_chat_id = chat_id


def get_task_choice_keyboard(user_id: int) -> InlineKeyboardMarkup:
    task_types = {
        "channels": "📢 Подписка на каналы",
        "posts": "📱 Просмотр постов",
    }
    buttons = []
    for task_key, task_name in task_types.items():
        is_selected = user_task_choice.get(user_id) == task_key
        text = f"{'✅ ' if is_selected else ''}{task_name}"
        buttons.append([InlineKeyboardButton(
            text=text, callback_data=f"task_choose_{task_key}"
        )])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="gram")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ============================================================
# УТИЛИТЫ КНОПОК
# ============================================================

def btn_text(btn) -> str:
    try:
        return btn.text if hasattr(btn, 'text') else ""
    except:
        return ""


def btn_url(btn) -> Optional[str]:
    """
    Получить URL из кнопки Telethon.
    MessageButton оборачивает реальный объект в .button
    """
    try:
        if hasattr(btn, 'url') and btn.url:
            return btn.url
        inner = getattr(btn, 'button', None)
        if inner is not None and hasattr(inner, 'url') and inner.url:
            return inner.url
    except:
        pass
    return None


def is_tg_url(url: Optional[str]) -> bool:
    """
    Проверяет что URL ведёт на Telegram.
    Поддерживает ОБА формата:
      - https://t.me/username
      - https://telegram.me/username  <-- именно это было в логах!
    """
    if not url:
        return False
    u = url.lower()
    return "t.me/" in u or "telegram.me/" in u


def msg_snap(msg) -> str:
    """Снимок сообщения для определения изменений."""
    if not msg:
        return ""
    parts = [msg.raw_text or ""]
    if msg.buttons:
        for row in msg.buttons:
            for b in row:
                parts.append(btn_text(b))
    return "|".join(parts)


def log_buttons(msg, tag: str = ""):
    """Логирует кнопки сообщения с типами и URL."""
    if not msg or not msg.buttons:
        return
    for ri, row in enumerate(msg.buttons):
        for bi, b in enumerate(row):
            inner = getattr(b, 'button', b)
            t = type(inner).__name__
            u = btn_url(b)
            logging.info(f"  {tag}[{ri}][{bi}] {t} | '{btn_text(b)}' | url={u}")


# ============================================================
# TELETHON: БАЗОВЫЕ ОПЕРАЦИИ
# ============================================================

async def get_last_msg(client: TelegramClient, bot_username: str):
    try:
        if not client.is_connected():
            await client.connect()
        msgs = await client.get_messages(bot_username, limit=1)
        return msgs[0] if msgs else None
    except Exception as e:
        logging.error(f"❌ get_last_msg: {e}")
        return None


async def wait_bot_response(
    client: TelegramClient,
    bot_username: str,
    snap_before: str,
    id_before: int,
    timeout: float = 15.0
):
    """
    Ждёт пока бот ответит.
    Бот может:
    1. Прислать НОВОЕ сообщение (другой id)
    2. ОТРЕДАКТИРОВАТЬ существующее (тот же id, другой контент)
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.5)
        msg = await get_last_msg(client, bot_username)
        if not msg:
            continue
        if msg.id != id_before:
            logging.info("📨 Бот прислал новое сообщение")
            return msg
        if msg_snap(msg) != snap_before:
            logging.info("✏️ Бот отредактировал сообщение")
            return msg
    logging.warning(f"⚠️ Нет ответа за {timeout} сек")
    return await get_last_msg(client, bot_username)


async def send_text(
    client: TelegramClient,
    bot_username: str,
    text: str,
    timeout: float = 15.0
):
    """Отправить ТЕКСТ боту и ждать ответа."""
    if not client.is_connected():
        await client.connect()
    before = await get_last_msg(client, bot_username)
    snap = msg_snap(before)
    mid = before.id if before else 0
    logging.info(f"📤 Текст: '{text}'")
    await client.send_message(bot_username, text)
    return await wait_bot_response(client, bot_username, snap, mid, timeout)


async def click_btn(
    client: TelegramClient,
    bot_username: str,
    btn,
    timeout: float = 15.0
):
    """Нажать КНОПКУ и ждать ответа бота."""
    if not client.is_connected():
        await client.connect()
    before = await get_last_msg(client, bot_username)
    snap = msg_snap(before)
    mid = before.id if before else 0
    logging.info(f"🖱 Кнопка: '{btn_text(btn)}'")
    try:
        await btn.click()
    except Exception as e:
        logging.error(f"❌ click: {e}")
        return None
    return await wait_bot_response(client, bot_username, snap, mid, timeout)


# ============================================================
# ПОДПИСКА НА КАНАЛ
# ============================================================

async def subscribe(client: TelegramClient, url: str) -> Tuple[bool, str]:
    try:
        logging.info(f"📢 Подписываюсь: {url}")
        if not client.is_connected():
            await client.connect()

        # Убираем параметры
        if "?" in url:
            url = url.split("?")[0]

        # Нормализуем telegram.me → t.me
        url = url.replace("https://telegram.me/", "https://t.me/")
        url = url.replace("http://telegram.me/", "https://t.me/")

        if "t.me/" in url:
            path = url.split("t.me/")[-1].rstrip("/")

            if path.startswith("+"):
                # Инвайт-ссылка
                h = path[1:].split("/")[0]
                await client(ImportChatInviteRequest(h))
            elif "joinchat/" in url:
                h = url.split("joinchat/")[-1].split("/")[0]
                await client(ImportChatInviteRequest(h))
            else:
                # Публичный канал/группа
                username = path.split("/")[0]
                entity = await client.get_entity(f"@{username}")
                await client(JoinChannelRequest(entity))

            logging.info(f"✅ Подписался: {url}")
            return True, "success"

        return False, f"unknown url format: {url}"

    except errors.FloodWaitError as e:
        logging.error(f"⏳ Flood: {e.seconds} сек")
        return False, f"flood:{e.seconds}"
    except Exception as e:
        err = str(e).lower()
        if "already" in err or "already participant" in err:
            logging.info("✅ Уже подписан")
            return True, "already"
        if "successfully requested" in err:
            logging.info("✅ Запрос отправлен")
            return True, "requested"
        logging.error(f"❌ Ошибка подписки {url}: {e}")
        return False, str(e)


# ============================================================
# ПАРСИНГ ЗАДАНИЙ
# ============================================================

def get_task_pairs(msg) -> List[Tuple[Any, Any]]:
    """
    Возвращает список пар (кнопка_канала, кнопка_проверить).

    Структура строки в PR GRAM:
      [0] KeyboardButtonUrl  — ссылка на канал (telegram.me/ или t.me/)
      [1] KeyboardButtonCallback — кнопка "🔄 Проверить"

    ВАЖНО: проверяем is_tg_url() который принимает оба формата!
    """
    pairs = []
    if not msg or not msg.buttons:
        return pairs

    for row in msg.buttons:
        sub = None
        chk = None
        for b in row:
            u = btn_url(b)
            t = btn_text(b).lower()
            # Кнопка подписки — есть URL на telegram
            if is_tg_url(u):
                sub = b
            # Кнопка проверки
            elif "провер" in t:
                chk = b
        if sub and chk:
            pairs.append((sub, chk))

    return pairs


def is_earn_type_menu(msg) -> bool:
    """Меню выбора типа задания."""
    if not msg or not msg.buttons:
        return False
    kws = [
        "подписаться на канал", "вступить в группу",
        "просмотр постов", "перейти в бота",
        "поставить реакци", "премиум буст"
    ]
    for row in msg.buttons:
        for b in row:
            t = btn_text(b).lower()
            if any(k in t for k in kws):
                return True
    return False


def is_captcha_message(msg) -> bool:
    if not msg:
        return False
    t = (msg.raw_text or "").lower()
    return any(k in t for k in [
        "подтвердите, что вы человек", "captcha", "verify you are human"
    ])


def find_next_page(msg):
    if not msg or not msg.buttons:
        return None
    for row in msg.buttons:
        for b in row:
            t = btn_text(b).strip()
            if t == ">" or t == "»":
                return b
    return None


# ============================================================
# ОБРАБОТКА ЗАДАНИЙ
# ============================================================

async def process_tasks(
    client: TelegramClient,
    bot_username: str,
    msg
):
    """
    Главная функция обработки заданий.

    Для каждой строки в сообщении:
    1. Берём URL из левой кнопки → подписываемся
    2. Ждём SUBSCRIBE_DELAY секунд
    3. Нажимаем правую кнопку "Проверить"
    4. Смотрим ответ бота
    """
    page = 0

    while True:
        page += 1
        pairs = get_task_pairs(msg)

        if not pairs:
            logging.warning(f"⚠️ Страница {page}: заданий не найдено")
            log_buttons(msg, "  ")
            return msg

        logging.info(f"📄 Страница {page}: {len(pairs)} заданий")

        for i, (sub_btn, chk_btn) in enumerate(pairs, 1):
            url = btn_url(sub_btn)
            name = btn_text(sub_btn)
            logging.info(f"\n--- [{i}/{len(pairs)}] '{name}' ---")
            logging.info(f"URL: {url}")

            # Шаг 1: Подписываемся
            ok, res = await subscribe(client, url)
            if not ok and res.startswith("flood:"):
                secs = int(res.split(":")[1])
                logging.warning(f"⏳ Flood {secs} сек")
                await asyncio.sleep(min(secs, 300))

            await asyncio.sleep(random.uniform(2, 4))

            # Шаг 2: Ждём перед проверкой
            logging.info(f"⏳ Жду {SUBSCRIBE_DELAY} сек перед проверкой...")
            await asyncio.sleep(SUBSCRIBE_DELAY)

            # Шаг 3: Нажимаем "Проверить"
            cur_chk = chk_btn
            for attempt in range(1, 4):
                logging.info(f"🔄 Проверить (попытка {attempt}/3)")
                result = await click_btn(client, bot_username, cur_chk, timeout=15)

                if not result:
                    result = await get_last_msg(client, bot_username)

                if not result:
                    await asyncio.sleep(2)
                    continue

                if is_captcha_message(result):
                    return result

                resp = (result.raw_text or "").lower()
                logging.info(f"📝 Ответ: {resp[:200]}")

                if any(w in resp for w in ["начислено", "успешно", "подписались"]):
                    logging.info(f"💰 Начислено за '{name}'!")
                    msg = result
                    break

                if "не подписан" in resp:
                    logging.warning("⚠️ Не засчитано, повторяю...")
                    await subscribe(client, url)
                    await asyncio.sleep(random.uniform(2, 4))
                    await asyncio.sleep(SUBSCRIBE_DELAY)
                    # Обновляем кнопку Проверить
                    new_pairs = get_task_pairs(result)
                    matched = next(
                        (c for s, c in new_pairs if btn_url(s) == url),
                        None
                    )
                    if matched:
                        cur_chk = matched
                    msg = result
                    continue

                msg = result
                break

        # Пагинация
        fresh = await get_last_msg(client, bot_username)
        if fresh:
            msg = fresh

        if is_captcha_message(msg):
            return msg

        nxt = find_next_page(msg)
        if nxt:
            logging.info("➡️ Следующая страница...")
            r = await click_btn(client, bot_username, nxt, timeout=10)
            if r:
                msg = r
                continue

        logging.info("✅ Все задания обработаны")
        return msg


# ============================================================
# ОСНОВНОЙ ЦИКЛ
# ============================================================

async def do_cycle(
    client: TelegramClient,
    bot_username: str,
    user_id: int,
    phone: str
):
    """
    Один цикл:
    1. Текст "👨‍💻 Заработать" → меню типов
    2. Клик по "📢 Подписаться на канал" → список заданий
    3. Для каждого задания: subscribe() → ждём → click("Проверить")
    """
    task_type = user_task_choice.get(user_id, "channels")

    # Проверяем капчу
    cur = await get_last_msg(client, bot_username)
    if cur and is_captcha_message(cur):
        await send_captcha_to_user(cur, user_chat_id, client)
        return

    # ШАГ 1: открываем меню Заработать
    earn_msg = await send_text(client, bot_username, "👨‍💻 Заработать", timeout=15)
    if not earn_msg:
        logging.warning("⚠️ Нет ответа на Заработать")
        return
    if is_captcha_message(earn_msg):
        await send_captcha_to_user(earn_msg, user_chat_id, client)
        return

    # Ждём если меню ещё не пришло
    if not is_earn_type_menu(earn_msg):
        await asyncio.sleep(2)
        earn_msg = await get_last_msg(client, bot_username)

    if not earn_msg or not is_earn_type_menu(earn_msg):
        logging.warning("⚠️ Не получили меню типов заданий")
        return

    logging.info("✅ Меню типов заданий получено")

    # ШАГ 2: нажимаем кнопку типа задания
    kw = "подписаться на канал" if task_type == "channels" else "просмотр постов"
    target_btn = None
    for row in earn_msg.buttons:
        for b in row:
            if kw in btn_text(b).lower():
                target_btn = b
                break
        if target_btn:
            break

    if not target_btn:
        logging.warning(f"⚠️ Кнопка '{kw}' не найдена")
        log_buttons(earn_msg, "  ")
        return

    task_msg = await click_btn(client, bot_username, target_btn, timeout=15)
    if not task_msg:
        logging.warning("⚠️ Нет ответа после нажатия типа задания")
        return
    if is_captcha_message(task_msg):
        await send_captcha_to_user(task_msg, user_chat_id, client)
        return

    # ШАГ 3: обрабатываем задания
    if task_type == "channels":
        pairs = get_task_pairs(task_msg)
        logging.info(f"📋 Найдено заданий: {len(pairs)}")

        if not pairs:
            logging.warning("⚠️ Заданий нет в сообщении:")
            logging.warning(f"Текст: '{(task_msg.raw_text or '')[:150]}'")
            log_buttons(task_msg, "  ")
            return

        result = await process_tasks(client, bot_username, task_msg)
        if result and is_captcha_message(result):
            await send_captcha_to_user(result, user_chat_id, client)

    else:
        # Просмотр постов
        wait_time = random.randint(8, 15)
        logging.info(f"👀 Читаю пост {wait_time} сек...")
        await asyncio.sleep(wait_time)
        if task_msg.buttons:
            for row in task_msg.buttons:
                for b in row:
                    t = btn_text(b).lower()
                    if any(k in t for k in ["просмотрел", "готово", "получить"]):
                        r = await click_btn(client, bot_username, b, timeout=10)
                        if r and is_captcha_message(r):
                            await send_captcha_to_user(r, user_chat_id, client)
                        return


# ============================================================
# КАПЧА
# ============================================================

async def send_captcha_to_user(msg, chat_id: int, client: TelegramClient) -> bool:
    if not chat_id or not bot_instance:
        return False
    try:
        try:
            bu = (msg.chat.username if msg.chat else None) or "gram_prbot"
        except:
            bu = "gram_prbot"
        captcha_storage[chat_id] = {'client': client, 'bot_username': bu}
        captcha_url = None
        if msg.buttons:
            for row in msg.buttons:
                for b in row:
                    u = btn_url(b)
                    if u:
                        captcha_url = u
                        break
                if captcha_url:
                    break
        if captcha_url:
            await bot_instance.send_message(
                chat_id,
                f"🔗 <b>Капча!</b>\n<code>{captcha_url}</code>",
                parse_mode=ParseMode.HTML
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🌐 Открыть", url=captcha_url)],
                [InlineKeyboardButton(
                    text="🔄 Проверить",
                    callback_data=f"captcha_check_{chat_id}"
                )],
                [InlineKeyboardButton(
                    text="⏹ Стоп",
                    callback_data=f"captcha_stop_{chat_id}"
                )]
            ])
            await bot_instance.send_message(chat_id, "Пройди и нажми Проверить", reply_markup=kb)
        return True
    except Exception as e:
        logging.error(f"❌ send_captcha: {e}")
        return False


# ============================================================
# CALLBACKS
# ============================================================

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
        new_msg = await get_last_msg(client, bot_username)
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
        await callback.message.edit_text("⏹ Остановлен")
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
    except Exception as e:
        logging.error(f"❌ task_choose: {e}")


# ============================================================
# СЕССИИ
# ============================================================

def cleanup_session_files(phone: str):
    try:
        pc = phone.replace('+', '')
        if not os.path.isdir("sessions"):
            return
        for f in os.listdir("sessions"):
            if f.startswith(pc):
                try:
                    os.remove(os.path.join("sessions", f))
                except:
                    pass
    except:
        pass


def _cleanup_wal(sn: str):
    for ext in ("-journal", "-wal", "-shm"):
        p = f"{sn}.session{ext}"
        if os.path.exists(p):
            try:
                os.remove(p)
            except:
                pass


async def _wal(client: TelegramClient):
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
            await _wal(client)
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
            logging.error("❌ Неверный номер")
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
                await _wal(client)
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
            logging.error("❌ 2FA")
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


# ============================================================
# ВОРКЕР
# ============================================================

async def run_gram_worker(client: TelegramClient, bot_username: str, phone: str):
    try:
        logging.info(f"🚀 Старт: {bot_username} | задержка: {SUBSCRIBE_DELAY} сек")
        await send_text(client, bot_username, "/start", timeout=8)
        await asyncio.sleep(2)

        cycle = 0
        while True:
            cycle += 1
            logging.info(f"\n{'='*50}\n🔁 ЦИКЛ #{cycle}\n{'='*50}")
            try:
                await do_cycle(client, bot_username, user_chat_id, phone)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logging.error(f"❌ Ошибка цикла: {e}")
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
