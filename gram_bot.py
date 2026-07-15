import asyncio
import re
import random
import logging
from telethon import TelegramClient, errors
from aiogram import Router, types
from typing import Optional, Dict

router = Router()

# ============ КОНФИГ ============

API_ID = 2040
API_HASH = "b18441a1ff607e10a989891a5462e627"

active_clients: Dict[str, TelegramClient] = {}  # phone -> client
active_tasks: Dict[str, asyncio.Task] = {}      # phone -> task
gram_bot_initialized = False


# ============ ОТПРАВКА КОДА ============

async def send_code(phone: str, bot_username: str) -> bool:
    """Отправка кода подтверждения"""
    try:
        # Создаем папку для сессий если нет
        os.makedirs("sessions", exist_ok=True)
        
        client = TelegramClient(f"sessions/{phone}", API_ID, API_HASH)
        await client.connect()
        
        if not await client.is_user_authorized():
            await client.send_code_request(phone)
        
        active_clients[phone] = client
        logging.info(f"📱 Код отправлен на {phone} для {bot_username}")
        return True
    except Exception as e:
        logging.error(f"❌ Ошибка отправки кода: {e}")
        return False


# ============ ЗАПУСК GRAM БОТА ============

async def start_gram_bot(phone: str, code: str, bot_username: str) -> bool:
    """Запуск Gram бота"""
    try:
        client = active_clients.get(phone)
        if not client:
            client = TelegramClient(f"sessions/{phone}", API_ID, API_HASH)
            await client.connect()
            active_clients[phone] = client
        
        # Авторизация
        await client.sign_in(phone, code)
        logging.info(f"✅ Авторизован: {phone}")
        
        # Запускаем фоновую задачу
        task = asyncio.create_task(run_gram_worker(client, bot_username))
        active_tasks[phone] = task
        
        return True
    except errors.SessionPasswordNeededError:
        logging.error("❌ Требуется 2FA пароль")
        return False
    except Exception as e:
        logging.error(f"❌ Ошибка авторизации: {e}")
        return False


# ============ ОСТАНОВКА ============

async def stop_gram_bot(phone: Optional[str] = None) -> bool:
    """Остановка Gram бота"""
    if phone and phone in active_tasks:
        active_tasks[phone].cancel()
        del active_tasks[phone]
        if phone in active_clients:
            await active_clients[phone].disconnect()
            del active_clients[phone]
        logging.info(f"⏹ Остановлен: {phone}")
        return True
    elif active_tasks:
        for phone, task in active_tasks.items():
            task.cancel()
            if phone in active_clients:
                await active_clients[phone].disconnect()
        active_tasks.clear()
        active_clients.clear()
        logging.info("⏹ Все Gram боты остановлены")
        return True
    return False


# ============ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ============

async def send_text(client: TelegramClient, bot_username: str, text: str, delay: float = 2):
    """Отправка текста боту"""
    try:
        await client.send_message(bot_username, text)
        logging.info(f"📤 {bot_username}: {text[:50]}")
        await asyncio.sleep(delay)
    except Exception as e:
        logging.error(f"❌ Ошибка отправки: {e}")

async def get_last_message(client: TelegramClient, bot_username: str):
    """Получение последнего сообщения"""
    try:
        msgs = await client.get_messages(bot_username, limit=1)
        return msgs[0] if msgs else None
    except Exception as e:
        logging.error(f"❌ Ошибка получения сообщений: {e}")
        return None

def extract_balance(text: str) -> Optional[str]:
    """Извлечение баланса"""
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

def log_message(msg, title="Сообщение"):
    """Логирование сообщения"""
    if not msg:
        logging.info(f"⚠️ {title}: пусто")
        return
    text = msg.raw_text or ""
    logging.info(f"\n{'='*50}")
    logging.info(f"📩 {title}")
    logging.info(f"Текст: {text[:250]}")
    
    balance = extract_balance(text)
    if balance:
        logging.info(f"💰 БАЛАНС: {balance} GRAM")
    
    if msg.buttons:
        logging.info("📋 Кнопки:")
        for row in msg.buttons:
            for btn in row:
                logging.info(f"   └─ '{btn.text}'")
    logging.info(f"{'='*50}")

