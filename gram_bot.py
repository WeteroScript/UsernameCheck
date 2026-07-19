"""
Модуль для Gram ботов (PR GRAM | DRAGON)
С поддержкой выбора категории ботов и игнорированием 100k заданий
"""

import asyncio
import random
import logging
import os
import sqlite3
import io
import urllib.request
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
user_bot_category: Dict[int, str] = {}  # user_id -> "regular" | "webapp" | "conditions"
session_locks: Dict[str, asyncio.Lock] = {}

SUBSCRIBE_DELAY = 60
BOT_TASK_DELAY = 30

PHOTO_TEXT = "@Bot_Farmers"


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
        "groups": "👥 Вступление в группы",
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
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="bot_prgramm_settings")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_bot_category_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Клавиатура выбора категории ботов"""
    current = user_bot_category.get(user_id, "regular")
    
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
            callback_data=f"bot_cat_{key}"
        )])
    
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="bot_prgramm_settings")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_bot_settings_keyboard() -> InlineKeyboardMarkup:
    """Настройки бота: сессии для работы / сменить бота / тип заданий / категория ботов / назад"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Сессии для работы", callback_data="gram_sessions")],
        [InlineKeyboardButton(text="🔄 Сменить бота", callback_data="gram_change_bot")],
        [InlineKeyboardButton(text="📋 Тип заданий", callback_data="gram_choose_task")],
        [InlineKeyboardButton(text="🤖 Категория ботов", callback_data="gram_bot_category")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="bot_prgramm")],
    ])


# ============================================================
# ГЕНЕРАЦИЯ ШРИФТА
# ============================================================

FONT_PATHS = [
    '/system/fonts/Roboto-Bold.ttf',
    '/system/fonts/Roboto-Black.ttf',
    '/system/fonts/Roboto-Regular.ttf',
    '/system/fonts/Roboto-Medium.ttf',
    '/system/fonts/DroidSans-Bold.ttf',
    '/system/fonts/NotoSans-Bold.ttf',
    '/system/fonts/NotoSans-Regular.ttf',
    '/system/fonts/SystemFont.ttf',
    '/data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSans-Bold.ttf',
    '/data/data/com.termux/files/usr/share/fonts/TTF/DejaVuSans.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
    '/usr/share/fonts/truetype/freefont/FreeSansBold.ttf',
    '/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf',
    'C:\\Windows\\Fonts\\arialbd.ttf',
    'C:\\Windows\\Fonts\\arial.ttf',
    'C:\\Windows\\Fonts\\segoeuib.ttf',
    'C:\\Windows\\Fonts\\calibrib.ttf',
    '/System/Library/Fonts/Supplemental/Arial Bold.ttf',
    '/Library/Fonts/Arial Bold.ttf',
    '/System/Library/Fonts/Helvetica.ttc',
]

_FONT_SEARCH_DIRS = [
    '/system/fonts',
    '/data/data/com.termux/files/usr/share/fonts',
    '/usr/share/fonts',
    os.path.expanduser('~/.fonts'),
    'C:\\Windows\\Fonts',
    '/Library/Fonts',
    '/System/Library/Fonts',
]

_LOCAL_FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_LOCAL_FONT_PATH = os.path.join(_LOCAL_FONT_DIR, "DejaVuSans-Bold.ttf")
_FONT_DOWNLOAD_URL = (
    "https://github.com/dejavu-fonts/dejavu-fonts/raw/master/ttf/DejaVuSans-Bold.ttf"
)

_font_cache: Dict[int, ImageFont.ImageFont] = {}
_resolved_font_path: Optional[str] = None
_font_path_resolved = False
_scalable_checked = False
_is_scalable = False


def _try_truetype(path: str, size: int) -> Optional[ImageFont.FreeTypeFont]:
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return None


