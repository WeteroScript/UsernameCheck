"""
Модуль для Gram ботов (PR GRAM | DRAGON)
"""

import asyncio
import random
import logging
import os
import sqlite3
import io
import base64
import urllib.request
import re
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

try:
    from embedded_font import FONT_B64
except ImportError:
    FONT_B64 = None

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

router = Router()

# ВАЖНО: раньше здесь были захардкожены официальные тестовые данные
# Telegram Desktop (api_id=2040). Это ПУБЛИЧНО ОПУБЛИКОВАННЫЕ (утёкшие)
# креды — Telegram считает их "скомпрометированными" (ошибка
# API_ID_PUBLISHED_FLOOD) и жёстко ограничивает/флагует авторизации через
# них, особенно для номеров не из РФ — из-за этого и не подключались
# номера кроме +7. Теперь значения берутся из .env — получи СВОИ
# api_id/api_hash на https://my.telegram.org/apps (это бесплатно) и
# пропиши их в .env, тогда любые страны будут подключаться нормально.
API_ID = int(os.getenv("API_ID", "2040"))
API_HASH = os.getenv("API_HASH", "b18441a1ff607e10a989891a5462e627")

if API_ID == 2040:
    logging.warning(
        "⚠️ Используются публичные (утёкшие) API_ID/API_HASH Telegram Desktop! "
        "Это ограничивает авторизацию номеров других стран, кроме +7. "
        "Получи свои на https://my.telegram.org/apps и укажи в .env "
        "(API_ID=... и API_HASH=...)."
    )

active_clients: Dict[str, TelegramClient] = {}
active_tasks: Dict[str, asyncio.Task] = {}
gram_bot_initialized = False
user_chat_id: Optional[int] = None
bot_username_for_task: Dict[str, str] = {}
bot_instance = None
captcha_storage: Dict[int, Dict[str, Any]] = {}
user_task_choice: Dict[int, str] = {}
user_bot_category: Dict[int, str] = {}
session_locks: Dict[str, asyncio.Lock] = {}
session_config_store: Dict[int, Dict[str, Dict[str, Any]]] = {}
user_bot_choice: Dict[int, str] = {}

SUBSCRIBE_DELAY = 60
BOT_TASK_DELAY = 30

PHOTO_TEXT = "."
FONT_CHOICE = "inter"

BUNDLED_FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "DejaVuSans-Bold.ttf")

GOOGLE_FONTS = {
    "inter": "https://github.com/google/fonts/raw/main/ofl/inter/Inter-Bold.ttf",
    "roboto": "https://github.com/google/fonts/raw/main/apache/roboto/Roboto-Bold.ttf",
    "dejavu": "https://github.com/dejavu-fonts/dejavu-fonts/raw/master/ttf/DejaVuSans-Bold.ttf",
}


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


def set_session_config(user_id: int, phone: str, key: str, value: Any):
    if user_id not in session_config_store:
        session_config_store[user_id] = {}
    if phone not in session_config_store[user_id]:
        session_config_store[user_id][phone] = {
            "enabled": False,
            "task_type": "channels",
            "bot_category": "regular"
        }
    session_config_store[user_id][phone][key] = value


def get_session_config(user_id: int, phone: str) -> Dict[str, Any]:
    if user_id not in session_config_store:
        session_config_store[user_id] = {}
    if phone not in session_config_store[user_id]:
        session_config_store[user_id][phone] = {
            "enabled": False,
            "task_type": "channels",
            "bot_category": "regular"
        }
    return session_config_store[user_id][phone]


def get_task_choice_keyboard(user_id: int, phone: str = None) -> InlineKeyboardMarkup:
    task_types = {
        "channels": "📢 Подписка на каналы",
        "groups": "👥 Вступление в группы",
        "posts": "📱 Просмотр постов",
        "bots": "🤖 Задания с ботами",
    }
    
    if phone:
        callback_prefix = f"task_choose_sess_"
        back_callback = f"sess_item_{phone}"
        current_task_type = get_session_config(user_id, phone).get("task_type", "channels")
    else:
        callback_prefix = "task_choose_"
        back_callback = "bot_prgramm_settings"
        current_task_type = user_task_choice.get(user_id, "channels")

    buttons = []
    for task_key, task_name in task_types.items():
        label = f"✅ {task_name}" if task_key == current_task_type else task_name
        buttons.append([InlineKeyboardButton(
            text=label,
            callback_data=f"{callback_prefix}{task_key}_{phone}" if phone else f"{callback_prefix}{task_key}"
        )])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_bot_category_keyboard(user_id: int, phone: str = None) -> InlineKeyboardMarkup:
    if phone:
        config = get_session_config(user_id, phone)
        current = config.get("bot_category", "regular")
        callback_prefix = f"bot_cat_sess_"
        back_callback = f"sess_item_{phone}"
    else:
        current = user_bot_category.get(user_id, "regular")
        callback_prefix = "bot_cat_"
        back_callback = "bot_prgramm_settings"
    
    categories = {
        "regular": "🤖 Обычные боты",
        "webapp": "🌐 Боты с Web App",
        "conditions": "📋 С дополнительными условиями"
    }
    
    buttons = []
    for key, name in categories.items():
        check = "✅ " if key == current else ""
        buttons.append([InlineKeyboardButton(
            text=f"{check}{name}",
            callback_data=f"{callback_prefix}{key}_{phone}" if phone else f"{callback_prefix}{key}"
        )])
    
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_bot_settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Тип заданий", callback_data="gram_choose_task")],
        [InlineKeyboardButton(text="🔄 Сменить бота", callback_data="gram_change_bot")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="bot_prgramm")],
    ])


