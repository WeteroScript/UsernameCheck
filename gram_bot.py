"""
Модуль для Gram ботов (PR GRAM | DRAGON)
С поддержкой заданий с ботами (генерация фото)
"""

import asyncio
import random
import logging
import os
import sqlite3
import io
from typing import Optional, Dict, Any, Tuple, List
from telethon import TelegramClient, errors
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from aiogram import Router, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.enums import ParseMode

# PIL для генерации фото
from PIL import Image, ImageDraw, ImageFont
import colorsys

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
BOT_TASK_DELAY = 30


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
        "bots": "🤖 Задания с ботами",
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
# ГЕНЕРАЦИЯ ФОТО ДЛЯ ЗАДАНИЙ С БОТАМИ
# ============================================================

def generate_bot_image() -> bytes:
    """
    Генерирует чёрный квадрат 1080x1080 с радужной надписью @Bot_Farmers
    Возвращает bytes для отправки
    """
    size = 1080
    image = Image.new('RGB', (size, size), 'black')
    draw = ImageDraw.Draw(image)
    
    text = "@Bot_Farmers"
    
    # Пытаемся загрузить шрифт
    font = None
    font_paths = [
        '/system/fonts/Roboto-Regular.ttf',
        '/system/fonts/Roboto-Bold.ttf',
        '/system/fonts/Roboto-Black.ttf',
        '/system/fonts/DroidSans-Bold.ttf',
        '/system/fonts/NotoSans-Bold.ttf',
        '/system/fonts/SystemFont.ttf'
    ]
    
    for path in font_paths:
        try:
            font = ImageFont.truetype(path, 80)
            break
        except:
            continue
    
    if font is None:
        # Если шрифт не найден — используем стандартный
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 80)
        except:
            font = ImageFont.load_default()
    
    # Рисуем радужный текст
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (size - tw) // 2
    y = (size - th) // 2
    
    x_pos = x
    for i, char in enumerate(text):
        hue = i / len(text)
        rgb = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
        color = tuple(int(c * 255) for c in rgb)
        draw.text((x_pos, y), char, fill=color, font=font)
        char_bbox = draw.textbbox((0, 0), char, font=font)
        x_pos += char_bbox[2] - char_bbox[0]
    
    # Сохраняем в bytes
    img_bytes = io.BytesIO()
    image.save(img_bytes, format='PNG')
    img_bytes.seek(0)
    
    return img_bytes.read()


# ============================================================
# УТИЛИТЫ КНОПОК
# ============================================================

def btn_text(btn) -> str:
    try:
        return btn.text if hasattr(btn, 'text') else ""
    except:
        return ""


def btn_url(btn) -> Optional[str]:
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
    if not url:
        return False
    u = url.lower()
    return "t.me/" in u or "telegram.me/" in u


def msg_snap(msg) -> str:
    if not msg:
        return ""
    parts = [msg.raw_text or ""]
    if msg.buttons:
        for row in msg.buttons:
            for b in row:
                parts.append(btn_text(b))
    return "|".join(parts)


def log_buttons(msg, tag: str = ""):
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


async def send_photo(
    client: TelegramClient,
    bot_username: str,
    photo_bytes: bytes,
    timeout: float = 15.0
):
    """Отправка фото в Gram бота и ожидание ответа"""
    if not client.is_connected():
        await client.connect()
    before = await get_last_msg(client, bot_username)
    snap = msg_snap(before)
    mid = before.id if before else 0
    logging.info(f"📸 Отправляю фото в {bot_username}")
    try:
        await client.send_file(bot_username, io.BytesIO(photo_bytes))
    except Exception as e:
        logging.error(f"❌ Ошибка отправки фото: {e}")
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

        if "?" in url:
            url = url.split("?")[0]

        url = url.replace("https://telegram.me/", "https://t.me/")
        url = url.replace("http://telegram.me/", "https://t.me/")

        if "t.me/" in url:
            path = url.split("t.me/")[-1].rstrip("/")

            if path.startswith("+"):
                h = path[1:].split("/")[0]
                await client(ImportChatInviteRequest(h))
            elif "joinchat/" in url:
                h = url.split("joinchat/")[-1].split("/")[0]
                await client(ImportChatInviteRequest(h))
            else:
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
# ЗАДАНИЯ С БОТАМИ (ОБНОВЛЕННАЯ ВЕРСИЯ)
# ============================================================

