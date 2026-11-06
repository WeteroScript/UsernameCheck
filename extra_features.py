"""
Модуль дополнительных функций: GitHub-поиск, Премиум, Получить айди,
Сообщить о баге.
"""

import logging
import aiohttp
import asyncio
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.enums import ParseMode, ButtonStyle
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from typing import Optional, List, Dict

from access_control import is_admin, is_premium, get_all_admins, is_tech_support_blocked
from i18n import tr

router = Router()
_bot_instance = None


def set_bot(bot):
    global _bot_instance
    _bot_instance = bot


class ExtraStates(StatesGroup):
    waiting_github_query = State()
    waiting_bug_report = State()
    waiting_id_channel = State()
    waiting_id_group = State()
    waiting_id_user = State()
    waiting_id_bot = State()


# ============ ГЛАВНОЕ МЕНЮ (доп. кнопки) ============

def get_extra_main_buttons() -> List[InlineKeyboardButton]:
    """Кнопки, которые добавляются в главное меню бота (плоский список —
    раскладка по колонкам делается в get_main_keyboard)."""
    return [
        InlineKeyboardButton(text="🐙 GitHub репозитории", callback_data="github_menu"),
        InlineKeyboardButton(text="🎥 Скачать видео", callback_data="video_menu"),
        InlineKeyboardButton(text="💎 Премиум", callback_data="premium_menu", style=ButtonStyle.DANGER),
        InlineKeyboardButton(text="🆔 Получить айди", callback_data="getid_menu"),
        InlineKeyboardButton(text="🐛 Сообщить о баге", callback_data="bugreport_menu"),
    ]


# ============ GITHUB ПОИСК ============

GITHUB_API_URL = "https://api.github.com/search/repositories"


async def search_github_repos(query: str, limit: int = 15) -> Optional[List[Dict]]:
    headers = {"Accept": "application/vnd.github+json"}
    params = {"q": query, "sort": "stars", "order": "desc", "per_page": limit}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                GITHUB_API_URL, headers=headers, params=params,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    logging.error(f"❌ GitHub API вернул статус {resp.status}")
                    return None
                data = await resp.json()
                return data.get("items", [])
    except Exception as e:
        logging.error(f"❌ search_github_repos: {e}")
        return None


def format_github_results(items: List[Dict]) -> str:
    lines = ["🐙 <b>Результаты поиска GitHub</b>\n"]
    for i, repo in enumerate(items, 1):
        name = repo.get("full_name", "?")
        url = repo.get("html_url", "")
        stars = repo.get("stargazers_count", 0)
        desc = (repo.get("description") or "").strip()
        if len(desc) > 100:
            desc = desc[:97] + "..."
        lines.append(f"{i}. <a href=\"{url}\">{name}</a> ⭐ {stars}")
        if desc:
            lines.append(f"   <i>{desc}</i>")
    return "\n".join(lines)


