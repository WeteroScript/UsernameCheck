import logging
import os
import asyncio
import json
import re
import random
import string
from datetime import datetime
from typing import Optional, Dict, List

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from dotenv import load_dotenv

# ============ Telethon для работы с gram_prbot ============
from telethon import TelegramClient

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в .env")

# ============ КОНФИГИ ============
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
if not API_ID or not API_HASH:
    print("⚠️ API_ID и API_HASH не заданы — функции с gram_prbot не будут работать")

# ============ НАСТРОЙКА ============
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# Состояния для ввода номера и кода
class GramStates(StatesGroup):
    waiting_phone = State()
    waiting_code = State()

# Активные сессии
active_viewers: Dict[int, dict] = {}  # user_id -> {"client": client, "bot": bot_name, "task": task}

# ============ БАЗЫ ДАННЫХ ДЛЯ ЮЗЕРНЕЙМОВ ============
TAKEN_DB_FILE = "taken_usernames.json"
FREE_DB_FILE = "free_usernames.json"
BANNED_DB_FILE = "banned_usernames.json"

def load_db(file_path: str) -> Dict:
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_db(file_path: str, data: Dict):
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except:
        pass

def add_to_taken_db(username: str, user_id=None, method="unknown", reason=""):
    db = load_db(TAKEN_DB_FILE)
    if username not in db:
        db[username] = {"checked_at": datetime.now().isoformat(), "checked_by": str(user_id) if user_id else "unknown", "method": method, "reason": reason}
        save_db(TAKEN_DB_FILE, db)
        return True
    return False

def add_to_free_db(username: str, user_id=None, method="unknown"):
    db = load_db(FREE_DB_FILE)
    if username not in db:
        db[username] = {"found_at": datetime.now().isoformat(), "found_by": str(user_id) if user_id else "unknown", "method": method, "verified": True}
        save_db(FREE_DB_FILE, db)
        return True
    return False

def is_in_taken_db(username: str) -> bool:
    return username in load_db(TAKEN_DB_FILE)

def is_in_free_db(username: str) -> bool:
    return username in load_db(FREE_DB_FILE)


# ============ ГЕНЕРАЦИЯ ЮЗЕРНЕЙМОВ ============
def generate_username(settings: Dict) -> str:
    letters = string.ascii_lowercase if settings.get("use_full_alphabet", True) else 'abcdefghijkmnopqrstuvwxyz'
    main_letter = settings.get("letter", "s")
    repeat_count = settings.get("repeat_count", 2)
    
    if main_letter not in letters:
        main_letter = random.choice(letters)
    
    other_letters = [c for c in letters if c != main_letter]
    remaining_count = max(1, min(5 - repeat_count, 5))
    
    chosen_others = random.sample(other_letters, min(remaining_count, len(other_letters)))
    if len(chosen_others) < remaining_count:
        chosen_others += [random.choice(other_letters) for _ in range(remaining_count - len(chosen_others))]
    
    pos = random.randint(0, remaining_count)
    result = chosen_others[:pos] + [main_letter] * repeat_count + chosen_others[pos:]
    return ''.join(result)[:5]


# ============ ПРОВЕРКА ЮЗЕРНЕЙМОВ (Bot API) ============
async def check_username_bot_api(username: str) -> Optional[bool]:
    """True = свободен, False = занят, None = неизвестно"""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChat"
        params = {"chat_id": f"@{username}"}
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, timeout=10) as resp:
                data = await resp.json()
                
                if data.get("ok") is True:
                    return False  # занят
                
                error_desc = data.get("description", "").lower()
                if "chat not found" in error_desc or "username is not occupied" in error_desc:
                    return True  # свободен
                if "username is occupied" in error_desc or "deactivated" in error_desc:
                    return False  # занят или забанен
                
                return None
    except:
        return None

