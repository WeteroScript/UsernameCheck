"""
Модуль "Каналы": подключение каналов бота, авто-приём заявок на
вступление, создание постов с кнопками.
"""

import json
import os
import logging
from aiogram import Router, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode, ButtonStyle
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from typing import Dict, List, Optional

router = Router()

CHANNELS_FILE = "connected_channels.json"
MAX_CHANNELS = 10
MAX_AUTO_ACCEPT = 10_000_000

# user_id -> [{"chat_id", "title", "username", "auto_accept_enabled",
#              "auto_accept_max", "auto_accept_done"}]
channels_data: Dict[int, List[Dict]] = {}

# Черновики постов (временные, в памяти): user_id -> {"chat_id", "text", "buttons": [{"text","url"}]}
post_drafts: Dict[int, Dict] = {}


def load_channels() -> Dict[int, List[Dict]]:
    if os.path.exists(CHANNELS_FILE):
        try:
            with open(CHANNELS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return {int(k): v for k, v in data.items()}
        except Exception as e:
            logging.error(f"❌ Ошибка загрузки {CHANNELS_FILE}: {e}")
    return {}


def save_channels():
    try:
        with open(CHANNELS_FILE, 'w', encoding='utf-8') as f:
            json.dump(channels_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.error(f"❌ Ошибка сохранения {CHANNELS_FILE}: {e}")


class ChannelStates(StatesGroup):
    waiting_connect = State()
    waiting_auto_accept_count = State()
    waiting_post_text = State()
    waiting_post_button = State()


BUTTON_COLOR_CHOICES = [
    ("default", "⚪ Обычный", None),
    ("primary", "🔵 Синий", ButtonStyle.PRIMARY),
    ("success", "🟢 Зелёный", ButtonStyle.SUCCESS),
    ("danger", "🔴 Красный", ButtonStyle.DANGER),
]
BUTTON_COLOR_MARK = {"default": "⚪", "primary": "🔵", "success": "🟢", "danger": "🔴"}


def _find_channel(user_id: int, chat_id: int) -> Optional[Dict]:
    for ch in channels_data.get(user_id, []):
        if ch["chat_id"] == chat_id:
            return ch
    return None


# ============ ГЛАВНОЕ МЕНЮ "КАНАЛЫ" ============

def get_channels_root_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Каналы", callback_data="ch_list")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main")],
    ])


@router.callback_query(lambda c: c.data == "channels_menu")
async def channels_menu(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "📢 <b>Каналы</b>\n\nУправление подключёнными каналами:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_channels_root_keyboard()
    )


def get_channel_list_keyboard(user_id: int) -> InlineKeyboardMarkup:
    chans = channels_data.get(user_id, [])
    buttons = []
    for ch in chans:
        mark = "🟢" if ch.get("auto_accept_enabled") else "⚪"
        buttons.append([InlineKeyboardButton(
            text=f"{mark} {ch['title']}", callback_data=f"ch_item_{ch['chat_id']}"
        )])
    if len(chans) < MAX_CHANNELS:
        buttons.append([InlineKeyboardButton(text="➕ Подключить канал", callback_data="ch_connect")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="channels_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(lambda c: c.data == "ch_list")
async def ch_list(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    chans = channels_data.get(user_id, [])
    if not chans:
        await callback.message.edit_text(
            "📢 <b>Каналы</b>\n\nНет подключённых каналов.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Подключить канал", callback_data="ch_connect")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="channels_menu")],
            ])
        )
        return
    await callback.message.edit_text(
        f"📢 <b>Подключённые каналы</b> ({len(chans)}/{MAX_CHANNELS})",
        parse_mode=ParseMode.HTML,
        reply_markup=get_channel_list_keyboard(user_id)
    )


# ============ ПОДКЛЮЧЕНИЕ КАНАЛА ============

