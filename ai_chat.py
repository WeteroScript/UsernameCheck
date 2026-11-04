"""
Раздел «Чат с ИИ» на базе совместимого с OpenAI Chat Completions API TokenPlan.
История хранится отдельно по Telegram user_id в JSON-файле.
"""

import asyncio
import json
import io
import zipfile
import xml.etree.ElementTree as ET
import logging
import os
import re
import uuid
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp
from aiogram import Router, types
from aiogram.enums import ButtonStyle
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from dotenv import load_dotenv
from access_control import is_premium

load_dotenv()

router = Router(name="ai_chat")

DEFAULT_API_URL = "https://tokenplan.lat/v1/chat/completions"
API_KEY_ENV = "TOKENPLAN_API_KEY"
HISTORY_PATH = Path(os.getenv("AI_CHAT_HISTORY_FILE", "data/ai_chats.json"))

MAX_CONTEXT_MESSAGES = 40
TELEGRAM_TEXT_LIMIT = 4000
MAX_UPLOAD_MB = max(1, int(os.getenv("AI_CHAT_MAX_UPLOAD_MB", "200")))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
MAX_EXTRACTED_CHARS = 80000
MAX_ARCHIVE_FILES = 40
MAX_GENERATED_FILE_BYTES = 10 * 1024 * 1024
TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".html", ".htm",
    ".py", ".js", ".ts", ".jsx", ".tsx", ".css", ".scss", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".log", ".sql", ".sh", ".bat", ".ps1",
    ".java", ".kt", ".go", ".rs", ".c", ".cpp", ".h", ".hpp", ".cs",
    ".php", ".rb", ".swift", ".r", ".ipynb", ".env.example",
}
SUPPORTED_FILE_EXTENSIONS = TEXT_EXTENSIONS | {".zip", ".pdf", ".docx", ".pptx", ".xlsx"}
REQUEST_COOLDOWN_SECONDS = 5
_last_request_at: Dict[int, float] = {}
_active_requests: set[int] = set()
_active_tasks: Dict[int, asyncio.Task] = {}
_waiting_messages: Dict[int, types.Message] = {}
SYSTEM_PROMPT = (
    "Ты — полезный ИИ-помощник. Отвечай на языке пользователя. "
    "Если пользователь просит код, приводи полный и понятный пример. "
    "Ты не имеешь доступа к файловой системе сервера, локальным папкам или инструментам "
    "работы с файлами. Но бот умеет создавать и отправлять файлы из текста твоего ответа. "
    "Поэтому на любую просьбу создать, подготовить или сохранить файл для пользователя "
    "верни его полное содержимое строго в формате [[FILE: имя_файла.расширение]], "
    "затем новая строка, содержимое файла, новая строка и [[/FILE]]. "
    "Не утверждай, что файл создан в рабочей папке или что у тебя нет инструментов: "
    "сформируй содержимое внутри маркеров. Не помещай содержимое файла вне маркеров. "
    "Можно вернуть несколько таких блоков. Перед блоками кратко объясни результат."
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


async def pause_ai_chat(user_id: int) -> None:
    """Останавливает активный запрос пользователя при переходе в /start."""
    task = _active_tasks.get(user_id)
    waiting = _waiting_messages.get(user_id)
    if task and not task.done():
        task.cancel()
    if waiting:
        try:
            await waiting.edit_text("Диалог приостановлен командой /start.")
        except Exception:
            logging.debug("Не удалось обновить сообщение ожидания при паузе ИИ-чата")


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


def _is_paid_model(model: str) -> bool:
    return model.startswith("claude") or model in {"gpt-6-astra", "gpt-6-sol"}


def _model_style(model: str) -> ButtonStyle:
    if _is_paid_model(model):
        return ButtonStyle.DANGER
    if model.startswith(("gpt", "deepseek", "minimax")):
        return ButtonStyle.SUCCESS
    return ButtonStyle.PRIMARY


def _models_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"🔒 {model}" if _is_paid_model(model) else model,
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




def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1251", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Не удалось определить кодировку текстового файла.")


