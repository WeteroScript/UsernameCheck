"""
Модуль для Gram ботов
С авто-прохождением капчи через Playwright
"""

import asyncio
import re
import random
import logging
import os
import io
from typing import Optional, Dict, Any
from telethon import TelegramClient, errors
from telethon.tl.functions.channels import LeaveChannelRequest
from telethon.tl.types import InputChannel, KeyboardButtonWebView
from telethon.tl.functions.messages import RequestWebViewRequest
from aiogram import Router, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.enums import ParseMode

# Импорт солвера капчи
from captcha_solver import solve_webapp_captcha

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

# Настройки авто-прохождения
AUTO_SOLVE_CAPTCHA = True
MAX_CAPTCHA_ATTEMPTS = 3
CAPTCHA_HEADLESS = False  # False - показывать браузер, True - скрытый


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


# ============ АВТО-ПРОХОЖДЕНИЕ КАПЧИ ============

async def solve_captcha_auto(client: TelegramClient, bot_username: str, msg) -> bool:
    """
    Автоматическое прохождение капчи через кнопки или WebApp
    """
    try:
        logging.info("🔄 Пытаюсь автоматически пройти капчу...")
        
        await asyncio.sleep(2)
        
        if not msg.buttons:
            return False
        
        # Ищем WebApp кнопку
        webapp_btn = None
        for row in msg.buttons:
            for btn in row:
                if is_webapp_button(btn):
                    webapp_btn = btn
                    logging.info(f"🔗 Найдена WebApp кнопка: {get_button_text(btn)}")
                    break
            if webapp_btn:
                break
        
        # Пробуем WebApp через Playwright
        if webapp_btn:
            url = get_webapp_url(webapp_btn)
            if url:
                try:
                    logging.info(f"🌐 Пытаюсь решить капчу через WebApp: {url}")
                    
                    # Решаем капчу через Playwright
                    solved = await solve_webapp_captcha(url, headless=CAPTCHA_HEADLESS)
                    
                    if solved:
                        logging.info("✅ Капча решена через WebApp!")
                        await asyncio.sleep(3)
                        new_msg = await get_last_message(client, bot_username)
                        if new_msg and not is_captcha_message(new_msg):
                            return True
                    else:
                        logging.warning("❌ Не удалось решить капчу через WebApp")
                        
                except Exception as e:
                    logging.error(f"❌ Ошибка WebApp солвера: {e}")
                    # Отправляем ошибку пользователю
                    if bot_instance and user_chat_id:
                        await bot_instance.send_message(
                            user_chat_id,
                            f"❌ Ошибка при решении капчи через WebApp:\n<code>{str(e)[:200]}</code>",
                            parse_mode=ParseMode.HTML
                        )
        
        # Пробуем обычные кнопки
        for row in msg.buttons:
            for btn in row:
                if is_webapp_button(btn):
                    continue
                btn_text = get_button_text(btn).lower()
                
                if "подтверд" in btn_text or "confirm" in btn_text:
                    logging.info(f"🔄 Нажимаю: '{get_button_text(btn)}'")
                    await btn.click()
                    await asyncio.sleep(3)
                    new_msg = await get_last_message(client, bot_username)
                    if new_msg and not is_captcha_message(new_msg):
                        logging.info("✅ Капча пройдена через кнопку!")
                        return True
                
                elif "продолж" in btn_text or "continue" in btn_text:
                    logging.info(f"🔄 Нажимаю: '{get_button_text(btn)}'")
                    await btn.click()
                    await asyncio.sleep(3)
                    new_msg = await get_last_message(client, bot_username)
                    if new_msg and not is_captcha_message(new_msg):
                        logging.info("✅ Капча пройдена через кнопку!")
                        return True
        
        # Отправляем "✅" как запасной вариант
        logging.info("🔄 Отправляю '✅'...")
        await client.send_message(bot_username, "✅")
        await asyncio.sleep(3)
        
        new_msg = await get_last_message(client, bot_username)
        if new_msg and not is_captcha_message(new_msg):
            logging.info("✅ Капча пройдена через '✅'!")
            return True
        
        return False
        
    except Exception as e:
        logging.error(f"❌ Ошибка авто-прохождения: {e}")
        return False


async def auto_solve_captcha(client: TelegramClient, bot_username: str, msg, attempt: int = 0) -> bool:
    """Комбинированный метод с повторами"""
    if attempt >= MAX_CAPTCHA_ATTEMPTS:
        logging.warning(f"❌ Превышено количество попыток ({MAX_CAPTCHA_ATTEMPTS})")
        return False
    
    logging.info(f"🔄 Попытка #{attempt + 1}...")
    
    solved = await solve_captcha_auto(client, bot_username, msg)
    if solved:
        return True
    
    await asyncio.sleep(3)
    new_msg = await get_last_message(client, bot_username)
    
    if new_msg and not is_captcha_message(new_msg):
        return True
    
    if new_msg and is_captcha_message(new_msg):
        return await auto_solve_captcha(client, bot_username, new_msg, attempt + 1)
    
    return False


