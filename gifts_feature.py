"""
Модуль "Подарки": отправка обычных и лимитированных подарков за
звёзды Telegram — от лица ПОДКЛЮЧЁННОГО АККАУНТА пользователя (через
Telethon), а не от лица самого бота.

Логика отправки (payments.getPaymentForm + payments.sendStarsForm через
InputInvoiceStarGift) — та же, что и в отдельном скрипте gift_sender.py,
включая поддержку текстового описания (message=TextWithEntities(...)).

ВАЖНО (честно): методы сырого MTProto API для подарков (payments.getStarGifts,
payments.getPaymentForm с InputInvoiceStarGift, payments.sendStarsForm,
payments.getStarsStatus) реализованы по актуальной на момент написания
документации Telegram API, но не протестированы вживую — в песочнице
разработки нет доступа к реальному Telegram. Если точные названия полей
после деплоя не совпадут — потребуется быстрая правка на реальных данных.
"""

import logging
from types import SimpleNamespace
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

# Максимальная длина текстового описания к подарку (по правилам Telegram).
GIFT_MESSAGE_MAX_LEN = 255

# Лимитированные (сезонные) подарки, которые нужно всегда показывать в
# разделе "Лимитированные", даже если live-каталог Telegram сейчас их не
# отдаёт (сезон закончился и т.п.). Если Telegram всё же отдаёт подарок с
# таким id через GetStarGiftsRequest — используются РЕАЛЬНЫЕ данные оттуда
# (точная цена, стикер), а этот список — просто резервный источник
# названия, чтобы подарок не пропадал из раздела.
MANUAL_LIMITED_GIFTS: Dict[int, str] = {
    6046178578163303744: "Мишка те₽₽ор 2026",
    5974210632977745012: "Мишка футболист 2026",
    6026193266406327981: "Мишка с молотком 2026",
    5969796561943660080: "Мишка Пасха 2026",
    5935895822435615975: "Мишка клоун 2026",
    5893356958802511476: "Мишка день Патрика 2026",
    5866352046986232958: "Мишка 8 марта 2026",
    5800655655995968830: "Мишка к дню св.Валентина 2026",
    5801108895304779062: "Сердце ко дню св. Валентина 2026",
    5956217000635139069: "Мишка новогодний 2026",
    5922558454332916696: "Ёлка новогодняя 2026",
}


def setup(user_sessions: Dict[int, List[str]], get_connected_client_func):
    global _user_sessions, _get_connected_client
    _user_sessions = user_sessions
    _get_connected_client = get_connected_client_func


class GiftStates(StatesGroup):
    waiting_username = State()
    waiting_description = State()


# Черновик отправки: user_id -> {
#   "_catalog": {id: gift, ...},               # переживает смену подарка
#   "phone", "category",
#   "gift_id", "gift_title", "gift_stars",      # gift_stars может быть None
#   "recipient_username", "message",
# }
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
        balance_obj = result.balance
        # balance может быть объектом StarsAmount (поле .amount) или, в
        # зависимости от версии схемы, простым числом — поддерживаем оба.
        if hasattr(balance_obj, "amount"):
            return int(balance_obj.amount)
        return int(balance_obj)
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
    """Возвращает подарки, доступные к отправке ПРЯМО СЕЙЧАС — включая
    сезонные/лимитированные (февральские, первоапрельские и т.п. — они
    почти всегда помечены limited=True, но это не то же самое, что
    "редкие/коллекционные"). Исключаем только реально распроданные
    (sold_out) — их всё равно нельзя отправить, Telegram отклонит покупку
    на своей стороне.

    Дополнительно подмешивает MANUAL_LIMITED_GIFTS: если Telegram по
    какому-то id из этого списка живых данных не отдаёт (сезон закончился),
    подставляется "заглушка" с известным названием, но без цены — цену в
    этом случае Telegram посчитает сам на этапе оплаты, отправка всё равно
    может не пройти, если подарок реально снят с продажи навсегда — это
    ограничение Telegram, а не бота.
    """
    try:
        result = await client(functions.payments.GetStarGiftsRequest(hash=0))
        raw_gifts = getattr(result, "gifts", [])
        gifts = [g for g in raw_gifts if not getattr(g, "sold_out", False)]
    except Exception as e:
        logging.error(f"❌ _fetch_gift_catalog: {e}")
        gifts = []

    known_ids = {g.id for g in gifts}
    for gid, title in MANUAL_LIMITED_GIFTS.items():
        if gid not in known_ids:
            gifts.append(SimpleNamespace(
                id=gid,
                title=title,
                stars=None,
                limited=True,
                sold_out=False,
                sticker=None,
                manual=True,
            ))
    return gifts


