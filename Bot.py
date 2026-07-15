import logging
import os
import asyncio
import json
import re
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from dotenv import load_dotenv

from username_bot import router as username_router, init_username_bot
from gram_bot import router as gram_router, init_gram_bot, active_clients, active_tasks, set_user_chat_id

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# Храним активные сессии пользователей
user_sessions: Dict[int, str] = {}  # user_id -> phone


# ============ КЛАВИАТУРЫ ============

def get_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Gram Бот", callback_data="gram")],
        [InlineKeyboardButton(text="👤 Юзернеймы", callback_data="users")],
        [InlineKeyboardButton(text="📱 Мои сессии", callback_data="sessions")],
    ])

def get_gram_main_keyboard(has_session: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура Gram бота"""
    buttons = []
    if has_session:
        buttons.append([InlineKeyboardButton(text="🔄 Запустить авто-просмотры", callback_data="gram_start")])
        buttons.append([InlineKeyboardButton(text="⏹ Остановить", callback_data="gram_stop")])
    else:
        buttons.append([InlineKeyboardButton(text="❌ Нет активной сессии", callback_data="no_session")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_sessions_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Клавиатура сессий"""
    buttons = []
    
    # Если есть активная сессия
    if user_id in user_sessions:
        phone = user_sessions[user_id]
        buttons.append([InlineKeyboardButton(text=f"📱 {phone}", callback_data=f"sess_info")])
        buttons.append([InlineKeyboardButton(text="🗑 Удалить сессию", callback_data="sess_delete")])
    else:
        buttons.append([InlineKeyboardButton(text="➕ Добавить сессию", callback_data="sess_add")])
    
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_session_actions_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура действий с сессией"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚪 Выйти со всех каналов", callback_data="sess_leave_channels")],
        [InlineKeyboardButton(text="🚪 Выйти со всех групп", callback_data="sess_leave_groups")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="sessions")],
    ])

def get_username_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✨ Генерировать", callback_data="gen")],
        [InlineKeyboardButton(text="🔍 Проверить все", callback_data="check")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="settings")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main")],
    ])


# ============ СОСТОЯНИЯ ============

class SessionStates(StatesGroup):
    waiting_phone = State()
    waiting_code = State()


# ============ ОБРАБОТЧИКИ ============

@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        f"👋 Привет, {message.from_user.first_name or 'Пользователь'}!\n\n"
        f"🤖 <b>Telegram Бот-Центр</b>\n\n"
        f"Выбери раздел:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard()
    )


# ============ CALLBACK: ГЛАВНОЕ МЕНЮ ============

@dp.callback_query(lambda c: c.data == "main")
async def main_menu(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🏠 <b>Главное меню</b>\n\nВыбери раздел:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard()
    )


# ============ CALLBACK: GRAM БОТ ============

@dp.callback_query(lambda c: c.data == "gram")
async def gram_menu(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    has_session = user_id in user_sessions
    
    text = "🤖 <b>Gram Бот</b>\n\n"
    
    if has_session:
        phone = user_sessions[user_id]
        text += f"✅ Активная сессия: <b>{phone}</b>\n\n"
        text += "Нажми 'Запустить авто-просмотры' для начала работы."
    else:
        text += "❌ Нет активной сессии.\n\n"
        text += "Сначала добавь сессию в разделе 'Мои сессии'."
    
    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_gram_main_keyboard(has_session)
    )


@dp.callback_query(lambda c: c.data == "gram_start")
async def gram_start(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    
    if user_id not in user_sessions:
        await callback.answer("❌ Нет активной сессии")
        return
    
    phone = user_sessions[user_id]
    
    # Проверяем, есть ли клиент
    if phone not in active_clients:
        await callback.answer("❌ Сессия не активна. Пересоздайте.")
        return
    
    # Запускаем Gram бота
    from gram_bot import start_gram_worker, set_user_chat_id
    
    set_user_chat_id(user_id)
    client = active_clients[phone]
    
    # Сохраняем bot_username (по умолчанию gram_prbot)
    bot_username = "@gram_prbot"
    
    # Запускаем воркер
    task = asyncio.create_task(start_gram_worker(client, bot_username, phone))
    active_tasks[phone] = task
    
    await callback.message.edit_text(
        f"✅ <b>Gram бот запущен!</b>\n\n"
        f"📱 {phone}\n"
        f"🤖 {bot_username}\n\n"
        f"Авто-просмотры работают в фоне.\n"
        f"Для остановки используй /stop_gram",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="gram")]
        ])
    )