@router.callback_query(lambda c: c.data == "ch_connect")
async def ch_connect(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if len(channels_data.get(user_id, [])) >= MAX_CHANNELS:
        await callback.answer(f"❌ Максимум {MAX_CHANNELS} каналов", show_alert=True)
        return
    await callback.answer()
    await state.set_state(ChannelStates.waiting_connect)
    await callback.message.edit_text(
        "➕ <b>Подключить канал</b>\n\n"
        "1. Добавь этого бота администратором в свой канал "
        "(нужны права: приглашение пользователей, публикация сообщений)\n"
        "2. Перешли сюда любой пост из канала, или пришли его @username\n\n"
        "/cancel — отменить",
        parse_mode=ParseMode.HTML
    )


@router.message(ChannelStates.waiting_connect)
async def ch_connect_input(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()
    user_id = message.from_user.id
    
    if text.lower() in ("/cancel", "отмена"):
        await state.clear()
        await message.answer("❌ Отменено", reply_markup=get_channels_root_keyboard())
        return
    
    chat_id = None
    if message.forward_from_chat:
        chat_id = message.forward_from_chat.id
    elif text:
        username = text.lstrip('@')
        if "t.me/" in username:
            username = username.split("t.me/")[-1].split("/")[0].split("?")[0]
        try:
            chat = await message.bot.get_chat(f"@{username}")
            chat_id = chat.id
        except Exception as e:
            await message.answer(f"❌ Не удалось найти канал «{text}»: {e}")
            return
    
    if not chat_id:
        await message.answer("❌ Перешли пост из канала или пришли его @username.")
        return
    
    try:
        chat = await message.bot.get_chat(chat_id)
        member = await message.bot.get_chat_member(chat_id, message.bot.id)
    except Exception as e:
        await message.answer(f"❌ Не удалось получить доступ к каналу: {e}")
        return
    
    if member.status not in ("administrator", "creator"):
        await message.answer(
            "❌ Бот не является администратором этого канала. "
            "Добавь бота в админы и попробуй снова."
        )
        return
    
    await state.clear()
    channels_data.setdefault(user_id, [])
    if _find_channel(user_id, chat_id):
        await message.answer("ℹ️ Этот канал уже подключён.", reply_markup=get_channels_root_keyboard())
        return
    
    channels_data[user_id].append({
        "chat_id": chat_id,
        "title": chat.title or chat.username or str(chat_id),
        "username": chat.username,
        "auto_accept_enabled": False,
        "auto_accept_max": 0,
        "auto_accept_done": 0,
    })
    save_channels()
    await message.answer(
        f"✅ Канал «{chat.title}» подключён.",
        reply_markup=get_channel_list_keyboard(user_id)
    )


# ============ МЕНЮ КОНКРЕТНОГО КАНАЛА ============

def get_channel_item_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Авто-приём заявок", callback_data=f"ch_auto_{chat_id}")],
        [InlineKeyboardButton(text="🆔 Получить айди канала", callback_data=f"ch_getid_{chat_id}")],
        [InlineKeyboardButton(text="📝 Создать пост", callback_data=f"ch_post_{chat_id}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="ch_list")],
    ])


@router.callback_query(lambda c: c.data.startswith("ch_item_"))
async def ch_item(callback: types.CallbackQuery):
    await callback.answer()
    chat_id = int(callback.data.replace("ch_item_", ""))
    user_id = callback.from_user.id
    ch = _find_channel(user_id, chat_id)
    if not ch:
        await callback.message.edit_text("❌ Канал не найден", reply_markup=get_channels_root_keyboard())
        return
    await callback.message.edit_text(
        f"📢 <b>{ch['title']}</b>\n\nВыбери действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_channel_item_keyboard(chat_id)
    )


@router.callback_query(lambda c: c.data.startswith("ch_getid_"))
async def ch_getid(callback: types.CallbackQuery):
    chat_id = int(callback.data.replace("ch_getid_", ""))
    await callback.answer(f"ID: {chat_id}", show_alert=True)


# ============ АВТО-ПРИЁМ ЗАЯВОК ============

def get_auto_accept_keyboard(ch: Dict) -> InlineKeyboardMarkup:
    chat_id = ch["chat_id"]
    state_text = "🟢 Включено" if ch.get("auto_accept_enabled") else "⚪ Выключено"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{state_text} (нажми, чтобы переключить)", callback_data=f"ch_auto_toggle_{chat_id}")],
        [InlineKeyboardButton(
            text=f"🔢 Кол-во автоматических принятий: {ch.get('auto_accept_max', 0)}",
            callback_data=f"ch_auto_count_{chat_id}"
        )],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"ch_item_{chat_id}")],
    ])