def _gift_title(gift) -> str:
    title = getattr(gift, "title", None)
    if title:
        return title
    sticker = getattr(gift, "sticker", None)
    if sticker is not None:
        for attr in getattr(sticker, "attributes", []):
            alt = getattr(attr, "alt", None)
            if alt:
                return alt
    return f"Подарок #{gift.id}"


def _gift_price_label(gift) -> str:
    stars = getattr(gift, "stars", None)
    if stars is None:
        return "цена уточняется при отправке"
    return f"{stars} ⭐"


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

    gift_drafts[callback.from_user.id] = {"_catalog": {g.id: g for g in gifts}}

    normal_count = sum(1 for g in gifts if not getattr(g, "limited", False))
    limited_count = sum(1 for g in gifts if getattr(g, "limited", False))

    balance_line = f"💰 Баланс: {balance} ⭐\n" if balance is not None else ""
    nft_line = f"🖼 NFT-подарков: {nft_count}\n" if nft_count is not None else ""

    await callback.message.edit_text(
        f"📱 <b>{phone}</b>\n\n{balance_line}{nft_line}\n"
        f"Выберите раздел подарков:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🎁 Обычные ({normal_count})", callback_data=f"gift_cat_{phone}_normal")],
            [InlineKeyboardButton(text=f"⏳ Лимитированные ({limited_count})", callback_data=f"gift_cat_{phone}_limited")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="gifts_menu")],
        ])
    )


async def _render_gift_category(target, user_id: int, phone: str, category: str, catalog: Dict):
    """Рисует список подарков категории. target — CallbackQuery или Message
    (для message используется .answer, для callback — .message.edit_text)."""
    if category == "limited":
        gifts = [g for g in catalog.values() if getattr(g, "limited", False)]
        title = "⏳ Лимитированные"
        hint = "Сезонные подарки — добавлены на ограниченное время.\n\n"
    else:
        gifts = [g for g in catalog.values() if not getattr(g, "limited", False)]
        title = "🎁 Обычные"
        hint = ""

    if not gifts:
        text = f"{title}\n\n❌ В этой категории пока нет подарков."
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"gift_acc_{phone}")]
        ])
    else:
        buttons = []
        for g in gifts[:30]:
            gtitle = _gift_title(g)
            # Для лимитированных подарков цена всегда 50⭐
            if category == "limited":
                price_label = "50 ⭐"
            else:
                price_label = _gift_price_label(g)
            # Лимитированным кнопкам задаём цветной стиль
            if category == "limited":
                btn = InlineKeyboardButton(
                    text=f"{gtitle} — {price_label}",
                    callback_data=f"gift_pick_{phone}_{g.id}",
                    style=ButtonStyle.SUCCESS
                )
            else:
                btn = InlineKeyboardButton(
                    text=f"{gtitle} — {price_label}",
                    callback_data=f"gift_pick_{phone}_{g.id}"
                )
            buttons.append([btn])
        buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"gift_acc_{phone}")])
        # Для лимитированных — показываем номер телефона в формате "Выбранная сессия"
        if category == "limited":
            header = f"Выбранная сессия: 📱+{phone.lstrip('+')}"
        else:
            header = f"📱 <b>{phone}</b>"
        text = f"{header}\n\n{title}\n\n{hint}Выберите подарок для отправки:"
        markup = InlineKeyboardMarkup(inline_keyboard=buttons)

    if isinstance(target, types.CallbackQuery):
        await target.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=markup)
    else:
        await target.answer(text, parse_mode=ParseMode.HTML, reply_markup=markup)


@router.callback_query(lambda c: c.data.startswith("gift_cat_"))
async def gift_category_selected(callback: types.CallbackQuery):
    rest = callback.data.replace("gift_cat_", "")
    phone, _, category = rest.rpartition("_")
    user_id = callback.from_user.id

    catalog = gift_drafts.get(user_id, {}).get("_catalog", {})
    if not catalog:
        await callback.answer("❌ Каталог устарел, начни заново", show_alert=True)
        await callback.message.edit_text(
            "❌ Каталог устарел",
            reply_markup=get_gifts_accounts_keyboard(user_id)
        )
        return

    await callback.answer()
    await _render_gift_category(callback, user_id, phone, category, catalog)