def _download_font() -> Optional[str]:
    if os.path.exists(_LOCAL_FONT_PATH):
        if _try_truetype(_LOCAL_FONT_PATH, 30):
            logging.info(f"🔤 Используется ранее скачанный шрифт: {_LOCAL_FONT_PATH}")
            return _LOCAL_FONT_PATH

    try:
        os.makedirs(_LOCAL_FONT_DIR, exist_ok=True)
        logging.info(f"⬇️ Скачиваю шрифт из {_FONT_DOWNLOAD_URL} ...")
        urllib.request.urlretrieve(_FONT_DOWNLOAD_URL, _LOCAL_FONT_PATH)
        if _try_truetype(_LOCAL_FONT_PATH, 30):
            logging.info(f"✅ Шрифт успешно скачан: {_LOCAL_FONT_PATH}")
            return _LOCAL_FONT_PATH
        else:
            logging.error("❌ Скачанный шрифт не работает")
            return None
    except Exception as e:
        logging.error(f"❌ Не удалось скачать шрифт: {e}")
        return None


def _search_any_font() -> Optional[str]:
    for d in _FONT_SEARCH_DIRS:
        if not os.path.isdir(d):
            continue
        try:
            for root, _, files in os.walk(d):
                for f in files:
                    if f.lower().endswith(('.ttf', '.otf')):
                        full = os.path.join(root, f)
                        if _try_truetype(full, 30):
                            return full
        except Exception:
            continue
    return None


def _resolve_font_path() -> Optional[str]:
    global _resolved_font_path, _font_path_resolved
    if _font_path_resolved:
        return _resolved_font_path

    for path in FONT_PATHS:
        if _try_truetype(path, 30):
            _resolved_font_path = path
            logging.info(f"🔤 Найден шрифт: {path}")
            _font_path_resolved = True
            return _resolved_font_path

    found = _search_any_font()
    if found:
        logging.info(f"🔤 Найден шрифт поиском: {found}")
        _resolved_font_path = found
        _font_path_resolved = True
        return _resolved_font_path

    downloaded = _download_font()
    if downloaded:
        _resolved_font_path = downloaded
        _font_path_resolved = True
        return _resolved_font_path

    logging.warning("⚠️ Ни один TrueType-шрифт не найден и не скачан")
    _resolved_font_path = None
    _font_path_resolved = True
    return None


def _load_font(size: int) -> ImageFont.ImageFont:
    size = max(1, int(size))
    if size in _font_cache:
        return _font_cache[size]

    path = _resolve_font_path()
    if path:
        font = _try_truetype(path, size)
        if font:
            _font_cache[size] = font
            return font

    font = ImageFont.load_default()
    _font_cache[size] = font
    return font


def _check_font_scalable() -> bool:
    global _scalable_checked, _is_scalable
    if _scalable_checked:
        return _is_scalable

    tmp_img = Image.new('RGB', (10, 10))
    tmp_draw = ImageDraw.Draw(tmp_img)

    f_small = _load_font(30)
    f_big = _load_font(200)

    w_small = tmp_draw.textbbox((0, 0), "A", font=f_small)[2]
    w_big = tmp_draw.textbbox((0, 0), "A", font=f_big)[2]

    _is_scalable = w_big > w_small * 1.5
    _scalable_checked = True

    if not _is_scalable:
        logging.warning("⚠️ Шрифт не масштабируется, включаю растровое масштабирование.")

    return _is_scalable


def _fit_font_to_box(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    max_height: int,
    min_size: int = 10,
    max_size: int = 900
) -> ImageFont.ImageFont:
    lo, hi = min_size, max_size
    best_size = min_size

    while lo <= hi:
        mid = (lo + hi) // 2
        font = _load_font(mid)
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]

        if w <= max_width and h <= max_height:
            best_size = mid
            lo = mid + 1
        else:
            hi = mid - 1

    return _load_font(best_size)


def _color_distance(c1: Tuple[int, int, int], c2: Tuple[int, int, int]) -> float:
    return sum((a - b) ** 2 for a, b in zip(c1, c2)) ** 0.5


