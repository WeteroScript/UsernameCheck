"""
Модуль для Gram ботов
С улучшенной проверкой капчи по кнопкам
"""

import asyncio
import re
import random
import logging
import os
from telethon import TelegramClient, errors
from aiogram import Router, types
from typing import Optional, Dict

router = Router()

# ============ КОНФИГ ============

API_ID = 2040
API_HASH = "b18441a1ff607e10a989891a5462e627"

active_clients: Dict[str, TelegramClient] = {}
active_tasks: Dict[str, asyncio.Task] = {}
gram_bot_initialized = False
user_chat_id: Optional[int] = None
bot_username_for_task: Dict[str, str] = {}  # phone -> bot_username


# ============ УСТАНОВКА USER CHAT ID ============

def set_user_chat_id(chat_id: int):
    global user_chat_id
    user_chat_id = chat_id
    logging.info(f"✅ User chat ID установлен: {chat_id}")


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
    """
    Проверка на капчу по тексту и кнопкам
    Капча обычно содержит: "Подтвердите, что вы человек" и кнопки "Подтвердить", "Продолжить"
    """
    if not msg:
        return False
    
    text = (msg.raw_text or "").lower()
    
    # Проверяем текст
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
    
    # Проверяем кнопки
    buttons_text = []
    has_confirm_button = False
    has_continue_button = False
    
    if msg.buttons:
        for row in msg.buttons:
            for btn in row:
                btn_text = (btn.text or "").lower()
                buttons_text.append(btn_text)
                
                if "подтверд" in btn_text or "confirm" in btn_text or "verify" in btn_text:
                    has_confirm_button = True
                    logging.info(f"🔍 Найдена кнопка подтверждения: '{btn.text}'")
                
                if "продолж" in btn_text or "continue" in btn_text or "next" in btn_text:
                    has_continue_button = True
                    logging.info(f"🔍 Найдена кнопка продолжения: '{btn.text}'")
    
    # Если есть текст капчи И (кнопка подтверждения ИЛИ кнопка продолжения)
    if text_has_captcha and (has_confirm_button or has_continue_button):
        logging.info("🚨 Обнаружена капча (текст + кнопки)")
        return True
    
    # Если есть только текст капчи (без кнопок)
    if text_has_captcha:
        logging.info("🚨 Обнаружена капча (только текст)")
        return True
    
    # Если есть только кнопки капчи
    if has_confirm_button and has_continue_button:
        logging.info("🚨 Обнаружена капча (только кнопки)")
        return True
    
    return False


async def send_captcha_to_user(msg, chat_id: int) -> bool:
    """Отправка сообщения с капчей пользователю"""
    if not chat_id:
        logging.error("❌ Chat ID не установлен")
        return False
    
    try:
        from bot import bot
        
        text = f"🚨 <b>Обнаружена капча!</b>\n\n"
        text += f"📝 <b>Текст:</b>\n<code>{msg.raw_text[:500]}</code>\n\n"
        
        if msg.buttons:
            text += f"📋 <b>Кнопки:</b>\n"
            for row in msg.buttons:
                for btn in row:
                    text += f"  • {btn.text}\n"
            text += "\n"
        
        text += f"⚠️ <b>Действие необходимо!</b>\n"
        text += f"1. Перейди в @gram_prbot или @gram_piarbot\n"
        text += f"2. Пройди капчу вручную\n"
        text += f"3. Вернись сюда и отправь /continue_gram"
        
        await bot.send_message(chat_id, text, parse_mode="HTML")
        
        # Пытаемся переслать само сообщение
        try:
            await bot.send_message(chat_id, "📩 <b>Оригинальное сообщение:</b>", parse_mode="HTML")
            await bot.forward_message(chat_id, msg.chat_id, msg.id)
        except:
            pass
        
        logging.info(f"✅ Капча отправлена пользователю {chat_id}")
        return True
        
    except Exception as e:
        logging.error(f"❌ Ошибка отправки капчи: {e}")
        return False


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
                logging.info(f"   └─ '{btn.text}'")
    logging.info(f"{'='*50}")

async def click_button(client: TelegramClient, bot_username: str, msg, keywords: list, wait: float = 2):
    if not msg or not msg.buttons:
        return None
    
    for row in msg.buttons:
        for btn in row:
            btn_text = (btn.text or "").lower()
            for kw in keywords:
                if kw.lower() in btn_text:
                    logging.info(f"✅ Нажимаю: '{btn.text}'")
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
        logging.info("⚠️ Кнопок нет")
        return None
    
    skip = ["назад", "◀️", "меню", "back", "заработать", "⬅️", "главн", "подтверд", "продолж"]
    
    for row in msg.buttons:
        for btn in row:
            btn_text = (btn.text or "").lower()
            if any(s in btn_text for s in skip):
                continue
            logging.info(f"✅ Нажимаю кнопку поста: '{btn.text}'")
            try:
                await btn.click()
                await asyncio.sleep(wait)
                return await get_last_message(client, bot_username)
            except Exception as e:
                logging.error(f"❌ Ошибка клика: {e}")
                return None
    
    logging.info("⚠️ Нет подходящих кнопок постов")
    return None

