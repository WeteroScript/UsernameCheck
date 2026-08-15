"""
Модуль "Подарки": отправка обычных (не NFT, не редких) подарков за
звёзды Telegram — от лица ПОДКЛЮЧЁННОГО АККАУНТА пользователя (через
Telethon), а не от лица самого бота.

ВАЖНО (честно): методы сырого MTProto API для подарков (payments.getStarGifts,
payments.getPaymentForm с InputInvoiceStarGift, payments.sendStarsForm,
payments.getStarsStatus) реализованы по актуальной на момент написания
документации Telegram API, но не протестированы вживую — в песочнице
разработки нет доступа к реальному Telegram. Если точные названия полей
после деплоя не совпадут — потребуется быстрая правка на реальных данных.
"""

import logging
from aiogram import Router, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode, ButtonStyle
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from telethon import functions, types as tl_types
from typing import Dict, List, Optional

router = Router()

# Заполняются из Bot.py при инициализации — свои копии, чтобы не тащить
# сюда прямую зависимость от Bot.py (которая и так уже импортирует
# множество модулей).
_user_sessions: Dict[int, List[str]] = {}
_get_connected_client = None  # async def(phone) -> TelegramClient | None


def setup(user_sessions: Dict[int, List[str]], get_connected_client_func):
    global _user_sessions, _get_connected_client
    _user_sessions = user_sessions
    _get_connected_client = get_connected_client_func


class GiftStates(StatesGroup):
    waiting_username = State()


# Черновик отправки: user_id -> {"phone", "gift_id", "gift_title", "gift_stars"}
gift_drafts: Dict[int, Dict] = {}


def get_gifts_accounts_keyboard(user_id: int) -> InlineKeyboardMarkup:
    phones = _user_sessions.get(user_id, [])
    buttons = [
        [InlineKeyboardButton(text=f"📱 {phone}", callback_data=f"gift_acc_{phone}")]
        for phone in phones
    ]
    if not buttons:
        buttons.append([InlineKeyboardButton(text="❌ Нет подключённых аккаунтов", callback_data="no_action")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.callback_query(lambda c: c.data == "gifts_menu")
async def gifts_menu(callback: types.CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "🎁 <b>Подарки</b>\n\nВыберите аккаунт:",
        parse_mode=ParseMode.HTML,
        reply_markup=get_gifts_accounts_keyboard(callback.from_user.id)
    )


async def _fetch_stars_balance(client) -> Optional[int]:
    try:
        me = await client.get_me()
        result = await client(functions.payments.GetStarsStatusRequest(peer=me))
        return int(result.balance.amount)
    except Exception as e:
        logging.error(f"❌ _fetch_stars_balance: {e}")
        return None


async def _fetch_nft_count(client) -> Optional[int]:
    try:
        me = await client.get_me()
        result = await client(functions.payments.GetSavedStarGiftsRequest(
            peer=me, offset="", limit=100
        ))
        count = sum(1 for g in result.gifts if isinstance(getattr(g, "gift", None), tl_types.StarGiftUnique))
        return count
    except Exception as e:
        logging.error(f"❌ _fetch_nft_count: {e}")
        return None


async def _fetch_gift_catalog(client) -> List:
    """Возвращает только обычные (не лимитированные/редкие) подарки —
    исключаем limited=True, чтобы не предлагать редкие/коллекционные."""
    try:
        result = await client(functions.payments.GetStarGiftsRequest(hash=0))
        gifts = getattr(result, "gifts", [])
        return [g for g in gifts if not getattr(g, "limited", False) and not getattr(g, "sold_out", False)]
    except Exception as e:
        logging.error(f"❌ _fetch_gift_catalog: {e}")
        return []


def _gift_title(gift) -> str:
    sticker = getattr(gift, "sticker", None)
    if sticker is not None:
        for attr in getattr(sticker, "attributes", []):
            alt = getattr(attr, "alt", None)
            if alt:
                return alt
    return f"Подарок #{gift.id}"


@router.callback_query(lambda c: c.data.startswith("gift_acc_"))
async def gift_account_selected(callback: types.CallbackQuery):
    phone = callback.data.replace("gift_acc_", "")
    await callback.answer("🔎 Загружаю...")
    
    client = await _get_connected_client(phone)
    if not client:
        await callback.message.edit_text(
            "❌ Аккаунт не подключён",
            reply_markup=get_gifts_accounts_keyboard(callback.from_user.id)
        )
        return
    
    balance = await _fetch_stars_balance(client)
    nft_count = await _fetch_nft_count(client)
    gifts = await _fetch_gift_catalog(client)
    
    if not gifts:
        await callback.message.edit_text(
            "❌ Не удалось загрузить каталог подарков (или он пуст).",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="gifts_menu")]
            ])
        )
        return
    
    buttons = []
    for g in gifts[:30]:
        title = _gift_title(g)
        buttons.append([InlineKeyboardButton(
            text=f"{title} — {g.stars} ⭐",
            callback_data=f"gift_pick_{phone}_{g.id}"
        )])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="gifts_menu")])
    
    balance_line = f"💰 Баланс: {balance} ⭐\n" if balance is not None else ""
    nft_line = f"🖼 NFT-подарков: {nft_count}\n" if nft_count is not None else ""
    
    await callback.message.edit_text(
        f"📱 <b>{phone}</b>\n\n{balance_line}{nft_line}\n"
        f"Выберите подарок для отправки:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )
    gift_drafts.setdefault(callback.from_user.id, {})["_catalog"] = {g.id: g for g in gifts}


@router.callback_query(lambda c: c.data.startswith("gift_pick_"))
async def gift_pick(callback: types.CallbackQuery, state: FSMContext):
    rest = callback.data.replace("gift_pick_", "")
    phone, _, gift_id_str = rest.rpartition("_")
    gift_id = int(gift_id_str)
    user_id = callback.from_user.id
    
    catalog = gift_drafts.get(user_id, {}).get("_catalog", {})
    gift = catalog.get(gift_id)
    if not gift:
        await callback.answer("❌ Каталог устарел, начни заново", show_alert=True)
        return
    
    await callback.answer()
    gift_drafts[user_id] = {
        "phone": phone,
        "gift_id": gift_id,
        "gift_title": _gift_title(gift),
        "gift_stars": gift.stars,
    }
    await state.set_state(GiftStates.waiting_username)
    await callback.message.edit_text(
        f"🎁 {_gift_title(gift)} — {gift.stars} ⭐\n\n"
        f"Введите юзернейм получателя (без @), или перешлите его сообщение:\n\n"
        f"/cancel — отменить"
    )


@router.message(GiftStates.waiting_username)
async def gift_username_input(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()
    user_id = message.from_user.id
    
    if text.lower() in ("/cancel", "отмена"):
        await state.clear()
        gift_drafts.pop(user_id, None)
        await message.answer("❌ Отменено", reply_markup=get_gifts_accounts_keyboard(user_id))
        return
    
    draft = gift_drafts.get(user_id)
    if not draft:
        await state.clear()
        await message.answer("❌ Сессия устарела, начни заново.")
        return
    
    username = None
    if message.forward_from and message.forward_from.username:
        username = message.forward_from.username
    elif text:
        username = text.lstrip('@')
        if "t.me/" in username:
            username = username.split("t.me/")[-1].split("/")[0].split("?")[0]
    
    if not username:
        await message.answer("❌ Пришли @username получателя, или перешли его сообщение.")
        return
    
    await state.clear()
    draft["recipient_username"] = username
    
    await message.answer(
        f"🎁 Подарок: {draft['gift_title']}\n"
        f"💰 Стоимость: {draft['gift_stars']} ⭐\n"
        f"👤 Юзернейм отправителю: @{username}\n\n"
        f"Подтвердите отправку.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data="gift_confirm", style=ButtonStyle.SUCCESS)],
            [InlineKeyboardButton(text="❌ Отменить", callback_data="gift_cancel", style=ButtonStyle.DANGER)],
        ])
    )