async def check_username_fragment(username: str) -> Optional[bool]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://fragment.com/username/{username}", timeout=10) as resp:
                if resp.status == 404:
                    return True
                if resp.status == 200:
                    html = await resp.text()
                    if any(x in html.lower() for x in ['auction', 'bid', 'price', 'sold']):
                        return False
                    if len(html) < 3000:
                        return True
                return None
    except:
        return None

async def check_username_parallel(username: str, user_id=None) -> bool:
    if is_in_free_db(username):
        return True
    if is_in_taken_db(username):
        return False
    
    results = await asyncio.gather(
        check_username_bot_api(username),
        check_username_fragment(username),
        return_exceptions=True
    )
    
    bot_res = results[0] if not isinstance(results[0], Exception) else None
    frag_res = results[1] if not isinstance(results[1], Exception) else None
    
    # Оптимистичная логика
    if bot_res is True or (bot_res is None and frag_res is True):
        add_to_free_db(username, user_id, "found")
        return True
    
    if bot_res is False:
        add_to_taken_db(username, user_id, "taken")
        return False
    
    # Fallback - свободен
    add_to_free_db(username, user_id, "optimistic")
    return True


# ============ КЛАВИАТУРЫ ============

def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Боты", callback_data="bots_menu")],
        [InlineKeyboardButton(text="👤 Юзернеймы", callback_data="username_menu")],
    ])

def bots_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Pr Gram", callback_data="prgram_menu")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")],
    ])

def prgram_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔹 @gram_piarbot", callback_data="bot_piar")],
        [InlineKeyboardButton(text="🔹 @gram_prbot", callback_data="bot_pr")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="bots_menu")],
    ])

def start_viewer_keyboard(bot_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Авто-Просмотры", callback_data=f"start_viewer_{bot_name}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="prgram_menu")],
    ])

def username_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Найти юзернейм", callback_data="username_find")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="username_stats")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")],
    ])


# ============ /START ============

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer(
        "👋 <b>Telegram Manager</b>\n\n"
        "Выбери действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu()
    )


# ============ НАВИГАЦИЯ ============

@dp.callback_query(lambda c: c.data == "main_menu")
async def back_main(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "👋 <b>Главное меню</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu()
    )

@dp.callback_query(lambda c: c.data == "bots_menu")
async def bots_menu_cmd(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🤖 <b>Выбери бота</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=bots_menu()
    )

@dp.callback_query(lambda c: c.data == "prgram_menu")
async def prgram_menu_cmd(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "📱 <b>Выбери бота Pr Gram</b>\n\n"
        "Нажми на бота, чтобы управлять им:",
        parse_mode=ParseMode.HTML,
        reply_markup=prgram_menu()
    )

@dp.callback_query(lambda c: c.data == "username_menu")
async def username_menu_cmd(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "👤 <b>Управление юзернеймами</b>\n\n"
        "Бот будет искать свободные 5-буквенные юзернеймы.",
        parse_mode=ParseMode.HTML,
        reply_markup=username_menu()
    )


# ============ ВЫБОР БОТА ============

@dp.callback_query(lambda c: c.data in ["bot_piar", "bot_pr"])
async def select_bot(callback: types.CallbackQuery):
    await callback.answer()
    bot_name = "gram_piarbot" if callback.data == "bot_piar" else "gram_prbot"
    display_name = "@" + bot_name
    
    await callback.message.edit_text(
        f"🤖 <b>{display_name}</b>\n\n"
        f"Что хочешь сделать с этим ботом?",
        parse_mode=ParseMode.HTML,
        reply_markup=start_viewer_keyboard(bot_name)
    )


# ============ ЗАПУСК ПРОСМОТРА ============

@dp.callback_query(lambda c: c.data.startswith("start_viewer_"))
async def start_viewer(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    bot_name = callback.data.replace("start_viewer_", "")
    
    await state.update_data(bot_name=bot_name)
    await callback.message.edit_text(
        f"📱 <b>Настройка {bot_name}</b>\n\n"
        f"Введи номер телефона в формате:\n"
        f"<code>+7XXXXXXXXXX</code>\n\n"
        f"Или нажми «Отмена» чтобы выйти.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="prgram_menu")]
        ])
    )
    await state.set_state(GramStates.waiting_phone)