# ============================================================
# КЛАВИАТУРЫ ДЛЯ СЕССИЙ
# ============================================================

def get_session_item_keyboard(user_id: int, phone: str) -> InlineKeyboardMarkup:
    config = get_session_config(user_id, phone)
    is_enabled = config.get("enabled", False)
    toggle_text = "⏹ Выключить" if is_enabled else "▶️ Включить"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 " + phone, callback_data="no_action")],
        [InlineKeyboardButton(text=toggle_text, callback_data=f"sess_toggle_{phone}")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data=f"sess_settings_{phone}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="bot_prgramm")],
    ])


def get_session_settings_keyboard(user_id: int, phone: str) -> InlineKeyboardMarkup:
    config = get_session_config(user_id, phone)
    task_type = config.get("task_type", "channels")
    task_names = {"channels": "📢 Подписка", "groups": "👥 Группы", "posts": "📱 Посты", "bots": "🤖 Боты"}
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📋 Тип: {task_names.get(task_type, task_type)}", callback_data=f"sess_task_{phone}")],
        [InlineKeyboardButton(text="🔄 Сменить бота", callback_data=f"sess_bot_{phone}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"sess_item_{phone}")],
    ])


# ============================================================
# ШРИФТЫ
# ============================================================

_font_cache: Dict[int, ImageFont.ImageFont] = {}
_font_bytes: Optional[bytes] = None
_font_path: Optional[str] = None
_font_loaded = False


def _get_embedded_font_bytes() -> Optional[bytes]:
    """Шрифт (DejaVu Sans Bold, Latin+Cyrillic), встроенный прямо в код как
    base64 в embedded_font.py. Не зависит от файлов на диске или сети —
    работает в любом окружении и всегда поддерживает русские буквы.
    Именно поэтому это ГЛАВНЫЙ и предпочтительный источник шрифта."""
    global _font_bytes
    if _font_bytes is not None:
        return _font_bytes
    if not FONT_B64:
        return None
    try:
        _font_bytes = base64.b64decode(FONT_B64)
        return _font_bytes
    except Exception as e:
        logging.error(f"❌ Не удалось декодировать встроенный шрифт: {e}")
        return None


def download_font_from_google(font_name: str = None) -> Optional[str]:
    if font_name is None:
        font_name = FONT_CHOICE
    if font_name not in GOOGLE_FONTS:
        return None
    os.makedirs("fonts", exist_ok=True)
    font_path = os.path.join("fonts", f"{font_name}.ttf")
    if os.path.exists(font_path):
        return font_path
    url = GOOGLE_FONTS[font_name]
    try:
        logging.info(f"⬇️ Скачиваю шрифт {font_name}...")
        urllib.request.urlretrieve(url, font_path)
        logging.info(f"✅ Шрифт скачан: {font_path}")
        return font_path
    except Exception as e:
        logging.error(f"❌ Ошибка скачивания шрифта: {e}")
    return None


def get_font_path() -> Optional[str]:
    global _font_path, _font_loaded
    if _font_loaded:
        return _font_path
    # Шрифт, который лежит прямо в репозитории — не зависит ни от сети,
    # ни от того, установлены ли шрифты в системе/Docker-образе.
    if os.path.exists(BUNDLED_FONT_PATH):
        try:
            ImageFont.truetype(BUNDLED_FONT_PATH, 30)
            _font_path = BUNDLED_FONT_PATH
            _font_loaded = True
            logging.info(f"✅ Используется встроенный шрифт (файл): {BUNDLED_FONT_PATH}")
            return _font_path
        except Exception as e:
            logging.warning(f"⚠️ Не удалось загрузить встроенный шрифт: {e}")
    google_font = download_font_from_google()
    if google_font:
        try:
            ImageFont.truetype(google_font, 30)
            _font_path = google_font
            _font_loaded = True
            logging.info(f"✅ Шрифт загружен: {google_font}")
            return _font_path
        except Exception as e:
            logging.warning(f"⚠️ Не удалось загрузить скачанный шрифт: {e}")
    local_fonts = [
        os.path.join("fonts", f"{FONT_CHOICE}.ttf"),
        os.path.join("fonts", "inter.ttf"),
        os.path.join("fonts", "roboto.ttf"),
        os.path.join("fonts", "DejaVuSans-Bold.ttf"),
    ]
    for path in local_fonts:
        if os.path.exists(path):
            try:
                ImageFont.truetype(path, 30)
                _font_path = path
                _font_loaded = True
                logging.info(f"✅ Найден локальный шрифт: {path}")
                return path
            except Exception as e:
                continue
    system_fonts = [
        '/System/Library/Fonts/Supplemental/Arial Bold.ttf',
        '/System/Library/Fonts/Supplemental/Helvetica Bold.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
        '/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf',
        'C:\\Windows\\Fonts\\arialbd.ttf',
        'C:\\Windows\\Fonts\\segoeuib.ttf',
        '/system/fonts/Roboto-Bold.ttf',
        '/system/fonts/NotoSans-Bold.ttf',
    ]
    for path in system_fonts:
        if os.path.exists(path):
            try:
                ImageFont.truetype(path, 30)
                _font_path = path
                _font_loaded = True
                logging.info(f"✅ Найден системный шрифт: {path}")
                return path
            except Exception as e:
                continue
    _font_loaded = True
    logging.warning("⚠️ Шрифт не найден, будет использован стандартный (без кириллицы)")
    return None


