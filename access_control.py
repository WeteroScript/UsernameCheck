"""
Модуль доступа: премиум-статус и админы.

Единый источник правды для всего проекта — импортируется из Bot.py,
gram_bot.py, username_bot.py везде, где нужна проверка прав.
Хранение — в JSON-файлах (тот же подход, что и у sessions/bot_choice
в Bot.py), чтобы статусы переживали перезапуск бота.
"""

import json
import os
import logging
from typing import Dict, Set, Optional, List
from datetime import datetime, timedelta

ADMINS_FILE = "admins.json"
PREMIUM_FILE = "premium_users.json"
MANDATORY_CHANNELS_FILE = "mandatory_channels.json"

PREMIUM_ICON = "💎"

# Захардкоженный суперадмин — есть всегда, даже если файл admins.json
# отсутствует или был случайно очищен. Задан пользователем явно.
SUPER_ADMIN_ID = 5877790074

# Канал обязательной подписки по умолчанию (задан явно, без срока действия).
DEFAULT_MANDATORY_CHANNEL_ID = -1004329748530

_admins: Set[int] = set()
_premium: Dict[int, Dict] = {}  # user_id -> {"granted_at": iso, "granted_by": id|None}
_mandatory_channels: Dict[int, Optional[str]] = {}  # chat_id -> expiry_iso | None (бессрочно)


