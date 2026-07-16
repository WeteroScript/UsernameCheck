"""
Модуль для Gram ботов
С выбором типа заданий и авто-подпиской через кнопки
"""

import asyncio
import re
import random
import logging
import os
import io
from typing import Optional, Dict, Any, List
from telethon import TelegramClient, errors
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.tl.types import InputChannel, KeyboardButtonWebView, KeyboardButtonUrl
from telethon.tl.functions.messages import RequestWebViewRequest
from aiogram import Router, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.enums import ParseMode

router = Router()

# ============ КОНФИГ ============

API_ID = 2040
API_HASH = "b18441a1ff607e10a989891a5462e627"

active_clients: Dict[str, TelegramClient] = {}
active_tasks: Dict[str, asyncio.Task] = {}
gram_bot_initialized = False
user_chat_id: Optional[int] = None
bot_username_for_task: Dict[str, str] = {}  # phone -> bot_username
bot_instance = None

# Хранилище для капч
captcha_storage: Dict[int, Dict[str, Any]] = {}

# Настройки
AUTO_SOLVE_CAPTCHA = False
AUTO_SUBSCRIBE = True

# Храним выбранный тип заданий для каждого пользователя
user_task_choice: Dict[int, str] = {}  # user_id -> task_type


# ============ УСТАНОВКА BOT ============

def set_bot_instance(bot):
    global bot_instance
    bot_instance = bot
    logging.info("✅ Экземпляр бота установлен")

def set_user_chat_id(chat_id: int):
    global user_chat_id
    user_chat_id = chat_id
    logging.info(f"✅ User chat ID установлен: {chat_id}")


# ============ РАБОТА С КНОПКАМИ ============

def is_webapp_button(btn) -> bool:
    try:
        if hasattr(btn, "url") and btn.url is not None:
            return True
        if hasattr(btn, "button"):
            return hasattr(btn.button, "url") and btn.button.url is not None
        return False
    except:
        return False

def is_url_button(btn) -> bool:
    """Проверка, является ли кнопка URL-кнопкой"""
    try:
        if hasattr(btn, "url"):
            return btn.url is not None
        if hasattr(btn, "button"):
            return hasattr(btn.button, "url") and btn.button.url is not None
        return False
    except:
        return False

def get_button_url(btn) -> Optional[str]:
    """Получение URL из кнопки"""
    try:
        if hasattr(btn, "url") and btn.url:
            return btn.url
        if hasattr(btn, "button") and hasattr(btn.button, "url"):
            return btn.button.url
        return None
    except:
        return None

def get_button_text(btn) -> str:
    try:
        if hasattr(btn, 'text'):
            return btn.text
        return str(btn)
    except:
        return "Неизвестно"

def get_button_data(btn) -> dict:
    return {
        "text": get_button_text(btn),
        "is_webapp": is_webapp_button(btn),
        "is_url": is_url_button(btn),
        "url": get_button_url(btn) if is_url_button(btn) or is_webapp_button(btn) else None
    }


# ============ КЛАВИАТУРЫ ============

