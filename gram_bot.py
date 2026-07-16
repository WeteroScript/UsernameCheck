"""
Модуль для Gram ботов
С ручным прохождением капчи и авто-подпиской на каналы
"""

import asyncio
import re
import random
import logging
import os
import io
from typing import Optional, Dict, Any
from telethon import TelegramClient, errors
from telethon.tl.functions.channels import JoinChannelRequest, LeaveChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.tl.types import InputChannel, KeyboardButtonWebView
from telethon.tl.functions.messages import RequestWebViewRequest
from aiogram import Router, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.enums import ParseMode

router = Router()

# ============ КОНФИГ ============

API_ID = 2040
API_HASH = "b18441a1ff607e10a989891a5462e627"  # ЗАМЕНИТЬ НА СВОЙ!

active_clients: Dict[str, TelegramClient] = {}
active_tasks: Dict[str, asyncio.Task] = {}
gram_bot_initialized = False
user_chat_id: Optional[int] = None
bot_username_for_task: Dict[str, str] = {}  # phone -> bot_username
bot_instance = None

# Хранилище для капч
captcha_storage: Dict[int, Dict[str, Any]] = {}  # chat_id -> {client, bot_username, msg_id}

# Настройки
AUTO_SOLVE_CAPTCHA = False  # ОТКЛЮЧЕНО
AUTO_SUBSCRIBE = True  # Авто-подписка на каналы


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
    """Проверка, является ли кнопка WebApp"""
    try:
        if hasattr(btn, "url") and btn.url is not None:
            return True
        if hasattr(btn, "button"):
            return hasattr(btn.button, "url") and btn.button.url is not None
        return False
    except:
        return False

def get_webapp_url(btn) -> Optional[str]:
    """Получение URL из WebApp кнопки"""
    try:
        if hasattr(btn, "url") and btn.url:
            return btn.url
        if hasattr(btn, "button") and hasattr(btn.button, "url"):
            return btn.button.url
        return None
    except:
        return None

def get_button_text(btn) -> str:
    """Получение текста кнопки"""
    try:
        if hasattr(btn, 'text'):
            return btn.text
        return str(btn)
    except:
        return "Неизвестно"

def get_button_data(btn) -> dict:
    """Получение полных данных кнопки"""
    return {
        "text": get_button_text(btn),
        "is_webapp": is_webapp_button(btn),
        "url": get_webapp_url(btn) if is_webapp_button(btn) else None
    }


# ============ АВТО-ПОДПИСКА НА КАНАЛЫ ============

async def subscribe_to_channel(client: TelegramClient, bot_username: str, link: str) -> bool:
    """Подписка на канал по ссылке"""
    try:
        logging.info(f"📢 Подписываюсь на канал: {link}")
        
        # Извлекаем username или invite hash
        if "t.me/" in link:
            # Парсим ссылку
            if "+" in link:
                # Приватный канал с invite link
                invite_hash = link.split("+")[-1]
                await client(ImportChatInviteRequest(invite_hash))
            else:
                # Публичный канал
                username = link.split("t.me/")[-1].split("/")[0].split("?")[0]
                if username:
                    entity = await client.get_entity(f"@{username}")
                    await client(JoinChannelRequest(entity))
        else:
            # Прямой username
            entity = await client.get_entity(f"@{link}")
            await client(JoinChannelRequest(entity))
        
        logging.info(f"✅ Подписался на канал: {link}")
        return True
        
    except Exception as e:
        logging.error(f"❌ Ошибка подписки на {link}: {e}")
        return False