def _extract_office_xml(data: bytes, extension: str) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = sorted(
                name for name in archive.namelist()
                if name.endswith(".xml")
                and (
                    name.startswith("word/")
                    or name.startswith("ppt/slides/")
                    or name.startswith("xl/sharedStrings")
                    or name.startswith("xl/worksheets/")
                )
            )
            parts = []
            for name in names:
                try:
                    root = ET.fromstring(archive.read(name))
                except (ET.ParseError, KeyError):
                    continue
                values = [
                    value.strip()
                    for value in root.itertext()
                    if value and value.strip()
                ]
                if values:
                    parts.append("\n".join(values))
            return "\n\n".join(parts)
    except zipfile.BadZipFile as exc:
        raise ValueError("Файл офисного формата повреждён.") from exc


def _extract_file_content(filename: str, data: bytes, depth: int = 0) -> str:
    extension = Path(filename).suffix.lower()
    if extension in TEXT_EXTENSIONS:
        return _decode_text(data)
    if extension == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except ImportError as exc:
            raise ValueError("Для чтения PDF установи зависимость pypdf.") from exc
        except Exception as exc:
            raise ValueError(f"Не удалось прочитать PDF: {exc}") from exc
    if extension in {".docx", ".pptx", ".xlsx"}:
        return _extract_office_xml(data, extension)
    if extension == ".zip":
        if depth >= 2:
            raise ValueError("Слишком большая глубина вложенных ZIP-архивов.")
        return _extract_zip_content(data, depth + 1)
    raise ValueError(
        f"Формат {extension or 'без расширения'} не поддерживается. "
        "Поддерживаются текстовые файлы, PDF, DOCX, PPTX, XLSX и ZIP с такими файлами."
    )


def _extract_zip_content(data: bytes, depth: int = 0) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            files = [item for item in archive.infolist() if not item.is_dir()]
            if len(files) > MAX_ARCHIVE_FILES:
                raise ValueError(f"В архиве больше {MAX_ARCHIVE_FILES} файлов.")
            total_size = sum(item.file_size for item in files)
            if total_size > 30 * 1024 * 1024:
                raise ValueError("Распакованный размер архива превышает 30 МБ.")

            extracted = []
            total_chars = 0
            for item in files:
                if item.file_size > MAX_UPLOAD_BYTES:
                    continue
                filename = Path(item.filename).name
                if not filename:
                    continue
                extension = Path(filename).suffix.lower()
                if extension not in SUPPORTED_FILE_EXTENSIONS:
                    continue
                try:
                    content = _extract_file_content(
                        filename, archive.read(item), depth=depth
                    ).strip()
                except (ValueError, RuntimeError) as exc:
                    extracted.append(f"[Файл {filename}: {exc}]")
                    continue
                if not content:
                    continue
                remaining = MAX_EXTRACTED_CHARS - total_chars
                if remaining <= 0:
                    break
                content = content[:remaining]
                extracted.append(f"===== {filename} =====\n{content}")
                total_chars += len(content)
            if not extracted:
                raise ValueError(
                    "В ZIP не найдено поддерживаемых файлов с извлекаемым содержимым."
                )
            return "\n\n".join(extracted)
    except zipfile.BadZipFile as exc:
        raise ValueError("Файл не является корректным ZIP-архивом.") from exc