async def click_button(client: TelegramClient, bot_username: str, msg, keywords: list, wait: float = 2):
    """Нажать кнопку по ключевым словам"""
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
    """Нажать первую кнопку поста"""
    if not msg or not msg.buttons:
        logging.info("⚠️ Кнопок нет")
        return None
    
    skip = ["назад", "◀️", "меню", "back", "заработать", "⬅️", "главн"]
    
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
    """Проверка кнопки 'Следующий пост'"""
    if not msg or not msg.buttons:
        return False
    for row in msg.buttons:
        for btn in row:
            btn_text = (btn.text or "").lower()
            if "следующ" in btn_text or "⏩" in btn_text:
                return True
    return False

async def go_to_earn_menu(client: TelegramClient, bot_username: str):
    """Возврат в меню заработка"""
    logging.info("🔄 Возвращаюсь в меню заработка...")
    await send_text(client, bot_username, "◀️ Назад", 1)
    await asyncio.sleep(1)
    await send_text(client, bot_username, "👨‍💻 Заработать", 2)
    return await get_last_message(client, bot_username)


# ============ ОБРАБОТКА ПОСТОВ ============

async def process_single_post(client: TelegramClient, bot_username: str, msg):
    """Обработка одного поста"""
    log_message(msg, "Пост / Задание")
    
    if not msg:
        return None
    
    text = msg.raw_text or ""
    
    # Ищем ссылку
    link_match = re.search(r"(https?://)?t\.me/\S+", text)
    if link_match:
        logging.info(f"🔗 Ссылка: {link_match.group()}")
    else:
        logging.info("ℹ️ Ссылки в тексте нет")
    
    # Имитация просмотра
    wait_time = random.randint(8, 15)
    logging.info(f"👀 Читаю пост {wait_time} сек...")
    await asyncio.sleep(wait_time)
    
    # Подтверждение
    confirm_keywords = ["✅", "Просмотрел", "Готово", "Выполнено", "Подтвердить", "Проверить", "Получить"]
    updated = await click_button(client, bot_username, msg, confirm_keywords, wait=2)
    
    if updated:
        log_message(updated, "После подтверждения")
        return updated
    else:
        logging.info("⚠️ Кнопка не найдена, отправляю ✅")
        await send_text(client, bot_username, "✅", 2)
        updated = await get_last_message(client, bot_username)
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
        
        if not current_msg:
            logging.info("⚠️ Не удалось получить сообщение")
            break
        
        if has_next_post_button(current_msg):
            logging.info("⏩ Найдена кнопка 'Следующий пост'")
            next_msg = await click_button(
                client, bot_username, current_msg,
                ["следующ", "⏩"], wait=2
            )
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
    log_message(msg, "Текущее меню")
    
    # Пытаемся найти "Просмотр постов"
    updated = await click_button(
        client, bot_username, msg,
        ["Просмотр постов", "Посты", "👁", "Заданий на просмотр"],
        wait=2
    )
    
    if not updated:
        logging.info("⚠️ Не нашёл кнопку 'Просмотр постов'")
        msg = await go_to_earn_menu(client, bot_username)
        updated = await click_button(
            client, bot_username, msg,
            ["Просмотр постов", "Посты", "👁"],
            wait=2
        )
        if not updated:
            logging.info("❌ Кнопка не найдена")
            return
    
    log_message(updated, "После клика 'Просмотр постов'")
    
    # Проверяем, может бот сразу прислал пост
    if has_next_post_button(updated) or re.search(r"(https?://)?t\.me/\S+", updated.raw_text or ""):
        logging.info("📄 Бот сразу прислал пост")
        await view_all_posts(client, bot_username, updated)
    else:
        # Жмем первый пост
        post_msg = await click_first_post_button(client, bot_username, updated, wait=2)
        if not post_msg:
            logging.info("⚠️ Постов нет")
            await go_to_earn_menu(client, bot_username)
            return
        await view_all_posts(client, bot_username, post_msg)
    
    # Возврат в меню
    await go_to_earn_menu(client, bot_username)


# ============ ВОРКЕР ============

async def run_gram_worker(client: TelegramClient, bot_username: str):
    """Основной воркер"""
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
    'stop_gram_bot',
    'active_clients',
    'active_tasks'
    ]
