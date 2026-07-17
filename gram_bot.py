"""
Модуль для Gram ботов - исправленная подписка
"""

import asyncio
import re
import random
import logging
import os
from typing import Optional, Dict, Any, Tuple, List
from telethon import TelegramClient, errors
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from aiogram import Router, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode

try:
    import aiohttp
except ImportError:
    aiohttp = None

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

# Время между подписками (в секундах)
SUBSCRIBE_DELAY = 60  # 1 минута

# Регэксп для поиска телеграм-ссылок в любом тексте
TG_LINK_RE = re.compile(
    r"(?:https?://)?(?:t(?:elegram)?\.me|telegram\.dog)/(\+?[A-Za-z0-9_]+)",
    re.IGNORECASE
)


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


# ============ РЕЗОЛВИНГ ССЫЛОК ============

def extract_tg_link_from_text(text: str) -> Optional[str]:
    """Ищет t.me/... ссылку в произвольном тексте (например, в редирект-URL)."""
    if not text:
        return None
    match = TG_LINK_RE.search(text)
    if match:
        return f"https://t.me/{match.group(1)}"
    return None


async def resolve_real_channel_link(url: str) -> Optional[str]:
    """
    Если ссылка напрямую ведёт на t.me - возвращает её.
    Если это редирект-ссылка стороннего сервиса - пытается получить
    финальный t.me адрес через HTTP запрос с follow redirects.
    """
    if not url:
        return None

    # 1. Сразу проверяем, есть ли t.me прямо в исходной ссылке
    direct = extract_tg_link_from_text(url)
    if direct:
        return direct

    # 2. Пробуем зарезолвить редирект через HTTP
    if aiohttp is None:
        logging.warning("⚠️ aiohttp не установлен, невозможно резолвить редирект-ссылку")
        return None

    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, allow_redirects=True) as resp:
                final_url = str(resp.url)
                tg_link = extract_tg_link_from_text(final_url)
                if tg_link:
                    logging.info(f"🔀 Редирект резолвлен: {url} -> {tg_link}")
                    return tg_link

                # Иногда ссылка на t.me прячется в теле HTML (meta refresh / JS redirect)
                try:
                    html = await resp.text()
                    tg_link = extract_tg_link_from_text(html)
                    if tg_link:
                        logging.info(f"🔀 Ссылка найдена в HTML: {tg_link}")
                        return tg_link
                except Exception:
                    pass
    except Exception as e:
        logging.error(f"❌ Не удалось зарезолвить ссылку {url}: {e}")

    return None


# ============ ПОДПИСКА ============

async def subscribe_to_channel(client: TelegramClient, link: str) -> Tuple[bool, str]:
    """
    Подписка на канал.
    Возвращает: (успех, сообщение)
    """
    try:
        logging.info(f"📢 Подписываюсь: {link}")

        if "?" in link:
            link = link.split("?")[0]

        if "t.me/" in link.lower() or "telegram.me/" in link.lower():
            if "+" in link:
                invite_hash = link.split("+")[-1]
                if "/" in invite_hash:
                    invite_hash = invite_hash.split("/")[0]
                await client(ImportChatInviteRequest(invite_hash))
                logging.info(f"✅ Подписался (invite): {link}")
                return True, "success"
            elif "joinchat/" in link.lower():
                invite_hash = link.split("joinchat/")[-1].split("/")[0]
                await client(ImportChatInviteRequest(invite_hash))
                logging.info(f"✅ Подписался (joinchat): {link}")
                return True, "success"
            else:
                # Вытаскиваем username из ссылки после t.me/
                m = re.search(r"t(?:elegram)?\.me/([A-Za-z0-9_]+)", link, re.IGNORECASE)
                username = m.group(1) if m else link.split("/")[-1]
                if username:
                    entity = await client.get_entity(f"@{username}")
                    await client(JoinChannelRequest(entity))
                    logging.info(f"✅ Подписался (channel): {link}")
                    return True, "success"
        else:
            # Ссылка не похожа на t.me - пробуем как username напрямую
            username = link.lstrip("@")
            entity = await client.get_entity(f"@{username}")
            await client(JoinChannelRequest(entity))
            logging.info(f"✅ Подписался: {link}")
            return True, "success"

        return False, "unrecognized_link"

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