def load_font(size: int) -> ImageFont.ImageFont:
    if size in _font_cache:
        return _font_cache[size]
    # 1) Встроенный в код шрифт (с поддержкой кириллицы) — грузится из
    #    памяти, поэтому гарантированно доступен в любом окружении.
    font_bytes = _get_embedded_font_bytes()
    if font_bytes:
        try:
            font = ImageFont.truetype(io.BytesIO(font_bytes), size)
            _font_cache[size] = font
            return font
        except Exception as e:
            logging.error(f"❌ Ошибка загрузки встроенного шрифта: {e}")
    # 2) Файл на диске / скачанный / системный шрифт (запасной вариант).
    path = get_font_path()
    if path:
        try:
            font = ImageFont.truetype(path, size)
            _font_cache[size] = font
            return font
        except Exception as e:
            logging.error(f"❌ Ошибка загрузки шрифта: {e}")
    # Pillow>=10.1 умеет масштабировать встроенный шрифт по умолчанию —
    # используем это, чтобы даже в худшем случае (ни встроенный, ни системный
    # шрифт не найден) текст не оставался микроскопическим на любом размере.
    try:
        font = ImageFont.load_default(size=size)
    except TypeError:
        font = ImageFont.load_default()
    _font_cache[size] = font
    return font


# ============================================================
# ГЕНЕРАЦИЯ ФОТО
# ============================================================

def random_bg_color() -> Tuple[int, int, int]:
    modes = [
        (0, 0, 0), (10, 10, 20), (20, 10, 10),
        (5, 20, 5), (15, 10, 25), (25, 20, 10),
    ]
    return random.choice(modes)


def random_text_color(bg: Tuple[int, int, int]) -> Tuple[int, int, int]:
    colors = [
        (255, 255, 255), (255, 200, 50), (50, 200, 255),
        (255, 100, 100), (100, 255, 100), (255, 150, 255),
        (200, 200, 255), (255, 200, 150), (150, 255, 200),
    ]
    return random.choice(colors)