async def find_and_subscribe_channels(client: TelegramClient, bot_username: str, msg) -> int:
    """Поиск и подписка на каналы в сообщении"""
    try:
        subscribed = 0
        text = msg.raw_text or ""
        
        # Ищем ссылки на каналы
        channel_links = re.findall(r't\.me/([a-zA-Z0-9_]+|joinchat/\S+)', text)
        channel_links = list(set(channel_links))  # Убираем дубли
        
        if not channel_links:
            logging.info("ℹ️ Ссылок на каналы не найдено")
            return 0
        
        logging.info(f"📢 Найдено {len(channel_links)} ссылок на каналы")
        
        for link in channel_links:
            try:
                full_link = f"t.me/{link}"
                success = await subscribe_to_channel(client, bot_username, full_link)
                if success:
                    subscribed += 1
                await asyncio.sleep(2)  # Задержка между подписками
            except Exception as e:
                logging.error(f"❌ Ошибка подписки: {e}")
                continue
        
        logging.info(f"✅ Подписался на {subscribed} каналов")
        return subscribed
        
    except Exception as e:
        logging.error(f"❌ Ошибка поиска каналов: {e}")
        return 0


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


async def start_gram_worker(client: TelegramClient, bot_username: str, phone: str):
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
    """Проверка на капчу по тексту и кнопкам"""
    if not msg:
        return False
    
    text = (msg.raw_text or "").lower()
    
    captcha_text_keywords = [
        "подтвердите, что вы человек",
        "подтвердите что вы человек",
        "captcha",
        "verify you are human",
        "are you human",
        "проверка на человека",
        "докажите, что вы не робот",
        "пожалуйста, подтвердите",
        "complete the verification",
        "human verification"
    ]
    
    text_has_captcha = False
    for keyword in captcha_text_keywords:
        if keyword in text:
            text_has_captcha = True
            logging.info(f"🔍 Найдено ключевое слово в тексте: '{keyword}'")
            break
    
    has_confirm = False
    has_continue = False
    has_webapp = False
    
    if msg.buttons:
        for row in msg.buttons:
            for btn in row:
                btn_text = get_button_text(btn).lower()
                
                if "подтверд" in btn_text or "confirm" in btn_text:
                    has_confirm = True
                if "продолж" in btn_text or "continue" in btn_text:
                    has_continue = True
                if is_webapp_button(btn):
                    has_webapp = True
    
    if text_has_captcha and (has_confirm or has_continue or has_webapp):
        return True
    if text_has_captcha:
        return True
    if has_confirm and has_continue:
        return True
    if has_webapp:
        return True
    
    return False


# ============ ОТПРАВКА ССЫЛКИ НА КАПЧУ ============

async def send_captcha_to_user(msg, chat_id: int, client: TelegramClient) -> bool:
    """Отправка пользователю ссылки на капчу"""
    if not chat_id or not bot_instance:
        logging.error("❌ Bot instance или chat_id не установлены")
        return False
    
    try:
        bot = bot_instance
        
        # Получаем bot_username
        try:
            if msg.chat:
                bot_username = msg.chat.username or "gram_prbot"
            else:
                bot_entity = await client.get_entity(msg.peer_id)
                bot_username = bot_entity.username or "gram_prbot"
        except:
            bot_username = "gram_prbot"
        
        # Сохраняем капчу
        captcha_storage[chat_id] = {
            'client': client,
            'bot_username': bot_username,
            'msg_id': msg.id
        }
        
        # Текст уведомления
        text = f"🚨 <b>Обнаружена капча!</b>\n\n"
        text += f"🤖 <b>Бот:</b> @{bot_username}\n\n"
        
        if msg.raw_text:
            text += f"📝 <b>Текст:</b>\n"
            text += f"<code>{msg.raw_text[:300]}</code>\n\n"
        
        # Ищем ссылку на капчу
        captcha_url = None
        if msg.buttons:
            for row in msg.buttons:
                for btn in row:
                    btn_data = get_button_data(btn)
                    if btn_data['is_webapp'] and btn_data['url']:
                        captcha_url = btn_data['url']
                        break
                    if btn_data['is_webapp']:
                        captcha_url = get_webapp_url(btn)
                        break
                if captcha_url:
                    break
        
        # Отправляем сообщение
        await bot.send_message(chat_id, text, parse_mode=ParseMode.HTML)
        
        # Если есть ссылка на капчу - отправляем отдельно
        if captcha_url:
            await bot.send_message(
                chat_id,
                f"🔗 <b>Ссылка на капчу:</b>\n"
                f"<code>{captcha_url}</code>\n\n"
                f"⚠️ <b>Что делать:</b>\n"
                f"1️⃣ Перейди по ссылке\n"
                f"2️⃣ Пройди капчу\n"
                f"3️⃣ Вернись сюда и отправь <b>/continue_gram</b>",
                parse_mode=ParseMode.HTML
            )
            
            # Кнопка для перехода
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🌐 Открыть капчу", url=captcha_url)],
                [InlineKeyboardButton(text="🔗 Перейти в бота", url=f"https://t.me/{bot_username}")],
                [InlineKeyboardButton(text="🔄 Проверить", callback_data=f"captcha_check_{chat_id}")],
                [InlineKeyboardButton(text="⏹ Остановить", callback_data=f"captcha_stop_{chat_id}")]
            ])
            await bot.send_message(chat_id, "👆 Нажми на кнопку, чтобы открыть капчу", reply_markup=keyboard)
        else:
            # Если ссылки нет - просто ссылка на бота
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔗 Перейти в бота", url=f"https://t.me/{bot_username}")],
                [InlineKeyboardButton(text="🔄 Проверить", callback_data=f"captcha_check_{chat_id}")],
                [InlineKeyboardButton(text="⏹ Остановить", callback_data=f"captcha_stop_{chat_id}")]
            ])
            await bot.send_message(
                chat_id,
                f"⚠️ Пройди капчу вручную в боте @{bot_username}, затем нажми 'Проверить'",
                reply_markup=keyboard
            )
        
        logging.info(f"✅ Ссылка на капчу отправлена пользователю {chat_id}")
        return True
        
    except Exception as e:
        logging.error(f"❌ Ошибка отправки капчи: {e}")
        return False