async def send_code(phone: str, bot_username: str) -> bool:
    try:
        phone = phone.strip()
        if not phone.startswith('+'):
            phone = '+' + phone

        os.makedirs("sessions", exist_ok=True)

        session_name = f"sessions/{phone.replace('+', '')}"
        client = TelegramClient(session_name, API_ID, API_HASH)

        await client.connect()

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
    except Exception as e:
        logging.error(f"❌ Ошибка отправки кода: {e}")
        return False


async def start_gram_bot(phone: str, code: str, bot_username: str, chat_id: int = None) -> bool:
    try:
        if chat_id:
            set_user_chat_id(chat_id)

        phone = phone.strip()
        if not phone.startswith('+'):
            phone = '+' + phone

        client = active_clients.get(phone)
        if not client:
            session_name = f"sessions/{phone.replace('+', '')}"
            client = TelegramClient(session_name, API_ID, API_HASH)
            await client.connect()
            active_clients[phone] = client

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
    except Exception as e:
        logging.error(f"❌ Ошибка авторизации: {e}")
        return False


async def start_gram_worker(client: TelegramClient, bot_username: str, phone: str, user_id: int = None):
    if user_id:
        set_user_chat_id(user_id)

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
        await client.send_message(bot_username, text)
        logging.info(f"📤 {bot_username}: {text[:50]}")
        await asyncio.sleep(delay)
    except Exception as e:
        logging.error(f"❌ Ошибка отправки: {e}")

async def get_last_message(client: TelegramClient, bot_username: str):
    try:
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

def extract_task_rows(msg) -> List[Tuple[Any, Any]]:
    """
    Извлекает пары (кнопка_подписки, кнопка_проверки) из строк сообщения.
    Ожидаемая структура строки: [Подписаться (url), Проверить (callback)]
    """
    pairs = []
    if not msg or not msg.buttons:
        return pairs

    for row in msg.buttons:
        subscribe_btn = None
        check_btn = None

        for btn in row:
            btn_text = get_button_text(btn).lower()
            url = get_button_url(btn)

            if url:
                subscribe_btn = btn
            elif "провер" in btn_text or "✅" in btn_text:
                check_btn = btn
            elif "подписат" in btn_text and not url:
                # Иногда текст кнопки "Подписаться" без явного .url атрибута,
                # но всё равно является url-кнопкой - подстрахуемся
                subscribe_btn = subscribe_btn or btn

        if subscribe_btn and check_btn:
            pairs.append((subscribe_btn, check_btn))

    return pairs


def find_next_page_button(msg):
    """Ищет кнопку перехода на следующую страницу списка заданий."""
    if not msg or not msg.buttons:
        return None

    for row in msg.buttons:
        for btn in row:
            text = get_button_text(btn).strip()
            if text in (">", "▶", "➡️", "След.", "Далее"):
                return btn
    return None


async def process_one_task_row(client: TelegramClient, bot_username: str,
                                 subscribe_btn, check_btn) -> str:
    """
    Обрабатывает одну строку задания: подписка + проверка.
    Возвращает: "done" / "already" / "failed" / "captcha"
    """
    url = get_button_url(subscribe_btn)
    btn_text = get_button_text(subscribe_btn)

    if not url:
        logging.warning(f"⚠️ У кнопки '{btn_text}' нет ссылки")
        return "failed"

    logging.info(f"🔗 Задание: '{btn_text}' -> {url}")

    real_link = await resolve_real_channel_link(url)
    if not real_link:
        logging.warning(f"⚠️ Не удалось определить канал по ссылке: {url}")
        real_link = url  # пробуем как есть, вдруг subscribe_to_channel справится

    success, result = await subscribe_to_channel(client, real_link)

    if not success and result.startswith("flood:"):
        wait_sec = int(result.split(":")[1])
        logging.warning(f"⏳ Flood wait {wait_sec} сек, жду...")
        await asyncio.sleep(min(wait_sec, 300))

    await asyncio.sleep(random.uniform(2, 4))

    # Нажимаем "Проверить" для этой же строки
    try:
        logging.info(f"✅ Нажимаю Проверить для '{btn_text}'")
        await check_btn.click()
        await asyncio.sleep(random.uniform(2, 3))
    except Exception as e:
        logging.error(f"❌ Ошибка клика Проверить: {e}")
        return "failed"

    updated_msg = await get_last_message(client, bot_username)

    if updated_msg and is_captcha_message(updated_msg):
        return "captcha"

    text = (updated_msg.raw_text or "").lower() if updated_msg else ""

    if "начислено" in text or "успешно" in text:
        logging.info("💰 Начисление получено!")
        return "done"
    if "не подписан" in text:
        logging.warning("⚠️ Бот считает, что подписка не пройдена")
        return "failed"

    # Если текст сообщения не изменился принципиально - считаем что обработано
    return "done"