def _random_bg_color() -> Tuple[int, int, int]:
    mode = random.choices(
        ["black", "dark", "colored"],
        weights=[45, 35, 20],
        k=1
    )[0]

    if mode == "black":
        return (0, 0, 0)
    elif mode == "dark":
        return tuple(random.randint(0, 35) for _ in range(3))
    else:
        return tuple(random.randint(0, 255) for _ in range(3))


def _generate_letter_colors(n: int, bg_color: Tuple[int, int, int]) -> List[Tuple[int, int, int]]:
    colors = []
    hue_shift = random.random()

    for i in range(n):
        base_hue = (i / max(n, 1) + hue_shift) % 1.0
        hue = (base_hue + random.uniform(-0.03, 0.03)) % 1.0
        sat = random.uniform(0.85, 1.0)
        val = random.uniform(0.9, 1.0)
        rgb = colorsys.hsv_to_rgb(hue, sat, val)
        color = tuple(int(c * 255) for c in rgb)

        if _color_distance(color, bg_color) < 90:
            color = tuple(min(255, c + 150) for c in color)

        colors.append(color)

    return colors


def _render_text_block(
    text: str,
    colors: List[Tuple[int, int, int]],
    font: ImageFont.ImageFont,
    padding: int = 6
) -> Image.Image:
    tmp = Image.new('RGBA', (10, 10), (0, 0, 0, 0))
    tmp_draw = ImageDraw.Draw(tmp)
    bbox_full = tmp_draw.textbbox((0, 0), text, font=font)
    w = (bbox_full[2] - bbox_full[0]) + padding * 2
    h = (bbox_full[3] - bbox_full[1]) + padding * 2
    w = max(w, 1)
    h = max(h, 1)

    canvas = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    x_offset = padding - bbox_full[0]
    y_offset = padding - bbox_full[1]

    x_pos = x_offset
    for ch, color in zip(text, colors):
        draw.text((x_pos, y_offset), ch, fill=color + (255,), font=font)
        ch_bbox = draw.textbbox((0, 0), ch, font=font)
        x_pos += ch_bbox[2] - ch_bbox[0]

    bbox_content = canvas.getbbox()
    if bbox_content:
        canvas = canvas.crop(bbox_content)

    return canvas


def generate_bot_image() -> bytes:
    size = 1080
    text = PHOTO_TEXT

    bg_color = _random_bg_color()
    image = Image.new('RGB', (size, size), bg_color)

    width_ratio = random.uniform(0.75, 0.92)
    target_width = int(size * width_ratio)

    colors = _generate_letter_colors(len(text), bg_color)

    if _check_font_scalable():
        tmp_draw = ImageDraw.Draw(image)
        max_height = int(size * random.uniform(0.30, 0.50))
        font = _fit_font_to_box(tmp_draw, text, target_width, max_height)
        text_block = _render_text_block(text, colors, font)
        logging.info(f"✅ Текст отрендерен через TrueType, размер блока: {text_block.size}")
    else:
        base_font = _load_font(60)
        raw_block = _render_text_block(text, colors, base_font)

        if raw_block.width > 0:
            scale = target_width / raw_block.width
        else:
            scale = 1.0

        new_w = max(1, target_width)
        new_h = max(1, int(raw_block.height * scale))
        text_block = raw_block.resize((new_w, new_h), Image.NEAREST)
        logging.info(f"✅ Текст отрендерен через bitmap+upscale, размер блока: {text_block.size}")

    max_x = max(0, size - text_block.width)
    max_y = max(0, size - text_block.height)
    base_x = max_x // 2
    base_y = max_y // 2

    margin = int(size * 0.02)
    dx_range = max(0, min(base_x - margin, max_x - base_x - margin))
    dy_range = max(0, min(base_y - margin, max_y - base_y - margin))
    dx = random.randint(-dx_range, dx_range) if dx_range > 0 else 0
    dy = random.randint(-dy_range, dy_range) if dy_range > 0 else 0

    paste_x = min(max(base_x + dx, 0), max_x)
    paste_y = min(max(base_y + dy, 0), max_y)

    image.paste(text_block, (paste_x, paste_y), text_block)

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
    if not client.is_connected():
        await client.connect()
    before = await get_last_msg(client, bot_username)
    snap = msg_snap(before)
    mid = before.id if before else 0
    logging.info(f"📸 Отправляю фото в {bot_username}")
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
# ЗАДАНИЯ С БОТАМИ (С ВЫБОРОМ КАТЕГОРИИ И ИГНОРИРОВАНИЕМ 100k)
# ============================================================