# ============ ОТПРАВКА КАПЧИ ПОЛЬЗОВАТЕЛЮ ============

async def send_captcha_to_user(msg, chat_id: int, client: TelegramClient) -> bool:
    """Отправка капчи пользователю с интерактивными кнопками"""
    if not chat_id or not bot_instance:
        logging.error("❌ Bot instance или chat_id не установлены")
        return False
    
    try:
        bot = bot_instance
        
        # Получаем bot_username правильно
        try:
            if msg.chat:
                bot_username = msg.chat.username or "gram_prbot"
            else:
                bot_entity = await client.get_entity(msg.peer_id)
                bot_username = bot_entity.username or "gram_prbot"
        except:
            bot_username = "gram_prbot"
        
        # Сохраняем капчу в хранилище
        captcha_storage[chat_id] = {
            'client': client,
            'bot_username': bot_username,
            'msg_id': msg.id,
            'chat_id_original': msg.chat_id
        }
        
        # Отправляем информацию о капче
        text = f"🚨 <b>Обнаружена капча!</b>\n\n"
        text += f"🤖 <b>Бот:</b> @{bot_username}\n\n"
        
        if msg.raw_text:
            text += f"📝 <b>Текст:</b>\n"
            text += f"<code>{msg.raw_text[:500]}</code>\n\n"
        
        # Создаем кнопки для прохождения капчи
        buttons = []
        
        if msg.buttons:
            for row in msg.buttons:
                btn_row = []
                for btn in row:
                    btn_data = get_button_data(btn)
                    btn_text = btn_data['text']
                    if btn_data['is_webapp']:
                        btn_text += " 🌐"
                    btn_row.append(
                        InlineKeyboardButton(
                            text=f"▶️ {btn_text}",
                            callback_data=f"captcha_click_{chat_id}_{btn_text}"
                        )
                    )
                if btn_row:
                    buttons.append(btn_row.copy())
        
        # Кнопка для ручного перехода в бота
        buttons.append([
            InlineKeyboardButton(
                text=f"🔗 Перейти в @{bot_username}",
                url=f"https://t.me/{bot_username}"
            )
        ])
        
        # Кнопка "Проверить статус"
        buttons.append([
            InlineKeyboardButton(
                text="🔄 Проверить статус",
                callback_data=f"captcha_check_{chat_id}"
            )
        ])
        
        # Кнопка "Остановить"
        buttons.append([
            InlineKeyboardButton(
                text="⏹ Остановить бота",
                callback_data=f"captcha_stop_{chat_id}"
            )
        ])
        
        # Отправляем сообщение с кнопками
        await bot.send_message(
            chat_id,
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        )
        
        # Отправляем медиа если есть
        if msg.photo:
            try:
                buffer = io.BytesIO()
                await client.download_media(msg, file=buffer)
                buffer.seek(0)
                await bot.send_photo(
                    chat_id,
                    BufferedInputFile(buffer.read(), filename="captcha.jpg"),
                    caption="📸 Изображение с капчей"
                )
            except Exception as e:
                logging.error(f"❌ Ошибка отправки фото: {e}")
        
        elif msg.document:
            try:
                buffer = io.BytesIO()
                await client.download_media(msg, file=buffer)
                buffer.seek(0)
                await bot.send_document(
                    chat_id,
                    BufferedInputFile(buffer.read(), filename=msg.document.attributes[0].file_name or "captcha.pdf"),
                    caption="📄 Документ с капчей"
                )
            except Exception as e:
                logging.error(f"❌ Ошибка отправки документа: {e}")
        
        logging.info(f"✅ Капча отправлена пользователю {chat_id}")
        return True
        
    except Exception as e:
        logging.error(f"❌ Ошибка отправки капчи: {e}")
        return False


# ============ CALLBACK ДЛЯ КНОПОК КАПЧИ ============