async def _document_to_text(message: types.Message) -> str:
    document = message.document
    if not document:
        raise ValueError("Документ не найден.")
    if document.file_size and document.file_size > MAX_UPLOAD_BYTES:
        raise ValueError(f"Максимальный размер загружаемого файла — {MAX_UPLOAD_MB} МБ.")
    filename = document.file_name or "document"
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_FILE_EXTENSIONS:
        raise ValueError(
            "Этот формат не поддерживается. Принимаются TXT, MD, CSV, JSON, "
            "XML, код, PDF, DOCX, PPTX, XLSX и ZIP с такими файлами."
        )

    try:
        # Временный файл не держит ещё одну копию загрузки в оперативной памяти.
        with tempfile.TemporaryFile(mode="w+b") as buffer:
            await message.bot.download(document, destination=buffer)
            buffer.seek(0)
            raw = buffer.read(MAX_UPLOAD_BYTES + 1)
    except Exception as exc:
        logging.exception("Не удалось скачать документ Telegram")
        raise RuntimeError("Не удалось скачать файл из Telegram.") from exc
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError(f"Максимальный размер загружаемого файла — {MAX_UPLOAD_MB} МБ.")
    content = _extract_file_content(filename, raw).strip()
    if not content:
        raise ValueError("В файле не удалось найти текст для передачи модели.")
    return (
        f"Пользователь приложил файл: {filename}\n"
        f"Извлечённое содержимое:\n{content[:MAX_EXTRACTED_CHARS]}"
    )


def _extract_generated_files(reply: str):
    pattern = re.compile(
        r"\[\[FILE:\s*([^\]\r\n]+)\]\]\s*\n(.*?)\n\[\[/FILE\]\]",
        re.DOTALL | re.IGNORECASE,
    )
    files = []
    empty_files = 0
    for match in pattern.finditer(reply):
        filename = Path(match.group(1).strip().replace("\\", "/")).name
        filename = re.sub(r"[^A-Za-z0-9А-Яа-яёЁ._-]", "_", filename)
        content = match.group(2)
        if not filename or filename in {".", ".."}:
            filename = f"generated_{len(files) + empty_files + 1}.txt"
        encoded = content.encode("utf-8")
        if not encoded.strip():
            empty_files += 1
            logging.warning("Модель вернула пустой файл %s; файл пропущен.", filename)
            continue
        if len(encoded) > MAX_GENERATED_FILE_BYTES:
            raise ValueError(f"Сгенерированный файл {filename} превышает 10 МБ.")
        files.append((filename, encoded))
    visible_reply = pattern.sub("", reply).strip()
    if empty_files:
        warning = (
            f"Пустых файлов от модели: {empty_files}. "
            "Они пропущены, поскольку Telegram не принимает файлы нулевого размера."
        )
        visible_reply = f"{visible_reply}\n\n{warning}".strip()
    return visible_reply, files


def _build_generated_zip(files: List[tuple]) -> bytes:
    """Упаковывает несколько сгенерированных файлов в один ZIP-архив."""
    if len(files) > MAX_ARCHIVE_FILES:
        raise ValueError(f"Модель вернула больше {MAX_ARCHIVE_FILES} файлов за один ответ.")
    total_size = sum(len(file_content) for _, file_content in files)
    if total_size > MAX_UPLOAD_BYTES:
        raise ValueError(
            f"Общий размер сгенерированных файлов превышает лимит {MAX_UPLOAD_MB} МБ."
        )

    output = io.BytesIO()
    used_names = set()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, (filename, file_content) in enumerate(files, start=1):
            safe_name = Path(filename).name or f"generated_{index}.txt"
            candidate = safe_name
            stem = Path(safe_name).stem
            suffix = Path(safe_name).suffix
            counter = 2
            while candidate.casefold() in used_names:
                candidate = f"{stem}_{counter}{suffix}"
                counter += 1
            used_names.add(candidate.casefold())
            archive.writestr(candidate, file_content)
    return output.getvalue()