async def process_bot_tasks(client: TelegramClient, bot_username: str, msg, user_id: int = None):
    """
    Обработка заданий с ботами с выбором категории и игнорированием 100k
    """
    try:
        logging.info("🤖 Обрабатываю задания с ботами...")

        if not msg or not msg.buttons:
            logging.warning("⚠️ Нет кнопок в сообщении")
            return msg

        # Получаем выбранную категорию
        category = user_bot_category.get(user_id, "regular")
        logging.info(f"📋 Выбранная категория: {category}")

        # Ключевые слова для поиска категории
        category_keywords = {
            "regular": ["обычные боты"],
            "webapp": ["боты с web app", "web app"],
            "conditions": ["с дополнительными условиями", "с доп. условиями"]
        }

        # Ищем кнопку выбранной категории
        keywords = category_keywords.get(category, ["обычные боты"])
        category_btn = find_button(msg, keywords)
        
        if category_btn:
            logging.info(f"📋 Нажимаю категорию: '{btn_text(category_btn)}'")
            result = await click_btn(client, bot_username, category_btn, timeout=15)
            if not result:
                logging.warning("⚠️ Нет ответа после выбора категории")
                return msg
            if is_captcha_message(result):
                await send_captcha_to_user(result, user_chat_id, client)
                return result
            msg = result
        else:
            logging.info(f"ℹ️ Кнопка категории не найдена, продолжаю с текущим сообщением")

        # Находим все кнопки заданий
        all_task_btns = find_all_buttons(msg, ["перейти в бота"])
        
        if not all_task_btns:
            logging.warning("⚠️ Не найдены кнопки заданий с ботом")
            log_buttons(msg, "  ")
            return msg

        logging.info(f"🔗 Найдено заданий: {len(all_task_btns)}")

        for task_btn in all_task_btns:
            task_text = btn_text(task_btn).lower()
            
            # Проверяем, не 100k ли это задание (для категории conditions)
            if category == "conditions":
                if "100 000" in task_text or "100000" in task_text or "100k" in task_text:
                    logging.info(f"⏭ Пропускаю задание 100k: '{task_text}'")
                    # Ищем кнопку "Скрыть" или "Пропустить" чтобы перейти к следующему
                    skip_btn = find_button(msg, ["скрыть", "пропустить", "▶️"])
                    if skip_btn:
                        await click_btn(client, bot_username, skip_btn, timeout=5)
                        await asyncio.sleep(1)
                    continue

            logging.info(f"🔗 Нажимаю задание: '{task_text}'")
            result = await click_btn(client, bot_username, task_btn, timeout=15)
            if not result:
                logging.warning("⚠️ Нет ответа после клика по заданию")
                continue
            if is_captcha_message(result):
                await send_captcha_to_user(result, user_chat_id, client)
                continue

            # Генерируем и отправляем фото
            photo_bytes = generate_bot_image()
            logging.info("✅ Сгенерировано фото")

            photo_result = await send_photo(client, bot_username, photo_bytes, timeout=15)
            if not photo_result:
                logging.warning("⚠️ Нет ответа на отправку фото")
                continue

            if is_captcha_message(photo_result):
                await send_captcha_to_user(photo_result, user_chat_id, client)
                continue

            await asyncio.sleep(1)

            # Ищем кнопку "Следующий бот"
            next_btn = find_button(photo_result, ["следующий бот"])
            if next_btn:
                logging.info("⏭️ Нажимаю 'Следующий бот'...")
                await click_btn(client, bot_username, next_btn, timeout=15)
            else:
                logging.info("✅ Кнопка 'Следующий бот' не найдена, задание завершено")

            delay = random.randint(2, 4)
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

        captcha_storage[chat_id] = {'client': client, 'bot_username': bu, 'msg_id': msg.id}

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
    if task_type == "bots":
        return msg  # bots обрабатываются отдельно в do_cycle

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
    elif task_type == "groups":
        kw = "вступить в группу"
    elif task_type == "bots":
        kw = "перейти в бота"
    else:
        kw = "просмотр постов"

    target_btn = find_button(earn_msg, [kw])

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
    if task_type == "channels" or task_type == "groups":
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
        result = await process_bot_tasks(client, bot_username, task_msg, user_id)
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
            "groups": "👥 Вступление в группы",
            "posts": "📱 Просмотр постов",
            "bots": "🤖 Задания с ботами"
        }
        if task_type in task_names:
            user_task_choice[user_id] = task_type
            await callback.answer(f"✅ {task_names[task_type]}")
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