@router.callback_query(lambda c: c.data and c.data.startswith("captcha_click_"))
async def captcha_click_callback(callback: types.CallbackQuery):
    """Обработчик нажатия на кнопку капчи"""
    try:
        parts = callback.data.split("_")
        chat_id = int(parts[2])
        btn_text = "_".join(parts[3:])
        
        # Проверяем наличие капчи
        if chat_id not in captcha_storage:
            await callback.answer("❌ Капча уже не актуальна")
            await callback.message.delete()
            return
        
        captcha_data = captcha_storage[chat_id]
        client = captcha_data['client']
        bot_username = captcha_data['bot_username']
        
        await callback.answer(f"🔄 Выполняю '{btn_text}'...")
        
        # Получаем оригинальное сообщение
        msg = await client.get_messages(bot_username, ids=captcha_data['msg_id'])
        
        if not msg or not msg.buttons:
            await callback.message.edit_text("❌ Сообщение с капчей не найдено")
            return
        
        # Ищем и нажимаем нужную кнопку
        clicked = False
        for row in msg.buttons:
            for btn in row:
                btn_data = get_button_data(btn)
                if btn_data['text'].lower() in btn_text.lower() or btn_text.lower() in btn_data['text'].lower():
                    try:
                        if btn_data['is_webapp']:
                            # WebApp кнопка - пробуем решить через Playwright
                            url = btn_data['url']
                            if url:
                                await callback.message.answer(
                                    f"🌐 <b>Решаю WebApp капчу...</b>\n\n"
                                    f"URL: <code>{url}</code>\n\n"
                                    f"⏳ Это может занять 10-30 секунд",
                                    parse_mode=ParseMode.HTML
                                )
                                
                                # Решаем капчу
                                solved = await solve_webapp_captcha(url, headless=CAPTCHA_HEADLESS)
                                
                                if solved:
                                    await callback.message.answer("✅ Капча успешно решена через WebApp!")
                                    
                                    # Проверяем результат
                                    await asyncio.sleep(2)
                                    new_msg = await get_last_message(client, bot_username)
                                    if new_msg and not is_captcha_message(new_msg):
                                        await callback.message.answer("✅ Капча пройдена! Продолжаю работу...")
                                        del captcha_storage[chat_id]
                                        
                                        # Перезапускаем бота
                                        phone = None
                                        for p, c in active_clients.items():
                                            if c == client:
                                                phone = p
                                                break
                                        if phone:
                                            await continue_gram_bot(phone)
                                        return
                                else:
                                    await callback.message.answer("❌ Не удалось решить капчу через WebApp")
                            else:
                                await callback.message.answer("❌ URL WebApp не найден")
                        else:
                            # Обычная кнопка
                            await btn.click()
                            clicked = True
                            await callback.message.edit_text(f"✅ Кнопка '{btn_data['text']}' нажата!")
                            
                            # Проверяем результат
                            await asyncio.sleep(2)
                            new_msg = await get_last_message(client, bot_username)
                            if new_msg and not is_captcha_message(new_msg):
                                await callback.message.answer("✅ Капча пройдена! Продолжаю работу...")
                                del captcha_storage[chat_id]
                                
                                phone = None
                                for p, c in active_clients.items():
                                    if c == client:
                                        phone = p
                                        break
                                if phone:
                                    await continue_gram_bot(phone)
                                return
                            else:
                                await callback.message.answer("⏳ Капча еще активна. Попробуйте другие кнопки.")
                    except Exception as e:
                        await callback.message.answer(f"❌ Ошибка: {e}")
                    break
            if clicked:
                break
        
        if not clicked:
            await callback.message.answer("❌ Кнопка не найдена")
            
    except Exception as e:
        logging.error(f"❌ Ошибка callback: {e}")
        await callback.answer(f"❌ Ошибка: {e}")


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
                "Попробуйте нажать кнопки ниже или перейдите в бота вручную.",
                parse_mode=ParseMode.HTML
            )
            
    except Exception as e:
        logging.error(f"❌ Ошибка проверки: {e}")
        await callback.answer(f"❌ Ошибка: {e}")