async def process_channel_list(client: TelegramClient, bot_username: str, msg, phone: str, max_pages: int = 5):
    """
    Обрабатывает список заданий на подписку (может быть несколько страниц).
    Проходит по каждой паре [Подписаться -> Проверить] на каждой странице.
    """
    try:
        logging.info("📋 Обрабатываю список заданий на подписку...")

        current_msg = msg
        page = 1

        while current_msg and page <= max_pages:
            pairs = extract_task_rows(current_msg)

            if not pairs:
                logging.warning("⚠️ На текущей странице не найдено пар [Подписаться/Проверить]")
            else:
                logging.info(f"📄 Страница {page}: найдено заданий - {len(pairs)}")

                for i, (sub_btn, chk_btn) in enumerate(pairs, 1):
                    logging.info(f"— Задание {i}/{len(pairs)} —")

                    result = await process_one_task_row(client, bot_username, sub_btn, chk_btn)

                    if result == "captcha":
                        return await get_last_message(client, bot_username)

                    # Обновляем сообщение перед следующей итерацией,
                    # т.к. бот мог перерисовать список после проверки
                    refreshed = await get_last_message(client, bot_username)
                    if refreshed:
                        current_msg = refreshed

                    await asyncio.sleep(random.uniform(1.5, 3))

            # Пробуем перейти на следующую страницу
            next_btn = find_next_page_button(current_msg)
            if next_btn:
                logging.info("➡️ Перехожу на следующую страницу заданий...")
                try:
                    await next_btn.click()
                    await asyncio.sleep(2)
                    current_msg = await get_last_message(client, bot_username)
                    page += 1
                    continue
                except Exception as e:
                    logging.error(f"❌ Ошибка перехода на след. страницу: {e}")
                    break
            else:
                logging.info("✅ Больше страниц заданий нет")
                break

        return current_msg or msg

    except Exception as e:
        logging.error(f"❌ Ошибка обработки списка заданий: {e}")
        return msg


async def do_one_cycle(client: TelegramClient, bot_username: str, user_id: int = None, phone: str = None):
    global SUBSCRIBE_DELAY

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
        has_task = len(extract_task_rows(updated)) > 0

        # Fallback-проверка: есть ли вообще кнопка "Проверить" в сообщении
        if not has_task and updated.buttons:
            for row in updated.buttons:
                for btn in row:
                    if "провер" in get_button_text(btn).lower():
                        has_task = True
                        break
                if has_task:
                    break

        if has_task:
            logging.info("📢 Обнаружено задание подписки")
            updated = await process_channel_list(client, bot_username, updated, phone)

            if updated and is_captcha_message(updated):
                await send_captcha_to_user(updated, user_chat_id, client)
                return

            await asyncio.sleep(2)
            await go_to_earn_menu(client, bot_username)

            logging.info(f"⏳ Пауза {SUBSCRIBE_DELAY} секунд перед следующим циклом...")
            await asyncio.sleep(SUBSCRIBE_DELAY)

            await do_one_cycle(client, bot_username, user_id, phone)
            return

        logging.info("⚠️ Задания подписки не найдены, возврат в меню")
        await go_to_earn_menu(client, bot_username)
        return

    # ===== ветка "posts" =====
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
    global SUBSCRIBE_DELAY

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
