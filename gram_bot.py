import asyncio
import re
import random
import logging
from telethon import TelegramClient, errors
from aiogram import Router, types
from typing import Optional

router = Router()

# ============ КОНФИГ ============

API_ID = 2040
API_HASH = "b18441a1ff607e10a989891a5462e627"

active_clients: dict = {}  # phone -> client
active_tasks: dict = {}    # phone -> task
gram_bot_initialized = False


# ============ ЛОГИКА GRAM БОТА ============

async def send_code(phone: str, bot_username: str) -> bool:
    """Отправка кода подтверждения"""
    try:
        client = TelegramClient(f"sessions/{phone}", API_ID, API_HASH)
        await client.connect()
        await client.send_code_request(phone)
        active_clients[phone] = client
        return True
    except Exception as e:
        logging.error(f"Ошибка отправки кода: {e}")
        return False

async def start_gram_bot(phone: str, code: str, bot_username: str) -> bool:
    """Запуск Gram бота"""
    try:
        client = active_clients.get(phone)
        if not client:
            client = TelegramClient(f"sessions/{phone}", API_ID, API_HASH)
            await client.connect()
        
        await client.sign_in(phone, code)
        active_clients[phone] = client
        
        # Запускаем фоновую задачу
        task = asyncio.create_task(run_gram_worker(client, bot_username))
        active_tasks[phone] = task
        
        return True
    except Exception as e:
        logging.error(f"Ошибка авторизации: {e}")
        return False

async def stop_gram_bot(phone: Optional[str] = None) -> bool:
    """Остановка Gram бота"""
    if phone and phone in active_tasks:
        active_tasks[phone].cancel()
        del active_tasks[phone]
        return True
    elif active_tasks:
        for task in active_tasks.values():
            task.cancel()
        active_tasks.clear()
        return True
    return False


# ============ РАБОЧИЙ ПРОЦЕСС ============

async def run_gram_worker(client: TelegramClient, bot_username: str):
    """Основной рабочий процесс"""
    try:
        await client.start()
        
        await send_text(client, bot_username, "/start", 3)
        await send_text(client, bot_username, "👨‍💻 Заработать", 2)
        
        cycle_count = 0
        while True:
            cycle_count += 1
            logging.info(f"🔄 Gram цикл #{cycle_count}")
            
            try:
                await do_one_cycle(client, bot_username)
            except Exception as e:
                logging.error(f"Ошибка в цикле: {e}")
                await asyncio.sleep(5)
            
            await asyncio.sleep(random.randint(5, 10))
            
    except asyncio.CancelledError:
        logging.info("⏹ Gram бот остановлен")
    except Exception as e:
        logging.error(f"Ошибка Gram бота: {e}")
    finally:
        await client.disconnect()

async def send_text(client: TelegramClient, bot_username: str, text: str, delay: float = 2):
    """Отправка текста"""
    await client.send_message(bot_username, text)
    logging.info(f"📤 Отправил: {text}")
    await asyncio.sleep(delay)

async def get_last_message(client: TelegramClient, bot_username: str):
    """Получение последнего сообщения"""
    msgs = await client.get_messages(bot_username, limit=1)
    return msgs[0] if msgs else None

def extract_balance(text: str) -> Optional[str]:
    """Извлечение баланса из текста"""
    if not text:
        return None
    patterns = [
        r"[Бб]аланс[:\s]+([\d\s.,]+)\s*GRAM",
        r"💰[:\s]*([\d\s.,]+)\s*GRAM",
        r"([\d\s.,]+)\s*GRAM",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip().replace(" ", "")
    return None

async def click_button(client: TelegramClient, bot_username: str, msg, keywords: list, wait: float = 2):
    """Нажатие кнопки по ключевым словам"""
    if not msg or not msg.buttons:
        return None
    for row in msg.buttons:
        for btn in row:
            btn_text = (btn.text or "").lower()
            for kw in keywords:
                if kw.lower() in btn_text:
                    logging.info(f"✅ Нажимаю: '{btn.text}'")
                    await btn.click()
                    await asyncio.sleep(wait)
                    return await get_last_message(client, bot_username)
    return None

async def do_one_cycle(client: TelegramClient, bot_username: str):
    """Один цикл работы"""
    msg = await get_last_message(client, bot_username)
    
    # Ищем кнопку "Просмотр постов"
    updated = await click_button(client, bot_username, msg, ["Просмотр постов", "Посты", "👁", "Заданий на просмотр"], wait=2)
    
    if not updated:
        # Возврат в меню
        await send_text(client, bot_username, "◀️ Назад", 1)
        await asyncio.sleep(1)
        await send_text(client, bot_username, "👨‍💻 Заработать", 2)
        msg = await get_last_message(client, bot_username)
        updated = await click_button(client, bot_username, msg, ["Просмотр постов", "Посты", "👁"], wait=2)
        
    if not updated:
        return
    
    # Нажимаем на первый пост
    if updated.buttons:
        for row in updated.buttons:
            for btn in row:
                btn_text = (btn.text or "").lower()
                if not any(s in btn_text for s in ["назад", "◀️", "меню", "back", "⬅️"]):
                    logging.info(f"✅ Нажимаю пост: '{btn.text}'")
                    await btn.click()
                    await asyncio.sleep(2)
                    post_msg = await get_last_message(client, bot_username)
                    
                    # Просматриваем пост
                    if post_msg:
                        wait_time = random.randint(8, 15)
                        logging.info(f"👀 Читаю пост {wait_time} сек...")
                        await asyncio.sleep(wait_time)
                        
                        # Подтверждаем
                        await click_button(client, bot_username, post_msg, ["✅", "Просмотрел", "Готово", "Подтвердить"], wait=2)
                    break
            break
    
    # Возврат в меню
    await send_text(client, bot_username, "◀️ Назад", 1)


# ============ ИНИЦИАЛИЗАЦИЯ ============

def init_gram_bot(dp):
    global gram_bot_initialized
    if not gram_bot_initialized:
        dp.include_router(router)
        gram_bot_initialized = True
        logging.info("✅ Модуль Gram ботов инициализирован")

# Экспорт для главного бота
__all__ = ['router', 'init_gram_bot', 'send_code', 'start_gram_bot', 'stop_gram_bot']