@router.callback_query(lambda c: c.data and c.data.startswith("captcha_stop_"))
async def captcha_stop_callback(callback: types.CallbackQuery):
    """Остановка бота из-за капчи"""
    try:
        parts = callback.data.split("_")
        chat_id = int(parts[2])
        
        if chat_id in captcha_storage:
            del captcha_storage[chat_id]
        
        await callback.answer("⏹ Бот остановлен")
        await callback.message.edit_text(
            "⏹ <b>Бот остановлен из-за капчи</b>\n\n"
            "Пройдите капчу вручную в боте, затем отправьте /continue_gram",
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
    """Обработка одного поста с проверкой капчи"""
    if is_captcha_message(msg):
        logging.warning("🚨 Обнаружена капча!")
        
        if AUTO_SOLVE_CAPTCHA:
            logging.info("🔄 Пытаюсь автоматически пройти капчу...")
            solved = await auto_solve_captcha(client, bot_username, msg)
            
            if solved:
                logging.info("✅ Капча автоматически пройдена!")
                new_msg = await get_last_message(client, bot_username)
                if new_msg and not is_captcha_message(new_msg):
                    return new_msg
                await asyncio.sleep(2)
                return await get_last_message(client, bot_username)
            else:
                logging.warning("❌ Авто-прохождение не удалось!")
                await send_captcha_to_user(msg, user_chat_id, client)
                return None
        else:
            await send_captcha_to_user(msg, user_chat_id, client)
            return None
    
    log_message(msg, "Пост / Задание")
    
    if not msg:
        return None
    
    text = msg.raw_text or ""
    
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
            if AUTO_SOLVE_CAPTCHA:
                solved = await auto_solve_captcha(client, bot_username, updated)
                if solved:
                    logging.info("✅ Капча автоматически пройдена!")
                    return await get_last_message(client, bot_username)
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
            if AUTO_SOLVE_CAPTCHA:
                solved = await auto_solve_captcha(client, bot_username, updated)
                if solved:
                    logging.info("✅ Капча автоматически пройдена!")
                    return await get_last_message(client, bot_username)
            await send_captcha_to_user(updated, user_chat_id, client)
            return None
        
        log_message(updated, "После текстового подтверждения")
        return updated


async def view_all_posts(client: TelegramClient, bot_username: str, msg):
    """Просмотр всех постов с проверкой капчи"""
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
            if AUTO_SOLVE_CAPTCHA:
                solved = await auto_solve_captcha(client, bot_username, current_msg)
                if solved:
                    logging.info("✅ Капча автоматически пройдена!")
                    current_msg = await get_last_message(client, bot_username)
                    continue
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
                if AUTO_SOLVE_CAPTCHA:
                    solved = await auto_solve_captcha(client, bot_username, next_msg)
                    if solved:
                        logging.info("✅ Капча автоматически пройдена!")
                        next_msg = await get_last_message(client, bot_username)
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
            log_message(current_msg, "Финальное сообщение")
            break
    
    logging.info(f"\n✅ Всего просмотрено постов: {posts_viewed}")


# ============ ОСНОВНОЙ ЦИКЛ ============

async def do_one_cycle(client: TelegramClient, bot_username: str):
    """Один полный цикл"""
    msg = await get_last_message(client, bot_username)
    
    if msg and is_captcha_message(msg):
        logging.warning("🚨 Капча в текущем меню!")
        if AUTO_SOLVE_CAPTCHA:
            solved = await auto_solve_captcha(client, bot_username, msg)
            if solved:
                logging.info("✅ Капча автоматически пройдена!")
                msg = await get_last_message(client, bot_username)
                if not is_captcha_message(msg):
                    logging.info("✅ Продолжаю работу...")
                else:
                    await send_captcha_to_user(msg, user_chat_id, client)
                    return
            else:
                await send_captcha_to_user(msg, user_chat_id, client)
                return
        else:
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
        if AUTO_SOLVE_CAPTCHA:
            solved = await auto_solve_captcha(client, bot_username, updated)
            if solved:
                logging.info("✅ Капча автоматически пройдена!")
                updated = await get_last_message(client, bot_username)
                if not is_captcha_message(updated):
                    logging.info("✅ Продолжаю работу...")
                else:
                    await send_captcha_to_user(updated, user_chat_id, client)
                    return
            else:
                await send_captcha_to_user(updated, user_chat_id, client)
                return
        else:
            await send_captcha_to_user(updated, user_chat_id, client)
            return
    
    if not updated:
        logging.info("⚠️ Не нашёл кнопку 'Просмотр постов'")
        msg = await go_to_earn_menu(client, bot_username)
        
        if msg and is_captcha_message(msg):
            logging.warning("🚨 Капча в меню!")
            if AUTO_SOLVE_CAPTCHA:
                solved = await auto_solve_captcha(client, bot_username, msg)
                if solved:
                    logging.info("✅ Капча автоматически пройдена!")
                    msg = await get_last_message(client, bot_username)
                else:
                    await send_captcha_to_user(msg, user_chat_id, client)
                    return
            else:
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
            if AUTO_SOLVE_CAPTCHA:
                solved = await auto_solve_captcha(client, bot_username, post_msg)
                if solved:
                    logging.info("✅ Капча автоматически пройдена!")
                    post_msg = await get_last_message(client, bot_username)
                else:
                    await send_captcha_to_user(post_msg, user_chat_id, client)
                    return
            else:
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
        logging.info(f"🤖 Авто-прохождение: {'ВКЛ' if AUTO_SOLVE_CAPTCHA else 'ВЫКЛ'}")
        logging.info(f"🌐 Режим браузера: {'Скрытый' if CAPTCHA_HEADLESS else 'Видимый'}")
        
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