def _is_file_creation_request(user_text: str) -> bool:
    """Определяет явные просьбы пользователя создать файл для отправки."""
    patterns = (
        r"\b(?:созда(?:й|ть|йте)|сдела(?:й|ть|йте)|сгенерируй|сгенерировать|сформируй|сформировать)\b.{0,80}(?:\b(?:файл|документ)\b|(?<!\w)\.?(?:txt|md|pdf|docx|csv|json)\b)",
        r"\bсохрани(?:ть)?\s+.{0,60}\bв\s+файл\b",
        r"\bподготов(?:ь|ить|ьте)\s+.{0,60}\bфайл\b",
        r"\bфайл\s+(?:для\s+)?скачивания\b",
        r"\bcreate\s+(?:a\s+)?(?:file|document)\b",
        r"\bmake\s+(?:me\s+)?(?:a\s+)?file\b",
        r"\bexport\s+(?:this|it|as)\b",
    )
    return any(re.search(pattern, user_text, re.IGNORECASE) for pattern in patterns)


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
        "💬 <b>Чат с ИИ</b>\n\nСоздавай отдельные диалоги. Контекст диалогов сохраняется.",
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
            f"Сохранено диалогов: {len(chats)}. Выбери диалог, затем отправь сообщение:"
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
    if _is_paid_model(model) and not is_premium(callback.from_user.id):
        await callback.answer("Эта модель доступна только с премиум-доступом.", show_alert=True)
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
        "Диалог активен. Отправь сообщение, чтобы начать.",
        parse_mode="HTML",
    )