@router.callback_query(lambda c: c.data.startswith("ch_auto_") and not c.data.startswith("ch_auto_toggle_") and not c.data.startswith("ch_auto_count_"))
async def ch_auto_menu(callback: types.CallbackQuery):
    await callback.answer()
    chat_id = int(callback.data.replace("ch_auto_", ""))
    user_id = callback.from_user.id
    ch = _find_channel(user_id, chat_id)
    if not ch:
        return
    await callback.message.edit_text(
        f"✅ <b>Авто-приём заявок</b>\n\n📢 {ch['title']}\n\n"
        f"Принято в этом заходе: {ch.get('auto_accept_done', 0)}/{ch.get('auto_accept_max', 0)}",
        parse_mode=ParseMode.HTML,
        reply_markup=get_auto_accept_keyboard(ch)
    )


@router.callback_query(lambda c: c.data.startswith("ch_auto_toggle_"))
async def ch_auto_toggle(callback: types.CallbackQuery):
    chat_id = int(callback.data.replace("ch_auto_toggle_", ""))
    user_id = callback.from_user.id
    ch = _find_channel(user_id, chat_id)
    if not ch:
        await callback.answer("❌ Канал не найден")
        return
    if not ch.get("auto_accept_enabled") and ch.get("auto_accept_max", 0) <= 0:
        await callback.answer("❌ Сначала укажи количество автоматических принятий", show_alert=True)
        return
    ch["auto_accept_enabled"] = not ch.get("auto_accept_enabled", False)
    if ch["auto_accept_enabled"]:
        ch["auto_accept_done"] = 0
    save_channels()
    await callback.answer("✅ Включено" if ch["auto_accept_enabled"] else "⏹ Выключено")
    await callback.message.edit_text(
        f"✅ <b>Авто-приём заявок</b>\n\n📢 {ch['title']}\n\n"
        f"Принято в этом заходе: {ch.get('auto_accept_done', 0)}/{ch.get('auto_accept_max', 0)}",
        parse_mode=ParseMode.HTML,
        reply_markup=get_auto_accept_keyboard(ch)
    )


@router.callback_query(lambda c: c.data.startswith("ch_auto_count_"))
async def ch_auto_count(callback: types.CallbackQuery, state: FSMContext):
    chat_id = int(callback.data.replace("ch_auto_count_", ""))
    await callback.answer()
    await state.update_data(auto_count_chat_id=chat_id)
    await state.set_state(ChannelStates.waiting_auto_accept_count)
    await callback.message.edit_text(
        f"🔢 Введи число от 0 до {MAX_AUTO_ACCEPT} — максимум заявок, которые бот примет автоматически.\n\n"
        f"/cancel — отменить",
        parse_mode=ParseMode.HTML
    )