@router.callback_query(lambda c: c.data.startswith("gift_pick_"))
async def gift_pick(callback: types.CallbackQuery, state: FSMContext):
    rest = callback.data.replace("gift_pick_", "")
    phone, _, gift_id_str = rest.rpartition("_")
    gift_id = int(gift_id_str)
    user_id = callback.from_user.id

    draft = gift_drafts.setdefault(user_id, {})
    catalog = draft.get("_catalog", {})
    gift = catalog.get(gift_id)
    if not gift:
        await callback.answer("❌ Каталог устарел, начни заново", show_alert=True)
        return

    await callback.answer()

    category = "limited" if getattr(gift, "limited", False) else "normal"
    # Сохраняем каталог, чтобы можно было вернуться и поменять подарок,
    # не перезапрашивая его у Telegram заново.
    draft.clear()
    draft["_catalog"] = catalog
    draft.update({
        "phone": phone,
        "category": category,
        "gift_id": gift_id,
        "gift_title": _gift_title(gift),
        "gift_stars": getattr(gift, "stars", None),
    })
    draft.pop("recipient_username", None)
    draft.pop("message", None)

    await state.set_state(GiftStates.waiting_username)
    await callback.message.edit_text(
        f"🎁 {draft['gift_title']} — {_gift_price_label(gift)}\n\n"
        f"Введите юзернейм получателя (без @), или перешлите его сообщение:\n\n"
        f"/cancel — отменить"
    )


def _build_gift_info_text(draft: Dict) -> str:
    stars_label = _gift_price_label(SimpleNamespace(stars=draft.get("gift_stars")))
    description = draft.get("message") or "нет"
    return (
        f"👤 Кому отправится: @{draft['recipient_username']}\n"
        f"🎁 Подарок: {draft['gift_title']}\n"
        f"💰 Стоимость подарка: {stars_label}\n"
        f"📝 Описание подарка: {description}"
    )