@router.callback_query(lambda callback: callback.data and callback.data.startswith("ai_open_"))
async def ai_open_chat(callback: types.CallbackQuery, state: FSMContext):
    chat_id = callback.data[len("ai_open_"):]
    chat = _find_chat(callback.from_user.id, chat_id)
    if chat and _is_paid_model(str(chat.get("model", ""))) and not is_premium(callback.from_user.id):
        await callback.answer("Эта модель доступна только с премиум-доступом.", show_alert=True)
        return
    if not chat:
        await callback.answer("Чат не найден.", show_alert=True)
        return

    await state.set_state(AIChatStates.chatting)
    await state.update_data(ai_chat_id=chat_id)
    await callback.answer()
    await callback.message.edit_text(
        f"Диалог с моделью <code>{chat.get('model', 'неизвестна')}</code> выбран.\n"
        "Отправь сообщение, чтобы продолжить диалог.",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@router.message(AIChatStates.chatting)
async def ai_chat_message(message: types.Message, state: FSMContext):
    user_text = (message.text or message.caption or "").strip()
    if message.document:
        try:
            document_text = await _document_to_text(message)
        except (ValueError, RuntimeError) as exc:
            await message.answer(f"Не удалось обработать файл: {exc}")
            return
        user_text = f"{user_text}\n\n{document_text}".strip()
    elif not user_text:
        await message.answer(
            "Отправь текст или документ. Поддерживаются TXT, MD, CSV, JSON, XML, "
            "файлы с кодом, PDF, DOCX, PPTX, XLSX и ZIP-архивы с такими файлами."
        )
        return
    if user_text.lower() in ("/cancel", "отмена"):
        await state.clear()
        await message.answer("Диалог приостановлен. Чтобы продолжить, открой «Мои чаты» и выбери его.")
        return

    user_id = message.from_user.id
    data = await state.get_data()
    chat = _find_chat(user_id, data.get("ai_chat_id") or "")
    if not chat:
        await state.clear()
        await message.answer("Активный диалог не выбран. Открой «Мои чаты» и выбери диалог или создай новый.")
        return
    model = str(chat.get("model", "неизвестная модель"))
    if _is_paid_model(model) and not is_premium(user_id):
        await state.clear()
        await message.answer("Эта модель доступна только с премиум-доступом. Выбери другую модель.")
        return
    if user_id in _active_requests:
        await message.answer("Предыдущий запрос ещё обрабатывается. Дождись ответа.")
        return

    now = time.monotonic()
    previous = _last_request_at.get(user_id)
    if previous is not None:
        remaining = REQUEST_COOLDOWN_SECONDS - (now - previous)
        if remaining > 0:
            await message.answer(f"Следующий запрос можно отправить через {int(remaining) + 1} сек.")
            return

    _last_request_at[user_id] = now
    _active_requests.add(user_id)
    _active_tasks[user_id] = asyncio.current_task()
    waiting = None
    try:
        waiting = await message.answer("👀 Ии готовит ответ")
        _waiting_messages[user_id] = waiting
        reply = await _request_model(model, chat.get("messages", []), user_text)
        visible_reply, generated_files = _extract_generated_files(reply)

        # Если пользователь явно запросил файл, а модель проигнорировала формат,
        # делаем один уточняющий запрос с исходным ответом в контексте.
        if _is_file_creation_request(user_text) and not generated_files:
            retry_context = [
                *chat.get("messages", [])[-MAX_CONTEXT_MESSAGES:],
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": reply},
            ]
            retry_instruction = (
                "Твой предыдущий ответ не создал файл: в нём отсутствует блок "
                "[[FILE: имя_файла.расширение]] ... [[/FILE]]. "
                "Сейчас выполни исходную просьбу пользователя. Верни полное содержимое "
                "каждого нужного файла строго внутри такого блока. Не описывай инструменты "
                "и не заявляй, что не можешь работать с файлами."
            )
            try:
                retry_reply = await _request_model(model, retry_context, retry_instruction)
                retry_visible, retry_files = _extract_generated_files(retry_reply)
                if retry_files:
                    reply = retry_reply
                    visible_reply, generated_files = retry_visible, retry_files
                else:
                    logging.warning(
                        "Модель %s не вернула блок файла после повторного запроса", model
                    )
                    reply = (
                        "Не удалось получить файл от модели: она не вернула содержимое "
                        "в требуемом формате. Повтори запрос, указав имя и формат файла."
                    )
                    visible_reply, generated_files = reply, []
            except RuntimeError:
                logging.exception("Повторный запрос для генерации файла завершился ошибкой")
                reply = (
                    "Не удалось создать файл: повторный запрос к модели завершился ошибкой. "
                    "Попробуй ещё раз позже."
                )
                visible_reply, generated_files = reply, []

        messages = chat.setdefault("messages", [])
        messages.extend([
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": reply},
        ])
        if chat.get("title") == "Новый чат":
            chat["title"] = _short_title(user_text, 48)
        chat["updated_at"] = _now()
        if not _save_history():
            logging.error("Ответ ИИ получен, но историю диалога не удалось сохранить.")

        display_text = f'"{model}" - {visible_reply}' if visible_reply else f'"{model}" - Файл готов.'
        chunks = _split_text(display_text)
        try:
            if waiting:
                await waiting.edit_text(chunks[0])
        except Exception:
            logging.exception("Не удалось изменить сообщение ожидания")
            await message.answer(chunks[0])
        for chunk in chunks[1:]:
            await message.answer(chunk)
        if len(generated_files) >= 2:
            try:
                archive_content = _build_generated_zip(generated_files)
            except ValueError as exc:
                await message.answer(f"Не удалось собрать ZIP-архив: {exc}")
            else:
                await message.answer_document(
                    BufferedInputFile(archive_content, filename="generated_files.zip"),
                    caption=f'Архив с {len(generated_files)} файлами от модели {model}',
                )
        elif generated_files:
            filename, file_content = generated_files[0]
            await message.answer_document(
                BufferedInputFile(file_content, filename=filename),
                caption=f"Файл от модели {model}",
            )
    except RuntimeError as exc:
        if waiting:
            await waiting.edit_text(f'"{model}" - Ошибка запроса: {exc}')
    except asyncio.CancelledError:
        raise
    except Exception:
        logging.exception("Непредвиденная ошибка при запросе к TokenPlan")
        if waiting:
            await waiting.edit_text(f'"{model}" - Произошла внутренняя ошибка при обращении к API.')
    finally:
        _active_requests.discard(user_id)
        _active_tasks.pop(user_id, None)
        _waiting_messages.pop(user_id, None)