@router.callback_query(lambda c: c.data == "github_menu")
async def github_menu(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(ExtraStates.waiting_github_query)
    await callback.message.edit_text(
        "🐙 <b>Поиск репозиториев GitHub</b>\n\n"
        "Введи ключевые слова для поиска:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="home_ai")]])
    )


@router.message(ExtraStates.waiting_github_query)
async def github_query_input(message: types.Message, state: FSMContext):
    query = (message.text or "").strip()
    if query.lower() in ("/cancel", "отмена"):
        await state.clear()
        await message.answer("❌ Отменено", reply_markup=_back_to_main_kb())
        return
    if not query:
        await message.answer("❌ Введи ключевые слова для поиска.")
        return
    
    await state.clear()
    wait_msg = await message.answer("🔎 Ищу репозитории...")
    items = await search_github_repos(query)
    
    if items is None:
        await wait_msg.edit_text(
            "❌ Не удалось выполнить поиск (GitHub API недоступен или превышен лимит запросов). Попробуй позже.",
            reply_markup=_back_to_main_kb()
        )
        return
    
    if not items:
        await wait_msg.edit_text(
            f"😔 По запросу «{query}» ничего не найдено.",
            reply_markup=_back_to_main_kb()
        )
        return
    
    text = format_github_results(items)
    
    if len(text) > 4096:
        plain_lines = [f"{i}. {r.get('full_name')} — {r.get('html_url')} (⭐ {r.get('stargazers_count', 0)})"
                        for i, r in enumerate(items, 1)]
        file_content = "\n".join(plain_lines)
        await wait_msg.delete()
        await message.answer_document(
            BufferedInputFile(file_content.encode('utf-8'), filename="github_search.txt"),
            caption=f"🐙 Результаты поиска: «{query}»",
            reply_markup=_back_to_main_kb()
        )
    else:
        await wait_msg.edit_text(
            text, parse_mode=ParseMode.HTML, disable_web_page_preview=True,
            reply_markup=_back_to_main_kb()
        )


# ============ ПРЕМИУМ ============

@router.callback_query(lambda c: c.data == "premium_menu")
async def premium_menu(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    await callback.answer()

    description = tr(user_id, "premium_description")
    if is_premium(user_id):
        description += "\n\n" + tr(user_id, "premium_active")

    await callback.message.edit_text(
        tr(user_id, "premium_title") + "\n\n" + description,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=tr(user_id, "premium_buy"),
                callback_data="premium_buy_crypto",
                style=ButtonStyle.SUCCESS,
            )],
            [InlineKeyboardButton(
                text=tr(user_id, "premium_ton"),
                url="https://t.me/BotFarmSupport",
            )],
            [InlineKeyboardButton(
                text=tr(user_id, "premium_back"),
                callback_data="home_profile",
                style=ButtonStyle.DANGER,
            )],
        ]),
    )


# ============ ПОЛУЧИТЬ АЙДИ ============

def get_getid_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆔 Мой ID", callback_data="getid_me", style=ButtonStyle.SUCCESS)],
        [InlineKeyboardButton(text="📢 Каналы", callback_data="getid_channel")],
        [InlineKeyboardButton(text="👥 Группы", callback_data="getid_group")],
        [InlineKeyboardButton(text="👤 Пользователи", callback_data="getid_user")],
        [InlineKeyboardButton(text="🤖 Боты", callback_data="getid_bot")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="home_profile")],
    ])


GETID_PROMPTS = {
    "channel": ("📢 Пришли ссылку/юзернейм канала, или перешли сюда любой пост из него.", ExtraStates.waiting_id_channel),
    "group": ("👥 Пришли ссылку/юзернейм группы, или перешли сюда любое сообщение из неё.", ExtraStates.waiting_id_group),
    "user": ("👤 Пришли @username пользователя, или перешли сюда любое его сообщение.", ExtraStates.waiting_id_user),
    "bot": ("🤖 Пришли @username бота.", ExtraStates.waiting_id_bot),
}


@router.callback_query(lambda c: c.data == "getid_me")
async def getid_me(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🆔 <b>Твой Telegram ID</b>\n\n<code>" + str(callback.from_user.id) + "</code>\n\n"
        "Этот номер можно передать администратору для настройки доступа.",
        parse_mode=ParseMode.HTML,
        reply_markup=get_getid_keyboard(),
    )


@router.message(Command("id", "myid"))
async def getid_self_command(message: types.Message):
    await message.answer(
        "🆔 Твой Telegram ID: <code>" + str(message.from_user.id) + "</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=get_getid_keyboard(),
    )


@router.callback_query(lambda c: c.data == "getid_menu")
async def getid_menu(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🆔 <b>Получить айди</b>\n\nВыбери, чей ID нужно узнать:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_getid_keyboard()
    )