def _load():
    global _admins, _premium
    if os.path.exists(ADMINS_FILE):
        try:
            with open(ADMINS_FILE, 'r', encoding='utf-8') as f:
                _admins = set(int(x) for x in json.load(f))
        except Exception as e:
            logging.error(f"❌ Ошибка загрузки admins.json: {e}")
    _admins.add(SUPER_ADMIN_ID)

    if os.path.exists(PREMIUM_FILE):
        try:
            with open(PREMIUM_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                _premium = {int(k): v for k, v in data.items()}
        except Exception as e:
            logging.error(f"❌ Ошибка загрузки premium_users.json: {e}")

    global _mandatory_channels
    if os.path.exists(MANDATORY_CHANNELS_FILE):
        try:
            with open(MANDATORY_CHANNELS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                _mandatory_channels = {int(k): v for k, v in data.items()}
        except Exception as e:
            logging.error(f"❌ Ошибка загрузки mandatory_channels.json: {e}")
    else:
        _mandatory_channels = {DEFAULT_MANDATORY_CHANNEL_ID: None}
        _save_mandatory()


def _save_admins():
    try:
        with open(ADMINS_FILE, 'w', encoding='utf-8') as f:
            json.dump(sorted(_admins), f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.error(f"❌ Ошибка сохранения admins.json: {e}")


def _save_premium():
    try:
        with open(PREMIUM_FILE, 'w', encoding='utf-8') as f:
            json.dump(_premium, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.error(f"❌ Ошибка сохранения premium_users.json: {e}")


def _save_mandatory():
    try:
        with open(MANDATORY_CHANNELS_FILE, 'w', encoding='utf-8') as f:
            json.dump(_mandatory_channels, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.error(f"❌ Ошибка сохранения mandatory_channels.json: {e}")


_load()


# ============ ПРОВЕРКИ ============

def is_admin(user_id: int) -> bool:
    return user_id in _admins


def is_premium(user_id: int) -> bool:
    """Премиум-функции доступны и премиум-пользователям, и админам
    (админам — всегда, независимо от отдельной премиум-записи).
    Премиум с истёкшим сроком действия — как будто его нет."""
    if is_admin(user_id):
        return True
    info = _premium.get(user_id)
    if not info:
        return False
    expiry = info.get("expires_at")
    if expiry is None:
        return True  # бессрочный премиум
    try:
        if datetime.fromisoformat(expiry) <= datetime.now():
            del _premium[user_id]
            _save_premium()
            return False
    except Exception:
        pass
    return True


# ============ УПРАВЛЕНИЕ АДМИНАМИ ============

def add_admin(user_id: int) -> bool:
    if user_id in _admins:
        return False
    _admins.add(user_id)
    _save_admins()
    return True


def remove_admin(user_id: int) -> bool:
    if user_id == SUPER_ADMIN_ID:
        return False  # суперадмина снять нельзя
    if user_id not in _admins:
        return False
    _admins.discard(user_id)
    _save_admins()
    return True


def get_all_admins() -> List[int]:
    return sorted(_admins)


# ============ УПРАВЛЕНИЕ ПРЕМИУМОМ ============

def grant_premium(user_id: int, granted_by: Optional[int] = None, days: float = 1) -> bool:
    is_new = user_id not in _premium
    expiry = None
    if days is not None and days > 0:
        expiry = (datetime.now() + timedelta(days=days)).isoformat()
    _premium[user_id] = {
        "granted_at": datetime.now().isoformat(),
        "granted_by": granted_by,
        "expires_at": expiry,
    }
    _save_premium()
    return is_new


def revoke_premium(user_id: int) -> bool:
    if user_id not in _premium:
        return False
    del _premium[user_id]
    _save_premium()
    return True


def get_all_premium() -> Dict[int, Dict]:
    return dict(_premium)


# ============ РАЗБОР "юз/айди" ИЗ АРГУМЕНТОВ АДМИН-КОМАНД ============

async def resolve_user_id(bot, arg: str) -> Optional[int]:
    """Принимает либо числовой user_id, либо @username и возвращает
    numeric user_id (или None, если распознать не удалось)."""
    arg = arg.strip()
    if arg.lstrip('-').isdigit():
        return int(arg)
    username = arg.lstrip('@')
    try:
        chat = await bot.get_chat(f"@{username}")
        return chat.id
    except Exception as e:
        logging.error(f"❌ resolve_user_id: не удалось найти @{username}: {e}")
        return None


# ============ ЛИМИТЫ АККАУНТОВ ПО ПРЕМИУМ-СТАТУСУ ============

MAX_SESSIONS_FREE = 3
MAX_SESSIONS_PREMIUM = 10


def get_max_sessions(user_id: int) -> int:
    return MAX_SESSIONS_PREMIUM if is_premium(user_id) else MAX_SESSIONS_FREE


# Коды стран, доступные для подключения аккаунта без премиума.
# +7 — Россия/Казахстан, остальные страны СНГ/Закавказья.
FREE_ALLOWED_COUNTRY_CODES = [
    "7",    # Россия, Казахстан
    "380",  # Украина
    "375",  # Беларусь
    "373",  # Молдова
    "995",  # Грузия
    "374",  # Армения
    "994",  # Азербайджан
    "996",  # Кыргызстан
    "993",  # Туркменистан
    "998",  # Узбекистан
    "992",  # Таджикистан
]


def is_phone_allowed(user_id: int, phone: str) -> bool:
    """Премиум/админам доступны любые номера. Без премиума — только
    страны СНГ/Закавказья из FREE_ALLOWED_COUNTRY_CODES."""
    if is_premium(user_id):
        return True
    digits = phone.lstrip('+')
    return any(digits.startswith(code) for code in FREE_ALLOWED_COUNTRY_CODES)


# ============ ОБЯЗАТЕЛЬНАЯ ПОДПИСКА НА КАНАЛЫ ============

def add_mandatory_channel(chat_id: int, hours: Optional[float] = None):
    """hours=None — бессрочно, иначе канал уберётся из обязательных
    сам собой через указанное количество часов."""
    expiry = None
    if hours is not None:
        expiry = (datetime.now() + timedelta(hours=hours)).isoformat()
    _mandatory_channels[chat_id] = expiry
    _save_mandatory()


def remove_mandatory_channel(chat_id: int) -> bool:
    if chat_id not in _mandatory_channels:
        return False
    del _mandatory_channels[chat_id]
    _save_mandatory()
    return True


def get_mandatory_channels() -> List[int]:
    """Актуальный (без истёкших по сроку) список ID каналов/групп
    обязательной подписки."""
    now = datetime.now()
    active = []
    expired = []
    for chat_id, expiry in _mandatory_channels.items():
        if expiry is None:
            active.append(chat_id)
            continue
        try:
            if datetime.fromisoformat(expiry) > now:
                active.append(chat_id)
            else:
                expired.append(chat_id)
        except Exception:
            active.append(chat_id)
    if expired:
        for chat_id in expired:
            del _mandatory_channels[chat_id]
        _save_mandatory()
    return active


# ============ БАН ============

BANNED_FILE = "banned_users.json"
_banned: Dict[int, str] = {}  # user_id -> причина


def _load_banned():
    global _banned
    if os.path.exists(BANNED_FILE):
        try:
            with open(BANNED_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                _banned = {int(k): v for k, v in data.items()}
        except Exception as e:
            logging.error(f"❌ Ошибка загрузки banned_users.json: {e}")


def _save_banned():
    try:
        with open(BANNED_FILE, 'w', encoding='utf-8') as f:
            json.dump(_banned, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.error(f"❌ Ошибка сохранения banned_users.json: {e}")


_load_banned()


def ban_user(user_id: int, reason: str = "") -> bool:
    if user_id == SUPER_ADMIN_ID:
        return False
    _banned[user_id] = reason or "без причины"
    _save_banned()
    return True


def unban_user(user_id: int) -> bool:
    if user_id not in _banned:
        return False
    del _banned[user_id]
    _save_banned()
    return True


def is_banned(user_id: int) -> bool:
    return user_id in _banned


def get_ban_reason(user_id: int) -> Optional[str]:
    return _banned.get(user_id)


# ============ ТЕХНИЧЕСКИЕ РАБОТЫ ============

TECHNICAL_FILE = "technical_mode.json"
_technical_mode = False


def _load_technical():
    global _technical_mode
    if os.path.exists(TECHNICAL_FILE):
        try:
            with open(TECHNICAL_FILE, 'r', encoding='utf-8') as f:
                _technical_mode = bool(json.load(f).get("enabled", False))
        except Exception as e:
            logging.error(f"❌ Ошибка загрузки technical_mode.json: {e}")


def _save_technical():
    try:
        with open(TECHNICAL_FILE, 'w', encoding='utf-8') as f:
            json.dump({"enabled": _technical_mode}, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.error(f"❌ Ошибка сохранения technical_mode.json: {e}")


_load_technical()


def set_technical_mode(enabled: bool):
    global _technical_mode
    _technical_mode = enabled
    _save_technical()


def is_technical_mode() -> bool:
    return _technical_mode


# ============ БЛОКИРОВКА ТЕХПОДДЕРЖКИ ("Сообщить о баге") ============

BLOCKED_TECHPOD_FILE = "blocked_techpod.json"
_blocked_techpod: Set[int] = set()


def _load_blocked_techpod():
    global _blocked_techpod
    if os.path.exists(BLOCKED_TECHPOD_FILE):
        try:
            with open(BLOCKED_TECHPOD_FILE, 'r', encoding='utf-8') as f:
                _blocked_techpod = set(int(x) for x in json.load(f))
        except Exception as e:
            logging.error(f"❌ Ошибка загрузки blocked_techpod.json: {e}")


def _save_blocked_techpod():
    try:
        with open(BLOCKED_TECHPOD_FILE, 'w', encoding='utf-8') as f:
            json.dump(sorted(_blocked_techpod), f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.error(f"❌ Ошибка сохранения blocked_techpod.json: {e}")


_load_blocked_techpod()


def block_tech_support(user_id: int) -> bool:
    if user_id in _blocked_techpod:
        return False
    _blocked_techpod.add(user_id)
    _save_blocked_techpod()
    return True


def unblock_tech_support(user_id: int) -> bool:
    if user_id not in _blocked_techpod:
        return False
    _blocked_techpod.discard(user_id)
    _save_blocked_techpod()
    return True


def is_tech_support_blocked(user_id: int) -> bool:
    return user_id in _blocked_techpod