@dp.callback_query(lambda c: c.data == "gram_stop")
async def gram_stop(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    
    if user_id not in user_sessions:
        await callback.answer("❌ Нет активной сессии")
        return
    
    phone = user_sessions[user_id]
    
    from gram_bot import stop_gram_bot
    result = await stop_gram_bot(phone)
    
    if result:
        await callback.message.edit_text(
            f"⏹ <b>Gram бот остановлен</b>\n\n"
            f"📱 {phone}",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="gram")]
            ])
        )
    else:
        await callback.answer("❌ Ошибка остановки")


@dp.callback_query(lambda c: c.data == "no_session")
async def no_session(callback: types.CallbackQuery):
    await callback.answer("Сначала добавь сессию в разделе 'Мои сессии'", show_alert=True)


@dp.message(Command("stop_gram"))
async def stop_gram_command(message: types.Message):
    user_id = message.from_user.id
    
    if user_id not in user_sessions:
        await message.answer("❌ Нет активной сессии")
        return
    
    phone = user_sessions[user_id]
    
    from gram_bot import stop_gram_bot
    result = await stop_gram_bot(phone)
    
    if result:
        await message.answer(f"✅ Gram бот остановлен для {phone}")
    else:
        await message.answer("❌ Gram бот не был запущен")


@dp.message(Command("continue_gram"))
async def continue_gram(message: types.Message):
    """Продолжить работу после капчи"""
    user_id = message.from_user.id
    
    if user_id not in user_sessions:
        await message.answer("❌ Нет активной сессии")
        return
    
    phone = user_sessions[user_id]
    
    from gram_bot import continue_gram_bot
    result = await continue_gram_bot(phone)
    
    if result:
        await message.answer(
            "✅ Gram бот продолжен!\n\n"
            "Если капча еще активна, пройдите её вручную в Telegram и нажмите /continue_gram снова."
        )
    else:
        await message.answer("❌ Ошибка продолжения. Попробуйте перезапустить бота.")


# ============ CALLBACK: СЕССИИ ============

@dp.callback_query(lambda c: c.data == "sessions")
async def sessions_menu(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    
    text = "📱 <b>Мои сессии</b>\n\n"
    
    if user_id in user_sessions:
        phone = user_sessions[user_id]
        text += f"✅ Активная сессия:\n"
        text += f"📱 <b>{phone}</b>\n\n"
        
        # Проверяем статус
        if phone in active_clients:
            text += "🟢 Сессия активна\n"
        else:
            text += "🔴 Сессия не активна (пересоздайте)\n"
        
        text += "\n<i>Нажми на номер для управления</i>"
    else:
        text += "❌ Нет активных сессий\n\n"
        text += "Нажми 'Добавить сессию' для авторизации"
    
    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_sessions_keyboard(user_id)
    )


@dp.callback_query(lambda c: c.data == "sess_add")
async def session_add(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(SessionStates.waiting_phone)
    
    await callback.message.edit_text(
        "📱 <b>Добавление сессии</b>\n\n"
        "Введите номер телефона в формате:\n"
        "<code>+79172993848</code>\n\n"
        "или отправьте /cancel для отмены",
        parse_mode=ParseMode.HTML
    )


@dp.message(SessionStates.waiting_phone)
async def session_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    
    if not re.match(r'^\+?\d{10,15}$', phone):
        await message.answer(
            "❌ Неверный формат номера.\n"
            "Используйте формат: <code>+79172993848</code>",
            parse_mode=ParseMode.HTML
        )
        return
    
    await state.update_data(phone=phone)
    await state.set_state(SessionStates.waiting_code)
    
    from gram_bot import send_code, set_user_chat_id
    set_user_chat_id(message.chat.id)
    result = await send_code(phone, "gram_prbot")
    
    if result:
        await message.answer(
            "📱 <b>Код отправлен!</b>\n\n"
            "Введите код подтверждения из Telegram:",
            parse_mode=ParseMode.HTML
        )
    else:
        await message.answer(
            "❌ Ошибка отправки кода.\n"
            "Проверьте номер и попробуйте снова.\n\n"
            "Отправьте /start для возврата в меню"
        )
        await state.clear()


@dp.message(SessionStates.waiting_code)
async def session_code(message: types.Message, state: FSMContext):
    code = message.text.strip()
    user_id = message.from_user.id
    
    data = await state.get_data()
    phone = data.get("phone")
    
    from gram_bot import start_gram_bot
    result = await start_gram_bot(phone, code, "gram_prbot", message.chat.id)
    
    await state.clear()
    
    if result:
        # Сохраняем сессию для пользователя
        user_sessions[user_id] = phone
        
        await message.answer(
            f"✅ <b>Сессия добавлена!</b>\n\n"
            f"📱 {phone}\n\n"
            f"Теперь можно использовать её в Gram боте.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🤖 Перейти в Gram", callback_data="gram")],
                [InlineKeyboardButton(text="📱 Мои сессии", callback_data="sessions")],
            ])
        )
    else:
        await message.answer(
            "❌ Ошибка авторизации.\n"
            "Проверьте код и попробуйте снова.\n\n"
            "Отправьте /start для возврата в меню"
        )