# ============ ВВОД НОМЕРА ============

@dp.message(GramStates.waiting_phone)
async def process_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    
    # Простая валидация
    if not re.match(r'^\+?\d{10,15}$', phone):
        await message.answer(
            "❌ Неверный формат. Введи номер в формате:\n<code>+7XXXXXXXXXX</code>",
            parse_mode=ParseMode.HTML
        )
        return
    
    await state.update_data(phone=phone)
    await message.answer(
        f"📱 Номер принят: <code>{phone}</code>\n\n"
        f"Теперь введи код подтверждения из Telegram:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="prgram_menu")]
        ])
    )
    await state.set_state(GramStates.waiting_code)


# ============ ВВОД КОДА И ЗАПУСК ============

@dp.message(GramStates.waiting_code)
async def process_code(message: types.Message, state: FSMContext):
    code = message.text.strip()
    data = await state.get_data()
    phone = data.get("phone")
    bot_name = data.get("bot_name")
    
    if not code.isdigit() or len(code) < 4:
        await message.answer("❌ Введи корректный код (цифры)")
        return
    
    await message.answer(
        f"🔄 Подключаюсь к <code>{phone}</code>...\n"
        f"Код: <code>{code}</code>\n\n"
        f"⏳ Пожалуйста, подожди...",
        parse_mode=ParseMode.HTML
    )
    
    # Запускаем скрипт в фоне
    asyncio.create_task(run_gram_viewer(message, phone, code, bot_name))
    await state.clear()


# ============ САМ СКРИПТ ПРОСМОТРА ============

async def run_gram_viewer(message: types.Message, phone: str, code: str, bot_name: str):
    user_id = message.from_user.id
    try:
        client = TelegramClient(f"sessions/{user_id}_{bot_name}", API_ID, API_HASH)
        await client.connect()
        
        if not await client.is_user_authorized():
            await client.sign_in(phone=phone, code=code)
        
        await message.answer(f"✅ <b>Успешный вход!</b>\n\n"
                           f"🤖 Бот: @{bot_name}\n"
                           f"📱 Номер: {phone}\n\n"
                           f"▶️ Запускаю авто-просмотры...",
                           parse_mode=ParseMode.HTML)
        
        # Сохраняем сессию
        active_viewers[user_id] = {
            "client": client,
            "bot_name": bot_name,
            "running": True
        }
        
        # Основной цикл просмотра
        await gram_cycle(client, bot_name, message)
        
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")
        logger.error(f"Gram viewer error: {e}")


async def gram_cycle(client: TelegramClient, bot_name: str, message: types.Message):
    """Цикл просмотра постов"""
    try:
        await client.send_message(bot_name, "/start")
        await asyncio.sleep(2)
        await client.send_message(bot_name, "👨‍💻 Заработать")
        await asyncio.sleep(2)
        
        cycle = 0
        while True:
            cycle += 1
            try:
                # Получаем последнее сообщение
                msgs = await client.get_messages(bot_name, limit=1)
                if not msgs:
                    await asyncio.sleep(5)
                    continue
                
                msg = msgs[0]
                
                # Ищем кнопку "Просмотр постов"
                if msg.buttons:
                    for row in msg.buttons:
                        for btn in row:
                            if "просмотр" in (btn.text or "").lower():
                                await btn.click()
                                await asyncio.sleep(2)
                                break
                
                # Просматриваем посты
                for _ in range(10):  # до 10 постов за цикл
                    msgs = await client.get_messages(bot_name, limit=1)
                    if not msgs:
                        break
                    
                    current = msgs[0]
                    
                    # Ищем ссылку
                    text = current.raw_text or ""
                    if "t.me/" in text:
                        # Смотрим пост
                        await asyncio.sleep(random.randint(5, 10))
                        
                        # Жмём "Просмотрел"
                        if current.buttons:
                            for row in current.buttons:
                                for btn in row:
                                    btn_text = (btn.text or "").lower()
                                    if any(k in btn_text for k in ["просмотрел", "готово", "✅"]):
                                        await btn.click()
                                        await asyncio.sleep(2)
                                        break
                    
                    # Ищем кнопку "Следующий пост"
                    if current.buttons:
                        next_btn = None
                        for row in current.buttons:
                            for btn in row:
                                if "следующ" in (btn.text or "").lower():
                                    next_btn = btn
                                    break
                            if next_btn:
                                break
                        
                        if next_btn:
                            await next_btn.click()
                            await asyncio.sleep(2)
                        else:
                            break
                    else:
                        break
                
                # Возврат в меню
                await client.send_message(bot_name, "◀️ Назад")
                await asyncio.sleep(1)
                await client.send_message(bot_name, "👨‍💻 Заработать")
                await asyncio.sleep(2)
                
                # Пауза между циклами
                await asyncio.sleep(random.randint(10, 20))
                
            except Exception as e:
                logger.error(f"Cycle error: {e}")
                await asyncio.sleep(10)
                
    except Exception as e:
        logger.error(f"Gram cycle error: {e}")


