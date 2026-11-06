"""Crypto Pay integration for lifetime Premium.

Invoices are denominated in RUB and can be paid with USDT or TON.
Every invoice expires after 15 minutes and is then deleted through Crypto Pay.
"""
import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any

import aiohttp
from aiogram import Router, types
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from access_control import grant_premium
from i18n import tr

router = Router()
API_BASE_URL = os.getenv("CRYPTO_PAY_API_URL", "https://pay.crypt.bot/api").rstrip("/")
API_TOKEN = os.getenv("CRYPTO_PAY_API_TOKEN", "").strip()
PAYMENTS_FILE = os.getenv("PREMIUM_PAYMENTS_FILE", "data/premium_payments.json")
INVOICE_LIFETIME_SECONDS = 15 * 60
_polling_task: asyncio.Task | None = None
_create_lock = asyncio.Lock()


def _load_payments() -> dict[str, dict[str, Any]]:
    try:
        with open(PAYMENTS_FILE, "r", encoding="utf-8") as source:
            data = json.load(source)
        if isinstance(data, dict):
            return {str(key): value for key, value in data.items() if isinstance(value, dict)}
    except FileNotFoundError:
        pass
    except Exception:
        logging.exception("Не удалось загрузить реестр Premium-платежей")
    return {}


payments: dict[str, dict[str, Any]] = _load_payments()


def _save_payments() -> None:
    parent = os.path.dirname(PAYMENTS_FILE)
    if parent:
        os.makedirs(parent, exist_ok=True)
    temporary = PAYMENTS_FILE + ".tmp"
    with open(temporary, "w", encoding="utf-8") as target:
        json.dump(payments, target, ensure_ascii=False, indent=2)
    os.replace(temporary, PAYMENTS_FILE)


async def _crypto_pay(method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if not API_TOKEN:
        raise RuntimeError("CRYPTO_PAY_API_TOKEN is not configured")
    timeout = aiohttp.ClientTimeout(total=15, connect=8)
    headers = {
        "Crypto-Pay-API-Token": API_TOKEN,
        "Accept": "application/json",
    }
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            f"{API_BASE_URL}/{method}",
            json=payload or {},
            headers=headers,
        ) as response:
            try:
                data = await response.json(content_type=None)
            except Exception as exc:
                raise RuntimeError(f"Crypto Pay returned non-JSON response ({response.status})") from exc
            if response.status != 200 or not isinstance(data, dict) or not data.get("ok"):
                error = data.get("error", "unknown_error") if isinstance(data, dict) else "invalid_response"
                raise RuntimeError(f"Crypto Pay {method} failed: {error}")
            result = data.get("result")
            return result if isinstance(result, dict) else {"items": result}


def _invoice_items(result: dict[str, Any]) -> list[dict[str, Any]]:
    items = result.get("items", [])
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    if isinstance(items, dict):
        return [items]
    return []


async def _fetch_invoice(invoice_id: str) -> dict[str, Any] | None:
    result = await _crypto_pay("getInvoices", {"invoice_ids": str(invoice_id)})
    items = _invoice_items(result)
    return items[0] if items else None


async def _notify_expired(bot, record: dict[str, Any]) -> None:
    user_id = int(record["user_id"])
    chat_id = int(record.get("chat_id") or user_id)
    message_id = record.get("message_id")
    if message_id:
        try:
            await bot.delete_message(chat_id, int(message_id))
        except Exception:
            logging.info("Не удалось удалить Telegram-сообщение со счётом %s", record.get("invoice_id"))
    try:
        await bot.send_message(user_id, tr(user_id, "payment_expired"), parse_mode=ParseMode.HTML)
    except Exception:
        logging.exception("Не удалось отправить уведомление об истечении счёта пользователю %s", user_id)


async def _activate_paid_invoice(bot, invoice_id: str, record: dict[str, Any]) -> None:
    if record.get("status") == "paid":
        return
    user_id = int(record["user_id"])
    # days=None записывает expires_at=null, то есть Premium без ограничения по времени.
    grant_premium(user_id, granted_by=None, days=None)
    record["status"] = "paid"
    record["paid_at"] = int(time.time())
    _save_payments()

    chat_id = int(record.get("chat_id") or user_id)
    message_id = record.get("message_id")
    if message_id:
        try:
            await bot.edit_message_text(
                tr(user_id, "payment_invoice_paid"),
                chat_id=chat_id,
                message_id=int(message_id),
                parse_mode=ParseMode.HTML,
                reply_markup=None,
            )
            return
        except Exception:
            pass
    try:
        await bot.send_message(user_id, tr(user_id, "payment_success"), parse_mode=ParseMode.HTML)
    except Exception:
        logging.exception("Не удалось уведомить пользователя %s об оплате счёта %s", user_id, invoice_id)