@router.callback_query(lambda c: c.data and c.data == "gram_task_type")
async def gram_task_type_callback(callback: types.CallbackQuery):
    try:
        user_id = callback.from_user.id
        await callback.answer()
        current = user_task_choice.get(user_id, "channels")
        task_names = {
            "channels": "📢 Подписка на каналы",
            "groups": "👥 Вступление в группы",
            "posts": "📱 Просмотр постов",
            "bots": "🤖 Задания с ботами"
        }
        await callback.message.edit_text(
            f"📋 <b>Выбор типа заданий</b>\n\n"
            f"Текущий тип: <b>{task_names.get(current, current)}</b>\n\n"
            f"Выберите тип заданий для автоматического выполнения:",
            parse_mode=ParseMode.HTML,
            reply_markup=get_task_choice_keyboard(user_id)
        )
    except Exception as e:
        logging.error(f"❌ gram_task_type_callback: {e}")
        await callback.answer("❌ Ошибка")


@router.callback_query(lambda c: c.data and c.data.startswith("bot_cat_"))
async def bot_category_callback(callback: types.CallbackQuery):
    """Выбор категории ботов"""
    try:
        category = callback.data.replace("bot_cat_", "")
        user_id = callback.from_user.id
        
        user_bot_category[user_id] = category
        
        cat_names = {
            "regular": "Обычные боты",
            "webapp": "Боты с Web App",
            "conditions": "С дополнительными условиями"
        }
        
        await callback.answer(f"✅ Выбрано: {cat_names.get(category, category)}")
        await callback.message.edit_text(
            f"✅ <b>Выбрана категория ботов:</b>\n{cat_names.get(category, category)}\n\n"
            f"Категория будет использована при выполнении заданий с ботами.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 Изменить категорию", callback_data="gram_bot_category")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="bot_prgramm_settings")]
            ])
        )
    except Exception as e:
        logging.error(f"❌ bot_category_callback: {e}")
        await callback.answer("❌ Ошибка")


@router.callback_query(lambda c: c.data and c.data == "gram_bot_category")
async def gram_bot_category_callback(callback: types.CallbackQuery):
    """Меню выбора категории ботов"""
    try:
        user_id = callback.from_user.id
        await callback.answer()
        await callback.message.edit_text(
            "📋 <b>Выбор категории ботов</b>\n\n"
            "Выберите категорию для заданий с ботами:\n\n"
            "🤖 Обычные боты — стандартные задания\n"
            "🌐 Боты с Web App — задания с веб-приложениями\n"
            "📋 С доп. условиями — задания с условиями (100k GRAM пропускаются)",
            parse_mode=ParseMode.HTML,
            reply_markup=get_bot_category_keyboard(user_id)
        )
    except Exception as e:
        logging.error(f"❌ gram_bot_category_callback: {e}")
        await callback.answer("❌ Ошибка")


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
            if phone not in active_tasks:
                logging.info(f"⏹ Воркер {phone} остановлен извне")
                break

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
                logging.info(f"⏹ Цикл {phone} отменён")
                break
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
    'get_bot_category_keyboard', 'get_bot_settings_keyboard',
    'active_clients', 'active_tasks'
]