def generate_bot_image() -> bytes:
    size = 1080
    text = PHOTO_TEXT
    bg = random_bg_color()
    image = Image.new('RGB', (size, size), bg)
    draw = ImageDraw.Draw(image)
    max_width = int(size * 0.90)
    max_height = int(size * 0.85)
    words = text.split()

    def wrap_text(font) -> List[str]:
        lines = []
        current = ""
        for word in words:
            test = f"{current} {word}".strip()
            bbox = draw.textbbox((0, 0), test, font=font)
            w = bbox[2] - bbox[0]
            if w <= max_width or not current:
                current = test
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines

    def block_size(font, lines: List[str]):
        line_bbox = font.getbbox("Ay")
        line_height = line_bbox[3] - line_bbox[1]
        spacing = max(2, int(font.size * 0.12)) if hasattr(font, "size") else 4
        widths = []
        for line in lines:
            b = draw.textbbox((0, 0), line, font=font)
            widths.append(b[2] - b[0])
        total_height = len(lines) * line_height + (len(lines) - 1) * spacing
        return max(widths) if widths else 0, total_height, line_height, spacing

    best_size = 50
    best_lines = [text]
    best_metrics = None
    for test_size in range(50, 700, 6):
        font = load_font(test_size)
        lines = wrap_text(font)
        max_w, total_h, line_h, spacing = block_size(font, lines)
        if max_w <= max_width and total_h <= max_height:
            best_size = test_size
            best_lines = lines
            best_metrics = (line_h, spacing)
        else:
            break

    font = load_font(best_size)
    if best_metrics is None:
        best_metrics = block_size(font, best_lines)[2:]
    line_h, spacing = best_metrics
    total_height = len(best_lines) * line_h + (len(best_lines) - 1) * spacing

    text_color = random_text_color(bg)
    shadow_color = (0, 0, 0)
    shadow_offset = max(3, best_size // 30)

    y = (size - total_height) // 2 + random.randint(-15, 15)
    for line in best_lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        lw = bbox[2] - bbox[0]
        x = (size - lw) // 2 + random.randint(-15, 15)
        for dx in range(-shadow_offset, shadow_offset + 1, shadow_offset):
            for dy in range(-shadow_offset, shadow_offset + 1, shadow_offset):
                if dx == 0 and dy == 0:
                    continue
                draw.text((x + dx, y + dy), line, font=font, fill=shadow_color)
        draw.text((x, y), line, font=font, fill=text_color)
        y += line_h + spacing

    border_size = max(5, size // 200)
    draw.rectangle(
        [border_size, border_size, size - border_size, size - border_size],
        outline=(255, 255, 255),
        width=border_size
    )
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


def find_button(msg, keywords: List[str]):
    if not msg or not msg.buttons:
        return None
    for row in msg.buttons:
        for b in row:
            t = btn_text(b).lower()
            if any(k in t for k in keywords):
                return b
    return None


def find_all_buttons(msg, keywords: List[str]) -> List[Any]:
    result = []
    if not msg or not msg.buttons:
        return result
    for row in msg.buttons:
        for b in row:
            t = btn_text(b).lower()
            if any(k in t for k in keywords):
                result.append(b)
    return result


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
            return msg
        if msg_snap(msg) != snap_before:
            return msg
    return await get_last_msg(client, bot_username)


CLICK_COOLDOWN = 1.5  # секунд между нажатиями кнопок в этих ботах
THROTTLED_BOTS = {"gram_prbot", "gram_piarbot"}
_last_click_time: Dict[str, float] = {}
_click_locks: Dict[str, asyncio.Lock] = {}


def _get_click_lock(key: str) -> asyncio.Lock:
    if key not in _click_locks:
        _click_locks[key] = asyncio.Lock()
    return _click_locks[key]


async def _throttle_click(bot_username: str):
    """Гарантирует минимум 1.5с между нажатиями кнопок именно в
    @gram_prbot / @gram_piarbot (задания зарабатываются кликами по кнопкам
    в этих ботах), даже если несколько сессий работают параллельно —
    иначе бот флудит и капчит. На остальных ботов (например, AI-решатель
    капчи) задержка не действует."""
    key = (bot_username or "").lower().lstrip('@')
    if key not in THROTTLED_BOTS:
        return
    lock = _get_click_lock(key)
    async with lock:
        loop = asyncio.get_event_loop()
        now = loop.time()
        last = _last_click_time.get(key, 0)
        wait = CLICK_COOLDOWN - (now - last)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_click_time[key] = loop.time()


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
    await _throttle_click(bot_username)
    before = await get_last_msg(client, bot_username)
    snap = msg_snap(before)
    mid = before.id if before else 0
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
    if not client.is_connected():
        await client.connect()
    before = await get_last_msg(client, bot_username)
    snap = msg_snap(before)
    mid = before.id if before else 0
    try:
        buf = io.BytesIO(photo_bytes)
        buf.name = "bot_farmers.png"
        await client.send_file(bot_username, buf)
    except Exception as e:
        logging.error(f"❌ Ошибка отправки фото: {e}")
        return None
    return await wait_bot_response(client, bot_username, snap, mid, timeout)


# ============================================================
# ПОДПИСКА НА КАНАЛ / ВСТУПЛЕНИЕ В ГРУППУ
# ============================================================

async def subscribe(client: TelegramClient, url: str) -> Tuple[bool, str]:
    try:
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
            return True, "success"
        return False, f"unknown url format: {url}"
    except errors.FloodWaitError as e:
        return False, f"flood:{e.seconds}"
    except Exception as e:
        err = str(e).lower()
        if "already" in err or "already participant" in err:
            return True, "already"
        if "successfully requested" in err:
            return True, "requested"
        return False, str(e)


# ============================================================
# ЗАДАНИЯ С БОТАМИ
# ============================================================

async def process_bot_tasks(client: TelegramClient, bot_username: str, msg, user_id: int = None, phone: str = None):
    try:
        if not msg or not msg.buttons:
            return msg
        if user_id and phone:
            config = get_session_config(user_id, phone)
            category = config.get("bot_category", "regular")
        else:
            category = user_bot_category.get(user_id, "regular")
        category_keywords = {
            "regular": ["обычные боты"],
            "webapp": ["боты с web app", "web app"],
            "conditions": ["с дополнительными условиями", "с доп. условиями"]
        }
        keywords = category_keywords.get(category, ["обычные боты"])
        category_btn = find_button(msg, keywords)
        if category_btn:
            result = await click_btn(client, bot_username, category_btn, timeout=15)
            if not result:
                return msg
            if is_captcha_message(result):
                return await get_last_msg(client, bot_username)
            msg = result
        all_task_btns = find_all_buttons(msg, ["перейти в бота"])
        if not all_task_btns:
            return msg
        for task_btn in all_task_btns:
            task_text = btn_text(task_btn)
            if re.search(r'100\s*000|100k|100000', task_text, re.IGNORECASE):
                skip_btn = find_button(msg, ["скрыть", "пропустить", "▶️", "следующий"])
                if skip_btn:
                    await click_btn(client, bot_username, skip_btn, timeout=5)
                    await asyncio.sleep(1)
                continue
            result = await click_btn(client, bot_username, task_btn, timeout=15)
            if not result:
                continue
            if is_captcha_message(result):
                continue
            photo_bytes = generate_bot_image()
            photo_result = await send_photo(client, bot_username, photo_bytes, timeout=15)
            if not photo_result:
                continue
            if is_captcha_message(photo_result):
                continue
            await asyncio.sleep(1)
            next_btn = find_button(photo_result, ["следующий бот"])
            if next_btn:
                await click_btn(client, bot_username, next_btn, timeout=15)
            delay = random.randint(2, 4)
            await asyncio.sleep(delay)
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
        captcha_storage[chat_id] = {
            'client': client,
            'bot_username': bu,
            'msg_id': msg.id,
            'answered': False
        }
        text = f"🧩 <b>Капча!</b>\n\n"
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
        msg_id = data['msg_id']
        if data.get('answered', False):
            await callback.message.edit_text("⏳ Капча уже обработана")
            return
        if not client:
            await callback.message.edit_text("❌ Клиент не найден")
            return
        if not client.is_connected():
            await client.connect()
        msg = await client.get_messages(bot_username, ids=msg_id)
        if not msg or not msg.buttons:
            await callback.message.edit_text("❌ Сообщение с капчей не найдено")
            return
        target_btn = None
        for row in msg.buttons:
            for btn in row:
                if btn_text(btn) == number:
                    target_btn = btn
                    break
            if target_btn:
                break
        if not target_btn:
            await callback.message.edit_text(f"❌ Кнопка {number} не найдена")
            return
        await _throttle_click(bot_username)
        await target_btn.click()
        data['answered'] = True
        await callback.message.edit_text(f"✅ Нажата кнопка {number} в @{bot_username}")
        await asyncio.sleep(2)
        new_msg = await client.get_messages(bot_username, limit=1)
        if new_msg and not is_captcha_message(new_msg):
            await callback.message.edit_text("✅ Капча пройдена!")
            del captcha_storage[chat_id]
            phone = next((p for p, c in active_clients.items() if c == client), None)
            if phone:
                await continue_gram_bot(phone)
        else:
            await callback.message.edit_text(f"⏳ Кнопка {number} нажата, капча еще активна. Попробуй другой номер.")
    except Exception as e:
        logging.error(f"❌ captcha_answer: {e}")
        await callback.answer(f"❌ Ошибка: {e}")


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


# ============================================================
# ОБРАБОТЧИКИ ДЛЯ КНОПОК ИЗ bot.py
# ============================================================

@router.callback_query(lambda c: c.data and c.data.startswith("sess_item_"))
async def sess_item_callback(callback: types.CallbackQuery):
    try:
        phone = callback.data.replace("sess_item_", "")
        user_id = callback.from_user.id
        await callback.answer()
        config = get_session_config(user_id, phone)
        is_enabled = config.get("enabled", False)
        task_type = config.get("task_type", "channels")
        task_names = {
            "channels": "📢 Подписка на каналы",
            "groups": "👥 Вступление в группы",
            "posts": "📱 Просмотр постов",
            "bots": "🤖 Задания с ботами"
        }
        text = f"📱 <b>{phone}</b>\n\n"
        text += f"📊 Статус: {'🟢 Включена' if is_enabled else '🔴 Выключена'}\n"
        text += f"📋 Задание: {task_names.get(task_type, task_type)}\n"
        text += f"🤖 Бот: {user_bot_choice.get(user_id, '@gram_piarbot')}\n\n"
        text += "Выбери действие:"
        await callback.message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=get_session_item_keyboard(user_id, phone)
        )
    except Exception as e:
        logging.error(f"❌ sess_item_callback: {e}")
        await callback.answer("❌ Ошибка")


@router.callback_query(lambda c: c.data and c.data.startswith("sess_toggle_"))
async def sess_toggle_callback(callback: types.CallbackQuery):
    try:
        phone = callback.data.replace("sess_toggle_", "")
        user_id = callback.from_user.id
        await callback.answer()
        config = get_session_config(user_id, phone)
        current = config.get("enabled", False)
        config["enabled"] = not current
        if config["enabled"] is False and phone in active_tasks:
            await stop_gram_bot(phone)
        if config["enabled"] is True and phone in active_clients:
            bot_name = user_bot_choice.get(user_id, "@gram_piarbot")
            client = active_clients[phone]
            if client.is_connected() and await client.is_user_authorized():
                await start_gram_worker(client, bot_name, phone, user_id)
        await callback.answer(f"✅ {'Включена' if config['enabled'] else 'Выключена'}")
        await sess_item_callback(callback)
    except Exception as e:
        logging.error(f"❌ sess_toggle_callback: {e}")
        await callback.answer("❌ Ошибка")


@router.callback_query(lambda c: c.data and c.data.startswith("sess_settings_"))
async def sess_settings_callback(callback: types.CallbackQuery):
    try:
        phone = callback.data.replace("sess_settings_", "")
        user_id = callback.from_user.id
        await callback.answer()
        await callback.message.edit_text(
            f"⚙️ <b>Настройки — {phone}</b>\n\n"
            "Выбери настройку:",
            parse_mode=ParseMode.HTML,
            reply_markup=get_session_settings_keyboard(user_id, phone)
        )
    except Exception as e:
        logging.error(f"❌ sess_settings_callback: {e}")
        await callback.answer("❌ Ошибка")


@router.callback_query(lambda c: c.data and c.data.startswith("sess_task_"))
async def sess_task_callback(callback: types.CallbackQuery):
    try:
        phone = callback.data.replace("sess_task_", "")
        user_id = callback.from_user.id
        await callback.answer()
        await callback.message.edit_text(
            f"📋 <b>Выбор типа заданий для {phone}</b>\n\n"
            "Выбери тип заданий:",
            parse_mode=ParseMode.HTML,
            reply_markup=get_task_choice_keyboard(user_id, phone)
        )
    except Exception as e:
        logging.error(f"❌ sess_task_callback: {e}")
        await callback.answer("❌ Ошибка")


@router.callback_query(lambda c: c.data and c.data.startswith("sess_cat_"))
async def sess_cat_callback(callback: types.CallbackQuery):
    try:
        phone = callback.data.replace("sess_cat_", "")
        user_id = callback.from_user.id
        await callback.answer()
        await callback.message.edit_text(
            f"📋 <b>Выбор категории ботов для {phone}</b>\n\n"
            "Выбери категорию:",
            parse_mode=ParseMode.HTML,
            reply_markup=get_bot_category_keyboard(user_id, phone)
        )
    except Exception as e:
        logging.error(f"❌ sess_cat_callback: {e}")
        await callback.answer("❌ Ошибка")


@router.callback_query(lambda c: c.data and c.data.startswith("sess_bot_") and not c.data.startswith("sess_bot_choice_"))
async def sess_bot_callback(callback: types.CallbackQuery):
    try:
        phone = callback.data.replace("sess_bot_", "")
        user_id = callback.from_user.id
        await callback.answer()
        current_bot = user_bot_choice.get(user_id, "@gram_piarbot")
        bots = [("@gram_piarbot", "gpiar"), ("@gram_prbot", "gpr")]
        buttons = []
        for name, code in bots:
            check = "✅ " if name == current_bot else ""
            buttons.append([InlineKeyboardButton(
                text=f"{check}{name}",
                callback_data=f"sess_bot_choice_{code}_{phone}"
            )])
        buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"sess_item_{phone}")])
        await callback.message.edit_text(
            f"🔄 <b>Смена бота для {phone}</b>\n\n"
            "Выбери бота:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )
    except Exception as e:
        logging.error(f"❌ sess_bot_callback: {e}")
        await callback.answer("❌ Ошибка")


@router.callback_query(lambda c: c.data and c.data.startswith("sess_bot_choice_"))
async def sess_bot_choice_callback(callback: types.CallbackQuery):
    try:
        remainder = callback.data[len("sess_bot_choice_"):]
        bot_code, _, phone = remainder.partition("_")
        user_id = callback.from_user.id
        bot_name = "@gram_piarbot" if bot_code == "gpiar" else "@gram_prbot"
        user_bot_choice[user_id] = bot_name
        await callback.answer(f"✅ {bot_name}")
        callback.data = f"sess_item_{phone}"
        await sess_item_callback(callback)
    except Exception as e:
        logging.error(f"❌ sess_bot_choice_callback: {e}")
        await callback.answer("❌ Ошибка")


# ============================================================
# ВЫБОР ТИПА ЗАДАНИЙ И КАТЕГОРИИ
# ============================================================

@router.callback_query(lambda c: c.data and c.data.startswith("task_choose_"))
async def task_choose_callback(callback: types.CallbackQuery):
    try:
        if "_sess_" in callback.data:
            parts = callback.data.split("_")
            task_type = parts[3]
            phone = parts[4]
            user_id = callback.from_user.id
        else:
            task_type = callback.data.replace("task_choose_", "")
            user_id = callback.from_user.id
            phone = None
        task_names = {
            "channels": "📢 Подписка на каналы",
            "groups": "👥 Вступление в группы",
            "posts": "📱 Просмотр постов",
            "bots": "🤖 Задания с ботами"
        }
        if task_type in task_names:
            if phone:
                set_session_config(user_id, phone, "task_type", task_type)
                await callback.answer(f"✅ {task_names[task_type]}")
                if task_type == "bots":
                    await callback.message.edit_text(
                        f"📋 <b>Выбор категории ботов для {phone}</b>\n\n"
                        "Выбери категорию:",
                        parse_mode=ParseMode.HTML,
                        reply_markup=get_bot_category_keyboard(user_id, phone)
                    )
                else:
                    await callback.message.edit_text(
                        f"✅ <b>Выбран тип:</b>\n{task_names[task_type]}\n\n"
                        f"Для сессии {phone}",
                        parse_mode=ParseMode.HTML,
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"sess_item_{phone}")],
                            [InlineKeyboardButton(text="📋 Изменить", callback_data=f"sess_task_{phone}")]
                        ])
                    )
            else:
                user_task_choice[user_id] = task_type
                await callback.answer(f"✅ {task_names[task_type]}")
                if task_type == "bots":
                    await callback.message.edit_text(
                        f"📋 <b>Выбор категории ботов</b>\n\n"
                        "Выбери категорию:",
                        parse_mode=ParseMode.HTML,
                        reply_markup=get_bot_category_keyboard(user_id)
                    )
                else:
                    await callback.message.edit_text(
                        f"✅ <b>Выбран тип заданий:</b>\n\n"
                        f"{task_names[task_type]}\n\n"
                        f"Тип будет использован при следующем запуске.",
                        parse_mode=ParseMode.HTML,
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="📋 Изменить тип", callback_data="gram_task_type")],
                            [InlineKeyboardButton(text="⬅️ Назад", callback_data="bot_prgramm_settings")],
                        ])
                    )
        else:
            await callback.answer("❌ Неизвестный тип")
    except Exception as e:
        logging.error(f"❌ task_choose: {e}")
        await callback.answer("❌ Ошибка")