@router.message(ChannelStates.waiting_auto_accept_count)
async def ch_auto_count_input(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()
    if text.lower() in ("/cancel", "отмена"):
        await state.clear()
        await message.answer("❌ Отменено", reply_markup=get_channels_root_keyboard())
        return
    if not text.isdigit() or not (0 <= int(text) <= MAX_AUTO_ACCEPT):
        await message.answer(f"❌ Введи число от 0 до {MAX_AUTO_ACCEPT}.")
        return
    
    data = await state.get_data()
    chat_id = data.get("auto_count_chat_id")
    user_id = message.from_user.id
    ch = _find_channel(user_id, chat_id)
    await state.clear()
    if not ch:
        await message.answer("❌ Канал не найден")
        return
    
    ch["auto_accept_max"] = int(text)
    ch["auto_accept_done"] = 0
    save_channels()
    await message.answer(
        f"✅ Установлено: {text} автоматических принятий.",
        reply_markup=get_auto_accept_keyboard(ch)
    )


async def handle_chat_join_request(update: types.ChatJoinRequest, bot):
    """Вызывается из главного обработчика chat_join_request в Bot.py."""
    chat_id = update.chat.id
    for user_id, chans in channels_data.items():
        ch = _find_channel(user_id, chat_id)
        if not ch or not ch.get("auto_accept_enabled"):
            continue
        if ch.get("auto_accept_done", 0) >= ch.get("auto_accept_max", 0):
            ch["auto_accept_enabled"] = False
            save_channels()
            continue
        try:
            await bot.approve_chat_join_request(chat_id, update.from_user.id)
            ch["auto_accept_done"] = ch.get("auto_accept_done", 0) + 1
            save_channels()
            if ch["auto_accept_done"] >= ch["auto_accept_max"]:
                ch["auto_accept_enabled"] = False
                save_channels()
                try:
                    await bot.send_message(
                        user_id,
                        f"✅ Авто-приём заявок в «{ch['title']}» завершён — "
                        f"принято {ch['auto_accept_done']} заявок. Авто-приём остановлен."
                    )
                except Exception as e:
                    logging.error(f"❌ handle_chat_join_request notify: {e}")
        except Exception as e:
            logging.error(f"❌ handle_chat_join_request approve: {e}")
        return  # канал привязан к одному владельцу — обрабатываем один раз


@router.chat_join_request()
async def on_chat_join_request(update: types.ChatJoinRequest):
    await handle_chat_join_request(update, update.bot)


# ============ СОЗДАНИЕ ПОСТА ============

@router.callback_query(lambda c: c.data.startswith("ch_post_"))
async def ch_post_start(callback: types.CallbackQuery, state: FSMContext):
    chat_id = int(callback.data.replace("ch_post_", ""))
    user_id = callback.from_user.id
    ch = _find_channel(user_id, chat_id)
    if not ch:
        await callback.answer("❌ Канал не найден")
        return
    await callback.answer()
    post_drafts[user_id] = {"chat_id": chat_id, "text": None, "buttons": []}
    await state.set_state(ChannelStates.waiting_post_text)
    await callback.message.edit_text(
        "📝 Введите текст для публикации поста\n\n/cancel — отменить"
    )


def get_post_buttons_keyboard(draft: Dict) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text="➕ Добавить кнопку", callback_data="post_add_btn")]]
    if draft["buttons"]:
        buttons.append([InlineKeyboardButton(text="➖ Удалить кнопку", callback_data="post_del_btn_menu")])
    buttons.append([InlineKeyboardButton(text="✅ Готово, далее", callback_data="post_continue")])
    buttons.append([InlineKeyboardButton(text="❌ Отменить", callback_data="post_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(ChannelStates.waiting_post_text)
async def ch_post_text_input(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()
    user_id = message.from_user.id
    if text.lower() in ("/cancel", "отмена"):
        await state.clear()
        post_drafts.pop(user_id, None)
        await message.answer("❌ Отменено", reply_markup=get_channels_root_keyboard())
        return
    if not text:
        await message.answer("❌ Пришли текст поста.")
        return
    
    draft = post_drafts.get(user_id)
    if not draft:
        await state.clear()
        await message.answer("❌ Сессия создания поста устарела, начни заново.")
        return
    
    draft["text"] = text
    await state.clear()
    await message.answer(
        "Добавить кнопки к посту?",
        reply_markup=get_post_buttons_keyboard(draft)
    )


@router.callback_query(lambda c: c.data == "post_add_btn")
async def post_add_btn(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id not in post_drafts:
        await callback.answer("❌ Сессия устарела", show_alert=True)
        return
    await callback.answer()
    await state.set_state(ChannelStates.waiting_post_button)
    await callback.message.edit_text(
        "введите имя кнопки и ссылку на неё, в таком формате:\n\n"
        "<code>Название|ссылка</code>\n\n"
        "После этого предложу выбрать цвет кнопки.\n\n"
        "/cancel — отменить",
        parse_mode=ParseMode.HTML
    )


@router.message(ChannelStates.waiting_post_button)
async def post_button_input(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()
    user_id = message.from_user.id
    if text.lower() in ("/cancel", "отмена"):
        await state.clear()
        draft = post_drafts.get(user_id)
        if draft:
            await message.answer("Добавить кнопки к посту?", reply_markup=get_post_buttons_keyboard(draft))
        return
    
    if "|" not in text:
        await message.answer("❌ Неверный формат. Пример: <code>Название|https://example.com</code>", parse_mode=ParseMode.HTML)
        return
    
    name, _, url = text.partition("|")
    name = name.strip()
    url = url.strip()
    if not name or not url:
        await message.answer("❌ Неверный формат. Пример: <code>Название|https://example.com</code>", parse_mode=ParseMode.HTML)
        return
    if not (url.startswith("http://") or url.startswith("https://") or url.startswith("tg://") or url.startswith("t.me/")):
        await message.answer("❌ Ссылка должна начинаться с http://, https:// или tg://")
        return
    if url.startswith("t.me/"):
        url = "https://" + url
    
    draft = post_drafts.get(user_id)
    if not draft:
        await state.clear()
        await message.answer("❌ Сессия устарела, начни заново.")
        return
    
    draft["pending_button"] = {"text": name, "url": url}
    await state.clear()
    await message.answer(
        "🎨 <b>Задать цвет кнопки</b>\n\nВыбери цвет:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"post_btn_color_{key}")]
            for key, label, _ in BUTTON_COLOR_CHOICES
        ])
    )


@router.callback_query(lambda c: c.data.startswith("post_btn_color_"))
async def post_btn_color_pick(callback: types.CallbackQuery):
    color_key = callback.data.replace("post_btn_color_", "")
    user_id = callback.from_user.id
    draft = post_drafts.get(user_id)
    if not draft or "pending_button" not in draft:
        await callback.answer("❌ Сессия устарела", show_alert=True)
        return
    
    style = next((s for k, _, s in BUTTON_COLOR_CHOICES if k == color_key), None)
    btn = draft.pop("pending_button")
    btn["color"] = color_key if style is not None else None
    draft["buttons"].append(btn)
    
    await callback.answer(f"✅ Кнопка «{btn['text']}» добавлена")
    await callback.message.edit_text(
        f"✅ Кнопка «{btn['text']}» добавлена.\n\nДобавить кнопки к посту?",
        reply_markup=get_post_buttons_keyboard(draft)
    )


@router.callback_query(lambda c: c.data == "post_del_btn_menu")
async def post_del_btn_menu(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    draft = post_drafts.get(user_id)
    if not draft or not draft["buttons"]:
        await callback.message.edit_text("Нет кнопок для удаления.", reply_markup=get_post_buttons_keyboard(draft or {"buttons": []}))
        return
    buttons = [
        [InlineKeyboardButton(
            text=f"❌ {BUTTON_COLOR_MARK.get(b.get('color'), '⚪')} {b['text']}",
            callback_data=f"post_del_btn_{i}"
        )]
        for i, b in enumerate(draft["buttons"])
    ]
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="post_back_to_buttons")])
    await callback.message.edit_text("Выбери кнопку для удаления:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(lambda c: c.data.startswith("post_del_btn_") and c.data.replace("post_del_btn_", "").isdigit())
async def post_del_btn_confirm(callback: types.CallbackQuery):
    idx = int(callback.data.replace("post_del_btn_", ""))
    user_id = callback.from_user.id
    draft = post_drafts.get(user_id)
    if not draft:
        await callback.answer("❌ Сессия устарела", show_alert=True)
        return
    await callback.answer()
    btn = draft["buttons"][idx] if 0 <= idx < len(draft["buttons"]) else None
    if not btn:
        await callback.message.edit_text("Добавить кнопки к посту?", reply_markup=get_post_buttons_keyboard(draft))
        return
    await callback.message.edit_text(
        f"Кнопка «{btn['text']}»:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"post_del_btn_confirm_{idx}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="post_del_btn_menu")],
        ])
    )