async def process_bot_tasks(client: TelegramClient, bot_username: str, msg):
    """
    Обработка заданий с ботами.
    Логика:
    1. Нажимаем на кнопку "Перейти в бота | +X GRAM"
    2. Генерируем чёрный квадрат с @Bot_Farmers
    3. Отправляем фото в Gram бота
    4. Нажимаем "Следующий бот"
    5. Нажимаем "Скрыть"
    """
    try:
        logging.info("🤖 Обрабатываю задания с ботами...")
        
        if not msg or not msg.buttons:
            logging.warning("⚠️ Нет кнопок в сообщении")
            return msg
        
        # Собираем все кнопки с ботами
        bot_buttons = []
        for row in msg.buttons:
            for b in row:
                t = btn_text(b).lower()
                if "перейти в бота" in t:
                    bot_buttons.append(b)
                    logging.info(f"🔗 Найдена кнопка бота: '{btn_text(b)}'")
        
        if not bot_buttons:
            logging.warning("⚠️ Не найдены кнопки с ботами")
            return msg
        
        logging.info(f"🤖 Найдено {len(bot_buttons)} заданий с ботами")
        
        # Генерируем фото один раз для всех заданий
        photo_bytes = generate_bot_image()
        logging.info("✅ Фото сгенерировано")
        
        for i, btn in enumerate(bot_buttons, 1):
            logging.info(f"\n--- Задание с ботом {i}/{len(bot_buttons)} ---")
            
            # 1. Нажимаем на кнопку бота
            result = await click_btn(client, bot_username, btn, timeout=15)
            if not result:
                logging.warning(f"⚠️ Нет ответа на бота {i}")
                continue
            
            if is_captcha_message(result):
                await send_captcha_to_user(result, user_chat_id, client)
                continue
            
            await asyncio.sleep(1)
            
            # 2. Отправляем сгенерированное фото в Gram бота
            photo_result = await send_photo(client, bot_username, photo_bytes, timeout=15)
            if not photo_result:
                logging.warning(f"⚠️ Нет ответа на фото {i}")
                continue
            
            if is_captcha_message(photo_result):
                await send_captcha_to_user(photo_result, user_chat_id, client)
                continue
            
            await asyncio.sleep(1)
            
            # 3. Ищем кнопку "Следующий бот"
            next_btn = None
            for row in photo_result.buttons:
                for b in row:
                    t = btn_text(b).lower()
                    if "следующий бот" in t or "⏭️" in t:
                        next_btn = b
                        break
                if next_btn:
                    break
            
            if next_btn:
                logging.info("⏭️ Нажимаю 'Следующий бот'...")
                hide_result = await click_btn(client, bot_username, next_btn, timeout=15)
                
                if hide_result and not is_captcha_message(hide_result):
                    # 4. Ищем кнопку "Скрыть"
                    hide_btn = None
                    for row in hide_result.buttons:
                        for b in row:
                            if "скрыть" in btn_text(b).lower():
                                hide_btn = b
                                break
                        if hide_btn:
                            break
                    
                    if hide_btn:
                        logging.info("🔘 Нажимаю 'Скрыть'...")
                        await click_btn(client, bot_username, hide_btn, timeout=5)
                    else:
                        logging.info("⏭️ Отправляю 'Следующий бот' еще раз...")
                        # Если нет "Скрыть", пробуем еще раз нажать "Следующий бот"
                        for row in hide_result.buttons:
                            for b in row:
                                if "следующий бот" in btn_text(b).lower() or "⏭️" in btn_text(b):
                                    await click_btn(client, bot_username, b, timeout=5)
                                    break
                            else:
                                continue
                            break
            else:
                logging.warning("⚠️ Кнопка 'Следующий бот' не найдена")
            
            # Пауза между заданиями
            if i < len(bot_buttons):
                delay = random.randint(3, 6)
                logging.info(f"⏳ Пауза {delay} сек...")
                await asyncio.sleep(delay)
        
        logging.info("✅ Все задания с ботами обработаны")
        return await get_last_msg(client, bot_username)
        
    except Exception as e:
        logging.error(f"❌ Ошибка обработки заданий с ботами: {e}")
        return msg