@router.callback_query(lambda c: c.data == "gift_cancel")
async def gift_cancel(callback: types.CallbackQuery):
    await callback.answer("❌ Отменено")
    gift_drafts.pop(callback.from_user.id, None)
    await callback.message.edit_text(
        "❌ Отправка отменена.",
        reply_markup=get_gifts_accounts_keyboard(callback.from_user.id)
    )


@router.callback_query(lambda c: c.data == "gift_confirm")
async def gift_confirm(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    draft = gift_drafts.get(user_id)
    if not draft or "recipient_username" not in draft:
        await callback.answer("❌ Сессия устарела", show_alert=True)
        return
    
    await callback.answer("🎁 Отправляю...")
    
    client = await _get_connected_client(draft["phone"])
    if not client:
        await callback.message.edit_text("❌ Аккаунт не подключён")
        return
    
    try:
        recipient = await client.get_entity(draft["recipient_username"])
        recipient_peer = await client.get_input_entity(recipient)
        
        invoice = tl_types.InputInvoiceStarGift(
            peer=recipient_peer,
            gift_id=draft["gift_id"],
        )
        form = await client(functions.payments.GetPaymentFormRequest(invoice=invoice))
        await client(functions.payments.SendStarsFormRequest(
            form_id=form.form_id,
            invoice=invoice,
        ))
        
        await callback.message.edit_text(
            f"✅ Подарок «{draft['gift_title']}» отправлен пользователю "
            f"@{draft['recipient_username']}!",
            reply_markup=get_gifts_accounts_keyboard(user_id)
        )
    except Exception as e:
        logging.error(f"❌ gift_confirm: {e}")
        await callback.message.edit_text(f"❌ Ошибка отправки подарка: {e}")
    finally:
        gift_drafts.pop(user_id, None)


# ============ ИНИЦИАЛИЗАЦИЯ ============

_gifts_router_initialized = False


def init_gifts_feature(dp):
    global _gifts_router_initialized
    if not _gifts_router_initialized:
        dp.include_router(router)
        _gifts_router_initialized = True
        logging.info("✅ Модуль 'Подарки' инициализирован")


__all__ = [
    'router',
    'init_gifts_feature',
    'setup',
  ]