# ============ ПОИСК ЮЗЕРНЕЙМОВ ============

@dp.callback_query(lambda c: c.data == "username_find")
async def username_find(callback: types.CallbackQuery):
    await callback.answer()
    
    msg = await callback.message.edit_text(
        "🎯 <b>Поиск свободных юзернеймов</b>\n\n"
        "🔍 Ищу... Это может занять время.\n"
        "Я буду присылать найденные варианты.",
        parse_mode=ParseMode.HTML
    )
    
    # Запускаем поиск
    asyncio.create_task(search_usernames(callback.message, callback.from_user.id))


async def search_usernames(message: types.Message, user_id: int):
    settings = {"letter": random.choice(string.ascii_lowercase), "repeat_count": 2, "use_full_alphabet": True}
    found = 0
    
    for i in range(100):  # 100 попыток
        username = generate_username(settings)
        
        if is_in_taken_db(username) or is_in_free_db(username):
            continue
        
        is_free = await check_username_parallel(username, user_id)
        
        if is_free:
            found += 1
            await message.answer(
                f"🎉 <b>#{found} НАЙДЕН!</b>\n\n"
                f"✅ <code>@{username}</code>\n\n"
                f"🔗 <a href='https://t.me/{username}'>Забрать</a> | "
                f"<a href='https://fragment.com/username/{username}'>Fragment</a>",
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
        
        # Обновляем статус каждые 10 попыток
        if i % 10 == 0:
            await message.edit_text(
                f"🎯 <b>Поиск</b>\n\n"
                f"Проверено: {i + 1}\n"
                f"Найдено: {found}",
                parse_mode=ParseMode.HTML
            )
        
        await asyncio.sleep(0.5)
    
    await message.edit_text(
        f"✅ <b>Поиск завершён!</b>\n\n"
        f"Всего найдено: <b>{found}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=username_menu()
    )


# ============ СТАТИСТИКА ============

@dp.callback_query(lambda c: c.data == "username_stats")
async def username_stats(callback: types.CallbackQuery):
    await callback.answer()
    taken = load_db(TAKEN_DB_FILE)
    free = load_db(FREE_DB_FILE)
    
    await callback.message.edit_text(
        f"📊 <b>Статистика</b>\n\n"
        f"✅ Свободных: <b>{len(free)}</b>\n"
        f"❌ Занятых: <b>{len(taken)}</b>\n\n"
        f"Всего проверено: <b>{len(taken) + len(free)}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=username_menu()
    )


# ============ ЗАПУСК ============

async def main():
    # Создаём папки
    os.makedirs("sessions", exist_ok=True)
    
    # Инициализируем базы
    for f in [TAKEN_DB_FILE, FREE_DB_FILE, BANNED_DB_FILE]:
        if not os.path.exists(f):
            save_db(f, {})
    
    logger.info("🚀 Бот запущен!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("⛔ Остановлен")