# ============================================================
# ПАРСИНГ ЗАДАНИЙ
# ============================================================

def get_task_pairs(msg) -> List[Tuple[Any, Any]]:
    pairs = []
    if not msg or not msg.buttons:
        return pairs

    for row in msg.buttons:
        sub = None
        chk = None
        for b in row:
            u = btn_url(b)
            t = btn_text(b).lower()
            if is_tg_url(u):
                sub = b
            elif "провер" in t or "✅" in t:
                chk = b
        if sub and chk:
            pairs.append((sub, chk))

    return pairs


def is_earn_type_menu(msg) -> bool:
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
        "подтвердите, что вы человек", "captcha", "verify you are human",
        "на какой фотографии изображён", "выберите правильный ответ"
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
        
        text = f"🚨 <b>Обнаружена капча!</b>\n\n"
        text += f"🤖 Бот: @{bu}\n\n"
        if msg.raw_text:
            text += f"📝 {msg.raw_text}\n\n"
        text += f"👇 Нажми на кнопку с правильным ответом"
        
        await bot_instance.send_message(chat_id, text, parse_mode=ParseMode.HTML)
        
        if msg.photo:
            try:
                file_data = await client.download_media(msg, file=bytes)
                if file_data:
                    await bot_instance.send_photo(
                        chat_id,
                        BufferedInputFile(file_data, filename="captcha.jpg"),
                        caption="🖼 Выбери правильный ответ"
                    )
                    logging.info(f"✅ Фото капчи отправлено")
            except Exception as e:
                logging.error(f"❌ Ошибка отправки фото: {e}")
        
        buttons = []
        row = []
        for i in range(1, 10):
            row.append(InlineKeyboardButton(
                text=str(i),
                callback_data=f"captcha_answer_{chat_id}_{i}"
            ))
            if len(row) == 3:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        
        buttons.append([
            InlineKeyboardButton(text="🔄 Проверить", callback_data=f"captcha_check_{chat_id}"),
            InlineKeyboardButton(text="⏹ Отмена", callback_data=f"captcha_stop_{chat_id}")
        ])
        
        await bot_instance.send_message(
            chat_id,
            "🔢 Нажми номер правильного ответа:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )
        
        return True
        
    except Exception as e:
        logging.error(f"❌ send_captcha: {e}")
        return False


# ============================================================
# ОБРАБОТКА ЗАДАНИЙ
# ============================================================

async def process_tasks(
    client: TelegramClient,
    bot_username: str,
    msg,
    task_type: str = "channels"
):
    """
    Обработка заданий в зависимости от типа
    """
    if task_type == "bots":
        return await process_bot_tasks(client, bot_username, msg)
    
    # Стандартная обработка для channels и posts
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

            ok, res = await subscribe(client, url)
            if not ok and res.startswith("flood:"):
                secs = int(res.split(":")[1])
                logging.warning(f"⏳ Flood {secs} сек")
                await asyncio.sleep(min(secs, 300))

            await asyncio.sleep(random.uniform(2, 4))

            logging.info(f"⏳ Жду {SUBSCRIBE_DELAY} сек перед проверкой...")
            await asyncio.sleep(SUBSCRIBE_DELAY)

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
    task_type = user_task_choice.get(user_id, "channels")
    logging.info(f"📋 Тип задания: {task_type}")

    cur = await get_last_msg(client, bot_username)
    if cur and is_captcha_message(cur):
        await send_captcha_to_user(cur, user_chat_id, client)
        return

    earn_msg = await send_text(client, bot_username, "👨‍💻 Заработать", timeout=15)
    if not earn_msg:
        logging.warning("⚠️ Нет ответа на Заработать")
        return
    if is_captcha_message(earn_msg):
        await send_captcha_to_user(earn_msg, user_chat_id, client)
        return

    if not is_earn_type_menu(earn_msg):
        await asyncio.sleep(2)
        earn_msg = await get_last_msg(client, bot_username)

    if not earn_msg or not is_earn_type_menu(earn_msg):
        logging.warning("⚠️ Не получили меню типов заданий")
        return

    logging.info("✅ Меню типов заданий получено")

    # Выбираем кнопку в зависимости от типа
    if task_type == "channels":
        kw = "подписаться на канал"
    elif task_type == "bots":
        kw = "перейти в бота"
    else:
        kw = "просмотр постов"
    
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

    # Обрабатываем задания в зависимости от типа
    if task_type == "channels":
        pairs = get_task_pairs(task_msg)
        logging.info(f"📋 Найдено заданий: {len(pairs)}")

        if not pairs:
            logging.warning("⚠️ Заданий нет в сообщении:")
            logging.warning(f"Текст: '{(task_msg.raw_text or '')[:150]}'")
            log_buttons(task_msg, "  ")
            return

        result = await process_tasks(client, bot_username, task_msg, task_type)
        if result and is_captcha_message(result):
            await send_captcha_to_user(result, user_chat_id, client)

    elif task_type == "bots":
        # Проверяем есть ли кнопки с ботами
        has_bots = False
        for row in task_msg.buttons:
            for b in row:
                if "перейти в бота" in btn_text(b).lower():
                    has_bots = True
                    break
            if has_bots:
                break
        
        if not has_bots:
            logging.warning("⚠️ Нет заданий с ботами")
            return
        
        result = await process_bot_tasks(client, bot_username, task_msg)
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


@router.callback_query(lambda c: c.data and c.data.startswith("captcha_answer_"))
async def captcha_answer_callback(callback: types.CallbackQuery):
    try:
        parts = callback.data.split("_")
        chat_id = int(parts[2])
        number = parts[3]
        
        await callback.answer(f"✅ Выбрано: {number}")
        
        if chat_id not in captcha_storage:
            await callback.message.edit_text("❌ Капча не найдена")
            return
        
        data = captcha_storage[chat_id]
        client = data['client']
        bot_username = data['bot_username']
        
        if not client.is_connected():
            await client.connect()
        
        # Ищем кнопку с нужным номером и нажимаем её
        msg = await client.get_messages(bot_username, ids=data.get('msg_id', 0))
        if msg and msg.buttons:
            for row in msg.buttons:
                for btn in row:
                    if btn_text(btn) == number:
                        await btn.click()
                        await asyncio.sleep(2)
                        break
                else:
                    continue
                break
        
        new_msg = await get_last_msg(client, bot_username)
        
        if new_msg and not is_captcha_message(new_msg):
            await callback.message.edit_text("✅ Капча пройдена!")
            del captcha_storage[chat_id]
            phone = next((p for p, c in active_clients.items() if c == client), None)
            if phone:
                await continue_gram_bot(phone)
        else:
            await callback.message.edit_text(f"⏳ Отправлено {number}, капча ещё активна")
            
    except Exception as e:
        logging.error(f"❌ captcha_answer: {e}")
        await callback.answer(f"❌ Ошибка: {e}")


@router.callback_query(lambda c: c.data and c.data.startswith("task_choose_"))
async def task_choose_callback(callback: types.CallbackQuery):
    try:
        task_type = callback.data.replace("task_choose_", "")
        user_id = callback.from_user.id
        task_names = {
            "channels": "📢 Подписка на каналы",
            "posts": "📱 Просмотр постов",
            "bots": "🤖 Задания с ботами"
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
        logging.info(f"🔄 Подключаюсь к сессии {phone}...")
        try:
            await client.connect()
        except Exception as e:
            logging.error(f"❌ Ошибка подключения: {e}")
            try:
                session_name = f"sessions/{phone.replace('+', '')}"
                new_client = TelegramClient(
                    session_name, API_ID, API_HASH,
                    connection_retries=5, retry_delay=1,
                    auto_reconnect=True, flood_sleep_threshold=60
                )
                await new_client.connect()
                if await new_client.is_user_authorized():
                    logging.info(f"✅ Клиент пересоздан для {phone}")
                    client = new_client
                    active_clients[phone] = client
                else:
                    logging.error(f"❌ Клиент не авторизован: {phone}")
                    return None
            except Exception as e2:
                logging.error(f"❌ Ошибка пересоздания клиента: {e2}")
                return None
    
    try:
        if not await client.is_user_authorized():
            logging.warning(f"⚠️ Клиент {phone} не авторизован")
            return None
    except Exception as e:
        logging.error(f"❌ Ошибка проверки авторизации: {e}")
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
        logging.info(f"⏹ Остановлен: {phone}")
        return True
    elif active_tasks:
        for p, t in list(active_tasks.items()):
            t.cancel()
        active_tasks.clear()
        logging.info("⏹ Все остановлены")
        return True
    return False


async def continue_gram_bot(phone: str) -> bool:
    if phone in active_clients and phone in bot_username_for_task:
        client = active_clients[phone]
        bot_username = bot_username_for_task[phone]
        
        if not client.is_connected():
            logging.info(f"🔄 Переподключаюсь к {phone}...")
            try:
                await client.connect()
            except Exception as e:
                logging.error(f"❌ Ошибка подключения: {e}")
                try:
                    session_name = f"sessions/{phone.replace('+', '')}"
                    new_client = TelegramClient(
                        session_name, API_ID, API_HASH,
                        connection_retries=5, retry_delay=1,
                        auto_reconnect=True, flood_sleep_threshold=60
                    )
                    await new_client.connect()
                    if await new_client.is_user_authorized():
                        logging.info(f"✅ Клиент пересоздан для {phone}")
                        client = new_client
                        active_clients[phone] = client
                    else:
                        logging.error(f"❌ Клиент не авторизован: {phone}")
                        return False
                except Exception as e2:
                    logging.error(f"❌ Ошибка пересоздания: {e2}")
                    return False
        
        try:
            if not await client.is_user_authorized():
                logging.error(f"❌ Клиент {phone} не авторизован")
                return False
        except Exception as e:
            logging.error(f"❌ Ошибка проверки авторизации: {e}")
            return False
        
        task = asyncio.create_task(run_gram_worker(client, bot_username, phone))
        active_tasks[phone] = task
        logging.info(f"✅ Gram бот продолжен: {phone}")
        return True
    
    return False


# ============================================================
# ВОРКЕР
# ============================================================

async def run_gram_worker(client: TelegramClient, bot_username: str, phone: str):
    try:
        logging.info(f"🚀 Старт: {bot_username} | задержка: {SUBSCRIBE_DELAY} сек")
        
        if not client.is_connected():
            logging.info("🔄 Подключаюсь...")
            await client.connect()
        
        if not await client.is_user_authorized():
            logging.error(f"❌ Клиент не авторизован: {phone}")
            if bot_instance and user_chat_id:
                await bot_instance.send_message(
                    user_chat_id,
                    f"❌ <b>Сессия {phone} не активна!</b>\n\n"
                    f"Пересоздайте сессию в разделе 'Мои сессии'",
                    parse_mode=ParseMode.HTML
                )
            return
        
        await send_text(client, bot_username, "/start", timeout=8)
        await asyncio.sleep(2)

        cycle = 0
        while True:
            cycle += 1
            logging.info(f"\n{'='*50}\n🔁 ЦИКЛ #{cycle}\n{'='*50}")
            
            if not client.is_connected():
                logging.warning("⚠️ Клиент отключен, переподключаю...")
                try:
                    await client.connect()
                    if not await client.is_user_authorized():
                        logging.error(f"❌ Клиент не авторизован после переподключения")
                        break
                except Exception as e:
                    logging.error(f"❌ Ошибка переподключения: {e}")
                    break
            
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
        if phone in active_tasks:
            del active_tasks[phone]
        try:
            await asyncio.sleep(1)
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
