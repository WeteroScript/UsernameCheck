"""
Раздел «Чат с ИИ» на базе совместимого с OpenAI Chat Completions API TokenPlan.
История хранится отдельно по Telegram user_id в JSON-файле.
"""

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp
from aiogram import Router, types
from aiogram.enums import ButtonStyle
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from dotenv import load_dotenv

load_dotenv()

router = Router(name="ai_chat")

DEFAULT_API_URL = "https://tokenplan.lat/v1/chat/completions"
API_KEY_ENV = "TOKENPLAN_API_KEY"
HISTORY_PATH = Path(os.getenv("AI_CHAT_HISTORY_FILE", "data/ai_chats.json"))

MAX_CONTEXT_MESSAGES = 40
TELEGRAM_TEXT_LIMIT = 4000
SYSTEM_PROMPT = (
    "Ты — полезный ИИ-помощник. Отвечай на языке пользователя. "
    "Если пользователь просит код, приводи полный и понятный пример."
)

MODELS = [
    "claude-opus-5-5",
    "claude-sonnet-5-5",
    "claude-haiku-5-5",
    "claude-opus-4-7",
    "gpt-6-astra",
    "gpt-6-sol",
    "gpt-5.5",
    "gemini-4-argon",
    "gemini-3.8-flash",
    "deepseek-v4.1-flash",
    "deepseek-v4-pro",
    "kimi-k3.1-pro",
    "kimi-k3",
    "kimi-k2.8",
    "kimi-k2.7",
    "kimi-k2.6",
    "glm-5.3",
    "qwen3.8-max",
    "minimax-m3",
    "hy3",
]

chat_history: Dict[str, List[Dict[str, Any]]] = {}


class AIChatStates(StatesGroup):
    chatting = State()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_history() -> Dict[str, List[Dict[str, Any]]]:
    if not HISTORY_PATH.exists():
        return {}
    try:
        with HISTORY_PATH.open("r", encoding="utf-8") as file:
            raw = json.load(file)
        if not isinstance(raw, dict):
            raise ValueError("Корневой элемент истории должен быть объектом")

        result: Dict[str, List[Dict[str, Any]]] = {}
        for user_id, chats in raw.items():
            if isinstance(chats, list):
                result[str(user_id)] = [
                    chat for chat in chats
                    if isinstance(chat, dict)
                    and isinstance(chat.get("id"), str)
                    and isinstance(chat.get("messages", []), list)
                ]
        return result
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logging.error("Не удалось загрузить историю ИИ-чатов: %s", exc)
        return {}


def _save_history() -> bool:
    temp_path = HISTORY_PATH.with_name(HISTORY_PATH.name + ".tmp")
    try:
        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with temp_path.open("w", encoding="utf-8") as file:
            json.dump(chat_history, file, indent=2, ensure_ascii=False)
        os.replace(temp_path, HISTORY_PATH)
        return True
    except OSError:
        logging.exception("Не удалось сохранить историю ИИ-чатов")
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def init_ai_chat(dispatcher) -> None:
    """Загружает историю и подключает роутер к Dispatcher."""
    global chat_history
    chat_history = _load_history()
    dispatcher.include_router(router)
    logging.info("Раздел «Чат с ИИ» подключён; сохранено пользователей: %s", len(chat_history))


def _user_chats(user_id: int) -> List[Dict[str, Any]]:
    return chat_history.setdefault(str(user_id), [])


def _find_chat(user_id: int, chat_id: str) -> Optional[Dict[str, Any]]:
    for chat in _user_chats(user_id):
        if chat.get("id") == chat_id:
            return chat
    return None


def _root_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="➕ Новый чат",
                callback_data="ai_new",
                style=ButtonStyle.SUCCESS,
            ),
            InlineKeyboardButton(
                text="📚 Мои чаты",
                callback_data="ai_my_chats",
                style=ButtonStyle.PRIMARY,
            ),
        ],
        [
            InlineKeyboardButton(
                text="🏠 Главное меню",
                callback_data="main",
                style=ButtonStyle.DANGER,
            )
        ],
    ])


def _new_chat_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🎛 Выбрать модель",
                callback_data="ai_choose_model",
                style=ButtonStyle.SUCCESS,
            )
        ],
        [
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data="ai_menu",
                style=ButtonStyle.PRIMARY,
            )
        ],
    ])


def _model_style(model: str) -> ButtonStyle:
    if model.startswith("claude"):
        return ButtonStyle.DANGER
    if model.startswith(("gpt", "deepseek", "minimax")):
        return ButtonStyle.SUCCESS
    return ButtonStyle.PRIMARY