# ============ CALLBACK ДЛЯ КНОПОК ============

@router.callback_query(lambda c: c.data and c.data.startswith("captcha_check_"))
async def captcha_check_callback(callback: types.CallbackQuery):
    """Проверка статуса капчи"""
    try:
        parts = callback.data.split("_")
        chat_id = int(parts[2])
        
        await callback.answer("🔄 Проверяю...")
        
        if chat_id not in captcha_storage:
            await callback.message.edit_text("✅ Капча уже пройдена!")
            return
        
        captcha_data = captcha_storage[chat_id]
        client = captcha_data['client']
        bot_username = captcha_data['bot_username']
        
        new_msg = await get_last_message(client, bot_username)
        
        if new_msg and not is_captcha_message(new_msg):
            await callback.message.edit_text("✅ <b>Капча пройдена!</b>\n\nПродолжаю работу...", parse_mode=ParseMode.HTML)
            del captcha_storage[chat_id]
            
            # Перезапускаем бота
            phone = None
            for p, c in active_clients.items():
                if c == client:
                    phone = p
                    break
            if phone:
                await continue_gram_bot(phone)
        else:
            await callback.message.edit_text(
                "⏳ <b>Капча еще активна</b>\n\n"
                "Пройдите капчу по ссылке выше или в боте вручную, затем нажмите 'Проверить'",
                parse_mode=ParseMode.HTML
            )
            
    except Exception as e:
        logging.error(f"❌ Ошибка проверки: {e}")
        await callback.answer(f"❌ Ошибка: {e}")