def get_task_choice_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Клавиатура выбора типа заданий"""
    buttons = []
    
    task_types = {
        "posts": "📱 Просмотр постов",
        "channels": "📢 Подписка на каналы",
        "groups": "👥 Вступление в группы",
        "bots": "🤖 Перейти в бота",
        "reactions": "❤️ Поставить реакции",
        "boosts": "🚀 Премиум буст"
    }
    
    for task_key, task_name in task_types.items():
        is_selected = user_task_choice.get(user_id) == task_key
        text = f"{'✅ ' if is_selected else ''}{task_name}"
        buttons.append([InlineKeyboardButton(text=text, callback_data=f"task_choose_{task_key}")])
    
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="gram")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ============ ПОДПИСКА НА КАНАЛЫ ============

async def subscribe_to_channel(client: TelegramClient, link: str) -> bool:
    """Подписка на канал по ссылке"""
    try:
        logging.info(f"📢 Подписываюсь: {link}")
        
        # Очищаем ссылку
        if "?" in link:
            link = link.split("?")[0]
        
        if "t.me/" in link:
            if "+" in link:
                # Приватный канал с invite link
                invite_hash = link.split("+")[-1]
                if "/" in invite_hash:
                    invite_hash = invite_hash.split("/")[0]
                await client(ImportChatInviteRequest(invite_hash))
            else:
                # Публичный канал
                username = link.split("t.me/")[-1].split("/")[0]
                if username:
                    entity = await client.get_entity(f"@{username}")
                    await client(JoinChannelRequest(entity))
        else:
            entity = await client.get_entity(f"@{link}")
            await client(JoinChannelRequest(entity))
        
        logging.info(f"✅ Подписался: {link}")
        return True
        
    except errors.FloodWaitError as e:
        logging.error(f"⏳ Flood wait: {e.seconds} сек")
        return False
    except errors.AlreadyInChannelError:
        logging.info(f"✅ Уже подписан: {link}")
        return True
    except Exception as e:
        logging.error(f"❌ Ошибка подписки {link}: {e}")
        return False


async def find_and_subscribe_from_buttons(client: TelegramClient, msg) -> int:
    """
    Поиск ссылок на каналы в кнопках и подписка
    """
    try:
        subscribed = 0
        
        if not msg or not msg.buttons:
            return 0
        
        for row in msg.buttons:
            for btn in row:
                btn_data = get_button_data(btn)
                btn_text = btn_data['text']
                url = btn_data.get('url')
                
                if not url:
                    continue
                
                if "t.me/" not in url:
                    continue
                
                if "captcha" in url.lower() or "webapp" in url.lower():
                    continue
                
                # Проверяем, что это кнопка подписки
                is_subscribe = False
                
                # Ключевые слова подписки
                subscribe_keywords = ["подписат", "вступит", "присоединит", "join", "subscribe", "➕", "💰", "📢"]
                
                for kw in subscribe_keywords:
                    if kw.lower() in btn_text.lower():
                        is_subscribe = True
                        break
                
                # Если есть сумма с знаком $ или +, тоже подписываемся
                if re.search(r'[\+\$]\s*\d+', btn_text):
                    is_subscribe = True
                
                if is_subscribe:
                    logging.info(f"🔗 Кнопка подписки: '{btn_text}'")
                    success = await subscribe_to_channel(client, url)
                    if success:
                        subscribed += 1
                        await asyncio.sleep(1.5)
        
        return subscribed
        
    except Exception as e:
        logging.error(f"❌ Ошибка поиска кнопок: {e}")
        return 0


async def find_and_subscribe_from_text(client: TelegramClient, msg) -> int:
    """
    Поиск ссылок в тексте (запасной вариант)
    """
    try:
        subscribed = 0
        text = msg.raw_text or ""
        
        channel_links = re.findall(r't\.me/([a-zA-Z0-9_]+|joinchat/\S+)', text)
        channel_links = list(set(channel_links))
        
        if not channel_links:
            return 0
        
        for link in channel_links:
            full_link = f"t.me/{link}"
            success = await subscribe_to_channel(client, full_link)
            if success:
                subscribed += 1
            await asyncio.sleep(2)
        
        return subscribed
        
    except Exception as e:
        logging.error(f"❌ Ошибка поиска в тексте: {e}")
        return 0


async def find_and_subscribe_channels(client: TelegramClient, msg) -> int:
    """
    Комбинированный поиск (сначала кнопки, потом текст)
    """
    total = 0
    
    # Сначала кнопки
    total += await find_and_subscribe_from_buttons(client, msg)
    
    # Если не нашли в кнопках - ищем в тексте
    if total == 0:
        total += await find_and_subscribe_from_text(client, msg)
    
    return total


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
    task = asyncio.create_task(run_gram_worker(client, bot_username))
    active_tasks[phone] = task
    logging.info(f"✅ Воркер запущен для {phone} с ботом {bot_username}")
    return task


async def stop_gram_bot(phone: Optional[str] = None) -> bool:
    if phone and phone in active_tasks:
        active_tasks[phone].cancel()
        del active_tasks[phone]
        if phone in bot_username_for_task:
            del bot_username_for_task[phone]
        logging.info(f"⏹ Остановлен: {phone}")
        return True
    elif active_tasks:
        for phone, task in list(active_tasks.items()):
            task.cancel()
        active_tasks.clear()
        bot_username_for_task.clear()
        logging.info("⏹ Все Gram боты остановлены")
        return True
    return False


async def continue_gram_bot(phone: str) -> bool:
    if phone in active_clients and phone in bot_username_for_task:
        client = active_clients[phone]
        bot_username = bot_username_for_task[phone]
        
        task = asyncio.create_task(run_gram_worker(client, bot_username))
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
        "подтвердите что вы человек",
        "captcha",
        "verify you are human",
        "are you human",
        "проверка на человека",
        "докажите, что вы не робот",
        "пожалуйста, подтвердите",
        "complete the verification",
        "human verification",
        "введите код",
        "enter the code",
        "security check"
    ]
    
    for keyword in captcha_keywords:
        if keyword in text:
            logging.info(f"🔍 Капча: '{keyword}'")
            return True
    
    return False


# ============ ОТПРАВКА КАПЧИ ============

async def send_captcha_to_user(msg, chat_id: int, client: TelegramClient) -> bool:
    if not chat_id or not bot_instance:
        logging.error("❌ Bot instance или chat_id не установлены")
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
                    btn_data = get_button_data(btn)
                    if btn_data['is_webapp'] and btn_data['url']:
                        captcha_url = btn_data['url']
                        break
                if captcha_url:
                    break
        
        if not captcha_url and msg.raw_text:
            url_match = re.search(r'https?://[^\s]+', msg.raw_text)
            if url_match:
                captcha_url = url_match.group()
        
        if captcha_url:
            await bot.send_message(
                chat_id,
                f"🔗 <b>Ссылка на капчу:</b>\n"
                f"<code>{captcha_url}</code>\n\n"
                f"⚠️ <b>Что делать:</b>\n"
                f"1️⃣ Перейди по ссылке\n"
                f"2️⃣ Пройди капчу\n"
                f"3️⃣ Вернись и отправь <b>/continue_gram</b>",
                parse_mode=ParseMode.HTML
            )
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🌐 Открыть капчу", url=captcha_url)],
                [InlineKeyboardButton(text="🔗 Перейти в бота", url=f"https://t.me/{bot_username}")],
                [InlineKeyboardButton(text="🔄 Проверить", callback_data=f"captcha_check_{chat_id}")],
                [InlineKeyboardButton(text="⏹ Остановить", callback_data=f"captcha_stop_{chat_id}")]
            ])
            await bot.send_message(chat_id, "👆 Открыть капчу", reply_markup=keyboard)
        else:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔗 Перейти в бота", url=f"https://t.me/{bot_username}")],
                [InlineKeyboardButton(text="🔄 Проверить", callback_data=f"captcha_check_{chat_id}")],
                [InlineKeyboardButton(text="⏹ Остановить", callback_data=f"captcha_stop_{chat_id}")]
            ])
            await bot.send_message(
                chat_id,
                f"⚠️ <b>Капча!</b>\n🤖 @{bot_username}\nПройдите капчу и нажмите 'Проверить'",
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard
            )
        
        return True
        
    except Exception as e:
        logging.error(f"❌ Ошибка отправки капчи: {e}")
        return False


# ============ CALLBACK ============

@router.callback_query(lambda c: c.data and c.data.startswith("captcha_check_"))
async def captcha_check_callback(callback: types.CallbackQuery):
    try:
        chat_id = int(callback.data.split("_")[2])
        await callback.answer("🔄 Проверяю...")
        
        if chat_id not in captcha_storage:
            await callback.message.edit_text("✅ Капча пройдена!")
            return
        
        captcha_data = captcha_storage[chat_id]
        client = captcha_data['client']
        bot_username = captcha_data['bot_username']
        
        new_msg = await get_last_message(client, bot_username)
        
        if new_msg and not is_captcha_message(new_msg):
            await callback.message.edit_text("✅ <b>Капча пройдена!</b>\n\nПродолжаю...", parse_mode=ParseMode.HTML)
            del captcha_storage[chat_id]
            
            phone = None
            for p, c in active_clients.items():
                if c == client:
                    phone = p
                    break
            if phone:
                await continue_gram_bot(phone)
        else:
            await callback.message.edit_text(
                "⏳ <b>Капча еще активна</b>\n\nПройдите капчу и нажмите 'Проверить'",
                parse_mode=ParseMode.HTML
            )
            
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
        await callback.message.edit_text(
            "⏹ <b>Бот остановлен</b>\n\nОтправьте /continue_gram для продолжения",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logging.error(f"❌ Ошибка остановки: {e}")


@router.callback_query(lambda c: c.data and c.data.startswith("task_choose_"))
async def task_choose_callback(callback: types.CallbackQuery):
    try:
        task_type = callback.data.replace("task_choose_", "")
        user_id = callback.from_user.id
        
        task_names = {
            "posts": "📱 Просмотр постов",
            "channels": "📢 Подписка на каналы",
            "groups": "👥 Вступление в группы",
            "bots": "🤖 Перейти в бота",
            "reactions": "❤️ Поставить реакции",
            "boosts": "🚀 Премиум буст"
        }
        
        if task_type in task_names:
            user_task_choice[user_id] = task_type
            await callback.answer(f"✅ {task_names[task_type]}")
            
            await callback.message.edit_text(
                f"✅ <b>Выбран тип:</b>\n{task_names[task_type]}\n\nЗапусти бота для выполнения.",
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


# ============ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ============

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

def log_message(msg, title="Сообщение"):
    if not msg:
        return
    text = msg.raw_text or ""
    logging.info(f"\n{'='*50}\n📩 {title}\nТекст: {text[:200]}")
    
    if msg.buttons:
        logging.info("📋 Кнопки:")
        for row in msg.buttons:
            for btn in row:
                btn_data = get_button_data(btn)
                btn_text = btn_data['text']
                if btn_data['is_url']:
                    btn_text += f" [URL]"
                logging.info(f"   └─ '{btn_text}'")
    logging.info(f"{'='*50}")

async def click_button(client: TelegramClient, bot_username: str, msg, keywords: list, wait: float = 2):
    if not msg or not msg.buttons:
        return None
    
    for row in msg.buttons:
        for btn in row:
            if is_webapp_button(btn):
                continue
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

async def click_first_post_button(client: TelegramClient, bot_username: str, msg, wait: float = 2):
    if not msg or not msg.buttons:
        return None
    
    skip = ["назад", "◀️", "меню", "back", "заработать", "⬅️", "главн", "подтверд", "продолж"]
    
    for row in msg.buttons:
        for btn in row:
            if is_webapp_button(btn) or is_url_button(btn):
                continue
            btn_text = get_button_text(btn).lower()
            if any(s in btn_text for s in skip):
                continue
            logging.info(f"✅ Нажимаю: '{get_button_text(btn)}'")
            try:
                await btn.click()
                await asyncio.sleep(wait)
                return await get_last_message(client, bot_username)
            except Exception as e:
                logging.error(f"❌ Ошибка: {e}")
                return None
    
    return None

def has_next_post_button(msg) -> bool:
    if not msg or not msg.buttons:
        return False
    for row in msg.buttons:
        for btn in row:
            if is_webapp_button(btn) or is_url_button(btn):
                continue
            btn_text = get_button_text(btn).lower()
            if "следующ" in btn_text or "⏩" in btn_text:
                return True
    return False

async def go_to_earn_menu(client: TelegramClient, bot_username: str):
    logging.info("🔄 Возврат в меню...")
    await send_text(client, bot_username, "◀️ Назад", 1)
    await asyncio.sleep(1)
    await send_text(client, bot_username, "👨‍💻 Заработать", 2)
    return await get_last_message(client, bot_username)


# ============ ОБРАБОТКА ПОСТОВ ============

async def process_single_post(client: TelegramClient, bot_username: str, msg, task_type: str = "posts"):
    if is_captcha_message(msg):
        await send_captcha_to_user(msg, user_chat_id, client)
        return None
    
    if not msg:
        return None
    
    # === АВТО-ПОДПИСКА НА КАНАЛЫ ===
    if AUTO_SUBSCRIBE and task_type in ["channels", "posts"]:
        # 1. СНАЧАЛА ПОДПИСЫВАЕМСЯ
        subscribed = await find_and_subscribe_channels(client, msg)
        if subscribed > 0:
            logging.info(f"✅ Подписался на {subscribed} каналов")
            
            # 2. ПОТОМ НАЖИМАЕМ "ПРОВЕРИТЬ"
            await asyncio.sleep(2)
            await click_button(client, bot_username, msg, ["Проверить", "✅", "Готово"], wait=2)
            
            # 3. ПОЛУЧАЕМ ОБНОВЛЕННОЕ СООБЩЕНИЕ
            msg = await get_last_message(client, bot_username)
            if msg and is_captcha_message(msg):
                await send_captcha_to_user(msg, user_chat_id, client)
                return None
    
    # === ОЖИДАНИЕ ПРОСМОТРА ===
    wait_time = random.randint(8, 15)
    logging.info(f"👀 Читаю пост {wait_time} сек...")
    await asyncio.sleep(wait_time)
    
    # === КНОПКИ ПОДТВЕРЖДЕНИЯ ===
    confirm_keywords = ["✅", "Просмотрел", "Готово", "Выполнено", "Подтвердить", "Проверить", "Получить"]
    
    if task_type == "channels":
        confirm_keywords.extend(["Подписался", "Готово", "✅"])
    elif task_type == "groups":
        confirm_keywords.extend(["Вступил", "Готово", "✅"])
    elif task_type == "bots":
        confirm_keywords.extend(["Перешел", "Запустил", "Готово", "✅"])
    elif task_type == "reactions":
        confirm_keywords.extend(["Поставил", "Готово", "✅"])
    
    updated = await click_button(client, bot_username, msg, confirm_keywords, wait=2)
    
    if updated:
        if is_captcha_message(updated):
            await send_captcha_to_user(updated, user_chat_id, client)
            return None
        return updated
    else:
        logging.info("⚠️ Кнопка не найдена, отправляю ✅")
        await send_text(client, bot_username, "✅", 2)
        updated = await get_last_message(client, bot_username)
        
        if updated and is_captcha_message(updated):
            await send_captcha_to_user(updated, user_chat_id, client)
            return None
        
        return updated


async def view_all_posts(client: TelegramClient, bot_username: str, msg, task_type: str = "posts"):
    posts_viewed = 0
    current_msg = msg
    
    while True:
        posts_viewed += 1
        logging.info(f"\n📄 --- Пост #{posts_viewed} ---")
        
        current_msg = await process_single_post(client, bot_username, current_msg, task_type)
        
        if current_msg is None:
            logging.warning("⏹ Остановка из-за капчи")
            break
        
        if not current_msg:
            logging.info("⚠️ Не удалось получить сообщение")
            break
        
        if is_captcha_message(current_msg):
            await send_captcha_to_user(current_msg, user_chat_id, client)
            break
        
        if has_next_post_button(current_msg):
            logging.info("⏩ Найдена кнопка 'Следующий пост'")
            next_msg = await click_button(
                client, bot_username, current_msg,
                ["следующ", "⏩"], wait=2
            )
            
            if next_msg and is_captcha_message(next_msg):
                await send_captcha_to_user(next_msg, user_chat_id, client)
                break
            
            if next_msg:
                current_msg = next_msg
                continue
            else:
                logging.info("⚠️ Не удалось кликнуть 'Следующий пост'")
                break
        else:
            logging.info("🏁 Посты закончились")
            break
    
    logging.info(f"\n✅ Всего просмотрено: {posts_viewed}")


# ============ ОСНОВНОЙ ЦИКЛ ============

async def do_one_cycle(client: TelegramClient, bot_username: str, user_id: int = None):
    task_type = "posts"
    if user_id and user_id in user_task_choice:
        task_type = user_task_choice[user_id]
    
    logging.info(f"📋 Тип: {task_type}")
    
    msg = await get_last_message(client, bot_username)
    
    if msg and is_captcha_message(msg):
        await send_captcha_to_user(msg, user_chat_id, client)
        return
    
    task_buttons = {
        "posts": ["Просмотр постов", "Посты", "👁", "Заданий на просмотр"],
        "channels": ["Подписаться на канал", "Каналы", "📢", "Заданий на каналы"],
        "groups": ["Вступить в группу", "Группы", "👥", "Заданий на группы"],
        "bots": ["Перейти в бота", "Боты", "🤖", "Заданий на боты"],
        "reactions": ["Поставить реакции", "Реакции", "❤️", "Заданий на реакции"],
        "boosts": ["Премиум буст", "Буст", "🚀", "Заданий на бусты"]
    }
    
    task_buttons_list = task_buttons.get(task_type, task_buttons["posts"])
    
    updated = await click_button(client, bot_username, msg, task_buttons_list, wait=2)
    
    if updated and is_captcha_message(updated):
        await send_captcha_to_user(updated, user_chat_id, client)
        return
    
    if not updated:
        msg = await go_to_earn_menu(client, bot_username)
        
        if msg and is_captcha_message(msg):
            await send_captcha_to_user(msg, user_chat_id, client)
            return
        
        updated = await click_button(client, bot_username, msg, task_buttons_list, wait=2)
        
        if not updated:
            logging.info(f"❌ Кнопка '{task_buttons_list[0]}' не найдена")
            return
    
    if has_next_post_button(updated) or re.search(r"(https?://)?t\.me/\S+", updated.raw_text or ""):
        logging.info("📄 Бот сразу прислал пост")
        await view_all_posts(client, bot_username, updated, task_type)
    else:
        post_msg = await click_first_post_button(client, bot_username, updated, wait=2)
        
        if post_msg and is_captcha_message(post_msg):
            await send_captcha_to_user(post_msg, user_chat_id, client)
            return
        
        if not post_msg:
            logging.info("⚠️ Постов нет")
            await go_to_earn_menu(client, bot_username)
            return
        await view_all_posts(client, bot_username, post_msg, task_type)
    
    await go_to_earn_menu(client, bot_username)


# ============ ВОРКЕР ============

async def run_gram_worker(client: TelegramClient, bot_username: str):
    try:
        logging.info(f"🚀 Запуск {bot_username}...")
        
        await send_text(client, bot_username, "/start", 3)
        await send_text(client, bot_username, "👨‍💻 Заработать", 2)
        
        cycle_count = 0
        
        while True:
            cycle_count += 1
            logging.info(f"\n\n{'#'*60}")
            logging.info(f"🔁 ЦИКЛ #{cycle_count}")
            logging.info(f"{'#'*60}")
            
            try:
                await do_one_cycle(client, bot_username, user_chat_id)
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