def _models_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=model,
            callback_data=f"ai_model_{model}",
            style=_model_style(model),
        )
        for model in MODELS
    ]
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    rows.append([
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data="ai_new",
            style=ButtonStyle.DANGER,
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _chat_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📚 Мои чаты",
                callback_data="ai_my_chats",
                style=ButtonStyle.PRIMARY,
            ),
            InlineKeyboardButton(
                text="➕ Новый чат",
                callback_data="ai_new",
                style=ButtonStyle.SUCCESS,
            ),
        ],
        [
            InlineKeyboardButton(
                text="🏠 Главное меню",
                callback_data="main",
                style=ButtonStyle.DANGER,
            )
        ],
    ])


def _short_title(value: str, limit: int = 32) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) > limit:
        return value[:limit - 1] + "…"
    return value or "Новый чат"


def _my_chats_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rows = []
    chats = sorted(
        _user_chats(user_id),
        key=lambda item: item.get("updated_at", ""),
        reverse=True,
    )
    for chat in chats[:30]:
        title = _short_title(str(chat.get("title", "Новый чат")), 27)
        model = str(chat.get("model", ""))
        model_label = model[:18]
        rows.append([
            InlineKeyboardButton(
                text=f"💬 {title} · {model_label}",
                callback_data=f"ai_open_{chat['id']}",
                style=ButtonStyle.PRIMARY,
            )
        ])
    rows.extend([
        [
            InlineKeyboardButton(
                text="➕ Новый чат",
                callback_data="ai_new",
                style=ButtonStyle.SUCCESS,
            )
        ],
        [
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data="ai_menu",
                style=ButtonStyle.DANGER,
            )
        ],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _chat_transcript(chat: Dict[str, Any]) -> str:
    lines = [
        f"💬 {chat.get('title', 'Новый чат')}",
        f"Модель: {chat.get('model', 'неизвестна')}",
        "",
    ]
    messages = chat.get("messages", [])[-12:]
    if not messages:
        lines.append("История пока пустая. Отправь первое сообщение.")
    else:
        for item in messages:
            role = "Ты" if item.get("role") == "user" else "ИИ"
            content = str(item.get("content", "")).strip()
            lines.append(f"{role}: {content}")
            lines.append("")
    text = "\n".join(lines).strip()
    if len(text) > 3600:
        text = "…\n" + text[-3580:]
    return text


def _split_text(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> List[str]:
    if not text:
        return ["Пустой ответ от API."]
    chunks = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = remaining.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _extract_reply(data: Dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError("API не вернул список choices")
    message = choices[0].get("message", {})
    content = message.get("content", "") if isinstance(message, dict) else ""
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        content = "\n".join(parts)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("API вернул пустой ответ")
    return content.strip()


async def _request_model(
    model: str,
    previous_messages: List[Dict[str, str]],
    user_text: str,
) -> str:
    api_key = os.getenv(API_KEY_ENV, "").strip()
    api_url = (os.getenv("TOKENPLAN_BASE_URL") or DEFAULT_API_URL).strip()
    if not api_key:
        raise RuntimeError(
            "Не задан TOKENPLAN_API_KEY. Добавь API-ключ TokenPlan в переменные "
            "окружения или в локальный .env-файл."
        )
    if not api_url:
        raise RuntimeError("Не задан TOKENPLAN_BASE_URL.")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            *previous_messages[-MAX_CONTEXT_MESSAGES:],
            {"role": "user", "content": user_text},
        ],
    }
    timeout = aiohttp.ClientTimeout(total=120)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(api_url, headers=headers, json=payload) as response:
                raw_body = await response.text()
                try:
                    data = json.loads(raw_body)
                except json.JSONDecodeError:
                    data = {}

                if response.status != 200:
                    error = data.get("error", {}) if isinstance(data, dict) else {}
                    if isinstance(error, dict):
                        detail = error.get("message") or error.get("type")
                    else:
                        detail = str(error) if error else ""
                    detail = detail or raw_body[:500] or "Нет подробностей от сервера"
                    raise RuntimeError(f"TokenPlan HTTP {response.status}: {detail}")

                if not isinstance(data, dict):
                    raise RuntimeError("TokenPlan вернул ответ в неизвестном формате")
                return _extract_reply(data)
    except asyncio.TimeoutError as exc:
        raise RuntimeError("Истекло время ожидания ответа TokenPlan (120 секунд).") from exc
    except aiohttp.ClientError as exc:
        logging.warning("Ошибка соединения с TokenPlan: %s", type(exc).__name__)
        raise RuntimeError("Не удалось подключиться к TokenPlan. Проверь адрес API и соединение.") from exc
    except ValueError as exc:
        raise RuntimeError(f"Некорректный ответ TokenPlan: {exc}") from exc


@router.callback_query(lambda callback: callback.data == "ai_menu")
async def ai_menu(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.edit_text(
        "💬 <b>Чат с ИИ</b>\n\nСоздавай отдельные диалоги с разными моделями. "
        "История каждого чата сохраняется.",
        parse_mode="HTML",
        reply_markup=_root_keyboard(),
    )


@router.callback_query(lambda callback: callback.data == "ai_new")
async def ai_new_chat(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.edit_text(
        "➕ <b>Новый чат</b>\n\nНажми кнопку ниже, чтобы выбрать модель:",
        parse_mode="HTML",
        reply_markup=_new_chat_keyboard(),
    )


@router.callback_query(lambda callback: callback.data == "ai_choose_model")
async def ai_choose_model(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.edit_text(
        "🎛 <b>Выбор модели</b>\n\nВыбери модель для нового диалога:",
        parse_mode="HTML",
        reply_markup=_models_keyboard(),
    )


@router.callback_query(lambda callback: callback.data == "ai_my_chats")
async def ai_my_chats(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    chats = _user_chats(callback.from_user.id)
    if not chats:
        text = "📚 <b>Мои чаты</b>\n\nСохранённых диалогов пока нет."
    else:
        text = (
            f"📚 <b>Мои чаты</b>\n\n"
            f"Сохранено диалогов: {len(chats)}. Выбери нужный:"
        )
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=_my_chats_keyboard(callback.from_user.id),
    )


@router.callback_query(lambda callback: callback.data and callback.data.startswith("ai_model_"))
async def ai_model_selected(callback: types.CallbackQuery, state: FSMContext):
    model = callback.data[len("ai_model_"):]
    if model not in MODELS:
        await callback.answer("Неизвестная модель.", show_alert=True)
        return

    user_id = callback.from_user.id
    chat_id = uuid.uuid4().hex[:12]
    chat = {
        "id": chat_id,
        "title": "Новый чат",
        "model": model,
        "messages": [],
        "created_at": _now(),
        "updated_at": _now(),
    }
    _user_chats(user_id).append(chat)
    if not _save_history():
        _user_chats(user_id).remove(chat)
        await callback.answer("Не удалось сохранить историю. Проверь права на запись.", show_alert=True)
        return

    await state.set_state(AIChatStates.chatting)
    await state.update_data(ai_chat_id=chat_id)
    await callback.answer(f"Выбрана модель: {model}")
    await callback.message.edit_text(
        f"✅ <b>Новый чат создан</b>\n\n"
        f"Модель: <code>{model}</code>\n\n"
        "Отправь сообщение обычным сообщением боту.",
        parse_mode="HTML",
        reply_markup=_chat_keyboard(),
    )


@router.callback_query(lambda callback: callback.data and callback.data.startswith("ai_open_"))
async def ai_open_chat(callback: types.CallbackQuery, state: FSMContext):
    chat_id = callback.data[len("ai_open_"):]
    chat = _find_chat(callback.from_user.id, chat_id)
    if not chat:
        await callback.answer("Чат не найден.", show_alert=True)
        return

    await state.set_state(AIChatStates.chatting)
    await state.update_data(ai_chat_id=chat_id)
    await callback.answer()
    await callback.message.edit_text(
        _chat_transcript(chat),
        reply_markup=_chat_keyboard(),
        disable_web_page_preview=True,
    )


@router.message(AIChatStates.chatting)
async def ai_chat_message(message: types.Message, state: FSMContext):
    user_text = (message.text or "").strip()
    if not user_text:
        await message.answer("Отправь текстовое сообщение. Файлы и голосовые сообщения пока не поддерживаются.")
        return

    if user_text.lower() in ("/cancel", "отмена"):
        await state.clear()
        await message.answer("Режим чата завершён. Открой раздел «Чат с ИИ» из главного меню.")
        return

    state_data = await state.get_data()
    chat_id = state_data.get("ai_chat_id")
    chat = _find_chat(message.from_user.id, chat_id or "")
    if not chat:
        await state.clear()
        await message.answer("Этот чат больше не найден. Открой «Мои чаты» и выбери диалог заново.")
        return

    try:
        reply = await _request_model(
            str(chat.get("model", "")),
            chat.get("messages", []),
            user_text,
        )
    except RuntimeError as exc:
        await message.answer(f"Ошибка запроса: {exc}", reply_markup=_chat_keyboard())
        return
    except Exception:
        logging.exception("Непредвиденная ошибка при запросе к TokenPlan")
        await message.answer(
            "Произошла внутренняя ошибка при обращении к API. Подробности записаны в журнал.",
            reply_markup=_chat_keyboard(),
        )
        return

    messages = chat.setdefault("messages", [])
    messages.append({"role": "user", "content": user_text})
    messages.append({"role": "assistant", "content": reply})
    if chat.get("title") == "Новый чат":
        chat["title"] = _short_title(user_text, 48)
    chat["updated_at"] = _now()

    if not _save_history():
        await message.answer(
            "Ответ получен, но историю не удалось записать на диск. Проверь права на запись.",
            reply_markup=_chat_keyboard(),
        )

    chunks = _split_text(reply)
    for index, chunk in enumerate(chunks):
        await message.answer(
            chunk,
            reply_markup=_chat_keyboard() if index == len(chunks) - 1 else None,
        )