@router.callback_query(lambda c: c.data.startswith("post_del_btn_confirm_"))
async def post_del_btn_execute(callback: types.CallbackQuery):
    idx = int(callback.data.replace("post_del_btn_confirm_", ""))
    user_id = callback.from_user.id
    draft = post_drafts.get(user_id)
    if draft and 0 <= idx < len(draft["buttons"]):
        removed = draft["buttons"].pop(idx)
        await callback.answer(f"🗑 Удалена: {removed['text']}")
    else:
        await callback.answer("❌ Не найдено")
    await callback.message.edit_text("Добавить кнопки к посту?", reply_markup=get_post_buttons_keyboard(draft or {"buttons": []}))


@router.callback_query(lambda c: c.data == "post_back_to_buttons")
async def post_back_to_buttons(callback: types.CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    draft = post_drafts.get(user_id, {"buttons": []})
    await callback.message.edit_text("Добавить кнопки к посту?", reply_markup=get_post_buttons_keyboard(draft))


@router.callback_query(lambda c: c.data == "post_cancel")
async def post_cancel(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer("❌ Отменено")
    user_id = callback.from_user.id
    post_drafts.pop(user_id, None)
    await state.clear()
    await callback.message.edit_text("❌ Создание поста отменено.", reply_markup=get_channels_root_keyboard())


def _build_post_markup(draft: Dict) -> Optional[InlineKeyboardMarkup]:
    if not draft["buttons"]:
        return None
    color_to_style = {k: s for k, _, s in BUTTON_COLOR_CHOICES}
    rows = []
    for b in draft["buttons"]:
        style = color_to_style.get(b.get("color"))
        kwargs = {"text": b["text"], "url": b["url"]}
        if style is not None:
            kwargs["style"] = style
        rows.append([InlineKeyboardButton(**kwargs)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(lambda c: c.data == "post_continue")
async def post_continue(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    draft = post_drafts.get(user_id)
    if not draft or not draft.get("text"):
        await callback.answer("❌ Сессия устарела", show_alert=True)
        return
    await callback.answer()
    
    markup = _build_post_markup(draft)
    await callback.message.edit_text("Отлично, подтвердите отправку поста:")
    # Готовый пост показываем отдельным сообщением — именно так, как он
    # будет опубликован в канале.
    await callback.message.answer(draft["text"], reply_markup=markup, disable_web_page_preview=True)
    await callback.message.answer(
        "Подтвердить отправку?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Опубликовать", callback_data="post_publish")],
            [InlineKeyboardButton(text="➖ Удалить кнопку", callback_data="post_del_btn_menu")],
            [InlineKeyboardButton(text="❌ Отменить", callback_data="post_cancel")],
        ])
    )


@router.callback_query(lambda c: c.data == "post_publish")
async def post_publish(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    draft = post_drafts.get(user_id)
    if not draft or not draft.get("text"):
        await callback.answer("❌ Сессия устарела", show_alert=True)
        return
    await callback.answer("📤 Публикую...")
    
    markup = _build_post_markup(draft)
    try:
        await callback.bot.send_message(
            draft["chat_id"], draft["text"], reply_markup=markup, disable_web_page_preview=True
        )
        await callback.message.edit_text("✅ Пост опубликован в канале.")
    except Exception as e:
        logging.error(f"❌ post_publish: {e}")
        await callback.message.edit_text(f"❌ Ошибка публикации: {e}")
    finally:
        post_drafts.pop(user_id, None)


# ============ ИНИЦИАЛИЗАЦИЯ ============

_channels_router_initialized = False


def init_channels_feature(dp):
    global _channels_router_initialized
    if not _channels_router_initialized:
        channels_data.update(load_channels())
        dp.include_router(router)
        _channels_router_initialized = True
        logging.info("✅ Модуль 'Каналы' инициализирован")


__all__ = [
    'router',
    'init_channels_feature',
    'get_channels_root_keyboard',
        ]