async def _payment_worker(bot) -> None:
    logging.info("Crypto Pay invoice monitor started (15-minute invoices)")
    while True:
        try:
            now = int(time.time())
            for invoice_id, record in list(payments.items()):
                if record.get("status") != "active":
                    continue
                try:
                    invoice = await _fetch_invoice(invoice_id)
                    api_status = (invoice or {}).get("status")
                    if api_status == "paid":
                        await _activate_paid_invoice(bot, invoice_id, record)
                        continue

                    if now >= int(record.get("expires_at", 0)) or api_status == "expired":
                        if api_status == "active":
                            try:
                                await _crypto_pay("deleteInvoice", {"invoice_id": int(invoice_id)})
                            except Exception:
                                logging.exception("Не удалось удалить истёкший Crypto Pay invoice %s", invoice_id)
                                continue
                        record["status"] = "expired"
                        record["expired_at"] = now
                        _save_payments()
                        await _notify_expired(bot, record)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logging.exception("Ошибка проверки Crypto Pay invoice %s", invoice_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("Ошибка фонового мониторинга Crypto Pay")
        await asyncio.sleep(8)


def init_premium_payments(dispatcher, bot) -> None:
    global _polling_task
    dispatcher.include_router(router)
    if API_TOKEN and (_polling_task is None or _polling_task.done()):
        _polling_task = asyncio.create_task(_payment_worker(bot))
    elif not API_TOKEN:
        logging.warning("Crypto Pay отключён: добавь CRYPTO_PAY_API_TOKEN в переменные окружения Bothost")


@router.callback_query(lambda callback: callback.data == "premium_buy_crypto")
async def premium_buy_crypto(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    await callback.answer()

    if not API_TOKEN:
        await callback.message.answer(tr(user_id, "payment_not_configured"), parse_mode=ParseMode.HTML)
        return

    async with _create_lock:
        now = int(time.time())
        # Не плодим несколько действующих счетов для одного пользователя.
        for invoice_id, record in payments.items():
            if int(record.get("user_id", 0)) == user_id and record.get("status") == "active":
                if int(record.get("expires_at", 0)) > now and record.get("invoice_url"):
                    markup = InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(text=tr(user_id, "payment_open_invoice"), url=record["invoice_url"])
                    ]])
                    await callback.message.answer(
                        tr(user_id, "payment_existing"),
                        reply_markup=markup,
                    )
                    return

        payload_id = f"premium_lifetime:{user_id}:{uuid.uuid4().hex}"
        try:
            invoice = await _crypto_pay("createInvoice", {
                "currency_type": "fiat",
                "fiat": "RUB",
                "amount": "300",
                "accepted_assets": "USDT,TON",
                "description": "Premium — пожизненный доступ",
                "payload": payload_id,
                "allow_comments": False,
                "allow_anonymous": False,
                "expires_in": INVOICE_LIFETIME_SECONDS,
            })
        except Exception:
            logging.exception("Не удалось создать Crypto Pay invoice для пользователя %s", user_id)
            await callback.message.answer(
                tr(user_id, "payment_create_error"),
                parse_mode=ParseMode.HTML,
            )
            return

        invoice_id = str(invoice.get("invoice_id", ""))
        invoice_url = invoice.get("bot_invoice_url") or invoice.get("pay_url")
        if not invoice_id or not invoice_url:
            logging.error("Crypto Pay вернул неполный объект счёта: invoice_id/url отсутствует")
            await callback.message.answer(tr(user_id, "payment_invalid_invoice"), parse_mode=ParseMode.HTML)
            return

        record = {
            "invoice_id": invoice_id,
            "user_id": user_id,
            "chat_id": callback.message.chat.id,
            "message_id": None,
            "invoice_url": invoice_url,
            "created_at": now,
            "expires_at": now + INVOICE_LIFETIME_SECONDS,
            "status": "active",
            "payload": payload_id,
        }
        payments[invoice_id] = record
        _save_payments()

        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=tr(user_id, "payment_invoice_button"), url=invoice_url)
        ]])
        sent = await callback.message.answer(
            tr(user_id, "payment_invoice_text"),
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )
        record["message_id"] = sent.message_id
        record["chat_id"] = sent.chat.id
        _save_payments()