def has_next_post_button(msg) -> bool:
    if not msg or not msg.buttons:
        return False
    for row in msg.buttons:
        for btn in row:
            btn_text = (btn.text or "").lower()
            if "следующ" in btn_text or "⏩" in btn_text:
                return True
    return False

async def go_to_earn_menu(client: TelegramClient, bot_username: str):
    logging.info("🔄 Возвращаюсь в меню заработка...")
    await send_text(client, bot_username, "◀️ Назад", 1)
    await asyncio.sleep(1)
    await send_text(client, bot_username, "👨‍💻 Заработать", 2)
    return await get_last_message(client, bot_username)


# ============ ОБРАБОТКА ПОСТОВ С КАПЧЕЙ ============

async def process_single_post(client: TelegramClient, bot_username: str, msg):
    # Проверяем капчу ДО обработки
    if is_captcha_message(msg):
        logging.warning("🚨 Обнаружена капча!")
        await send_captcha_to_user(msg, user_chat_id)
        logging.info("⏹ Бот остановлен из-за капчи")
        return None
    
    log_message(msg, "Пост / Задание")
    
    if not msg:
        return None
    
    text = msg.raw_text or ""
    
    link_match = re.search(r"(https?://)?t\.me/\S+", text)
    if link_match:
        logging.info(f"🔗 Ссылка: {link_match.group()}")
    else:
        logging.info("ℹ️ Ссылки в тексте нет")
    
    wait_time = random.randint(8, 15)
    logging.info(f"👀 Читаю пост {wait_time} сек...")
    await asyncio.sleep(wait_time)
    
    confirm_keywords = ["✅", "Просмотрел", "Готово", "Выполнено", "Подтвердить", "Проверить", "Получить"]
    updated = await click_button(client, bot_username, msg, confirm_keywords, wait=2)
    
    if updated:
        # Проверяем капчу ПОСЛЕ подтверждения
        if is_captcha_message(updated):
            logging.warning("🚨 Капча после подтверждения!")
            await send_captcha_to_user(updated, user_chat_id)
            logging.info("⏹ Бот остановлен из-за капчи")
            return None
        log_message(updated, "После подтверждения")
        return updated
    else:
        logging.info("⚠️ Кнопка не найдена, отправляю ✅")
        await send_text(client, bot_username, "✅", 2)
        updated = await get_last_message(client, bot_username)
        
        # Проверяем капчу ПОСЛЕ отправки
        if updated and is_captcha_message(updated):
            logging.warning("🚨 Капча после отправки!")
            await send_captcha_to_user(updated, user_chat_id)
            logging.info("⏹ Бот остановлен из-за капчи")
            return None
        
        log_message(updated, "После текстового подтверждения")
        return updated


async def view_all_posts(client: TelegramClient, bot_username: str, msg):
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
        
        # Проверяем капчу ПЕРЕД следующим постом
        if is_captcha_message(current_msg):
            logging.warning("🚨 Капча перед следующим постом!")
            await send_captcha_to_user(current_msg, user_chat_id)
            logging.info("⏹ Бот остановлен из-за капчи")
            break
        
        if has_next_post_button(current_msg):
            logging.info("⏩ Найдена кнопка 'Следующий пост'")
            next_msg = await click_button(
                client, bot_username, current_msg,
                ["следующ", "⏩"], wait=2
            )
            
            # Проверяем капчу ПОСЛЕ перехода
            if next_msg and is_captcha_message(next_msg):
                logging.warning("🚨 Капча после перехода!")
                await send_captcha_to_user(next_msg, user_chat_id)
                logging.info("⏹ Бот остановлен из-за капчи")
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
    msg = await get_last_message(client, bot_username)
    
    # Проверяем капчу в текущем меню
    if msg and is_captcha_message(msg):
        logging.warning("🚨 Капча в текущем меню!")
        await send_captcha_to_user(msg, user_chat_id)
        logging.info("⏹ Бот остановлен из-за капчи")
        return
    
    log_message(msg, "Текущее меню")
    
    updated = await click_button(
        client, bot_username, msg,
        ["Просмотр постов", "Посты", "👁", "Заданий на просмотр"],
        wait=2
    )
    
    # Проверяем капчу ПОСЛЕ клика
    if updated and is_captcha_message(updated):
        logging.warning("🚨 Капча после клика!")
        await send_captcha_to_user(updated, user_chat_id)
        logging.info("⏹ Бот остановлен из-за капчи")
        return
    
    if not updated:
        logging.info("⚠️ Не нашёл кнопку 'Просмотр постов'")
        msg = await go_to_earn_menu(client, bot_username)
        
        # Проверяем капчу в меню
        if msg and is_captcha_message(msg):
            logging.warning("🚨 Капча в меню!")
            await send_captcha_to_user(msg, user_chat_id)
            logging.info("⏹ Бот остановлен из-за капчи")
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
        
        # Проверяем капчу в посте
        if post_msg and is_captcha_message(post_msg):
            logging.warning("🚨 Капча в посте!")
            await send_captcha_to_user(post_msg, user_chat_id)
            logging.info("⏹ Бот остановлен из-за капчи")
            return
        
        if not post_msg:
            logging.info("⚠️ Постов нет")
            await go_to_earn_menu(client, bot_username)
            return
        await view_all_posts(client, bot_username, post_msg)
    
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
    'active_clients',
    'active_tasks'
            ]