@dp.callback_query(lambda c: c.data == "sess_info")
async def session_info(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    
    if user_id not in user_sessions:
        await callback.answer("❌ Сессия не найдена")
        return
    
    phone = user_sessions[user_id]
    
    if phone not in active_clients:
        await callback.answer("❌ Сессия не активна")
        return
    
    client = active_clients[phone]
    
    try:
        me = await client.get_me()
        username = f"@{me.username}" if me.username else "Нет юзернейма"
        user_id_telegram = me.id
        first_name = me.first_name or ""
        last_name = me.last_name or ""
        name = f"{first_name} {last_name}".strip()
        
        # Проверяем активна ли задача
        is_active = phone in active_tasks and not active_tasks[phone].done()
        
        text = f"📱 <b>Информация о сессии</b>\n\n"
        text += f"👤 <b>{name or 'Без имени'}</b>\n"
        text += f"📱 {phone}\n"
        text += f"🆔 {user_id_telegram}\n"
        text += f"👤 {username}\n"
        text += f"📊 Статус: {'🟢 Активна' if is_active else '🟡 Остановлена'}\n"
        
    except Exception as e:
        text = f"📱 <b>Информация о сессии</b>\n\n"
        text += f"📱 {phone}\n"
        text += f"❌ Ошибка получения данных: {e}\n"
    
    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_session_actions_keyboard()
    )


@dp.callback_query(lambda c: c.data == "sess_delete")
async def session_delete(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    
    if user_id not in user_sessions:
        await callback.answer("❌ Сессия не найдена")
        return
    
    phone = user_sessions[user_id]
    
    # Останавливаем если запущена
    from gram_bot import stop_gram_bot
    await stop_gram_bot(phone)
    
    # Удаляем
    if phone in active_clients:
        try:
            await active_clients[phone].disconnect()
        except:
            pass
        del active_clients[phone]
    
    if phone in active_tasks:
        del active_tasks[phone]
    
    del user_sessions[user_id]
    
    await callback.message.edit_text(
        f"🗑 <b>Сессия удалена</b>\n\n"
        f"📱 {phone}\n\n"
        f"Сессия успешно удалена.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📱 Мои сессии", callback_data="sessions")],
            [InlineKeyboardButton(text="🏠 Меню", callback_data="main")]
        ])
    )


# ============ ВЫХОД ИЗ КАНАЛОВ/ГРУПП ============

@dp.callback_query(lambda c: c.data == "sess_leave_channels")
async def session_leave_channels(callback: types.CallbackQuery):
    await callback.answer("⏳ Выхожу из каналов...")
    user_id = callback.from_user.id
    
    if user_id not in user_sessions:
        await callback.answer("❌ Нет активной сессии")
        return
    
    phone = user_sessions[user_id]
    
    if phone not in active_clients:
        await callback.answer("❌ Сессия не активна")
        return
    
    client = active_clients[phone]
    
    try:
        left_count = 0
        error_count = 0
        skipped_count = 0
        
        await callback.message.edit_text(
            f"⏳ <b>Выход из каналов...</b>\n\n"
            f"📱 {phone}\n"
            f"Это может занять некоторое время",
            parse_mode=ParseMode.HTML
        )
        
        # Получаем все диалоги
        async for dialog in client.iter_dialogs():
            try:
                # Проверяем что это канал
                if dialog.is_channel:
                    # Пропускаем личный канал (Saved Messages)
                    if dialog.entity.username == "me":
                        skipped_count += 1
                        logging.info(f"⏭ Пропускаю личный канал: {dialog.name}")
                        continue
                    
                    # Пробуем выйти
                    try:
                        await client.leave_channel(dialog.entity)
                        left_count += 1
                        logging.info(f"🚪 Вышел из канала: {dialog.name} (ID: {dialog.id})")
                        await asyncio.sleep(1)  # Задержка чтобы не спамить
                    except Exception as e:
                        error_msg = str(e).lower()
                        if "not a member" in error_msg or "already left" in error_msg:
                            skipped_count += 1
                            logging.info(f"⏭ Уже не участник: {dialog.name}")
                        else:
                            error_count += 1
                            logging.error(f"❌ Ошибка выхода из {dialog.name}: {e}")
                                
            except Exception as e:
                error_count += 1
                logging.error(f"❌ Ошибка обработки диалога: {e}")
                continue
        
        # Итоговое сообщение
        result_text = (
            f"✅ <b>Выход из каналов завершен!</b>\n\n"
            f"📱 {phone}\n"
            f"🚪 Вышел из: {left_count} каналов\n"
            f"⏭ Пропущено (личные/уже вышел): {skipped_count}\n"
            f"❌ Ошибок: {error_count}\n\n"
            f"<i>Личные чаты не были затронуты</i>"
        )
        
        if error_count > 0:
            result_text += f"\n\n⚠️ <b>Возможные причины ошибок:</b>\n"
            result_text += f"• Вы администратор канала (нельзя выйти)\n"
            result_text += f"• Канал был удален или заблокирован\n"
            result_text += f"• Нет прав на выход из канала"
        
        await callback.message.edit_text(
            result_text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад к сессии", callback_data="sess_info")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="main")]
            ])
        )
        
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Критическая ошибка: {e}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="sessions")]
            ])
        )