@router.callback_query(lambda c: c.data and c.data.startswith("captcha_stop_"))
async def captcha_stop_callback(callback: types.CallbackQuery):
    """Остановка бота"""
    try:
        parts = callback.data.split("_")
        chat_id = int(parts[2])
        
        if chat_id in captcha_storage:
            del captcha_storage[chat_id]
        
        await callback.answer("⏹ Бот остановлен")
        await callback.message.edit_text(
            "⏹ <b>Бот остановлен</b>\n\n"
            "Для продолжения отправьте /continue_gram",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logging.error(f"❌ Ошибка остановки: {e}")


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
        logging.error(f"❌ Ошибка получения сообщений: {e}")
        return None

def log_message(msg, title="Сообщение"):
    if not msg:
        logging.info(f"⚠️ {title}: пусто")
        return
    text = msg.raw_text or ""
    logging.info(f"\n{'='*50}")
    logging.info(f"📩 {title}")
    logging.info(f"Текст: {text[:250]}")
    
    if msg.buttons:
        logging.info("📋 Кнопки:")
        for row in msg.buttons:
            for btn in row:
                btn_data = get_button_data(btn)
                btn_text = btn_data['text']
                if btn_data['is_webapp']:
                    btn_text += " (WebApp)"
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
            if is_webapp_button(btn):
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
                logging.error(f"❌ Ошибка клика: {e}")
                return None
    
    return None

def has_next_post_button(msg) -> bool:
    if not msg or not msg.buttons:
        return False
    for row in msg.buttons:
        for btn in row:
            if is_webapp_button(btn):
                continue
            btn_text = get_button_text(btn).lower()
            if "следующ" in btn_text or "⏩" in btn_text:
                return True
    return False

async def go_to_earn_menu(client: TelegramClient, bot_username: str):
    logging.info("🔄 Возвращаюсь в меню заработка...")
    await send_text(client, bot_username, "◀️ Назад", 1)
    await asyncio.sleep(1)
    await send_text(client, bot_username, "👨‍💻 Заработать", 2)
    return await get_last_message(client, bot_username)


# ============ ОБРАБОТКА ПОСТОВ ============

async def process_single_post(client: TelegramClient, bot_username: str, msg):
    """Обработка одного поста"""
    # Проверяем капчу
    if is_captcha_message(msg):
        logging.warning("🚨 Обнаружена капча!")
        await send_captcha_to_user(msg, user_chat_id, client)
        return None
    
    log_message(msg, "Пост / Задание")
    
    if not msg:
        return None
    
    text = msg.raw_text or ""
    
    # Авто-подписка на каналы
    if AUTO_SUBSCRIBE:
        subscribed = await find_and_subscribe_channels(client, bot_username, msg)
        if subscribed > 0:
            logging.info(f"✅ Подписался на {subscribed} каналов")
    
    # Ищем ссылки
    link_match = re.search(r"(https?://)?t\.me/\S+", text)
    if link_match:
        logging.info(f"🔗 Ссылка: {link_match.group()}")
    
    wait_time = random.randint(8, 15)
    logging.info(f"👀 Читаю пост {wait_time} сек...")
    await asyncio.sleep(wait_time)
    
    confirm_keywords = ["✅", "Просмотрел", "Готово", "Выполнено", "Подтвердить", "Проверить", "Получить"]
    updated = await click_button(client, bot_username, msg, confirm_keywords, wait=2)
    
    if updated:
        if is_captcha_message(updated):
            logging.warning("🚨 Капча после подтверждения!")
            await send_captcha_to_user(updated, user_chat_id, client)
            return None
        log_message(updated, "После подтверждения")
        return updated
    else:
        logging.info("⚠️ Кнопка не найдена, отправляю ✅")
        await send_text(client, bot_username, "✅", 2)
        updated = await get_last_message(client, bot_username)
        
        if updated and is_captcha_message(updated):
            logging.warning("🚨 Капча после отправки!")
            await send_captcha_to_user(updated, user_chat_id, client)
            return None
        
        log_message(updated, "После текстового подтверждения")
        return updated


async def view_all_posts(client: TelegramClient, bot_username: str, msg):
    """Просмотр всех постов"""
    posts_viewed = 0
    current_msg = msg
    
    while True:
        posts_viewed += 1
        logging.info(f"\n📄 --- Пост #{posts_viewed} ---")
        
        current_msg = await process_single_post(client, bot_username, current_msg)
        
        if current_msg is None:
            logging.warning("⏹ Остановка из-за капчи")
            break
        
        if not current_msg:
            logging.info("⚠️ Не удалось получить сообщение")
            break
        
        if is_captcha_message(current_msg):
            logging.warning("🚨 Капча перед следующим постом!")
            await send_captcha_to_user(current_msg, user_chat_id, client)
            break
        
        if has_next_post_button(current_msg):
            logging.info("⏩ Найдена кнопка 'Следующий пост'")
            next_msg = await click_button(
                client, bot_username, current_msg,
                ["следующ", "⏩"], wait=2
            )
            
            if next_msg and is_captcha_message(next_msg):
                logging.warning("🚨 Капча после перехода!")
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
            log_message(current_msg, "Финальное сообщение")
            break
    
    logging.info(f"\n✅ Всего просмотрено постов: {posts_viewed}")


# ============ ОСНОВНОЙ ЦИКЛ ============

async def do_one_cycle(client: TelegramClient, bot_username: str):
    """Один полный цикл"""
    msg = await get_last_message(client, bot_username)
    
    if msg and is_captcha_message(msg):
        logging.warning("🚨 Капча в текущем меню!")
        await send_captcha_to_user(msg, user_chat_id, client)
        return
    
    log_message(msg, "Текущее меню")
    
    updated = await click_button(
        client, bot_username, msg,
        ["Просмотр постов", "Посты", "👁", "Заданий на просмотр"],
        wait=2
    )
    
    if updated and is_captcha_message(updated):
        logging.warning("🚨 Капча после клика!")
        await send_captcha_to_user(updated, user_chat_id, client)
        return
    
    if not updated:
        logging.info("⚠️ Не нашёл кнопку 'Просмотр постов'")
        msg = await go_to_earn_menu(client, bot_username)
        
        if msg and is_captcha_message(msg):
            logging.warning("🚨 Капча в меню!")
            await send_captcha_to_user(msg, user_chat_id, client)
            return
        
        updated = await click_button(
            client, bot_username, msg,
            ["Просмотр постов", "Посты", "👁"],
            wait=2
        )
        
        if not updated:
            logging.info("❌ Кнопка не найдена")
            return
    
    log_message(updated, "После клика 'Просмотр постов'")
    
    if has_next_post_button(updated) or re.search(r"(https?://)?t\.me/\S+", updated.raw_text or ""):
        logging.info("📄 Бот сразу прислал пост")
        await view_all_posts(client, bot_username, updated)
    else:
        post_msg = await click_first_post_button(client, bot_username, updated, wait=2)
        
        if post_msg and is_captcha_message(post_msg):
            logging.warning("🚨 Капча в посте!")
            await send_captcha_to_user(post_msg, user_chat_id, client)
            return
        
        if not post_msg:
            logging.info("⚠️ Постов нет")
            await go_to_earn_menu(client, bot_username)
            return
        await view_all_posts(client, bot_username, post_msg)
    
    await go_to_earn_menu(client, bot_username)


# ============ ВОРКЕР ============

async def run_gram_worker(client: TelegramClient, bot_username: str):
    """Основной воркер"""
    try:
        logging.info(f"🚀 Запуск {bot_username}...")
        logging.info(f"📢 Авто-подписка: {'ВКЛ' if AUTO_SUBSCRIBE else 'ВЫКЛ'}")
        logging.info(f"🤖 Авто-капча: {'ВКЛ' if AUTO_SOLVE_CAPTCHA else 'ВЫКЛ'}")
        
        await send_text(client, bot_username, "/start", 3)
        await send_text(client, bot_username, "👨‍💻 Заработать", 2)
        
        cycle_count = 0
        
        while True:
            cycle_count += 1
            logging.info(f"\n\n{'#'*60}")
            logging.info(f"🔁 ЦИКЛ #{cycle_count}")
            logging.info(f"{'#'*60}")
            
            try:
                await do_one_cycle(client, bot_username)
            except Exception as e:
                logging.error(f"❌ Ошибка в цикле: {e}")
                import traceback
                traceback.print_exc()
                await asyncio.sleep(5)
            
            pause = random.randint(5, 10)
            logging.info(f"⏸️ Пауза {pause} сек...")
            await asyncio.sleep(pause)
            
    except asyncio.CancelledError:
        logging.info(f"⏹ {bot_username} остановлен")
    except Exception as e:
        logging.error(f"❌ Критическая ошибка {bot_username}: {e}")
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
    'active_clients',
    'active_tasks'
]