def _build_gift_menu_keyboard(draft: Dict) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="✅ Отправить", callback_data="gift_send", style=ButtonStyle.SUCCESS)],
        [InlineKeyboardButton(text="👤 Ввести юзернейм", callback_data="gift_edit_username")],
    ]
    if draft.get("message"):
        rows.append([InlineKeyboardButton(text="🗑 Убрать описание подарка", callback_data="gift_remove_desc")])
    else:
        rows.append([InlineKeyboardButton(text="📝 Добавить описание подарка", callback_data="gift_add_desc")])
    rows.append([InlineKeyboardButton(text="🔄 Поменять подарок", callback_data="gift_change")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="gift_cancel", style=ButtonStyle.DANGER)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_gift_menu(target, draft: Dict):
    text = _build_gift_info_text(draft) + "\n\nЧто дальше?"
    markup = _build_gift_menu_keyboard(draft)
    if isinstance(target, types.CallbackQuery):
        await target.message.edit_text(text, reply_markup=markup)
    else:
        await target.answer(text, reply_markup=markup)


@router.message(GiftStates.waiting_username)
async def gift_username_input(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()
    user_id = message.from_user.id

    if text.lower() in ("/cancel", "отмена"):
        await state.clear()
        draft = gift_drafts.get(user_id)
        if draft and "recipient_username" in draft:
            # Это была правка юзернейма из меню — просто возвращаемся в меню.
            await _show_gift_menu(message, draft)
        else:
            gift_drafts.pop(user_id, None)
            await message.answer("❌ Отменено", reply_markup=get_gifts_accounts_keyboard(user_id))
        return

    draft = gift_drafts.get(user_id)
    if not draft or "gift_id" not in draft:
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
    await _show_gift_menu(message, draft)


@router.callback_query(lambda c: c.data == "gift_edit_username")
async def gift_edit_username(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    draft = gift_drafts.get(user_id)
    if not draft or "gift_id" not in draft:
        await callback.answer("❌ Сессия устарела", show_alert=True)
        return

    await callback.answer()
    await state.set_state(GiftStates.waiting_username)
    await callback.message.edit_text(
        "👤 Введите новый юзернейм получателя (без @), или перешлите его сообщение:\n\n"
        "/cancel — вернуться назад"
    )


@router.callback_query(lambda c: c.data == "gift_add_desc")
async def gift_add_desc(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    draft = gift_drafts.get(user_id)
    if not draft or "recipient_username" not in draft:
        await callback.answer("❌ Сессия устарела", show_alert=True)
        return

    await callback.answer()
    await state.set_state(GiftStates.waiting_description)
    await callback.message.edit_text(
        f"📝 Введите описание к подарку (до {GIFT_MESSAGE_MAX_LEN} символов):\n\n"
        f"/cancel — вернуться назад без описания"
    )


@router.message(GiftStates.waiting_description)
async def gift_description_input(message: types.Message, state: FSMContext):
    text = (message.text or "").strip()
    user_id = message.from_user.id

    draft = gift_drafts.get(user_id)
    if not draft or "recipient_username" not in draft:
        await state.clear()
        await message.answer("❌ Сессия устарела, начни заново.")
        return

    if text.lower() in ("/cancel", "отмена"):
        await state.clear()
        await _show_gift_menu(message, draft)
        return

    if not text:
        await message.answer("❌ Описание не может быть пустым. Пришли текст или /cancel.")
        return

    if len(text) > GIFT_MESSAGE_MAX_LEN:
        await message.answer(
            f"❌ Слишком длинное описание ({len(text)} символов). "
            f"Максимум — {GIFT_MESSAGE_MAX_LEN}. Попробуй ещё раз."
        )
        return

    draft["message"] = text
    await state.clear()
    await _show_gift_menu(message, draft)


@router.callback_query(lambda c: c.data == "gift_remove_desc")
async def gift_remove_desc(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    draft = gift_drafts.get(user_id)
    if not draft or "recipient_username" not in draft:
        await callback.answer("❌ Сессия устарела", show_alert=True)
        return

    draft.pop("message", None)
    await callback.answer("🗑 Описание убрано")
    await _show_gift_menu(callback, draft)


@router.callback_query(lambda c: c.data == "gift_change")
async def gift_change(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    draft = gift_drafts.get(user_id)
    if not draft or "phone" not in draft:
        await callback.answer("❌ Сессия устарела", show_alert=True)
        return

    catalog = draft.get("_catalog", {})
    if not catalog:
        await callback.answer("❌ Каталог устарел, начни заново", show_alert=True)
        await callback.message.edit_text(
            "❌ Каталог устарел",
            reply_markup=get_gifts_accounts_keyboard(user_id)
        )
        return

    await callback.answer()
    await _render_gift_category(callback, user_id, draft["phone"], draft.get("category", "normal"), catalog)


@router.callback_query(lambda c: c.data == "gift_cancel")
async def gift_cancel(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer("❌ Отменено")
    gift_drafts.pop(callback.from_user.id, None)
    await callback.message.edit_text(
        "❌ Отправка отменена.",
        reply_markup=get_gifts_accounts_keyboard(callback.from_user.id)
    )


@router.callback_query(lambda c: c.data == "gift_send")
async def gift_send(callback: types.CallbackQuery):
    """Экран финального подтверждения — сам подарок ещё НЕ отправляется."""
    user_id = callback.from_user.id
    draft = gift_drafts.get(user_id)
    if not draft or "recipient_username" not in draft:
        await callback.answer("❌ Сессия устарела", show_alert=True)
        return

    await callback.answer()
    text = _build_gift_info_text(draft) + "\n\nПодтвердите отправку."
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data="gift_confirm", style=ButtonStyle.SUCCESS)],
            [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="gift_back_to_menu")],
        ])
    )


@router.callback_query(lambda c: c.data == "gift_back_to_menu")
async def gift_back_to_menu(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    draft = gift_drafts.get(user_id)
    if not draft or "recipient_username" not in draft:
        await callback.answer("❌ Сессия устарела", show_alert=True)
        return

    await callback.answer()
    await _show_gift_menu(callback, draft)


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

        # Описание подарка (как в gift_sender.py) — TextWithEntities.
        msg = None
        if draft.get("message"):
            msg = tl_types.TextWithEntities(text=draft["message"], entities=[])

        invoice = tl_types.InputInvoiceStarGift(
            peer=recipient_peer,
            gift_id=draft["gift_id"],
            message=msg,
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