@router.callback_query(lambda c: c.data and c.data.startswith("bot_cat_"))
async def bot_category_callback(callback: types.CallbackQuery):
    try:
        if "_sess_" in callback.data:
            parts = callback.data.split("_")
            category = parts[3]
            phone = parts[4]
            user_id = callback.from_user.id
        else:
            category = callback.data.replace("bot_cat_", "")
            user_id = callback.from_user.id
            phone = None
        cat_names = {
            "regular": "Обычные боты",
            "webapp": "Боты с Web App",
            "conditions": "С дополнительными условиями"
        }
        if category in cat_names:
            if phone:
                set_session_config(user_id, phone, "bot_category", category)
                await callback.answer(f"✅ {cat_names[category]}")
                await callback.message.edit_text(
                    f"✅ <b>Выбрана категория:</b>\n{cat_names[category]}\n\n"
                    f"Для сессии {phone}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"sess_item_{phone}")],
                        [InlineKeyboardButton(text="📋 Изменить", callback_data=f"sess_cat_{phone}")]
                    ])
                )
            else:
                user_bot_category[user_id] = category
                await callback.answer(f"✅ {cat_names[category]}")
                await callback.message.edit_text(
                    f"✅ <b>Выбрана категория ботов:</b>\n{cat_names[category]}\n\n"
                    f"Категория будет использована при выполнении заданий с ботами.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="📋 Изменить категорию", callback_data="gram_bot_category")],
                        [InlineKeyboardButton(text="⬅️ Назад", callback_data="bot_prgramm_settings")]
                    ])
                )
        else:
            await callback.answer("❌ Неизвестная категория")
    except Exception as e:
        logging.error(f"❌ bot_category_callback: {e}")
        await callback.answer("❌ Ошибка")