@router.callback_query(lambda c: c.data.startswith("getid_") and c.data.replace("getid_", "") in GETID_PROMPTS)
async def getid_prompt(callback: types.CallbackQuery, state: FSMContext):
    kind = callback.data.replace("getid_", "")
    prompt, target_state = GETID_PROMPTS[kind]
    await callback.answer()
    await state.set_state(target_state)
    await callback.message.edit_text(
        f"{prompt}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="getid_menu")]])
    )


async def _resolve_and_reply_id(message: types.Message, state: FSMContext, label: str):
    text = (message.text or "").strip()
    if text.lower() in ("/cancel", "отмена"):
        await state.clear()
        await message.answer("❌ Отменено", reply_markup=get_getid_keyboard())
        return

    await state.clear()

    if message.forward_from:
        await message.answer(
            f"🆔 <b>{label}</b>\n\nID: <code>{message.forward_from.id}</code>",
            parse_mode=ParseMode.HTML, reply_markup=get_getid_keyboard()
        )
        return
    if message.forward_from_chat:
        chat = message.forward_from_chat
        await message.answer(
            f"🆔 <b>{label}</b>\n\n"
            f"Название: {chat.title or chat.username or '—'}\n"
            f"ID: <code>{chat.id}</code>",
            parse_mode=ParseMode.HTML, reply_markup=get_getid_keyboard()
        )
        return

    if not text:
        await message.answer("❌ Пришли ссылку/юзернейм, или перешли сообщение.")
        return

    if text.lower() in {"me", "я", "мой id", "мой айди"}:
        await message.answer(
            "🆔 <b>Твой Telegram ID</b>\n\n<code>" + str(message.from_user.id) + "</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=get_getid_keyboard(),
        )
        return
    if text.lstrip("-").isdigit():
        await message.answer(
            f"🆔 <b>{label}</b>\n\nID: <code>{text}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=get_getid_keyboard(),
        )
        return

    username = text.lstrip('@')
    if "t.me/" in username:
        username = username.split("t.me/")[-1].split("/")[0].split("?")[0]
    username = username.strip().lstrip("@")
    if username and username.lower() == (message.from_user.username or "").lower():
        await message.answer(
            "🆔 <b>Твой Telegram ID</b>\n\n<code>" + str(message.from_user.id) + "</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=get_getid_keyboard(),
        )
        return

    # Попытка 1: Bot API (работает для каналов, групп, ботов и пользователей которых бот знает)
    try:
        chat = await message.bot.get_chat(f"@{username}")
        title = getattr(chat, "title", None) or getattr(chat, "full_name", None) or chat.username or "—"
        await message.answer(
            f"🆔 <b>{label}</b>\n\n"
            f"Название: {title}\n"
            f"ID: <code>{chat.id}</code>",
            parse_mode=ParseMode.HTML, reply_markup=get_getid_keyboard()
        )
        return
    except Exception:
        pass

    # Попытка 2: Telethon — работает для ЛЮБЫХ пользователей даже незнакомых боту
    try:
        from Bot import active_clients
        client = next(iter(active_clients.values()), None) if active_clients else None
        if client:
            entity = await client.get_entity(f"@{username}")
            if hasattr(entity, "id"):
                name = (
                    getattr(entity, "title", None)
                    or f"{getattr(entity, 'first_name', '') or ''} {getattr(entity, 'last_name', '') or ''}".strip()
                    or username
                )
                await message.answer(
                    f"🆔 <b>{label}</b>\n\n"
                    f"Название: {name}\n"
                    f"ID: <code>{entity.id}</code>",
                    parse_mode=ParseMode.HTML, reply_markup=get_getid_keyboard()
                )
                return
    except Exception as e:
        logging.error(f"❌ _resolve_and_reply_id Telethon(@{username}): {type(e).__name__}: {e}")

    logging.error(f"❌ _resolve_and_reply_id(@{username}): не найдено ни Bot API, ни Telethon")
    await message.answer(
        f"Не удалось получить ID для «{text}». Telegram не всегда раскрывает ботам ID произвольных пользователей по одному @username. "
        "Для своего ID нажми «Мой ID» или отправь /id. Для другого пользователя перешли его сообщение "
        "или попроси его открыть бота и отправить /start.",
        reply_markup=get_getid_keyboard()
    )