@dp.callback_query(lambda c: c.data == "sess_leave_groups")
async def session_leave_groups(callback: types.CallbackQuery):
    await callback.answer("⏳ Выхожу из групп...")
    user_id = callback.from_user.id
    
    if user_id not in user_sessions:
        await callback.answer("❌ Нет активной сессии")
        return
    
    phone = user_sessions[user_id]
    
    if phone not in active_clients:
        await callback.answer("❌ Сессия не активна")
        return
    
    client = active_clients[phone]
    
    try:
        left_count = 0
        error_count = 0
        skipped_count = 0
        
        await callback.message.edit_text(
            f"⏳ <b>Выход из групп...</b>\n\n"
            f"📱 {phone}\n"
            f"Это может занять некоторое время",
            parse_mode=ParseMode.HTML
        )
        
        async for dialog in client.iter_dialogs():
            try:
                # Проверяем что это группа
                if dialog.is_group:
                    # Пробуем выйти
                    try:
                        await client.leave_group(dialog.entity)
                        left_count += 1
                        logging.info(f"🚪 Вышел из группы: {dialog.name} (ID: {dialog.id})")
                        await asyncio.sleep(1)  # Задержка чтобы не спамить
                    except Exception as e:
                        error_msg = str(e).lower()
                        if "not a member" in error_msg or "already left" in error_msg:
                            skipped_count += 1
                            logging.info(f"⏭ Уже не участник: {dialog.name}")
                        else:
                            error_count += 1
                            logging.error(f"❌ Ошибка выхода из группы {dialog.name}: {e}")
                                
            except Exception as e:
                error_count += 1
                logging.error(f"❌ Ошибка обработки диалога: {e}")
                continue
        
        # Итоговое сообщение
        result_text = (
            f"✅ <b>Выход из групп завершен!</b>\n\n"
            f"📱 {phone}\n"
            f"🚪 Вышел из: {left_count} групп\n"
            f"⏭ Пропущено (уже вышел): {skipped_count}\n"
            f"❌ Ошибок: {error_count}\n\n"
            f"<i>Личные чаты не были затронуты</i>"
        )
        
        if error_count > 0:
            result_text += f"\n\n⚠️ <b>Возможные причины ошибок:</b>\n"
            result_text += f"• Вы администратор группы (нельзя выйти)\n"
            result_text += f"• Группа была удалена или заблокирована\n"
            result_text += f"• Нет прав на выход из группы"
        
        await callback.message.edit_text(
            result_text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад к сессии", callback_data="sess_info")],
                [InlineKeyboardButton(text="🏠 Меню", callback_data="main")]
            ])
        )
        
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Критическая ошибка: {e}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="sessions")]
            ])
        )


# ============ CALLBACK: ЮЗЕРНЕЙМЫ ============

@dp.callback_query(lambda c: c.data == "users")
async def username_menu(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "👤 <b>Раздел Юзернеймы</b>\n\n"
        "🔍 Поиск свободных 5-значных юзернеймов\n\n"
        "Выбери действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_username_keyboard()
    )


@dp.message(Command("cancel"))
async def cancel_command(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "❌ Отменено.",
        reply_markup=get_main_keyboard()
    )


# ============ ИНИЦИАЛИЗАЦИЯ ============

async def main():
    # Инициализируем модули
    init_username_bot(dp)
    init_gram_bot(dp)
    
    # Запускаем бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("⛔ Бот остановлен")