@router.callback_query(lambda c: c.data and c.data == "gram_bot_category")
async def gram_bot_category_callback(callback: types.CallbackQuery):
    try:
        user_id = callback.from_user.id
        await callback.answer()
        await callback.message.edit_text(
            "📋 <b>Выбор категории ботов</b>\n\n"
            "Выберите категорию для заданий с ботами:\n\n"
            "🤖 Обычные боты — стандартные задания\n"
            "🌐 Боты с Web App — задания с веб-приложениями\n"
            "📋 С доп. условиями — задания с дополнительными условиями",
            parse_mode=ParseMode.HTML,
            reply_markup=get_bot_category_keyboard(user_id)
        )
    except Exception as e:
        logging.error(f"❌ gram_bot_category_callback: {e}")
        await callback.answer("❌ Ошибка")


# ============================================================
# ОБРАБОТКА ЗАДАНИЙ (КАНАЛЫ/ГРУППЫ)
# ============================================================

async def process_tasks(
    client: TelegramClient,
    bot_username: str,
    msg,
    task_type: str = "channels"
):
    if task_type == "bots":
        return msg
    page = 0
    while True:
        page += 1
        pairs = get_task_pairs(msg)
        if not pairs:
            return msg
        for i, (sub_btn, chk_btn) in enumerate(pairs, 1):
            url = btn_url(sub_btn)
            name = btn_text(sub_btn)
            ok, res = await subscribe(client, url)
            if not ok and res.startswith("flood:"):
                secs = int(res.split(":")[1])
                await asyncio.sleep(min(secs, 300))
            await asyncio.sleep(random.uniform(2, 4))
            await asyncio.sleep(SUBSCRIBE_DELAY)
            cur_chk = chk_btn
            for attempt in range(1, 4):
                result = await click_btn(client, bot_username, cur_chk, timeout=15)
                if not result:
                    result = await get_last_msg(client, bot_username)
                if not result:
                    await asyncio.sleep(2)
                    continue
                if is_captcha_message(result):
                    return result
                resp = (result.raw_text or "").lower()
                if any(w in resp for w in ["начислено", "успешно", "подписались"]):
                    msg = result
                    break
                if "не подписан" in resp:
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
            r = await click_btn(client, bot_username, nxt, timeout=10)
            if r:
                msg = r
                continue
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
    config = get_session_config(user_id, phone)
    task_type = config.get("task_type", "channels")
    logging.info(f"📋 Тип задания: {task_type} (сессия {phone})")
    cur = await get_last_msg(client, bot_username)
    if cur and is_captcha_message(cur):
        if task_type == "bots":
            logging.info(f"🤖 Капча проигнорирована (тип заданий: боты, сессия {phone})")
            return
        await send_captcha_to_user(cur, user_chat_id, client)
        return
    earn_msg = await send_text(client, bot_username, "👨‍💻 Заработать", timeout=15)
    if not earn_msg:
        return
    if is_captcha_message(earn_msg):
        if task_type == "bots":
            logging.info(f"🤖 Капча проигнорирована (тип заданий: боты, сессия {phone})")
            return
        await send_captcha_to_user(earn_msg, user_chat_id, client)
        return
    if not is_earn_type_menu(earn_msg):
        await asyncio.sleep(2)
        earn_msg = await get_last_msg(client, bot_username)
    if not earn_msg or not is_earn_type_menu(earn_msg):
        return
    if task_type == "channels":
        kw = "подписаться на канал"
    elif task_type == "groups":
        kw = "вступить в группу"
    elif task_type == "bots":
        kw = "перейти в бота"
    else:
        kw = "просмотр постов"
    target_btn = find_button(earn_msg, [kw])
    if not target_btn:
        return
    task_msg = await click_btn(client, bot_username, target_btn, timeout=15)
    if not task_msg:
        return
    if is_captcha_message(task_msg):
        if task_type == "bots":
            logging.info(f"🤖 Капча проигнорирована (тип заданий: боты, сессия {phone})")
            return
        await send_captcha_to_user(task_msg, user_chat_id, client)
        return
    if task_type == "channels" or task_type == "groups":
        pairs = get_task_pairs(task_msg)
        if not pairs:
            return
        result = await process_tasks(client, bot_username, task_msg, task_type)
        if result and is_captcha_message(result):
            await send_captcha_to_user(result, user_chat_id, client)
    elif task_type == "bots":
        result = await process_bot_tasks(client, bot_username, task_msg, user_id, phone)
        if result and is_captcha_message(result):
            logging.info(f"🤖 Капча проигнорирована (тип заданий: боты, сессия {phone})")
            return
    else:
        wait_time = random.randint(8, 15)
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
            else:
                logging.info(f"✅ Уже авторизован: {phone}")
            active_clients[phone] = client
            return True
        except errors.FloodWaitError as e:
            return False
        except errors.PhoneNumberInvalidError:
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
                return True
            await client.sign_in(phone, code)
            return True
        except errors.SessionPasswordNeededError:
            return False
        except errors.PhoneCodeInvalidError:
            return False
        except errors.PhoneCodeExpiredError:
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
                    client = new_client
                    active_clients[phone] = client
                else:
                    return None
            except Exception as e2:
                logging.error(f"❌ Ошибка пересоздания клиента: {e2}")
                return None
    try:
        if not await client.is_user_authorized():
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
                        client = new_client
                        active_clients[phone] = client
                    else:
                        return False
                except Exception as e2:
                    logging.error(f"❌ Ошибка пересоздания: {e2}")
                    return False
        try:
            if not await client.is_user_authorized():
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
            if phone not in active_tasks:
                break
            cycle += 1
            logging.info(f"\n{'='*50}\n🔁 ЦИКЛ #{cycle}\n{'='*50}")
            if not client.is_connected():
                try:
                    await client.connect()
                    if not await client.is_user_authorized():
                        break
                except Exception as e:
                    logging.error(f"❌ Ошибка переподключения: {e}")
                    break
            try:
                await do_cycle(client, bot_username, user_chat_id, phone)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(f"❌ Ошибка цикла: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(5)
            p_config = get_session_config(user_chat_id, phone)
            if p_config.get("task_type") == "bots":
                # Для заданий с ботами — интервал 5-10 МИНУТ между заданиями
                # (а не секунд), т.к. боты-задания обычно требуют больше
                # времени на выполнение и слишком частые повторы могут
                # выглядеть подозрительно / вызывать капчу.
                p = random.randint(5 * 60, 10 * 60)
                logging.info(f"⏸️ Пауза {p} сек (~{p//60} мин) — тип заданий: боты...")
            else:
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
    'get_bot_category_keyboard', 'get_bot_settings_keyboard',
    'active_clients', 'active_tasks',
    'set_session_config', 'get_session_config'
            ]