@router.message(ExtraStates.waiting_id_channel)
async def getid_channel_input(message: types.Message, state: FSMContext):
    await _resolve_and_reply_id(message, state, "Канал")

@router.message(ExtraStates.waiting_id_group)
async def getid_group_input(message: types.Message, state: FSMContext):
    await _resolve_and_reply_id(message, state, "Группа")

@router.message(ExtraStates.waiting_id_user)
async def getid_user_input(message: types.Message, state: FSMContext):
    await _resolve_and_reply_id(message, state, "Пользователь")

@router.message(ExtraStates.waiting_id_bot)
async def getid_bot_input(message: types.Message, state: FSMContext):
    await _resolve_and_reply_id(message, state, "Бот")


# ============ СООБЩИТЬ О БАГЕ ============

@router.callback_query(lambda c: c.data == "bugreport_menu")
async def bugreport_menu(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if is_tech_support_blocked(user_id):
        await callback.answer("❌ Доступ к этому разделу для тебя закрыт", show_alert=True)
        return
    await callback.answer()
    await state.set_state(ExtraStates.waiting_bug_report)
    await callback.message.edit_text(
        "🐛 <b>Сообщить о баге</b>\n\n"
        "Что-то работает не так? Опиши, что произошло и как это повторить. "
        "Сообщение сразу уйдёт администраторам — разберёмся.\n\n"
        "Напиши, в чём проблема:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="home_profile")]])
    )


@router.message(ExtraStates.waiting_bug_report)
async def bugreport_input(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()
    if is_tech_support_blocked(message.from_user.id):
        await state.clear()
        await message.answer("❌ Доступ к этому разделу для тебя закрыт", reply_markup=_back_to_main_kb())
        return
    if text.lower() in ("/cancel", "отмена"):
        await state.clear()
        await message.answer("❌ Отменено", reply_markup=_back_to_main_kb())
        return
    if not text:
        await message.answer("❌ Опиши баг текстом.")
        return
    
    await state.clear()
    user = message.from_user
    report_text = (
        f"🐛 <b>Новый баг-репорт</b>\n\n"
        f"Юз: @{user.username if user.username else '—'}\n"
        f"Айди: <code>{user.id}</code>\n"
        f"Сообщение пользователя:\n{text}"
    )
    sent_to_anyone = False
    for admin_id in get_all_admins():
        try:
            await message.bot.send_message(admin_id, report_text, parse_mode=ParseMode.HTML)
            sent_to_anyone = True
        except Exception as e:
            logging.error(f"❌ bugreport: не удалось отправить админу {admin_id}: {e}")
    
    if sent_to_anyone:
        await message.answer(
            "✅ Спасибо! Сообщение о баге отправлено администраторам.",
            reply_markup=_back_to_main_kb()
        )
    else:
        await message.answer(
            "⚠️ Сообщение принято, но не удалось уведомить администраторов (попробуй позже).",
            reply_markup=_back_to_main_kb()
        )


# ============ ВСПОМОГАТЕЛЬНОЕ ============

def _back_to_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="home_profile")],
    ])


# ============ ИНИЦИАЛИЗАЦИЯ ============

_extra_router_initialized = False


def init_extra_features(dp):
    global _extra_router_initialized
    if not _extra_router_initialized:
        dp.include_router(router)
        _extra_router_initialized = True
        logging.info("✅ Модуль доп. функций инициализирован (GitHub/Премиум/Айди/Баг-репорт)")


__all__ = [
    'router',
    'init_extra_features',
    'set_bot',
    'get_extra_main_buttons',
        ]
