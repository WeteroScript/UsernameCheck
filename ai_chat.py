"""
Раздел «Чат с ИИ» на базе совместимого с OpenAI Chat Completions API TokenPlan.
История хранится отдельно по Telegram user_id в JSON-файле.
"""

import asyncio
import json
import base64
import hashlib
import io
import zipfile
import xml.etree.ElementTree as ET
import logging
import os
import re
import uuid
import time
import tempfile
import shutil
import html
from urllib.parse import urlsplit, urlunsplit
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import aiohttp
from aiogram import Router, types
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.enums import ButtonStyle
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from dotenv import load_dotenv
from cryptography.fernet import Fernet, InvalidToken
from access_control import is_admin, is_premium
from ai_usage import format_ai_quota, get_ai_quota, get_ai_usage_models, quota_block_reason, record_ai_usage, record_free_request, record_ai_request, ensure_ai_request_total
from github_integration import fetch_repository_context, create_repository_pull_request
from storage import data_file, data_directory

load_dotenv()

router = Router(name="ai_chat")

DEFAULT_API_URL = "https://tokenplan.lat/v1"
API_KEY_ENV = "TOKENPLAN_API_KEY"
BACKUP_API_KEY_ENV = "TOKENPLAN_API_KEY_BACKUP"
API_KEY_COOLDOWN_SECONDS = 5 * 60 * 60
_api_key_cooldowns: Dict[str, float] = {}
HISTORY_PATH = Path(data_file(os.getenv("AI_CHAT_HISTORY_FILE", "ai_chats.json")))
SETTINGS_PATH = Path(data_file(os.getenv("AI_CHAT_SETTINGS_FILE", "ai_chat_settings.json")))
MAX_CUSTOM_PROMPT_CHARS = 20000
MAX_CUSTOM_PROMPT_FILE_BYTES = 64 * 1024
CHAT_ARCHIVE_DIR = Path(data_directory("ai_chat_archives"))
MAX_CHAT_ARCHIVES = 8
MAX_CHAT_ARCHIVE_CONTEXT_CHARS = 400_000

MAX_CONTEXT_MESSAGES = 40
TELEGRAM_TEXT_LIMIT = 4000
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
_document_processing_semaphore = asyncio.Semaphore(1)
_MAX_ZIP_MEMBER_BYTES = 16 * 1024 * 1024
_MAX_ZIP_TOTAL_BYTES = 128 * 1024 * 1024
_MAX_ZIP_TEXT_CHARS = 400_000
_active_tasks: Dict[int, asyncio.Task] = {}
_auto_tasks: Dict[int, asyncio.Task] = {}
_auto_stop_events: Dict[int, asyncio.Event] = {}
_waiting_messages: Dict[int, types.Message] = {}
_MESSAGE_DEDUPE_TTL_SECONDS = 600
_MAX_SEEN_AI_MESSAGES = 10000
_seen_ai_messages: Dict[Tuple[int, int], float] = {}
SYSTEM_PROMPT = (
    "Ты — ИИ-помощник внутри Telegram-бота. Отвечай на языке пользователя. "
    "ПОДХОД К БОЛЬШИМ ПРОЕКТАМ: если пользователь просит создать полноценное приложение, "
    "сайт, бота, игру, клон существующего сервиса или другой большой проект из нескольких "
    "файлов и компонентов, НЕ начинай сразу писать весь код и не пытайся выдать весь проект "
    "одним огромным ответом. Сначала составь понятный поэтапный план из 4–8 конкретных "
    "этапов. Укажи, что будет реализовано на каждом этапе, какие основные файлы появятся "
    "и что пользователь получит в результате. В первом ответе выдай только план, оцени "
    "последовательность работ и обязательно задай прямой вопрос: «Приступать к работе? "
    "Ответь “Да”, чтобы начать, или “Нет”, чтобы отменить». До явного согласия пользователя "
    "не генерируй исходники и не создавай файлы. "
    "ЕСЛИ ПОЛЬЗОВАТЕЛЬ СОГЛАСИЛСЯ: когда после такого плана пользователь отвечает "
    "«да», «начинай», «приступай», «поехали» или аналогично явно подтверждает старт, "
    "выполни только первый этап плана. Не пытайся реализовать все этапы в одном запросе. "
    "В конце этапа кратко перечисли готовое, укажи следующий этап и спроси, продолжать ли. "
    "Переходи к следующему этапу только после явного согласия пользователя. Повторяй "
    "этот цикл до завершения проекта. Если пользователь отвечает «нет» или отменяет "
    "работу, не продолжай генерацию. Если пользователь просит изменить план, сначала "
    "обнови план и снова запроси подтверждение. Не трактуй обычное «да» как согласие "
    "на проект, если непосредственно перед этим ты не предлагал начать или продолжить его. "
    "Работай последовательно: каждый этап должен опираться на предыдущие решения, "
    "соблюдать единую архитектуру, имена файлов, интерфейсы и стиль проекта. Не "
    "переписывай без необходимости уже завершённые части. Если для целостности нужно "
    "изменить ранее созданный файл, явно укажи это и верни его полную обновлённую версию. "
    "В финальном этапе проверь согласованность структуры, импортов, зависимостей, "
    "конфигурации и инструкций запуска; подготовь итоговый комплект исходников. "
    "Для ZIP-проекта на этапах выдавай файлы отдельными блоками FILE, а в финале "
    "постарайся собрать полный итоговый комплект проекта в архив, включая все нужные "
    "файлы, а не только файлы последнего этапа. Если полный комплект не помещается "
    "в один ответ, честно сообщи об этом и предложи разбить финальную сборку на части. "
    "ВАЖНО: создание вложений уже реализовано на стороне бота. Ты возвращаешь содержимое "
    "файлов в специальных блоках FILE, а бот автоматически создаёт из них настоящие "
    "файлы и отправляет пользователю. Тебе не требуется доступ к диску, файловой системе, "
    "терминалу, Python или отдельному инструменту. Никогда не отказывайся от создания "
    "файлов или ZIP из-за отсутствия таких средств, не говори, что не можешь прикреплять "
    "файлы, и не подменяй запрошенный файл инструкцией о самостоятельном создании. "
    "Когда пользователь просит отдельный файл или когда наступил этап проекта, "
    "на котором нужно выдать исходники, верни полное содержимое каждого файла в блоке: "
    "[[FILE: имя_файла.расширение]] затем перевод строки, полное содержимое, затем "
    "перевод строки и [[/FILE]]. Не обрамляй блоки Markdown-кодом. Каждый блок должен "
    "содержать полный непустой файл без заглушек, пропусков и многоточий. "
    "Если пользователь явно просит готовый ZIP/архив, приоритет — сформировать файлы сразу, "
    "а не выдавать скрипт-сборщик, инструкции запуска или только план. Если объём проекта "
    "не помещается в один ответ, объясни ограничение и предложи поэтапную сборку, сохраняя "
    "единый итоговый комплект. Если пользователь просит ZIP, архив или проект, верни каждый входящий файл отдельным "
    "полноценным блоком FILE. Бот автоматически упаковывает два или более файлов из "
    "одного ответа в настоящий ZIP и отправляет его. Не пытайся самостоятельно создавать "
    "ZIP. Для одного файла бот отправит отдельное вложение. "
    "Поддерживаются текстовые файлы: TXT, MD, CSV, JSON, XML, HTML, Python, JavaScript, "
    "TypeScript, CSS, SQL и другие исходники. Не выдавай обычный текст за бинарный PDF, "
    "DOCX, XLSX или PPTX. Если нужен такой формат, объясни именно это ограничение, "
    "но всё равно создай доступные исходники отдельными блоками FILE. "
    "Не заявляй, что вложение готово, пока не вернул его полное содержимое в блоке FILE. "
    "ДОСТУП К РАНЕЕ ПРИСЛАННЫМ ZIP: содержимое ZIP-архивов, которые пользователь загрузил "
    "в этот же диалог, сохраняется ботом отдельно и повторно передаётся тебе в следующих "
    "запросах. Используй эти архивы как исходный код проекта на последующих этапах; "
    "не проси загрузить их повторно, если содержимое присутствует в блоке сохранённых архивов. "
    "Архивы других диалогов недоступны и не должны смешиваться."
)
SYSTEM_PROMPT += (
    "СТИЛЬ ОТОБРАЖАЕМОГО ОТВЕТА: обычные ответы пиши простым, естественным текстом на языке пользователя. "
    "Не добавляй названия файлов, расширения, пути, технические метки, названия языков программирования "
    "и служебные подписи. Не используй Markdown-разметку, тройные кавычки, блоки кода, декоративные "
    "символы, стрелки, подчёркивания, каретки, лишние кавычки, точки с запятой и нагромождение "
    "знаков препинания. Не вставляй слова bash, python, json и подобные обозначения в обычный ответ. "
    "Формулируй мысли короткими абзацами и обычными предложениями. Сохраняй нормальную пунктуацию, "
    "если она нужна для смысла. Исходный код возвращай только внутри обязательных FILE или GITHUB_FILE "
    "блоков, когда пользователь прямо просит код или файлы. Вне этих блоков объясняй результат "
    "человеческим языком и не перечисляй имена созданных файлов. "
    "ПИШИ КОМПАКТНО: не вставляй пустые строки между обычными предложениями. "
    "Между смысловыми блоками используй максимум одну пустую строку. Не превращай "
    "каждое предложение в отдельный абзац и не создавай большие пустые отступы. "
)
SYSTEM_PROMPT += (
    "РАБОТА С GITHUB: если пользователь прислал ссылку на репозиторий GitHub, бот может передать тебе "
    "снимок доступных текстовых файлов этого репозитория. Изучи структуру и существующий код до "
    "предложения изменений. Не утверждай, что видишь файлы, которых нет в переданном снимке. "
    "Если нужного файла не хватает, попроси пользователя указать его путь или прислать файл. "
    "Когда пользователь просит изменить репозиторий, верни каждый новый или изменённый файл "
    "полностью в отдельном блоке формата [[GITHUB_FILE: путь/от/корня/репозитория]] затем перевод "
    "строки, полное содержимое файла, затем перевод строки и [[/GITHUB_FILE]]. Не используй "
    "абсолютные пути и .. в путях. Не возвращай блоки для файлов, которые менять не нужно. "
    "Если пользователь дал ссылку вида /tree/ИМЯ_ВЕТКИ или явно выбрал ветку, бот коммитит изменения " +
    "непосредственно только в эту ветку: без новой AI-ветки и без Pull Request. Не переключайся на main " +
    "и не записывай в другие ветки. Если конкретная ветка не указана, используй безопасный режим " +
    "с отдельной AI-веткой и Pull Request. "
    "Если пользователь просит только анализ или объяснение, не создавай GITHUB_FILE-блоки. "
    "Не помещай секреты, токены, пароли или содержимое .env в новые файлы и не копируй их из контекста. "
    "ИНСТРУКЦИЯ ПО ИСПОЛЬЗОВАНИЮ GITHUB: перед изменением репозитория определи owner/name, "
    "основную ветку и назначение файлов по переданному снимку. Сохраняй текущую структуру, "
    "стиль, зависимости и соглашения проекта. Для добавления файла верни полный путь от корня "
    "репозитория и полное содержимое через GITHUB_FILE; для изменения верни полную обновлённую "
    "версию, а не diff или отдельный фрагмент. Не изменяй файл, которого нет в снимке, если "
    "не уверен в его назначении: запроси его у пользователя. Для GitHub используй только "
    "GITHUB_FILE-блоки, не FILE-блоки и не локальный ZIP. После получения блоков бот " +
    "записывает изменения напрямую в выбранную пользователем ветку; если ветка не выбрана — создаёт " +
    "AI-ветку и Pull Request. Никогда не изменяй другие ветки и не утверждай, что PR слит. "
    "Порядок: изучить репозиторий, определить минимальный набор файлов, подготовить полные файлы, "
    "проверить импорты, пути и зависимости, затем вернуть GITHUB_FILE-блоки. Если репозиторий "
    "не подключён или недоступен, объясни, что сначала нужно подключить GitHub в меню бота и "
    "установить приложение с доступом к нужному репозиторию. "
    "ВАЖНО О РАЗДЕЛЕНИИ ОТВЕТСТВЕННОСТИ: тебе не требуется самостоятельно иметь интернет, "
    "терминал, файловую систему или GitHub API. Это выполняет оркестратор Telegram-бота. "
    "Если в сообщении присутствует снимок репозитория и пользователь просит внести изменение, "
    "не отвечай, что у тебя нет доступа к GitHub: верни точные GITHUB_FILE-блоки, после чего "
    "бот применит их строго к выбранной ветке либо создаст отдельную AI-ветку и Pull Request, " +
    "если ветка не выбрана. Для создания ZIP верни все файлы в FILE-блоках; "
    "бот соберёт настоящий архив и сохранит относительные пути. Не заявляй, что операция завершена, "
    "пока бот не подтвердил её результат."
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
user_settings: Dict[str, Dict[str, Any]] = {}
available_models: Dict[str, List[str]] = {}


class AIChatStates(StatesGroup):
    chatting = State()
    waiting_prompt = State()
    waiting_auto_prompt = State()
    waiting_api_key = State()
    waiting_api_url = State()


class _AutoRequestMessage:
    """Minimal Telegram message adapter so auto requests use the regular chat pipeline."""

    def __init__(self, bot, user: types.User, chat: types.Chat) -> None:
        self.bot = bot
        self.from_user = user
        self.chat = chat
        self.message_id = int(time.time_ns() % 2_000_000_000)
        self.text = ""
        self.caption = None
        self.document = None

    async def answer(self, text: str, **kwargs):
        return await self.bot.send_message(self.chat.id, text, **kwargs)

    async def answer_document(self, document, caption: Optional[str] = None, **kwargs):
        return await self.bot.send_document(
            self.chat.id, document, caption=caption, **kwargs
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_duplicate_ai_message(message: types.Message) -> bool:
    """Не запускает один Telegram message_id повторно в пределах процесса."""
    key = (int(message.chat.id), int(message.message_id))
    now = time.monotonic()
    previous = _seen_ai_messages.get(key)
    if previous is not None and now - previous <= _MESSAGE_DEDUPE_TTL_SECONDS:
        return True

    _seen_ai_messages[key] = now
    if len(_seen_ai_messages) > _MAX_SEEN_AI_MESSAGES:
        cutoff = now - _MESSAGE_DEDUPE_TTL_SECONDS
        for old_key, seen_at in list(_seen_ai_messages.items()):
            if seen_at < cutoff:
                _seen_ai_messages.pop(old_key, None)
        overflow = len(_seen_ai_messages) - _MAX_SEEN_AI_MESSAGES
        if overflow > 0:
            for old_key in list(_seen_ai_messages)[:overflow]:
                _seen_ai_messages.pop(old_key, None)
    return False



async def _update_ai_progress(message: Optional[types.Message], stage: str) -> None:
    """Обновляет отдельное сообщение с текущим этапом обработки запроса."""
    if message is None:
        return
    try:
        await message.edit_text(f"🔄 Сейчас: {stage}")
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            logging.debug("Не удалось обновить этап работы ИИ", exc_info=True)
    except Exception:
        logging.debug("Не удалось обновить этап работы ИИ", exc_info=True)


async def _animate_waiting_message(message: types.Message) -> None:
    """Показывает фактическое время ожидания ответа модели."""
    elapsed = 0
    while True:
        await asyncio.sleep(1)
        elapsed += 1
        try:
            time_label = f"{elapsed // 60}m {elapsed % 60}s" if elapsed >= 60 else f"{elapsed}s"
            await message.edit_text(f"👀 Ии готовит ответ. {time_label}.")
        except Exception:
            # Ошибка редактирования не должна прерывать запрос к модели.
            logging.debug("Не удалось обновить таймер ожидания ИИ", exc_info=True)


async def _stop_waiting_timer(task: Optional[asyncio.Task]) -> None:
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            # Ошибка таймера не должна мешать освобождению блокировки запроса.
            logging.debug("Задача таймера завершилась с ошибкой", exc_info=True)


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

def _settings_cipher():
    secret = (
        os.getenv("AI_CHAT_SETTINGS_SECRET")
        or os.getenv("GITHUB_STATE_SECRET")
        or ""
    ).strip()
    if not secret:
        return None
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _load_user_settings() -> Dict[str, Dict[str, Any]]:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        with SETTINGS_PATH.open("r", encoding="utf-8") as file:
            raw = json.load(file)
        if not isinstance(raw, dict):
            raise ValueError("Корневой элемент настроек должен быть объектом")
        cipher = _settings_cipher()
        result: Dict[str, Dict[str, Any]] = {}
        for user_id, value in raw.items():
            if not isinstance(value, dict):
                continue
            settings = dict(value)
            encrypted_key = settings.pop("api_key_encrypted", None)
            if encrypted_key:
                if cipher is None:
                    logging.error("Не задан секрет шифрования; пользовательский API key не загружен")
                else:
                    try:
                        settings["api_key"] = cipher.decrypt(
                            str(encrypted_key).encode("ascii")
                        ).decode("utf-8")
                    except (InvalidToken, ValueError, UnicodeDecodeError):
                        logging.exception("Не удалось расшифровать пользовательский API key")
            result[str(user_id)] = settings
        return result
    except (OSError, json.JSONDecodeError, ValueError):
        logging.exception("Не удалось загрузить настройки ИИ-чата")
        return {}


def _save_user_settings() -> bool:
    temp_path = SETTINGS_PATH.with_name(SETTINGS_PATH.name + ".tmp")
    try:
        cipher = _settings_cipher()
        serialized: Dict[str, Dict[str, Any]] = {}
        for user_id, value in user_settings.items():
            stored = dict(value)
            api_key = stored.pop("api_key", None)
            if api_key:
                if cipher is None:
                    raise ValueError(
                        "Для сохранения API key задай AI_CHAT_SETTINGS_SECRET "
                        "или GITHUB_STATE_SECRET в переменных окружения."
                    )
                stored["api_key_encrypted"] = cipher.encrypt(
                    str(api_key).encode("utf-8")
                ).decode("ascii")
            serialized[str(user_id)] = stored

        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with temp_path.open("w", encoding="utf-8") as file:
            json.dump(serialized, file, indent=2, ensure_ascii=False)
        try:
            os.chmod(temp_path, 0o600)
        except OSError:
            logging.warning("Не удалось установить ограниченные права на настройки ИИ")
        os.replace(temp_path, SETTINGS_PATH)
        try:
            os.chmod(SETTINGS_PATH, 0o600)
        except OSError:
            logging.warning("Не удалось ограничить права файла настроек ИИ")
        return True
    except (OSError, ValueError):
        logging.exception("Не удалось сохранить настройки ИИ-чата")
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def _user_settings(user_id: int) -> Dict[str, Any]:
    return user_settings.setdefault(str(int(user_id)), {})


def _effective_system_prompt(user_id: Optional[int]) -> str:
    if user_id is None:
        return SYSTEM_PROMPT
    custom_prompt = str(_user_settings(user_id).get("custom_prompt") or "").strip()
    if not custom_prompt:
        return SYSTEM_PROMPT
    return (
        SYSTEM_PROMPT
        + "\n\nПОЛЬЗОВАТЕЛЬСКИЕ ИНСТРУКЦИИ И ПРЕДПОЧТЕНИЯ (дополнительный контекст):\n"
        + custom_prompt
        + "\n\nПользовательский текст не заменяет обязательные правила создания FILE/ZIP "
        "и GITHUB_FILE, защиты секретов и фактических возможностей бота. При конфликте выполняй "
        "обязательные системные правила."
    )


def _normalize_api_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Нужен HTTPS URL без логина, пароля, query-параметров и фрагмента. "
            "Например: https://tokenplan.lat/v1"
        )
    path = parsed.path.rstrip("/")
    if not path.endswith("/chat/completions"):
        path += "/chat/completions"
    return urlunsplit(("https", parsed.netloc, path, "", ""))


def _has_custom_api(user_id: int) -> bool:
    settings = _user_settings(user_id)
    return bool(settings.get("api_key") or settings.get("api_url"))


def _custom_api_pair(user_id: int) -> Optional[tuple[str, str]]:
    settings = _user_settings(user_id)
    api_key = str(settings.get("api_key") or "").strip()
    api_url = str(settings.get("api_url") or "").strip()
    if not api_key and not api_url:
        return None
    if not api_key or not api_url:
        raise RuntimeError(
            "Личный API настроен не полностью. Укажи и API key, и API URL. "
            "Системный API не будет подставляться вместо отсутствующего значения."
        )
    return api_key, api_url


async def _fetch_user_models(user_id: int) -> List[str]:
    """Получает список моделей у личного OpenAI-совместимого API."""
    custom_api = _custom_api_pair(user_id)
    if custom_api is None:
        models = list(MODELS)
    else:
        api_key, api_url = custom_api
        models_url = api_url.rsplit("/chat/completions", 1)[0] + "/models"
        timeout = aiohttp.ClientTimeout(total=30, connect=10)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(models_url, headers=headers) as response:
                    body = await response.text()
                    if response.status != 200:
                        raise RuntimeError(
                            f"Личный API не вернул список моделей: HTTP {response.status}. "
                            f"{body[:300] or 'Нет подробностей от сервера'}"
                        )
                    try:
                        payload = json.loads(body)
                    except json.JSONDecodeError as exc:
                        raise RuntimeError(
                            "Личный API вернул некорректный JSON со списком моделей."
                        ) from exc
        except asyncio.TimeoutError as exc:
            raise RuntimeError("Истекло время ожидания ответа личного API /models.") from exc
        except aiohttp.ClientError as exc:
            raise RuntimeError(f"Не удалось подключиться к личному API /models: {exc}") from exc

        if isinstance(payload, list):
            entries = payload
        elif isinstance(payload, dict):
            entries = payload.get("data") or payload.get("models") or payload.get("model_list") or []
        else:
            entries = []
        models = []
        seen = set()
        if isinstance(entries, list):
            for entry in entries:
                model_id = entry.get("id") if isinstance(entry, dict) else entry
                if not isinstance(model_id, str):
                    continue
                model_id = model_id.strip()
                if model_id and len(model_id) <= 200 and model_id not in seen:
                    seen.add(model_id)
                    models.append(model_id)
        if not models:
            raise RuntimeError(
                "Личный API вернул пустой или неподдерживаемый список моделей. "
                "Проверь, что адрес /models поддерживается и ключ имеет доступ."
            )

    available_models[str(int(user_id))] = models
    return models


def _cached_user_models(user_id: int) -> Optional[List[str]]:
    models = available_models.get(str(int(user_id)))
    return list(models) if models else None


async def pause_ai_chat(user_id: int) -> None:
    """Останавливает активный запрос и цикл авто-запроса при переходе в /start."""
    auto_task = _auto_tasks.get(user_id)
    if auto_task and not auto_task.done():
        auto_task.cancel()
    task = _active_tasks.get(user_id)
    waiting = _waiting_messages.get(user_id)
    if task and task is not auto_task and not task.done():
        task.cancel()
    if waiting:
        try:
            await waiting.edit_text("Диалог приостановлен командой /start.")
        except Exception:
            logging.debug("Не удалось обновить сообщение ожидания при паузе ИИ-чата")


def init_ai_chat(dispatcher) -> None:
    """Загружает историю и пользовательские настройки, затем подключает роутер."""
    global chat_history, user_settings
    chat_history = _load_history()
    user_settings = _load_user_settings()
    historical_requests = sum(
        1
        for user_chats in chat_history.values()
        if isinstance(user_chats, list)
        for chat in user_chats
        if isinstance(chat, dict)
        for item in chat.get("messages", [])
        if isinstance(item, dict) and item.get("role") == "user"
    )
    ensure_ai_request_total(historical_requests)
    dispatcher.include_router(router)
    logging.info("Раздел «Чат с ИИ» подключён; сохранено пользователей: %s", len(chat_history))


def _user_chats(user_id: int) -> List[Dict[str, Any]]:
    return chat_history.setdefault(str(user_id), [])


def _history_message_without_old_files(content: str) -> str:
    """Не сохраняет и не передаёт модели содержимое файлов из прошлых сообщений."""
    marker = "\n\nПользователь приложил файл:"
    if marker in content:
        return content.split(marker, 1)[0] + "\n\n[Содержимое ранее приложенного файла не хранится в истории.]"
    if content.startswith("Пользователь приложил файл:"):
        return "[Содержимое ранее приложенного файла не хранится в истории.]"
    return content


def _history_without_old_files(messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    cleaned = []
    for item in messages:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            continue
        content = item.get("content", "")
        if not isinstance(content, str):
            continue
        cleaned.append({
            "role": item["role"],
            "content": _history_message_without_old_files(content)
                if item["role"] == "user" else content,
        })
    return cleaned


def _chat_archive_directory(user_id: int, chat_id: str) -> Path:
    safe_chat_id = hashlib.sha256(str(chat_id).encode("utf-8")).hexdigest()[:24]
    return CHAT_ARCHIVE_DIR / str(int(user_id)) / safe_chat_id


def _persist_chat_archive_context(
    user_id: int,
    chat: Dict[str, Any],
    filename: str,
    document_text: str,
) -> None:
    """Persist extracted ZIP contents so later turns in this chat can reuse them."""
    marker = "Извлечённое содержимое:\n"
    content = document_text.split(marker, 1)[1] if marker in document_text else document_text
    content = content.strip()
    if not content:
        raise ValueError("В ZIP не найдено содержимое для сохранения.")

    archive_id = uuid.uuid4().hex
    directory = _chat_archive_directory(user_id, str(chat.get("id") or ""))
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{archive_id}.txt"
    temporary = directory / f"{archive_id}.tmp"
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, target)
    except OSError:
        temporary.unlink(missing_ok=True)
        logging.exception("Не удалось сохранить содержимое ZIP для чата %s", chat.get("id"))
        raise RuntimeError("Не удалось сохранить ZIP в постоянное хранилище бота.")

    archives = chat.setdefault("project_archives", [])
    if not isinstance(archives, list):
        archives = []
        chat["project_archives"] = archives
    original_archives = list(archives)
    archives.append({
        "id": archive_id,
        "filename": Path(filename).name[:180],
        "uploaded_at": _now(),
        "chars": len(content),
    })

    # Keep a bounded, recent project context. Oldest archives are pruned first.
    removed_ids = []
    while len(archives) > MAX_CHAT_ARCHIVES or sum(
        max(0, int(item.get("chars", 0))) for item in archives if isinstance(item, dict)
    ) > MAX_CHAT_ARCHIVE_CONTEXT_CHARS:
        oldest = archives.pop(0)
        old_id = str(oldest.get("id") or "") if isinstance(oldest, dict) else ""
        if re.fullmatch(r"[a-f0-9]{32}", old_id):
            removed_ids.append(old_id)

    if not _save_history():
        chat["project_archives"] = original_archives
        target.unlink(missing_ok=True)
        raise RuntimeError("Не удалось сохранить сведения о ZIP в истории чата.")

    for old_id in removed_ids:
        try:
            (directory / f"{old_id}.txt").unlink(missing_ok=True)
        except OSError:
            logging.warning("Не удалось удалить устаревший контекст ZIP %s", old_id)


def _load_chat_archive_context(user_id: int, chat: Dict[str, Any]) -> str:
    """Load persisted ZIP contents belonging only to the selected chat."""
    archives = chat.get("project_archives", [])
    if not isinstance(archives, list):
        return ""
    directory = _chat_archive_directory(user_id, str(chat.get("id") or ""))
    sections = []
    total_chars = 0
    for item in archives:
        if not isinstance(item, dict):
            continue
        archive_id = str(item.get("id") or "")
        if not re.fullmatch(r"[a-f0-9]{32}", archive_id):
            continue
        try:
            content = (directory / f"{archive_id}.txt").read_text(encoding="utf-8")
        except FileNotFoundError:
            logging.warning("Сохранённый ZIP-контекст отсутствует: chat=%s archive=%s", chat.get("id"), archive_id)
            continue
        except OSError:
            logging.exception("Не удалось прочитать ZIP-контекст: chat=%s archive=%s", chat.get("id"), archive_id)
            continue
        remaining = MAX_CHAT_ARCHIVE_CONTEXT_CHARS - total_chars
        if remaining <= 0:
            break
        if len(content) > remaining:
            content = content[:remaining] + "\n[Контекст архива обрезан общим лимитом чата.]"
        sections.append(
            f"===== Архив: {Path(str(item.get('filename') or 'archive.zip')).name} =====\n{content}"
        )
        total_chars += len(content)
    return "\n\n".join(sections)


def _find_chat(user_id: int, chat_id: str) -> Optional[Dict[str, Any]]:
    # Каждый chat_id хранит собственные model и messages. Не объединять историю
    # по имени модели: два чата одной модели должны оставаться независимыми.
    for chat in _user_chats(user_id):
        if chat.get("id") == chat_id:
            if not isinstance(chat.get("messages"), list):
                chat["messages"] = []
            return chat
    return None


def _auto_request_keyboard(user_id: int) -> InlineKeyboardMarkup:
    settings = _user_settings(user_id)
    task = _auto_tasks.get(user_id)
    running = bool(task and not task.done())
    prompt = str(settings.get("auto_prompt") or "").strip()
    chat = _find_chat(user_id, str(settings.get("auto_chat_id") or ""))
    prompt_label = "✅ Запрос задан" if prompt else "⚠️ Запрос не задан"
    chat_label = (
        "✅ Чат: " + _short_title(str(chat.get("title") or chat.get("model") or "Чат"), 22)
        if chat else "⚠️ Чат не выбран"
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Ввести запрос", callback_data="ai_auto_prompt", style=ButtonStyle.PRIMARY)],
        [InlineKeyboardButton(text="💬 Выбрать чат", callback_data="ai_auto_choose_chat", style=ButtonStyle.PRIMARY)],
        [InlineKeyboardButton(
            text="⏹ Стоп" if running else "▶️ Старт",
            callback_data="ai_auto_toggle",
            style=ButtonStyle.DANGER if running else ButtonStyle.SUCCESS,
        )],
        [InlineKeyboardButton(text="❔ Как работает?", callback_data="ai_auto_help", style=ButtonStyle.PRIMARY)],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="ai_menu", style=ButtonStyle.DANGER)],
    ])


def _auto_request_screen_text(user_id: int, status: Optional[str] = None) -> str:
    settings = _user_settings(user_id)
    prompt = str(settings.get("auto_prompt") or "").strip()
    chat = _find_chat(user_id, str(settings.get("auto_chat_id") or ""))
    prompt_preview = html.escape(_short_title(prompt, 180)) if prompt else "не задан"
    chat_label = (
        f"{html.escape(str(chat.get('title') or 'Без названия'))} "
        f"(<code>{html.escape(str(chat.get('model') or 'unknown'))}</code>)"
        if chat else "не выбран"
    )
    task = _auto_tasks.get(user_id)
    running = bool(task and not task.done())
    state_label = status or ("работает" if running else "остановлен")
    return (
        "<b>🔁 Авто-запрос</b>\n\n"
        f"Запрос: <i>{prompt_preview}</i>\n"
        f"Чат: {chat_label}\n"
        f"Состояние: <b>{state_label}</b>\n\n"
        "Повторение запускается после ответа, но не чаще одного раза в 5 секунд. "
        "На каждый цикл действуют общие лимиты ИИ."
    )


def _root_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Новый чат", callback_data="ai_new", style=ButtonStyle.SUCCESS),
            InlineKeyboardButton(text="📁 Мои чаты", callback_data="ai_my_chats", style=ButtonStyle.PRIMARY),
        ],
        [InlineKeyboardButton(text="🔒 Авто-запрос · Premium", callback_data="ai_auto", style=ButtonStyle.SUCCESS)],
        [InlineKeyboardButton(text="📊 Лимиты и расход", callback_data="ai_usage_overview", style=ButtonStyle.PRIMARY)],
        [
            InlineKeyboardButton(text="⚙️", callback_data="ai_settings", style=ButtonStyle.PRIMARY),
            InlineKeyboardButton(text="🐙 GitHub", callback_data="github_menu", style=ButtonStyle.PRIMARY),
        ],
        [InlineKeyboardButton(text="🏠 Главное меню", callback_data="home_ai", style=ButtonStyle.DANGER)],
    ])


def _ai_settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Свой промпт", callback_data="ai_prompt_settings", style=ButtonStyle.PRIMARY)],
        [InlineKeyboardButton(text="🔑 Свой API key", callback_data="ai_api_settings", style=ButtonStyle.PRIMARY)],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="ai_menu", style=ButtonStyle.DANGER)],
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
    normalized = str(model or "").lower()
    return (
        normalized.startswith("claude")
        or normalized in {"gpt-6-astra", "gpt-6-sol"}
        or normalized == "gemini-4-argon"
        or normalized.startswith("gemini-4-argon-")
        or normalized == "kimi-k3"
        or normalized.startswith(("kimi-k3-", "kimi-k3."))
        or normalized == "deepseek-v4.1-flash"
        or normalized.startswith("deepseek-v4.1-flash-")
    )


def _supports_effort(model: str) -> bool:
    """Check whether the model family/version exposes a documented effort parameter."""
    normalized = model.lower().replace(".", "-")
    if normalized.startswith("claude"):
        unsupported = ("claude-3", "sonnet-4-5", "haiku-4-5")
        return not any(version in normalized for version in unsupported)
    return normalized.startswith(("gpt-5", "gpt-6"))


def _effort_levels(model: str) -> List[Tuple[str, str]]:
    normalized = model.lower().replace(".", "-")
    if normalized.startswith("claude"):
        levels = [
            ("low", "Низкое"),
            ("medium", "Среднее"),
            ("high", "Высокое"),
        ]
        if any(version in normalized for version in (
            "opus-4-7", "opus-4-8", "opus-5", "sonnet-5",
            "haiku-5-5", "fable", "mythos",
        )):
            levels.append(("xhigh", "Очень высокое"))
        if any(version in normalized for version in (
            "opus-4-6", "opus-4-7", "opus-4-8", "opus-5",
            "sonnet-4-6", "sonnet-5", "haiku-5", "fable", "mythos",
        )):
            levels.append(("max", "Максимальное"))
        return levels
    if normalized.startswith("gpt-6"):
        return [
            ("low", "Низкое"),
            ("medium", "Среднее"),
            ("high", "Высокое"),
            ("xhigh", "Очень высокое"),
            ("max", "Максимальное"),
        ]
    if normalized.startswith("gpt-5"):
        levels = [
            ("low", "Низкое"),
            ("medium", "Среднее"),
            ("high", "Высокое"),
        ]
        if any(version in normalized for version in ("gpt-5-2", "gpt-5-3", "gpt-5-4", "gpt-5-5", "gpt-5-6")):
            levels.append(("xhigh", "Очень высокое"))
        return levels
    return []


def _effort_keyboard(chat_id: str, model: str, current: Optional[str]) -> InlineKeyboardMarkup:
    rows = []
    for value, label in _effort_levels(model):
        prefix = "✅ " if value == current else ""
        rows.append([InlineKeyboardButton(
            text=prefix + label,
            callback_data=f"ai_effort_set_{chat_id}_{value}",
            style=ButtonStyle.SUCCESS if value == current else ButtonStyle.PRIMARY,
        )])
    rows.append([InlineKeyboardButton(
        text=("✅ " if current is None else "") + "По умолчанию",
        callback_data=f"ai_effort_set_{chat_id}_default",
        style=ButtonStyle.SUCCESS if current is None else ButtonStyle.PRIMARY,
    )])
    rows.append([InlineKeyboardButton(
        text="⬅️ Назад к настройкам",
        callback_data=f"ai_chat_settings_{chat_id}",
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _ai_quota_text(user_id: int, model: str) -> str:
    if is_admin(user_id):
        return (
            "📊 <b>Администраторский доступ</b>\n"
            "Лимиты ИИ: <b>без ограничений</b>."
        )
    return format_ai_quota(user_id, model, is_premium(user_id))


def _ai_quota_error(user_id: int, model: str) -> Optional[str]:
    if is_admin(user_id):
        return None
    return quota_block_reason(user_id, model, is_premium(user_id))


def _model_style(model: str) -> ButtonStyle:
    if _is_paid_model(model):
        return ButtonStyle.DANGER
    if model.startswith(("gpt", "deepseek", "minimax")):
        return ButtonStyle.SUCCESS
    return ButtonStyle.PRIMARY


def _models_keyboard(
    models: List[str],
    page: int = 0,
    chat_id: Optional[str] = None,
    custom_api: bool = False,
) -> InlineKeyboardMarkup:
    page_size = 40
    page_count = max(1, (len(models) + page_size - 1) // page_size)
    page = max(0, min(page, page_count - 1))
    start = page * page_size
    buttons = []
    for index, model in enumerate(models[start:start + page_size], start=start):
        label = model if len(model) <= 54 else model[:51] + "..."
        if _is_paid_model(model):
            label = f"🔒 {label}"
        callback_data = (
            f"ai_chat_set_model_{chat_id}_{index}" if chat_id
            else f"ai_model_{index}"
        )
        buttons.append(InlineKeyboardButton(
            text=label,
            callback_data=callback_data,
            style=ButtonStyle.PRIMARY if custom_api else _model_style(model),
        ))
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    if page_count > 1:
        navigation = []
        if page > 0:
            callback_data = (
                f"ai_chat_models_page_{chat_id}_{page - 1}" if chat_id
                else f"ai_models_page_{page - 1}"
            )
            navigation.append(InlineKeyboardButton(
                text="◀️", callback_data=callback_data, style=ButtonStyle.PRIMARY
            ))
        navigation.append(InlineKeyboardButton(
            text=f"{page + 1}/{page_count}", callback_data="ai_models_page_info",
            style=ButtonStyle.PRIMARY,
        ))
        if page + 1 < page_count:
            callback_data = (
                f"ai_chat_models_page_{chat_id}_{page + 1}" if chat_id
                else f"ai_models_page_{page + 1}"
            )
            navigation.append(InlineKeyboardButton(
                text="▶️", callback_data=callback_data, style=ButtonStyle.PRIMARY
            ))
        rows.append(navigation)
    rows.append([InlineKeyboardButton(
        text="⬅️ Назад к настройкам" if chat_id else "⬅️ Назад",
        callback_data=f"ai_chat_settings_{chat_id}" if chat_id else "ai_new",
        style=ButtonStyle.DANGER,
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _short_title(value: str, limit: int = 32) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) > limit:
        return value[:limit - 1] + "…"
    return value or "Новый чат"


def _model_folder_key(model: str) -> str:
    """Короткий стабильный идентификатор папки модели для callback_data."""
    return hashlib.sha256(model.encode("utf-8")).hexdigest()[:12]


def _model_folder_title(model: str) -> str:
    """Преобразует ID модели в читаемое имя папки."""
    parts = model.strip().split("-")
    if model.lower().startswith("claude-") and len(parts) >= 4:
        if parts[-1].isdigit() and parts[-2].isdigit():
            version = f"{parts[-2]}.{parts[-1]}"
            family = " ".join(part.capitalize() for part in parts[1:-2])
            return f"Claude {family} {version}"
    labels = {
        "gpt": "GPT", "deepseek": "DeepSeek", "kimi": "Kimi",
        "gemini": "Gemini", "glm": "GLM", "qwen": "Qwen",
        "minimax": "MiniMax", "hy": "HY",
    }
    return " ".join(labels.get(part.lower(), part.upper() if part.lower() == "v" else part.capitalize())
                    for part in parts)


def _chat_count_label(count: int) -> str:
    last_two = count % 100
    last_one = count % 10
    if 11 <= last_two <= 14:
        noun = "чатов"
    elif last_one == 1:
        noun = "чат"
    elif 2 <= last_one <= 4:
        noun = "чата"
    else:
        noun = "чатов"
    return f"{count} {noun}"


def _my_chats_keyboard(
    user_id: int,
    model_filter: Optional[str] = None,
) -> InlineKeyboardMarkup:
    chats = _user_chats(user_id)
    rows = []

    if model_filter is None:
        folders: Dict[str, List[Dict[str, Any]]] = {}
        for chat in chats:
            model = str(chat.get("model") or "unknown")
            folders.setdefault(model, []).append(chat)

        ordered_folders = sorted(
            folders.items(),
            key=lambda item: max(str(chat.get("updated_at") or "") for chat in item[1]),
            reverse=True,
        )
        for model, model_chats in ordered_folders:
            rows.append([InlineKeyboardButton(
                text=f"📁 {_model_folder_title(model)} · {_chat_count_label(len(model_chats))}",
                callback_data=f"ai_folder_{_model_folder_key(model)}",
                style=ButtonStyle.PRIMARY,
            )])
    else:
        model_chats = sorted(
            (chat for chat in chats if str(chat.get("model") or "unknown") == model_filter),
            key=lambda item: str(item.get("updated_at") or ""),
            reverse=True,
        )
        for chat in model_chats[:30]:
            title = _short_title(str(chat.get("title", "Новый чат")), 28)
            rows.append([
                InlineKeyboardButton(
                    text=f"💬 {title}",
                    callback_data=f"ai_open_{chat['id']}",
                    style=ButtonStyle.PRIMARY,
                ),
                InlineKeyboardButton(
                    text="⚙️",
                    callback_data=f"ai_chat_settings_{chat['id']}",
                    style=ButtonStyle.SUCCESS,
                ),
            ])

    rows.extend([
        [InlineKeyboardButton(text="➕ Новый чат", callback_data="ai_new", style=ButtonStyle.SUCCESS)],
        [InlineKeyboardButton(
            text="⬅️ Назад к папкам" if model_filter is not None else "⬅️ Назад",
            callback_data="ai_my_chats" if model_filter is not None else "ai_menu",
            style=ButtonStyle.DANGER,
        )],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _chat_settings_keyboard(chat_id: str, model: Optional[str] = None) -> InlineKeyboardMarkup:
    back_callback = f"ai_folder_{_model_folder_key(model)}" if model else "ai_my_chats"
    rows = [
        [InlineKeyboardButton(text="📊 Лимиты и расход", callback_data=f"ai_chat_quota_{chat_id}", style=ButtonStyle.PRIMARY)],
    ]
    if model and _supports_effort(model):
        rows.append([InlineKeyboardButton(text="🧠 Усиление модели", callback_data=f"ai_chat_effort_{chat_id}", style=ButtonStyle.SUCCESS)])
    rows.extend([
        [InlineKeyboardButton(text="🗑 Удалить чат", callback_data=f"ai_chat_delete_{chat_id}", style=ButtonStyle.DANGER)],
        [InlineKeyboardButton(text="🎛 Поменять модель", callback_data=f"ai_chat_change_model_{chat_id}", style=ButtonStyle.SUCCESS)],
        [InlineKeyboardButton(text="⬅️ Назад к папке", callback_data=back_callback, style=ButtonStyle.PRIMARY)],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _chat_model_keyboard(chat_id: str) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"🔒 {model}" if _is_paid_model(model) else model,
            callback_data=f"ai_chat_set_model_{chat_id}_{model}",
            style=_model_style(model),
        )
        for model in MODELS
    ]
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(
        text="⬅️ Назад к настройкам",
        callback_data=f"ai_chat_settings_{chat_id}",
        style=ButtonStyle.PRIMARY,
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _prompt_settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить промпт", callback_data="ai_prompt_edit", style=ButtonStyle.SUCCESS)],
        [InlineKeyboardButton(text="🗑 Сбросить промпт", callback_data="ai_prompt_reset", style=ButtonStyle.DANGER)],
        [InlineKeyboardButton(text="⬅️ К настройкам", callback_data="ai_settings", style=ButtonStyle.PRIMARY)],
    ])


def _api_settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔑 Установить API key", callback_data="ai_api_set_key", style=ButtonStyle.SUCCESS)],
        [InlineKeyboardButton(text="🌐 Установить API URL", callback_data="ai_api_set_url", style=ButtonStyle.PRIMARY)],
        [InlineKeyboardButton(text="🗑 Удалить ключ", callback_data="ai_api_delete_key", style=ButtonStyle.DANGER)],
        [InlineKeyboardButton(text="♻️ Сбросить свой API", callback_data="ai_api_reset", style=ButtonStyle.DANGER)],
        [InlineKeyboardButton(text="⬅️ К настройкам", callback_data="ai_settings", style=ButtonStyle.PRIMARY)],
    ])



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
            # Скрываем технические блоки исходников из текстового просмотра истории.
            content = re.sub(
                r"\\[\\[(?:FILE|GITHUB_FILE):[^\\]]+\\]\\][\\s\\S]*?\\[\\[/(?:FILE|GITHUB_FILE)\\]\\]",
                "",
                content,
                flags=re.IGNORECASE,
            ).strip()
            if not content:
                content = "В этом сообщении были отправлены файлы."
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


def _telegram_text_length(text: str) -> int:
    """Telegram measures message length in UTF-16 code units."""
    return len(text.encode("utf-16-le")) // 2


def _split_reply_chunks(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> List[str]:
    """Split text into Telegram-safe chunks measured in UTF-16 code units."""
    chunks: List[str] = []
    current: List[str] = []
    current_length = 0
    for character in str(text or ""):
        character_length = 2 if ord(character) > 0xFFFF else 1
        if current and current_length + character_length > limit:
            chunks.append("".join(current))
            current = []
            current_length = 0
        current.append(character)
        current_length += character_length
    if current:
        chunks.append("".join(current))
    return chunks or [" "]


def _visible_stream_text(raw_text: str) -> str:
    """Hide generated-file payloads while allowing ordinary answer text to stream."""
    opening_tokens = (
        ("[[GITHUB_FILE:", "[[/GITHUB_FILE]]"),
        ("[[FILE:", "[[/FILE]]"),
    )
    lowered = raw_text.lower()
    visible: List[str] = []
    cursor = 0

    while cursor < len(raw_text):
        matches = [
            (lowered.find(opening.lower(), cursor), opening, closing)
            for opening, closing in opening_tokens
        ]
        matches = [match for match in matches if match[0] >= 0]
        if not matches:
            safe_end = len(raw_text)
            tail = lowered[cursor:]
            for opening, _ in opening_tokens:
                token = opening.lower()
                max_overlap = min(len(token) - 1, len(tail))
                for overlap in range(max_overlap, 0, -1):
                    if tail.endswith(token[:overlap]):
                        safe_end = min(safe_end, len(raw_text) - overlap)
                        break
            visible.append(raw_text[cursor:safe_end])
            break

        start, opening, closing = min(matches, key=lambda item: item[0])
        visible.append(raw_text[cursor:start])
        closing_index = lowered.find(closing.lower(), start + len(opening))
        if closing_index < 0:
            break
        cursor = closing_index + len(closing)

    return "".join(visible)


def _compact_display_spacing(text: str) -> str:
    """Remove excessive empty lines without changing meaningful line breaks."""
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"\n[ \t]*\n+", "\n", normalized)
    return normalized.strip()


async def _answer_reply_text(message: types.Message, text: str) -> types.Message:
    try:
        return await message.answer(text)
    except TelegramRetryAfter as exc:
        await asyncio.sleep(float(exc.retry_after) + 0.1)
        return await message.answer(text)


class _StreamingReplyDisplay:
    """Показывает ответ по одной законченной строке, обновляя то же сообщение."""

    EDIT_INTERVAL_SECONDS = 0.15
    # Telegram does not expose client-side wrapping width; this approximates a phone line.
    VISUAL_LINE_WIDTH = 37

    def __init__(
        self,
        message: types.Message,
        waiting: Optional[types.Message],
    ) -> None:
        self.message = message
        self.messages: List[types.Message] = [waiting] if waiting else []
        self.chunks: List[str] = ["👀 Ии готовит ответ. 0s."] if waiting else []
        self.raw_text = ""
        self.visible_text = ""
        self.rendered_text = ""
        self.last_edit_at = 0.0
        self._render_lock = asyncio.Lock()

    @property
    def has_visible_text(self) -> bool:
        return bool(self.visible_text.strip())

    def append(self, piece: str) -> None:
        self.raw_text += piece
        self.visible_text = _compact_display_spacing(_visible_stream_text(self.raw_text))

    @classmethod
    def _next_boundary(cls, text: str, start: int) -> Optional[int]:
        """Find the next phone-width line boundary without splitting words."""
        newline_at = text.find("\n", start)
        paragraph_end = newline_at if newline_at >= 0 else len(text)
        remaining = text[start:paragraph_end]
        if not remaining:
            return newline_at + 1 if newline_at >= 0 else None
        if len(remaining) <= cls.VISUAL_LINE_WIDTH:
            return newline_at + 1 if newline_at >= 0 else None
        split_at = remaining.rfind(" ", 0, cls.VISUAL_LINE_WIDTH + 1)
        if split_at < 0:
            split_at = remaining.find(" ", cls.VISUAL_LINE_WIDTH)
        if split_at < 0:
            return newline_at + 1 if newline_at >= 0 else None
        return start + split_at + 1

    async def _update_messages(self, text: str) -> None:
        new_chunks = _split_reply_chunks(text)
        for index, chunk in enumerate(new_chunks):
            if index < len(self.messages):
                if self.chunks[index] == chunk:
                    continue
                target = self.messages[index]
                try:
                    try:
                        await target.edit_text(chunk)
                    except TelegramRetryAfter as exc:
                        await asyncio.sleep(float(exc.retry_after) + 0.1)
                        await target.edit_text(chunk)
                except TelegramBadRequest as exc:
                    if "message is not modified" in str(exc).lower():
                        self.chunks[index] = chunk
                        continue
                    logging.exception("Не удалось обновить потоковый ответ в Telegram")
                    try:
                        replacement = await _answer_reply_text(self.message, chunk)
                    except Exception:
                        logging.exception("Не удалось отправить резервное сообщение потокового ответа")
                        continue
                    self.messages[index] = replacement
                    try:
                        await target.delete()
                    except Exception:
                        logging.debug("Не удалось удалить заменённое сообщение ожидания", exc_info=True)
                except Exception:
                    logging.exception("Не удалось обновить потоковый ответ в Telegram")
                    try:
                        replacement = await _answer_reply_text(self.message, chunk)
                    except Exception:
                        logging.exception("Не удалось отправить резервное сообщение потокового ответа")
                        continue
                    self.messages[index] = replacement
                    try:
                        await target.delete()
                    except Exception:
                        logging.debug("Не удалось удалить заменённое сообщение ожидания", exc_info=True)
                self.chunks[index] = chunk
            else:
                try:
                    new_message = await _answer_reply_text(self.message, chunk)
                except Exception:
                    logging.exception("Не удалось отправить следующую часть потокового ответа")
                    continue
                self.messages.append(new_message)
                self.chunks.append(chunk)

        while len(self.messages) > len(new_chunks):
            old_message = self.messages.pop()
            self.chunks.pop()
            try:
                await old_message.delete()
            except Exception:
                logging.debug("Не удалось удалить лишнюю часть потокового ответа", exc_info=True)

    async def render(self, force: bool = False) -> None:
        async with self._render_lock:
            text = self.visible_text
            if not text.strip():
                return

            # При повторной генерации (например, после внутреннего retry)
            # начинаем отображение заново, но редактируем прежнее сообщение.
            if self.rendered_text and not text.startswith(self.rendered_text):
                self.rendered_text = ""

            while len(self.rendered_text) < len(text):
                boundary = self._next_boundary(text, len(self.rendered_text))
                if boundary is None:
                    if not force:
                        break
                    boundary = len(text)

                next_text = text[:boundary]
                if not next_text or next_text == self.rendered_text:
                    break

                if self.last_edit_at:
                    delay = self.EDIT_INTERVAL_SECONDS - (time.monotonic() - self.last_edit_at)
                    if delay > 0:
                        await asyncio.sleep(delay)

                await self._update_messages(next_text)
                self.rendered_text = next_text
                self.last_edit_at = time.monotonic()

                # visible_text мог измениться, пока ждали Telegram rate limit.
                text = self.visible_text
                if not text.startswith(self.rendered_text):
                    self.rendered_text = ""
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
        return _extract_zip_content(data, depth + 1)
    raise ValueError(
        f"Формат {extension or 'без расширения'} не поддерживается. "
        "Поддерживаются текстовые файлы, PDF, DOCX, PPTX, XLSX и ZIP с такими файлами."
    )


def _extract_zip_content(data, depth: int = 0) -> str:
    """Extract supported archive entries sequentially; file count is unrestricted."""
    started_at = time.monotonic()
    if depth > 3:
        raise ValueError("Слишком большая глубина вложенных ZIP-архивов (максимум 3).")

    source = data if hasattr(data, "read") else io.BytesIO(data)
    extracted = []
    total_uncompressed = 0
    total_chars = 0
    processed_files = 0
    skipped_files = 0

    try:
        if hasattr(source, "seek"):
            source.seek(0)
        with zipfile.ZipFile(source) as archive:
            for item in archive.infolist():
                if item.is_dir():
                    continue

                relative_name = item.filename.replace("\\", "/").lstrip("/")
                if not relative_name or any(part == ".." for part in relative_name.split("/")):
                    continue
                if Path(relative_name).suffix.lower() not in SUPPORTED_FILE_EXTENSIONS:
                    continue

                # Не читаем гигантские элементы и ZIP-бомбы в память.
                if item.file_size > _MAX_ZIP_MEMBER_BYTES:
                    skipped_files += 1
                    extracted.append(f"[Файл {relative_name} пропущен: размер после распаковки превышает {_MAX_ZIP_MEMBER_BYTES // (1024 * 1024)} МБ.]")
                    continue
                if total_uncompressed + item.file_size > _MAX_ZIP_TOTAL_BYTES:
                    skipped_files += 1
                    extracted.append("[Остальные файлы пропущены: общий лимит распаковки 128 МБ достигнут.]")
                    break

                total_uncompressed += item.file_size
                try:
                    file_data = archive.read(item)
                    content = _extract_file_content(
                        relative_name, file_data, depth=depth
                    ).strip()
                    if not content:
                        continue

                    remaining_chars = _MAX_ZIP_TEXT_CHARS - total_chars
                    if remaining_chars <= 0:
                        skipped_files += 1
                        extracted.append("[Остальное содержимое пропущено: достигнут лимит текста 400 000 символов.]")
                        break
                    if len(content) > remaining_chars:
                        content = content[:remaining_chars] + "\n[Содержимое обрезано: достигнут лимит 400 000 символов.]"
                        extracted.append(f"===== {relative_name} =====\n{content}")
                        total_chars = _MAX_ZIP_TEXT_CHARS
                        skipped_files += 1
                        break

                    extracted.append(f"===== {relative_name} =====\n{content}")
                    total_chars += len(content)
                    processed_files += 1
                except (ValueError, RuntimeError, zipfile.BadZipFile) as exc:
                    extracted.append(f"[Файл {relative_name}: {exc}]")

        if not extracted:
            raise ValueError("В ZIP не найдено поддерживаемых файлов с извлекаемым содержимым.")

        logging.info(
            "ZIP extraction completed: files=%s skipped=%s uncompressed_bytes=%s chars=%s depth=%s elapsed=%.2fs",
            processed_files, skipped_files, total_uncompressed, total_chars,
            depth, time.monotonic() - started_at,
        )
        return "\n\n".join(extracted)
    except zipfile.BadZipFile as exc:
        raise ValueError("Файл не является корректным ZIP-архивом.") from exc


async def _document_to_text(message: types.Message) -> str:
    document = message.document
    if not document:
        raise ValueError("Документ не найден.")
    filename = document.file_name or "document"
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_FILE_EXTENSIONS:
        raise ValueError(
            "Этот формат не поддерживается. Принимаются TXT, MD, CSV, JSON, "
            "XML, код, PDF, DOCX, PPTX, XLSX и ZIP с такими файлами."
        )

    try:
        # Архив остаётся во временном файле: не создаём дополнительную копию
        # всего ZIP на 100+ МБ в оперативной памяти.
        async with _document_processing_semaphore:
            with tempfile.TemporaryFile(mode="w+b") as buffer:
                await message.bot.download(document, destination=buffer)
                buffer.flush()
                extraction_started_at = time.monotonic()
                if extension == ".zip":
                    content = await asyncio.to_thread(_extract_zip_content, buffer)
                else:
                    buffer.seek(0)
                    raw = buffer.read()
                    content = await asyncio.to_thread(_extract_file_content, filename, raw)
    except Exception as exc:
        logging.exception("Не удалось скачать или обработать документ Telegram: %s", filename)
        if isinstance(exc, (ValueError, RuntimeError)):
            raise
        raise RuntimeError("Не удалось скачать или обработать файл. Проверь размер и целостность архива.") from exc

    content = content.strip()
    logging.info(
        "Document extraction completed: filename=%s size=%s chars=%s elapsed=%.2fs",
        filename, document.file_size, len(content), time.monotonic() - extraction_started_at,
    )
    if not content:
        raise ValueError("В файле не удалось найти текст для передачи модели.")
    return (
        f"Пользователь приложил файл: {filename}\n"
        f"Извлечённое содержимое:\n{content}"
    )




def _extract_github_reference(user_text: str) -> Optional[str]:
    match = re.search(
        r"https?://(?:www\.)?github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?(?:/[^\s]*)?",
        user_text,
        re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(0).rstrip(".,;:!?)\\]}>")



def _github_reference_has_branch(reference: Optional[str]) -> bool:
    if not reference:
        return False
    return bool(re.search(
        r"github\.com/[^/\s]+/[^/\s]+/(?:tree|blob)/",
        reference,
        re.IGNORECASE,
    ))


def _extract_github_branch_name(user_text: str) -> Optional[str]:
    """Read an explicitly labelled branch name, e.g. 'ветка: feature/api'."""
    patterns = (
        r"\bветк(?:а|у|е)\s*[:=]\s*[`\"]?([A-Za-z0-9][A-Za-z0-9._/-]*)",
        r"\bв\s+ветке\s+[`\"]?([A-Za-z0-9][A-Za-z0-9._/-]*)",
        r"\bbranch\s*[:=]\s*[`\"]?([A-Za-z0-9][A-Za-z0-9._/-]*)",
    )
    for pattern in patterns:
        match = re.search(pattern, user_text, re.IGNORECASE)
        if match:
            return match.group(1).rstrip(".,;:!?)\\]}>")
    return None



def _is_github_change_request(user_text: str) -> bool:
    """Detect explicit repository-edit requests, not analysis or review requests."""
    negative = re.search(
        r"\b(?:не|don't|do not)\s+(?:изменяй|изменять|исправляй|исправлять|меняй|менять|modify|change|edit)\b",
        user_text,
        re.IGNORECASE,
    )
    if negative:
        return False
    patterns = (
        r"\b(?:исправ(?:ь|ить|ьте)|почини(?:ть)?|добав(?:ь|ить|ьте)|дополни(?:ть)?|"
        r"встав(?:ь|ить|ьте)|измени(?:ть)?|поменя(?:й|ть|йте)|обнов(?:и|ить|ите)|"
        r"удали(?:ть)?|созда(?:й|ть|йте)|реализу(?:й|йте|йте)|перепиши(?:ть)?|"
        r"переработа(?:й|ть|йте)|рефактор(?:и|ить|ируй|ируйте))\b",
        r"\b(?:внеси|внесите)\s+изменени",
        r"\b(?:fix|patch|add|create|update|remove|delete|edit|modify|implement|refactor)\b",
    )
    return any(re.search(pattern, user_text, re.IGNORECASE) for pattern in patterns)


def _extract_github_files(reply: str):
    pattern = re.compile(
        r"\[\[GITHUB_FILE:\s*([^\]\r\n]+)\]\][ \t]*\r?\n(.*?)\r?\n?\[\[/GITHUB_FILE\]\]",
        re.DOTALL | re.IGNORECASE,
    )
    files = []
    for match in pattern.finditer(reply):
        path = match.group(1).strip().replace("\\", "/")
        content = match.group(2)
        if not path or path.startswith("/") or any(part in ("", ".", "..") for part in path.split("/")):
            raise ValueError("Модель вернула недопустимый путь GitHub-файла: " + path[:120])
        # Пустые файлы допустимы в GitHub (например, пустой __init__.py).
        files.append((path, content))
    return pattern.sub("", reply).strip(), files


def _normalize_generated_path(raw_path: str, fallback: str) -> str:
    """Validate a relative path and preserve nested project folders."""
    path = str(raw_path or "").strip().replace("\\", "/")
    if not path:
        path = fallback
    if path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        raise ValueError("Модель вернула абсолютный путь файла: " + path[:120])
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Модель вернула небезопасный путь файла: " + path[:120])
    safe_parts = []
    for part in parts:
        clean = re.sub(r"[^A-Za-z0-9А-Яа-яёЁ._-]", "_", part)
        if clean in {"", ".", ".."}:
            raise ValueError("Модель вернула недопустимый путь файла: " + path[:120])
        safe_parts.append(clean)
    normalized = "/".join(safe_parts)
    if len(normalized) > 240:
        raise ValueError("Путь файла слишком длинный: " + normalized[:120])
    return normalized


def _extract_generated_files(reply: str):
    pattern = re.compile(
        r"\[\[FILE:\s*([^\]\r\n]+)\]\][ \t]*\r?\n(.*?)\r?\n?\[\[/FILE\]\]",
        re.DOTALL | re.IGNORECASE,
    )
    files = []
    empty_files = 0
    for match in pattern.finditer(reply):
        raw_path = match.group(1).strip().replace("\\", "/")
        filename = _normalize_generated_path(raw_path, f"generated_{len(files) + 1}.txt")
        file_content = match.group(2)
        if not file_content.strip():
            empty_files += 1
            logging.info("Пустой файл %s сохранён: ZIP поддерживает файлы нулевого размера.", filename)
        encoded = file_content.encode("utf-8")
        files.append((filename, encoded))
    visible_reply = pattern.sub("", reply).strip()
    if empty_files:
        warning = (
            f"Пустых файлов: {empty_files}. Они сохранены в ZIP: архив допускает файлы нулевого размера."
        )
        visible_reply = f"{visible_reply}\n\nℹ️ {warning}".strip()
    return visible_reply, files, empty_files


def _build_generated_zip(files: List[tuple]) -> bytes:
    """Create a ZIP while preserving the project's relative directory structure."""
    output = io.BytesIO()
    used_names = set()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, (filename, file_content) in enumerate(files, start=1):
            safe_name = _normalize_generated_path(filename, f"generated_{index}.txt")
            candidate = safe_name
            path_obj = Path(safe_name)
            parent = path_obj.parent.as_posix()
            stem = path_obj.stem
            suffix = path_obj.suffix
            counter = 2
            while candidate.casefold() in used_names:
                renamed = f"{stem}_{counter}{suffix}"
                candidate = f"{parent}/{renamed}" if parent != "." else renamed
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



def _is_archive_request(user_text: str) -> bool:
    terms = (r"\bzip\b", r"архив", r"все файлы", r"готовый проект")
    return any(re.search(term, user_text, re.IGNORECASE) for term in terms)


def _is_archive_followup_request(user_text: str, history: List[Dict[str, Any]]) -> bool:
    """Recognize short 'send it' follow-ups when the previous AI turn discussed an archive."""
    request = str(user_text or "").strip()
    if len(request) > 80 or not re.fullmatch(
        r"(?:скинь|пришли|отправь|дай|загрузи|выдай)(?:\s+(?:его|архив|файл|проект))?[.!?…]*",
        request,
        re.IGNORECASE,
    ):
        return False

    for item in reversed(history[-8:]):
        if not isinstance(item, dict) or item.get("role") != "assistant":
            continue
        content = str(item.get("content") or "")
        if re.search(r"\b(?:zip|архив|файлы собраны|полный комплект|проект готов)\b", content, re.IGNORECASE):
            return True
    return False


def _is_large_project_request(user_text: str) -> bool:
    """Определяет запросы на создание крупных многофайловых проектов."""
    project_terms = (
        r"\b(?:приложени|проект|сайт|веб[- ]?приложени|бот|игр|клон|копи|"
        r"application|website|webapp|project|clone|telegram)\w*\b"
    )
    action_terms = (
        r"\b(?:созда(?:й|ть|йте)|собер(?:и|ите)|разработа(?:й|ть|йте)|"
        r"сдела(?:й|ть|йте)|постро(?:й|ить|йте)|build|create|develop|make)\b"
    )
    return bool(
        re.search(project_terms, user_text, re.IGNORECASE)
        and re.search(action_terms, user_text, re.IGNORECASE)
    )


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


def _normalise_provider_usage(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    prompt = raw.get("prompt_tokens", raw.get("input_tokens", 0))
    completion = raw.get("completion_tokens", raw.get("output_tokens", 0))
    try:
        prompt = max(0, int(prompt or 0))
        completion = max(0, int(completion or 0))
        total = max(0, int(raw.get("total_tokens") or (prompt + completion)))
    except (TypeError, ValueError):
        return None
    if total <= 0:
        return None
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "estimated": False,
    }


async def _report_model_usage(
    callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]],
    raw_usage: Any,
    messages: List[Dict[str, str]],
    result: str,
) -> None:
    if callback is None:
        return
    usage = _normalise_provider_usage(raw_usage)
    if usage is None:
        prompt_chars = sum(
            len(str(item.get("content") or ""))
            for item in messages if isinstance(item, dict)
        )
        prompt_tokens = max(1, (prompt_chars + 2) // 3)
        completion_tokens = max(1, (len(result) + 2) // 3)
        usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "estimated": True,
        }
    await callback(usage)


_CONTINUATION_PROMPT = (
    "Продолжи предыдущий ответ строго с места остановки. "
    "Не повторяй уже выданный текст и не начинай ответ заново. "
    "Сохрани исходную задачу, форматирование и структуру. "
    "Заверши текущую мысль и полностью выполни исходный запрос. "
    "Верни только продолжение."
)


def _has_incomplete_structured_output(text: str) -> bool:
    """Detect truncated file payloads even when a provider reports finish_reason=stop."""
    lowered = str(text or "").lower()
    for opening, closing in (
        ("[[file:", "[[/file]]"),
        ("[[github_file:", "[[/github_file]]"),
    ):
        if lowered.count(opening) > lowered.count(closing):
            return True

    # An unclosed fenced block is another strong structural signal of truncation.
    value = str(text or "")
    if value.count("```") % 2 == 1:
        return True

    # Also catch a code payload that stops directly after an unfinished operator.
    has_code_payload = (
        "[[file:" in lowered
        or "[[github_file:" in lowered
        or "```" in value
    )
    if has_code_payload:
        last_line = next((line.strip() for line in reversed(value.splitlines()) if line.strip()), "")
        if re.search(r"(?:\+=|-=|\*=|/=|//=|%=|(?<![=!<>])=(?!=)|[+*/%&|^])\s*$", last_line):
            return True
        if re.search(r"\b(?:and|or|not|return|raise|yield|await)\s*$", last_line, re.IGNORECASE):
            return True
    return False



async def _continue_limited_model_response(
    model: str,
    previous_messages: List[Dict[str, str]],
    user_text: str,
    partial_result: str,
    raw_usage: Any,
    request_messages: List[Dict[str, str]],
    user_id: Optional[int],
    on_delta: Optional[Callable[[str], Awaitable[None]]],
    on_usage: Optional[Callable[[Dict[str, Any]], Awaitable[None]]],
    effort: Optional[str],
    continuation_depth: int,
    report_partial_usage: bool = True,
) -> str:
    """Continue a truncated response, validating the fully combined structure."""
    if report_partial_usage:
        await _report_model_usage(on_usage, raw_usage, request_messages, partial_result)
    if continuation_depth >= 8:
        raise RuntimeError(
            "Модель не завершила структуру ответа после 8 продолжений. "
            "Неполный результат не будет сохранён как готовый файл."
        )

    logging.warning(
        "Incomplete model response; continuing: model=%s depth=%s chars=%s",
        model, continuation_depth + 1, len(partial_result),
    )
    continuation_context = [
        *previous_messages[-MAX_CONTEXT_MESSAGES:],
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": partial_result},
    ]
    continuation = await _request_model(
        model,
        continuation_context,
        _CONTINUATION_PROMPT,
        user_id=user_id,
        on_delta=on_delta,
        on_usage=on_usage,
        effort=effort,
        _continuation_depth=continuation_depth + 1,
    )
    separator = (
        " " if partial_result and continuation
        and partial_result[-1].isalnum() and continuation[0].isalnum()
        else ""
    )
    combined_result = partial_result + separator + continuation
    if _has_incomplete_structured_output(combined_result):
        return await _continue_limited_model_response(
            model=model,
            previous_messages=previous_messages,
            user_text=user_text,
            partial_result=combined_result,
            raw_usage=None,
            request_messages=[],
            user_id=user_id,
            on_delta=on_delta,
            on_usage=on_usage,
            effort=effort,
            continuation_depth=continuation_depth + 1,
            report_partial_usage=False,
        )
    return combined_result


async def _request_claude_native(
    api_key: str,
    api_url: str,
    model: str,
    messages: List[Dict[str, str]],
    user_text: str,
    on_delta: Optional[Callable[[str], Awaitable[None]]],
    on_usage: Optional[Callable[[Dict[str, Any]], Awaitable[None]]],
    effort: Optional[str],
    user_id: Optional[int] = None,
    _continuation_depth: int = 0,
) -> str:
    """Use TokenPlan's Anthropic-compatible Messages endpoint for Claude effort control."""
    base_url = api_url.rstrip("/")
    if base_url.endswith("/chat/completions"):
        base_url = base_url[:-len("/chat/completions")]
    endpoint = base_url + "/messages"
    system_prompt = next(
        (str(item.get("content") or "") for item in messages if item.get("role") == "system"),
        "",
    )
    conversation = [
        {"role": item["role"], "content": item.get("content", "")}
        for item in messages
        if item.get("role") in {"user", "assistant"}
    ]
    conversation.append({"role": "user", "content": user_text})
    payload: Dict[str, Any] = {
        "model": model,
        "max_tokens": 16384,
        "stream": True,
        "system": system_prompt,
        "messages": conversation,
    }
    if effort:
        payload["output_config"] = {"effort": effort}
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
        "Accept": "text/event-stream, application/json",
    }
    timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_read=None)
    request_messages = [{"role": "system", "content": system_prompt}, *conversation]
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(endpoint, headers=headers, json=payload) as response:
            body_type = response.headers.get("Content-Type", "").lower()
            if response.status != 200:
                body = await response.text()
                if user_id is not None and _custom_api_pair(user_id) is None and _quota_error_response(response.status, body):
                    raise _TokenPlanQuotaExceeded(api_key, body[:500] or "Лимит API исчерпан")
                if response.status == 502:
                    request_id = (
                        response.headers.get("x-request-id")
                        or response.headers.get("cf-ray")
                        or response.headers.get("x-correlation-id")
                    )
                    raise _TokenPlanHTTP502(
                        body[:500] or "API-шлюз вернул HTTP 502",
                        request_id,
                    )
                raise RuntimeError(
                    f"TokenPlan Claude API HTTP {response.status}: "
                    f"{body[:500] or 'Нет подробностей от сервера'}"
                )
            if "text/event-stream" in body_type:
                chunks: List[str] = []
                raw_usage: Dict[str, Any] = {}
                message_stopped = False
                stop_reason: Optional[str] = None
                async for raw_line in response.content:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data_text = line[5:].strip()
                    if not data_text or data_text == "[DONE]":
                        continue
                    try:
                        event = json.loads(data_text)
                    except json.JSONDecodeError:
                        continue
                    event_type = event.get("type")
                    if event_type == "message_start":
                        usage = (event.get("message") or {}).get("usage")
                        if isinstance(usage, dict):
                            raw_usage.update(usage)
                    elif event_type == "message_delta":
                        usage = event.get("usage")
                        if isinstance(usage, dict):
                            raw_usage.update(usage)
                        delta = event.get("delta") or {}
                        if isinstance(delta, dict) and delta.get("stop_reason") is not None:
                            stop_reason = str(delta["stop_reason"])
                    elif event_type == "content_block_delta":
                        delta = event.get("delta") or {}
                        piece = delta.get("text")
                        if isinstance(piece, str) and piece:
                            chunks.append(piece)
                            if on_delta is not None:
                                await on_delta(piece)
                    elif event_type == "message_stop":
                        message_stopped = True
                    elif event_type == "error":
                        raise RuntimeError("TokenPlan Claude stream error: " + str(event.get("error", {}))[:400])
                result = "".join(chunks).strip()
                if not message_stopped:
                    raise RuntimeError(
                        "Поток Claude завершился без события message_stop. "
                        "Ответ считается незавершённым; следующий авто-запрос не будет запущен."
                    )
            else:
                data = await response.json(content_type=None)
                if not isinstance(data, dict):
                    raise RuntimeError("TokenPlan Claude API вернул ответ неизвестного формата")
                content = data.get("content") or []
                result = "".join(
                    str(item.get("text") or "")
                    for item in content
                    if isinstance(item, dict) and item.get("type") == "text"
                ).strip()
                raw_usage = data.get("usage") or {}
                stop_reason = str(data.get("stop_reason") or "") or None

            if not result:
                raise RuntimeError("TokenPlan Claude API вернул пустой ответ.")
            if "text/event-stream" not in body_type and on_delta is not None:
                await on_delta(result)

            if stop_reason == "max_tokens" or _has_incomplete_structured_output(result):
                history_without_system = [
                    item for item in messages if item.get("role") in {"user", "assistant"}
                ]
                return await _continue_limited_model_response(
                    model=model,
                    previous_messages=history_without_system,
                    user_text=user_text,
                    partial_result=result,
                    raw_usage=raw_usage,
                    request_messages=request_messages,
                    user_id=user_id,
                    on_delta=on_delta,
                    on_usage=on_usage,
                    effort=effort,
                    continuation_depth=_continuation_depth,
                )

            await _report_model_usage(on_usage, raw_usage, request_messages, result)
            logging.info(
                "Claude stream completed: model=%s stop_reason=%s chars=%s usage=%s",
                model, stop_reason or "unknown", len(result), _normalise_provider_usage(raw_usage),
            )
            return result


class _TokenPlanHTTP502(RuntimeError):
    def __init__(self, detail: str, request_id: Optional[str] = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.request_id = request_id


class _TokenPlanQuotaExceeded(RuntimeError):
    def __init__(self, api_key: str, detail: str) -> None:
        super().__init__(detail)
        self.api_key = api_key
        self.detail = detail


def _system_api_keys() -> List[str]:
    keys = []
    for name in (API_KEY_ENV, BACKUP_API_KEY_ENV):
        key = str(os.getenv(name, "")).strip()
        if key and key not in keys:
            keys.append(key)
    return keys


def _select_system_api_key() -> str:
    now = time.monotonic()
    for key in _system_api_keys():
        if _api_key_cooldowns.get(key, 0.0) <= now:
            return key
    keys = _system_api_keys()
    return keys[0] if keys else ""


def _quota_error_response(status: int, body: str) -> bool:
    lowered = body.lower()
    markers = (
        "insufficient_quota", "quota exceeded", "quota_exceeded",
        "billing hard limit", "usage limit", "token limit",
        "credits exhausted", "out of credits", "лимит исчерпан",
    )
    return status == 429 or (status in (402, 403) and any(x in lowered for x in markers))


async def _wait_for_available_system_key(exhausted_key: str) -> str:
    keys = _system_api_keys()
    if not keys:
        raise RuntimeError("Не заданы TOKENPLAN_API_KEY и TOKENPLAN_API_KEY_BACKUP.")
    _api_key_cooldowns[exhausted_key] = time.monotonic() + API_KEY_COOLDOWN_SECONDS
    while True:
        now = time.monotonic()
        for key in keys:
            if _api_key_cooldowns.get(key, 0.0) <= now:
                return key
        delay = min(max(1.0, _api_key_cooldowns.get(key, 0.0) - now) for key in keys)
        logging.warning("Оба TokenPlan API-ключа временно исчерпаны; ожидание %.0f секунд", delay)
        await asyncio.sleep(delay)


async def _request_model_once(
    model: str,
    previous_messages: List[Dict[str, str]],
    user_text: str,
    user_id: Optional[int] = None,
    on_delta: Optional[Callable[[str], Awaitable[None]]] = None,
    on_usage: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    effort: Optional[str] = None,
    _retry_attempt: int = 0,
    _continuation_depth: int = 0,
) -> str:
    custom_api = _custom_api_pair(user_id) if user_id is not None else None
    if custom_api is not None:
        # Личные реквизиты используются парой, без подстановки системных значений.
        api_key, api_url = custom_api
    else:
        api_key = _select_system_api_key()
        api_url = str(os.getenv("TOKENPLAN_BASE_URL") or DEFAULT_API_URL).strip()
    if not api_url.rstrip("/").endswith("/chat/completions"):
        api_url = api_url.rstrip("/") + "/chat/completions"
    if not api_key:
        raise RuntimeError(
            "Не задан TOKENPLAN_API_KEY. Добавь API-ключ TokenPlan в переменные "
            "окружения или в локальный .env-файл."
        )
    if not api_url:
        raise RuntimeError("Не задан TOKENPLAN_BASE_URL.")

    if model.startswith("claude") and custom_api is None:
        return await _request_claude_native(
            api_key, api_url, model,
            [{"role": "system", "content": _effective_system_prompt(user_id)}, *previous_messages[-MAX_CONTEXT_MESSAGES:]],
            user_text, on_delta, on_usage, effort,
            user_id=user_id,
            _continuation_depth=_continuation_depth,
        )

    payload = {
        "model": model,
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": 16384,
        "messages": [
            {"role": "system", "content": _effective_system_prompt(user_id)},
            *previous_messages[-MAX_CONTEXT_MESSAGES:],
            {"role": "user", "content": user_text},
        ],
    }
    if effort:
        payload["reasoning_effort"] = effort
    # Общий timeout и sock_read отключены: долгий поток не должен обрываться клиентом.
    timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_read=None)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream, application/json",
    }

    started_at = time.monotonic()
    first_token_at = None
    logging.info("TokenPlan request started: model=%s user_id=%s stream=true", model, user_id)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(api_url, headers=headers, json=payload) as response:
                content_type = response.headers.get("Content-Type", "").lower()
                request_id = (
                    response.headers.get("x-request-id")
                    or response.headers.get("cf-ray")
                    or response.headers.get("x-correlation-id")
                )
                logging.info(
                    "TokenPlan response headers: status=%s content_type=%s request_id=%s elapsed=%.1fs",
                    response.status, content_type, request_id or "-", time.monotonic() - started_at,
                )
                if response.status != 200:
                    raw_body = await response.text()
                    if custom_api is None and _quota_error_response(response.status, raw_body):
                        raise _TokenPlanQuotaExceeded(api_key, raw_body[:500] or "Лимит API исчерпан")
                    try:
                        data = json.loads(raw_body)
                    except json.JSONDecodeError:
                        data = {}
                    error = data.get("error", {}) if isinstance(data, dict) else {}
                    if isinstance(error, dict):
                        detail = error.get("message") or error.get("type")
                    else:
                        detail = str(error) if error else ""
                    detail = detail or raw_body[:500] or "Нет подробностей от сервера"
                    request_suffix = f"; request_id={request_id}" if request_id else ""
                    response_error_text = (detail + " " + raw_body).lower()
                    unsupported_parameter = any(
                        term in response_error_text
                        for term in ("unsupported", "not support", "unknown", "invalid", "unexpected", "extra inputs")
                    )
                    stream_rejected = (
                        response.status in (400, 422)
                        and ("stream" in response_error_text or "stream_options" in response_error_text)
                        and unsupported_parameter
                    )
                    usage_option_rejected = (
                        response.status in (400, 422)
                        and ("stream_options" in response_error_text or "include_usage" in response_error_text)
                        and unsupported_parameter
                    )
                    if stream_rejected or usage_option_rejected:
                        logging.warning("TokenPlan rejected streaming usage options; retrying once without streaming")
                        payload.pop("stream", None)
                        payload.pop("stream_options", None)
                        async with session.post(api_url, headers=headers, json=payload) as fallback:
                            fallback_body = await fallback.text()
                            try:
                                fallback_data = json.loads(fallback_body)
                            except json.JSONDecodeError:
                                fallback_data = {}
                            if fallback.status != 200:
                                if fallback.status == 502:
                                    fallback_request_id = (
                                        fallback.headers.get("x-request-id")
                                        or fallback.headers.get("cf-ray")
                                        or fallback.headers.get("x-correlation-id")
                                    )
                                    raise _TokenPlanHTTP502(
                                        fallback_body[:500] or "API-шлюз вернул HTTP 502",
                                        fallback_request_id,
                                    )
                                raise RuntimeError(
                                    f"TokenPlan HTTP {fallback.status}: "
                                    f"{fallback_body[:500] or 'Нет подробностей от сервера'}"
                                )
                            if not isinstance(fallback_data, dict):
                                raise RuntimeError("TokenPlan вернул ответ в неизвестном формате")
                            fallback_result = _extract_reply(fallback_data)
                            fallback_choices = fallback_data.get("choices") or []
                            fallback_finish_reason = (
                                str(fallback_choices[0].get("finish_reason") or "")
                                if fallback_choices and isinstance(fallback_choices[0], dict)
                                else ""
                            )
                            if on_delta is not None:
                                await on_delta(fallback_result)
                            if fallback_finish_reason in {"length", "max_tokens"} or _has_incomplete_structured_output(fallback_result):
                                return await _continue_limited_model_response(
                                    model=model,
                                    previous_messages=previous_messages,
                                    user_text=user_text,
                                    partial_result=fallback_result,
                                    raw_usage=fallback_data.get("usage"),
                                    request_messages=payload["messages"],
                                    user_id=user_id,
                                    on_delta=on_delta,
                                    on_usage=on_usage,
                                    effort=effort,
                                    continuation_depth=_continuation_depth,
                                )
                            if fallback_finish_reason == "content_filter":
                                raise RuntimeError(
                                    "Провайдер остановил генерацию фильтром содержимого. Ответ не будет применён к GitHub."
                                )
                            await _report_model_usage(
                                on_usage, fallback_data.get("usage"), payload["messages"], fallback_result
                            )
                            logging.info(
                                "TokenPlan non-stream fallback completed: model=%s finish_reason=%s chars=%s elapsed=%.1fs",
                                model, fallback_finish_reason or "unknown", len(fallback_result),
                                time.monotonic() - started_at,
                            )
                            return fallback_result
                    if response.status == 502:
                        raise _TokenPlanHTTP502(detail, request_id)
                    if response.status in (500, 503, 504) and _retry_attempt < 2:
                        delay = (2, 5)[_retry_attempt]
                        logging.warning(
                            "TokenPlan temporary HTTP error: model=%s status=%s attempt=%s/3 retry_in=%ss request_id=%s",
                            model, response.status, _retry_attempt + 1, delay, request_id or "-",
                        )
                        await asyncio.sleep(delay)
                        return await _request_model(
                            model,
                            previous_messages,
                            user_text,
                            user_id=user_id,
                            on_delta=on_delta,
                            on_usage=on_usage,
                            effort=effort,
                            _retry_attempt=_retry_attempt + 1,
                        )
                    if response.status in (500, 503, 504):
                        detail = (
                            f"{detail}. Временная ошибка API-шлюза сохранялась после "
                            "3 попыток (первичная + 2 повтора)."
                        )
                    raise RuntimeError(f"TokenPlan HTTP {response.status}: {detail}{request_suffix}")

                if "text/event-stream" in content_type:
                    chunks: List[str] = []
                    stream_finished = False
                    stream_done_marker = False
                    finish_reason_value: Optional[str] = None
                    reported_usage: Any = None
                    async for raw_line in response.content:
                        line = raw_line.decode("utf-8", errors="replace").strip()
                        if not line.startswith("data:"):
                            continue
                        event_data = line[5:].strip()
                        if event_data == "[DONE]":
                            stream_finished = True
                            stream_done_marker = True
                            break
                        if not event_data:
                            continue
                        try:
                            event = json.loads(event_data)
                        except json.JSONDecodeError:
                            logging.debug("Пропущено не-JSON SSE-событие TokenPlan")
                            continue
                        if isinstance(event, dict) and event.get("error"):
                            raise RuntimeError("TokenPlan stream error: " + str(event["error"])[:500])
                        if isinstance(event, dict) and isinstance(event.get("usage"), dict):
                            reported_usage = event["usage"]
                        choices = event.get("choices", []) if isinstance(event, dict) else []
                        if not isinstance(choices, list):
                            continue
                        for choice in choices:
                            if not isinstance(choice, dict):
                                continue
                            delta = choice.get("delta") or {}
                            piece = delta.get("content") if isinstance(delta, dict) else None
                            if isinstance(piece, list):
                                piece = "".join(
                                    part.get("text", "")
                                    for part in piece
                                    if isinstance(part, dict) and isinstance(part.get("text"), str)
                                )
                            if isinstance(piece, str) and piece:
                                chunks.append(piece)
                                if first_token_at is None:
                                    first_token_at = time.monotonic()
                                    logging.info(
                                        "TokenPlan first token: model=%s user_id=%s ttft=%.1fs",
                                        model, user_id, first_token_at - started_at,
                                    )
                                if on_delta is not None:
                                    await on_delta(piece)
                            if choice.get("finish_reason") is not None:
                                stream_finished = True
                                finish_reason_value = str(choice.get("finish_reason"))
                    result = "".join(chunks).strip()
                    if not result:
                        raise RuntimeError(
                            "TokenPlan включил потоковый режим, но не передал текст ответа. "
                            "Проверь поддержку streaming у API."
                        )
                    if not stream_done_marker and finish_reason_value is None:
                        logging.error(
                            "TokenPlan SSE stream ended without a completion marker: model=%s chars=%s elapsed=%.1fs",
                            model, len(result), time.monotonic() - started_at,
                        )
                        raise RuntimeError(
                            "Поток ответа TokenPlan оборвался без признака завершения генерации. "
                            "Неполный ответ не сохранён и не будет применён к GitHub."
                        )
                    if finish_reason_value in {"length", "max_tokens"} or _has_incomplete_structured_output(result):
                        return await _continue_limited_model_response(
                            model=model,
                            previous_messages=previous_messages,
                            user_text=user_text,
                            partial_result=result,
                            raw_usage=reported_usage,
                            request_messages=payload["messages"],
                            user_id=user_id,
                            on_delta=on_delta,
                            on_usage=on_usage,
                            effort=effort,
                            continuation_depth=_continuation_depth,
                        )
                    if finish_reason_value == "content_filter":
                        raise RuntimeError(
                            "Провайдер остановил генерацию фильтром содержимого. Ответ не будет применён к GitHub."
                        )
                    if not stream_done_marker:
                        logging.info(
                            "TokenPlan SSE stream completed by finish_reason=%s without [DONE]: model=%s elapsed=%.1fs",
                            finish_reason_value, model, time.monotonic() - started_at,
                        )
                    await _report_model_usage(
                        on_usage, reported_usage, payload["messages"], result
                    )
                    logging.info(
                        "TokenPlan stream completed: model=%s finish_reason=%s usage=%s chars=%s elapsed=%.1fs",
                        model, finish_reason_value or "unknown",
                        _normalise_provider_usage(reported_usage), len(result),
                        time.monotonic() - started_at,
                    )
                    return result

                # Некоторые OpenAI-совместимые шлюзы игнорируют stream=true и возвращают JSON.
                raw_body = await response.text()
                try:
                    data = json.loads(raw_body)
                except json.JSONDecodeError:
                    data = {}
                if not isinstance(data, dict):
                    raise RuntimeError("TokenPlan вернул ответ в неизвестном формате")
                result = _extract_reply(data)
                response_choices = data.get("choices") or []
                finish_reason_value = (
                    str(response_choices[0].get("finish_reason") or "")
                    if response_choices and isinstance(response_choices[0], dict)
                    else ""
                )
                if on_delta is not None:
                    await on_delta(result)
                if finish_reason_value in {"length", "max_tokens"} or _has_incomplete_structured_output(result):
                    return await _continue_limited_model_response(
                        model=model,
                        previous_messages=previous_messages,
                        user_text=user_text,
                        partial_result=result,
                        raw_usage=data.get("usage"),
                        request_messages=payload["messages"],
                        user_id=user_id,
                        on_delta=on_delta,
                        on_usage=on_usage,
                        effort=effort,
                        continuation_depth=_continuation_depth,
                    )
                if finish_reason_value == "content_filter":
                    raise RuntimeError(
                        "Провайдер остановил генерацию фильтром содержимого. Ответ не будет применён к GitHub."
                    )
                await _report_model_usage(
                    on_usage, data.get("usage"), payload["messages"], result
                )
                logging.info(
                    "TokenPlan non-stream response completed: model=%s finish_reason=%s usage=%s chars=%s elapsed=%.1fs",
                    model, finish_reason_value or "unknown",
                    _normalise_provider_usage(data.get("usage")), len(result),
                    time.monotonic() - started_at,
                )
                return result
    except asyncio.TimeoutError as exc:
        raise RuntimeError("Истекло время ожидания ответа TokenPlan (10 минут).") from exc
    except aiohttp.ClientError as exc:
        logging.warning("Ошибка соединения с TokenPlan: %s", type(exc).__name__)
        raise RuntimeError("Не удалось подключиться к TokenPlan. Проверь адрес API и соединение.") from exc
    except ValueError as exc:
        raise RuntimeError(f"Некорректный ответ TokenPlan: {exc}") from exc


async def _request_model(
    model: str,
    previous_messages: List[Dict[str, str]],
    user_text: str,
    user_id: Optional[int] = None,
    on_delta: Optional[Callable[[str], Awaitable[None]]] = None,
    on_usage: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    effort: Optional[str] = None,
    _retry_attempt: int = 0,
    _continuation_depth: int = 0,
) -> str:
    """Retry HTTP 502 every five minutes until the provider accepts the request."""
    retry_count = 0
    while True:
        try:
            return await _request_model_once(
                model,
                previous_messages,
                user_text,
                user_id=user_id,
                on_delta=on_delta,
                on_usage=on_usage,
                effort=effort,
                _retry_attempt=_retry_attempt,
                _continuation_depth=_continuation_depth,
            )
        except _TokenPlanHTTP502 as exc:
            retry_count += 1
            logging.warning(
                "TokenPlan HTTP 502: model=%s retry=%s retry_in=300s request_id=%s detail=%s",
                model, retry_count, exc.request_id or "-", exc.detail[:300],
            )
            await asyncio.sleep(300)


@router.callback_query(lambda callback: callback.data == "ai_menu")
async def ai_menu(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    user_id = callback.from_user.id
    chats = _user_chats(user_id)
    premium = is_premium(user_id)
    if is_admin(user_id):
        quota_text = "\n\nАдминистраторский доступ: лимиты ИИ отключены."
    elif premium:
        quota_text = (
            "\n\nТокены учитываются отдельно для каждой модели. "
            "Открой настройки нужного чата, чтобы увидеть расход за 5 часов и 7 дней."
        )
    else:
        quota_text = "\n\n" + format_ai_quota(user_id, "", False)
    await callback.message.edit_text(
        "💬 <b>Чат с ИИ</b>\n\nСоздавай отдельные диалоги. Контекст диалогов сохраняется."
        + quota_text,
        parse_mode="HTML",
        reply_markup=_root_keyboard(),
    )



@router.callback_query(lambda callback: callback.data == "ai_auto")
async def ai_auto_menu(callback: types.CallbackQuery, state: FSMContext):
    task = _auto_tasks.get(callback.from_user.id)
    running = bool(task and not task.done())
    if not is_premium(callback.from_user.id) and not running:
        await callback.answer("Авто-запрос доступен только пользователям Premium.", show_alert=True)
        return
    await callback.answer()
    await state.clear()
    await callback.message.edit_text(
        _auto_request_screen_text(callback.from_user.id),
        parse_mode="HTML",
        reply_markup=_auto_request_keyboard(callback.from_user.id),
    )


@router.callback_query(lambda callback: callback.data == "ai_auto_prompt")
async def ai_auto_prompt(callback: types.CallbackQuery, state: FSMContext):
    if not is_premium(callback.from_user.id):
        await callback.answer("Авто-запрос доступен только пользователям Premium.", show_alert=True)
        return
    await callback.answer()
    await state.set_state(AIChatStates.waiting_auto_prompt)
    await callback.message.edit_text(
        "✍️ Отправь текст запроса, который бот будет повторять. "
        "Максимальная длина — 20 000 символов. Для отмены отправь /cancel."
    )


@router.message(AIChatStates.waiting_auto_prompt)
async def ai_auto_save_prompt(message: types.Message, state: FSMContext):
    if not is_premium(message.from_user.id):
        await state.clear()
        await message.answer("Авто-запрос доступен только пользователям Premium.")
        return
    value = (message.text or "").strip()
    if value.lower() in {"/cancel", "отмена"}:
        await state.clear()
        await message.answer(
            _auto_request_screen_text(message.from_user.id),
            parse_mode="HTML",
            reply_markup=_auto_request_keyboard(message.from_user.id),
        )
        return
    if not value:
        await message.answer("Запрос не должен быть пустым.")
        return
    if len(value) > 20000:
        await message.answer("Запрос слишком длинный. Максимум — 20 000 символов.")
        return
    settings = _user_settings(message.from_user.id)
    previous = settings.get("auto_prompt")
    settings["auto_prompt"] = value
    if not _save_user_settings():
        if previous is None:
            settings.pop("auto_prompt", None)
        else:
            settings["auto_prompt"] = previous
        await message.answer("Не удалось сохранить запрос. Проверь постоянное хранилище.")
        return
    await state.clear()
    await message.answer(
        _auto_request_screen_text(message.from_user.id),
        parse_mode="HTML",
        reply_markup=_auto_request_keyboard(message.from_user.id),
    )


@router.callback_query(lambda callback: callback.data == "ai_auto_choose_chat")
async def ai_auto_choose_chat(callback: types.CallbackQuery, state: FSMContext):
    if not is_premium(callback.from_user.id):
        await callback.answer("Авто-запрос доступен только пользователям Premium.", show_alert=True)
        return
    chats = _user_chats(callback.from_user.id)
    if not chats:
        await callback.answer("Сначала создай хотя бы один ИИ-чат.", show_alert=True)
        return
    rows = []
    for chat in sorted(chats, key=lambda item: str(item.get("updated_at") or ""), reverse=True)[:50]:
        label = _short_title(
            f"{chat.get('title') or 'Без названия'} · {chat.get('model') or 'unknown'}", 48
        )
        selected = str(_user_settings(callback.from_user.id).get("auto_chat_id") or "") == str(chat["id"])
        rows.append([InlineKeyboardButton(
            text=("✅ " if selected else "") + label,
            callback_data=f"ai_auto_chat_{chat['id']}",
            style=ButtonStyle.SUCCESS if selected else ButtonStyle.PRIMARY,
        )])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="ai_auto")])
    await callback.answer()
    await state.clear()
    await callback.message.edit_text(
        "💬 Выбери ИИ-чат, в который авто-запрос будет отправлять сообщения:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(lambda callback: callback.data and callback.data.startswith("ai_auto_chat_"))
async def ai_auto_set_chat(callback: types.CallbackQuery):
    if not is_premium(callback.from_user.id):
        await callback.answer("Авто-запрос доступен только пользователям Premium.", show_alert=True)
        return
    chat_id = callback.data[len("ai_auto_chat_"):]
    chat = _find_chat(callback.from_user.id, chat_id)
    if not chat:
        await callback.answer("Чат не найден.", show_alert=True)
        return
    settings = _user_settings(callback.from_user.id)
    previous = settings.get("auto_chat_id")
    settings["auto_chat_id"] = chat_id
    if not _save_user_settings():
        if previous is None:
            settings.pop("auto_chat_id", None)
        else:
            settings["auto_chat_id"] = previous
        await callback.answer("Не удалось сохранить выбранный чат.", show_alert=True)
        return
    await callback.answer("Чат выбран.")
    await callback.message.edit_text(
        _auto_request_screen_text(callback.from_user.id),
        parse_mode="HTML",
        reply_markup=_auto_request_keyboard(callback.from_user.id),
    )


@router.callback_query(lambda callback: callback.data == "ai_auto_help")
async def ai_auto_help(callback: types.CallbackQuery):
    if not is_premium(callback.from_user.id):
        await callback.answer("Авто-запрос доступен только пользователям Premium.", show_alert=True)
        return
    await callback.answer(
        "Старт: повторяет запрос после каждого ответа (не чаще раза в 5 с), пока модель не завершит этап "
        "или не внесёт изменения в GitHub. На такой контрольной точке автоповтор останавливается. "
        "Стоп: ждёт текущий ответ и не запускает следующий цикл. Лимиты действуют.",
        show_alert=True,
    )


def _auto_turn_requires_checkpoint(chat: Optional[Dict[str, Any]]) -> bool:
    """Останавливает автоповтор на завершённом этапе и после побочных действий в GitHub."""
    if not chat:
        return False
    last_answer = next(
        (
            str(item.get("content") or "")
            for item in reversed(chat.get("messages", []))
            if isinstance(item, dict) and item.get("role") == "assistant"
        ),
        "",
    ).strip().lower()
    if not last_answer:
        return False

    checkpoint_markers = (
        "следующий этап",
        "продолжать ли",
        "продолжать?",
        "подтверди следующий этап",
        "приступать к работе?",
        "изменения записаны непосредственно в ветку",
        "изменения отправлены в github",
        "pull request:",
        "задача полностью завершена",
        "все этапы завершены",
    )
    if any(marker in last_answer for marker in checkpoint_markers):
        return True
    return bool(re.search(r"(^|\n)\s*(готово|задача завершена|работа завершена)\s*[:.!]", last_answer))


async def _auto_request_loop(
    bot,
    user: types.User,
    target_chat: types.Chat,
    status_message: types.Message,
    state: FSMContext,
    chat_id: str,
    prompt: str,
    stop_event: asyncio.Event,
) -> None:
    fake_message = _AutoRequestMessage(bot, user, target_chat)
    stopped_by_error = False
    stop_reason = "Остановлен."

    async def run_turn() -> bool:
        await state.set_state(AIChatStates.chatting)
        await state.update_data(ai_chat_id=chat_id)
        return await _process_ai_chat_message(
            fake_message,
            state,
            user_text_override=prompt,
            skip_duplicate=True,
        )

    async def wait_for_request_cooldown() -> None:
        remaining = REQUEST_COOLDOWN_SECONDS - (
            time.monotonic() - _last_request_at.get(user.id, 0.0)
        )
        if remaining <= 0:
            return
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=remaining + 0.15)
        except asyncio.TimeoutError:
            pass

    try:
        while True:
            if not is_premium(user.id):
                stopped_by_error = True
                stop_reason = "Premium истёк. Авто-запрос остановлен."
                break

            # Остановка теперь означает «не запускать следующий цикл».
            # Никакого дополнительного финального запроса после нажатия «Стоп».
            if stop_event.is_set():
                stop_reason = "Остановлен после текущего ответа."
                break

            success = await run_turn()
            if not success:
                stopped_by_error = True
                stop_reason = "Остановлен из-за ошибки или исчерпания лимита."
                break

            chat = _find_chat(user.id, chat_id)
            if _auto_turn_requires_checkpoint(chat):
                stop_reason = (
                    "Пауза на контрольной точке: этап завершён или внесены изменения в GitHub. "
                    "Проверь результат и подтверди дальнейшую работу вручную."
                )
                break

            if stop_event.is_set():
                stop_reason = "Остановлен после текущего ответа."
                break

            # Повторяем только пока модель не выдала контрольную точку.
            await wait_for_request_cooldown()
    except asyncio.CancelledError:
        raise
    except Exception:
        stopped_by_error = True
        stop_reason = "Остановлен из-за внутренней ошибки."
        logging.exception("Ошибка авто-запроса пользователя %s", user.id)
    finally:
        _auto_tasks.pop(user.id, None)
        _auto_stop_events.pop(user.id, None)
        try:
            await status_message.edit_text(
                _auto_request_screen_text(user.id, stop_reason),
                parse_mode="HTML",
                reply_markup=_auto_request_keyboard(user.id),
            )
        except Exception:
            logging.debug("Не удалось обновить меню авто-запроса", exc_info=True)


@router.callback_query(lambda callback: callback.data == "ai_auto_toggle")
async def ai_auto_toggle(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    task = _auto_tasks.get(user_id)
    if task and not task.done():
        stop_event = _auto_stop_events.get(user_id)
        if stop_event:
            stop_event.set()
        await callback.answer(
            "Текущий ответ завершится. Следующий запрос запускаться не будет.",
            show_alert=True,
        )
        await callback.message.edit_text(
            _auto_request_screen_text(user_id, "остановка после текущего ответа"),
            parse_mode="HTML",
            reply_markup=_auto_request_keyboard(user_id),
        )
        return
    if not is_premium(user_id):
        await callback.answer("Авто-запрос доступен только пользователям Premium.", show_alert=True)
        return

    settings = _user_settings(user_id)
    prompt = str(settings.get("auto_prompt") or "").strip()
    chat_id = str(settings.get("auto_chat_id") or "")
    chat = _find_chat(user_id, chat_id)
    if not prompt:
        await callback.answer("Сначала введи текст запроса.", show_alert=True)
        return
    if not chat:
        await callback.answer("Сначала выбери существующий ИИ-чат.", show_alert=True)
        return
    model = str(chat.get("model") or "")
    if _is_paid_model(model) and not is_premium(user_id):
        await callback.answer("Для выбранной модели нужен Premium.", show_alert=True)
        return
    quota_error = _ai_quota_error(user_id, model)
    if quota_error:
        await callback.answer(quota_error[:190], show_alert=True)
        return
    if user_id in _active_requests:
        await callback.answer("Сначала дождись завершения текущего запроса.", show_alert=True)
        return

    await state.set_state(AIChatStates.chatting)
    await state.update_data(ai_chat_id=chat_id)
    stop_event = asyncio.Event()
    _auto_stop_events[user_id] = stop_event
    task = asyncio.create_task(
        _auto_request_loop(
            callback.bot,
            callback.from_user,
            callback.message.chat,
            callback.message,
            state,
            chat_id,
            prompt,
            stop_event,
        )
    )
    _auto_tasks[user_id] = task
    await callback.answer("Авто-запрос запущен.")
    await callback.message.edit_text(
        _auto_request_screen_text(user_id, "работает"),
        parse_mode="HTML",
        reply_markup=_auto_request_keyboard(user_id),
    )


@router.callback_query(lambda callback: callback.data == "ai_usage_overview")
async def ai_usage_overview(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    await callback.answer()
    if is_admin(user_id):
        quota_text = (
            "📊 <b>Администраторский доступ</b>\n\n"
            "Бесплатные запросы и токены: <b>без ограничений</b>."
        )
    elif not is_premium(user_id):
        quota_text = format_ai_quota(user_id, "", False)
    else:
        models = get_ai_usage_models(user_id, limit=10)
        models.extend(
            str(chat.get("model") or "")
            for chat in _user_chats(user_id)
            if chat.get("model") and str(chat.get("model")) not in models
        )
        models = models[:10]
        if models:
            quota_text = "\n\n".join(
                format_ai_quota(user_id, model, True) for model in models
            )
        else:
            quota_text = (
                "📊 <b>Лимиты Premium</b>\n\n"
                "Расхода пока нет. После первого запроса здесь появятся счётчики "
                "за 5 часов и 7 дней отдельно для каждой модели."
            )
    await callback.message.edit_text(
        quota_text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="ai_usage_overview")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="ai_menu")],
        ]),
    )


@router.callback_query(lambda callback: callback.data == "ai_settings")
async def ai_settings(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.edit_text(
        "⚙️ <b>Настройки ИИ-чатов</b>\n\n"
        "Пользовательский промпт и личный API настраиваются отдельно.",
        parse_mode="HTML",
        reply_markup=_ai_settings_keyboard(),
    )


@router.callback_query(lambda callback: callback.data == "ai_prompt_settings")
async def ai_prompt_settings(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    prompt = str(_user_settings(callback.from_user.id).get("custom_prompt") or "")
    status = f"Установлен, {len(prompt)} символов." if prompt else "Не установлен."
    await callback.message.edit_text(
        "<b>✍️ Свой промпт</b>\n\n"
        f"Текущий статус: {status}\n\n"
        "Можно отправить текстом или прикрепить TXT-файл. Промпт будет добавляться "
        "к каждому запросу. Обязательные инструкции по созданию файлов, ZIP и GitHub сохраняются.",
        parse_mode="HTML",
        reply_markup=_prompt_settings_keyboard(),
    )


@router.callback_query(lambda callback: callback.data == "ai_prompt_edit")
async def ai_prompt_edit(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(AIChatStates.waiting_prompt)
    await callback.message.edit_text(
        "Отправь промпт обычным текстом или TXT-файлом (до 64 КБ). "
        f"Максимальная длина — {MAX_CUSTOM_PROMPT_CHARS} символов. Для отмены отправь /cancel."
    )


@router.callback_query(lambda callback: callback.data == "ai_prompt_reset")
async def ai_prompt_reset(callback: types.CallbackQuery, state: FSMContext):
    settings = _user_settings(callback.from_user.id)
    old = settings.pop("custom_prompt", None)
    if not _save_user_settings():
        if old is not None:
            settings["custom_prompt"] = old
        await callback.answer("Не удалось сохранить настройки.", show_alert=True)
        return
    await callback.answer("Промпт сброшен.")
    await callback.message.edit_text(
        "<b>✍️ Свой промпт</b>\n\nПользовательский промпт сброшен.",
        parse_mode="HTML",
        reply_markup=_prompt_settings_keyboard(),
    )


@router.callback_query(lambda callback: callback.data == "ai_api_settings")
async def ai_api_settings(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    settings = _user_settings(callback.from_user.id)
    has_custom_key = bool(settings.get("api_key"))
    custom_url = str(settings.get("api_url") or "")
    if has_custom_key:
        key_status = "установлен"
    elif os.getenv(API_KEY_ENV) and not custom_url:
        key_status = "не задан; доступен системный ключ бота"
    else:
        key_status = "не установлен"
    if custom_url:
        parsed = urlsplit(custom_url)
        url_status = html.escape(parsed.netloc + parsed.path)
    else:
        url_status = "используется адрес из настроек бота"
    if has_custom_key and custom_url:
        api_status = "Личный API активен: запросы и список моделей используют только твои key и URL."
    elif has_custom_key or custom_url:
        api_status = (
            "Личный API настроен не полностью. Нужны одновременно API key и API URL; "
            "системные значения не подставляются."
        )
    else:
        api_status = "Используются настройки API из окружения бота."
    await callback.message.edit_text(
        "<b>🔑 Свой API key</b>\n\n"
        f"API key: <b>{key_status}</b>\n"
        f"API URL: <code>{url_status}</code>\n\n"
        f"{html.escape(api_status)}\n\n"
        "Ключ не показывается в меню и не записывается в историю переписки; на диске он хранится зашифрованно. "
        "Модели загружаются с endpoint /models твоего API. Для TokenPlan обычно подходит "
        "<code>https://tokenplan.lat/v1</code>.",
        parse_mode="HTML",
        reply_markup=_api_settings_keyboard(),
    )


@router.callback_query(lambda callback: callback.data == "ai_api_set_key")
async def ai_api_set_key(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(AIChatStates.waiting_api_key)
    await callback.message.edit_text(
        "Отправь API key одним текстовым сообщением. Ключ не будет повторён в ответе. "
        "Для отмены отправь /cancel."
    )


@router.callback_query(lambda callback: callback.data == "ai_api_set_url")
async def ai_api_set_url(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(AIChatStates.waiting_api_url)
    await callback.message.edit_text(
        "Отправь HTTPS Base URL, например https://tokenplan.lat/v1. "
        "Путь /chat/completions бот добавит автоматически. Для отмены отправь /cancel."
    )


@router.callback_query(lambda callback: callback.data == "ai_api_reset")
async def ai_api_reset(callback: types.CallbackQuery, state: FSMContext):
    settings = _user_settings(callback.from_user.id)
    old_key = settings.pop("api_key", None)
    old_url = settings.pop("api_url", None)
    if not _save_user_settings():
        if old_key is not None:
            settings["api_key"] = old_key
        if old_url is not None:
            settings["api_url"] = old_url
        await callback.answer("Не удалось сохранить настройки.", show_alert=True)
        return
    available_models.pop(str(callback.from_user.id), None)
    await callback.answer("Свой API сброшен.")
    await callback.message.edit_text(
        "<b>🔑 Свой API key</b>\n\nБудут использоваться настройки API из окружения бота.",
        parse_mode="HTML",
        reply_markup=_api_settings_keyboard(),
    )


@router.callback_query(lambda callback: callback.data == "ai_api_delete_key")
async def ai_api_delete_key(callback: types.CallbackQuery, state: FSMContext):
    settings = _user_settings(callback.from_user.id)
    old_key = settings.pop("api_key", None)
    if old_key is None:
        await callback.answer("Пользовательский ключ не задан.", show_alert=True)
        return
    if not _save_user_settings():
        settings["api_key"] = old_key
        await callback.answer("Не удалось удалить ключ.", show_alert=True)
        return
    available_models.pop(str(callback.from_user.id), None)
    await callback.answer("Ключ удалён.")
    message_text = "<b>🔑 Свой API key</b>\n\nКлюч удалён."
    if settings.get("api_url"):
        message_text += " Сохранённый API URL оставлен. Чтобы снова использовать личный API, задай новый ключ."
    else:
        message_text += " Теперь используются настройки API из окружения бота."
    await callback.message.edit_text(
        message_text,
        parse_mode="HTML",
        reply_markup=_api_settings_keyboard(),
    )


@router.callback_query(lambda callback: callback.data == "ai_models_page_info")
async def ai_models_page_info(callback: types.CallbackQuery):
    await callback.answer()


@router.callback_query(lambda callback: callback.data and callback.data.startswith("ai_chat_settings_"))
async def ai_chat_settings(callback: types.CallbackQuery, state: FSMContext):
    chat_id = callback.data[len("ai_chat_settings_"):]
    chat = _find_chat(callback.from_user.id, chat_id)
    if not chat:
        await callback.answer("Чат не найден.", show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_text(
        "<b>⚙️ Настройки чата</b>\n\n"
        f"Имя: <b>{html.escape(str(chat.get('title') or 'Новый чат'))}</b>\n"
        f"Модель: <code>{html.escape(str(chat.get('model') or 'неизвестна'))}</code>",
        parse_mode="HTML",
        reply_markup=_chat_settings_keyboard(chat_id, str(chat.get("model") or "unknown")),
    )


@router.callback_query(lambda callback: callback.data and callback.data.startswith("ai_chat_quota_"))
async def ai_chat_quota(callback: types.CallbackQuery):
    chat_id = callback.data[len("ai_chat_quota_"):]
    chat = _find_chat(callback.from_user.id, chat_id)
    if not chat:
        await callback.answer("Чат не найден.", show_alert=True)
        return
    model = str(chat.get("model") or "unknown")
    await callback.answer()
    await callback.message.edit_text(
        _ai_quota_text(callback.from_user.id, model),
        parse_mode="HTML",
        reply_markup=_chat_settings_keyboard(chat_id, model),
    )


@router.callback_query(lambda callback: callback.data and callback.data.startswith("ai_chat_effort_"))
async def ai_chat_effort(callback: types.CallbackQuery):
    chat_id = callback.data[len("ai_chat_effort_"):]
    chat = _find_chat(callback.from_user.id, chat_id)
    if not chat:
        await callback.answer("Чат не найден.", show_alert=True)
        return
    model = str(chat.get("model") or "")
    if not _supports_effort(model):
        await callback.answer("Для этой модели настройка усилия не поддерживается.", show_alert=True)
        return
    current = chat.get("reasoning_effort")
    current_label = next(
        (label for value, label in _effort_levels(model) if value == current),
        "По умолчанию",
    )
    await callback.answer()
    await callback.message.edit_text(
        "<b>🧠 Усиление модели</b>\n\n"
        f"Модель: <code>{html.escape(model)}</code>\n"
        f"Текущий режим: <b>{current_label}</b>\n\n"
        "Более высокое усилие обычно увеличивает глубину анализа и расход токенов. "
        "Доступность параметра зависит от поддержки со стороны TokenPlan и конкретной модели.",
        parse_mode="HTML",
        reply_markup=_effort_keyboard(chat_id, model, current),
    )


@router.callback_query(lambda callback: callback.data and callback.data.startswith("ai_effort_set_"))
async def ai_effort_set(callback: types.CallbackQuery):
    payload = callback.data[len("ai_effort_set_"):]
    chat_id, separator, level = payload.rpartition("_")
    chat = _find_chat(callback.from_user.id, chat_id)
    if not separator or not chat:
        await callback.answer("Чат не найден.", show_alert=True)
        return
    model = str(chat.get("model") or "")
    allowed = {value for value, _ in _effort_levels(model)}
    if level == "default":
        chat.pop("reasoning_effort", None)
    elif level in allowed:
        chat["reasoning_effort"] = level
    else:
        await callback.answer("Недопустимый уровень усилия для этой модели.", show_alert=True)
        return
    chat["updated_at"] = _now()
    if not _save_history():
        await callback.answer("Не удалось сохранить настройку.", show_alert=True)
        return
    await callback.answer("Уровень усилия сохранён.")
    current = chat.get("reasoning_effort")
    current_label = next(
        (label for value, label in _effort_levels(model) if value == current),
        "По умолчанию",
    )
    await callback.message.edit_text(
        "<b>🧠 Усиление модели</b>\n\n"
        f"Модель: <code>{html.escape(model)}</code>\n"
        f"Текущий режим: <b>{current_label}</b>",
        parse_mode="HTML",
        reply_markup=_effort_keyboard(chat_id, model, current),
    )


@router.callback_query(lambda callback: callback.data and callback.data.startswith("ai_chat_change_model_"))
async def ai_chat_change_model(callback: types.CallbackQuery, state: FSMContext):
    chat_id = callback.data[len("ai_chat_change_model_"):]
    chat = _find_chat(callback.from_user.id, chat_id)
    if not chat:
        await callback.answer("Чат не найден.", show_alert=True)
        return
    await callback.answer()
    try:
        models = _cached_user_models(callback.from_user.id) or await _fetch_user_models(callback.from_user.id)
    except RuntimeError as exc:
        await callback.message.edit_text(
            f"Не удалось загрузить модели: {html.escape(str(exc))}",
            parse_mode="HTML",
            reply_markup=_chat_settings_keyboard(chat_id, str(chat.get("model") or "unknown")),
        )
        return
    await callback.message.edit_text(
        f"Выбери новую модель для чата «{html.escape(str(chat.get('title') or 'Новый чат'))}»:",
        parse_mode="HTML",
        reply_markup=_models_keyboard(
            models, chat_id=chat_id, custom_api=_has_custom_api(callback.from_user.id)
        ),
    )


@router.callback_query(lambda callback: callback.data and callback.data.startswith("ai_chat_set_model_"))
async def ai_chat_set_model(callback: types.CallbackQuery, state: FSMContext):
    payload = callback.data[len("ai_chat_set_model_"):]
    chat_id, separator, model_index = payload.partition("_")
    if not separator or not model_index.isdigit():
        await callback.answer("Некорректный выбор модели.", show_alert=True)
        return
    chat = _find_chat(callback.from_user.id, chat_id)
    if not chat:
        await callback.answer("Чат не найден.", show_alert=True)
        return
    models = _cached_user_models(callback.from_user.id)
    if models is None:
        try:
            models = await _fetch_user_models(callback.from_user.id)
        except RuntimeError as exc:
            await callback.answer(str(exc)[:180], show_alert=True)
            return
    index = int(model_index)
    if index < 0 or index >= len(models):
        await callback.answer("Список моделей устарел. Открой выбор модели ещё раз.", show_alert=True)
        return
    model = models[index]
    if _is_paid_model(model) and not is_premium(callback.from_user.id):
        await callback.answer("Эта модель доступна только с премиум-доступом.", show_alert=True)
        return
    previous_model = chat.get("model")
    previous_effort = chat.get("reasoning_effort")
    chat["model"] = model
    if previous_effort not in {value for value, _ in _effort_levels(model)}:
        chat.pop("reasoning_effort", None)
    chat["updated_at"] = _now()
    if not _save_history():
        chat["model"] = previous_model
        if previous_effort is not None:
            chat["reasoning_effort"] = previous_effort
        await callback.answer("Не удалось сохранить модель.", show_alert=True)
        return
    await callback.answer("Модель изменена.")
    await callback.message.edit_text(
        "<b>⚙️ Настройки чата</b>\n\n"
        f"Имя: <b>{html.escape(str(chat.get('title') or 'Новый чат'))}</b>\n"
        f"Модель: <code>{html.escape(model)}</code>\n"
        f"Усилие: <b>{html.escape(str(chat.get('reasoning_effort') or 'по умолчанию'))}</b>",
        parse_mode="HTML",
        reply_markup=_chat_settings_keyboard(chat_id, str(chat.get("model") or "unknown")),
    )


@router.callback_query(lambda callback: callback.data and callback.data.startswith("ai_chat_delete_"))
async def ai_chat_delete(callback: types.CallbackQuery, state: FSMContext):
    chat_id = callback.data[len("ai_chat_delete_"):]
    chats = _user_chats(callback.from_user.id)
    target = next((item for item in chats if item.get("id") == chat_id), None)
    if target is None:
        await callback.answer("Чат не найден.", show_alert=True)
        return
    chats.remove(target)
    if not _save_history():
        chats.append(target)
        await callback.answer("Не удалось удалить чат.", show_alert=True)
        return
    try:
        shutil.rmtree(_chat_archive_directory(callback.from_user.id, chat_id))
    except FileNotFoundError:
        pass
    except OSError:
        logging.warning("Не удалось удалить ZIP-контекст удалённого чата %s", chat_id, exc_info=True)
    active_data = await state.get_data()
    if active_data.get("ai_chat_id") == chat_id:
        await state.clear()
    await callback.answer("Чат удалён.")
    remaining = _user_chats(callback.from_user.id)
    text_value = (
        f"<b>📚 Мои чаты</b>\n\nСохранено диалогов: {len(remaining)}."
        if remaining else "<b>📚 Мои чаты</b>\n\nСохранённых диалогов пока нет."
    )
    await callback.message.edit_text(
        text_value,
        parse_mode="HTML",
        reply_markup=_my_chats_keyboard(callback.from_user.id),
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
    try:
        models = await _fetch_user_models(callback.from_user.id)
    except RuntimeError as exc:
        await callback.message.edit_text(
            f"Не удалось загрузить модели: {html.escape(str(exc))}",
            parse_mode="HTML",
            reply_markup=_new_chat_keyboard(),
        )
        return
    custom_api = _has_custom_api(callback.from_user.id)
    await callback.message.edit_text(
        "🎛 <b>Выбор модели</b>\n\n"
        + ("Модели получены из твоего API:" if custom_api else "Выбери модель для нового диалога:"),
        parse_mode="HTML",
        reply_markup=_models_keyboard(models, custom_api=custom_api),
    )


@router.callback_query(lambda callback: callback.data and callback.data.startswith("ai_models_page_")
                       and callback.data != "ai_models_page_info")
async def ai_models_page(callback: types.CallbackQuery):
    try:
        page = int(callback.data[len("ai_models_page_"):])
        models = _cached_user_models(callback.from_user.id) or await _fetch_user_models(callback.from_user.id)
    except (ValueError, RuntimeError) as exc:
        await callback.answer(str(exc)[:180], show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_reply_markup(
        reply_markup=_models_keyboard(
            models, page=page, custom_api=_has_custom_api(callback.from_user.id)
        )
    )


@router.callback_query(lambda callback: callback.data and callback.data.startswith("ai_chat_models_page_"))
async def ai_chat_models_page(callback: types.CallbackQuery):
    payload = callback.data[len("ai_chat_models_page_"):]
    chat_id, separator, page_text = payload.rpartition("_")
    chat = _find_chat(callback.from_user.id, chat_id)
    if not separator or not page_text.isdigit() or not chat:
        await callback.answer("Не удалось открыть страницу моделей.", show_alert=True)
        return
    try:
        models = _cached_user_models(callback.from_user.id) or await _fetch_user_models(callback.from_user.id)
    except RuntimeError as exc:
        await callback.answer(str(exc)[:180], show_alert=True)
        return
    await callback.answer()
    await callback.message.edit_reply_markup(
        reply_markup=_models_keyboard(
            models, page=int(page_text), chat_id=chat_id,
            custom_api=_has_custom_api(callback.from_user.id),
        )
    )


@router.callback_query(lambda callback: callback.data and callback.data.startswith("ai_folder_"))
async def ai_model_folder(callback: types.CallbackQuery, state: FSMContext):
    folder_key = callback.data[len("ai_folder_"):]
    user_id = callback.from_user.id
    chats = _user_chats(user_id)
    matching_models = {
        str(chat.get("model") or "unknown")
        for chat in chats
        if _model_folder_key(str(chat.get("model") or "unknown")) == folder_key
    }
    if not matching_models:
        await callback.answer("Папка не найдена или в ней больше нет чатов.", show_alert=True)
        return
    model = next(iter(matching_models))
    count = sum(1 for chat in chats if str(chat.get("model") or "unknown") == model)
    await callback.answer()
    await state.clear()
    await callback.message.edit_text(
        f"📁 <b>{html.escape(_model_folder_title(model))}</b>\n\n"
        f"Сохранено диалогов: {count}. Выбери чат, чтобы продолжить:",
        parse_mode="HTML",
        reply_markup=_my_chats_keyboard(user_id, model_filter=model),
    )


@router.callback_query(lambda callback: callback.data == "ai_my_chats")
async def ai_my_chats(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    chats = _user_chats(callback.from_user.id)
    if not chats:
        text = "📚 <b>Мои чаты</b>\n\nСохранённых диалогов пока нет. Создай первый чат."
    else:
        model_count = len({str(chat.get("model") or "unknown") for chat in chats})
        text = (
            "📚 <b>Мои чаты</b>\n\n"
            f"Всего диалогов: {len(chats)} · Папок моделей: {model_count}.\n"
            "Выбери папку модели:"
        )
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=_my_chats_keyboard(callback.from_user.id),
    )




@router.callback_query(lambda callback: callback.data and callback.data.startswith("ai_model_"))
async def ai_model_selected(callback: types.CallbackQuery, state: FSMContext):
    index_text = callback.data[len("ai_model_"):]
    if not index_text.isdigit():
        await callback.answer("Некорректный выбор модели.", show_alert=True)
        return
    models = _cached_user_models(callback.from_user.id)
    if models is None:
        try:
            models = await _fetch_user_models(callback.from_user.id)
        except RuntimeError as exc:
            await callback.answer(str(exc)[:180], show_alert=True)
            return
    index = int(index_text)
    if index < 0 or index >= len(models):
        await callback.answer("Список моделей устарел. Открой выбор модели ещё раз.", show_alert=True)
        return
    model = models[index]
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
        + _ai_quota_text(user_id, model)
        + "\n\nДиалог активен. Отправь сообщение, чтобы начать.",
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
        f"Диалог с моделью <code>{chat.get('model', 'неизвестна')}</code> выбран.\n\n"
        + _ai_quota_text(callback.from_user.id, str(chat.get("model") or "unknown"))
        + "\n\nОтправь сообщение, чтобы продолжить диалог.",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )



@router.message(AIChatStates.waiting_prompt)
async def ai_save_custom_prompt(message: types.Message, state: FSMContext):
    text_value = (message.text or "").strip()
    if text_value.lower() in {"/cancel", "отмена"}:
        await state.clear()
        await message.answer("Изменение промпта отменено.", reply_markup=_root_keyboard())
        return
    if message.document:
        filename = message.document.file_name or ""
        if Path(filename).suffix.lower() != ".txt":
            await message.answer("Для промпта принимаются только TXT-файлы.")
            return
        if message.document.file_size and message.document.file_size > MAX_CUSTOM_PROMPT_FILE_BYTES:
            await message.answer("TXT-файл превышает лимит 64 КБ.")
            return
        try:
            with tempfile.TemporaryFile(mode="w+b") as buffer:
                await message.bot.download(message.document, destination=buffer)
                buffer.seek(0)
                raw = buffer.read(MAX_CUSTOM_PROMPT_FILE_BYTES + 1)
            if len(raw) > MAX_CUSTOM_PROMPT_FILE_BYTES:
                raise ValueError("TXT-файл превышает лимит 64 КБ.")
            text_value = _decode_text(raw).strip()
        except Exception as exc:
            logging.exception("Не удалось прочитать пользовательский TXT-промпт")
            await message.answer(f"Не удалось прочитать TXT-файл: {exc}")
            return
    if not text_value:
        await message.answer("Отправь непустой текст или TXT-файл.")
        return
    if len(text_value) > MAX_CUSTOM_PROMPT_CHARS:
        await message.answer(f"Промпт слишком длинный. Максимум — {MAX_CUSTOM_PROMPT_CHARS} символов.")
        return
    settings = _user_settings(message.from_user.id)
    previous = settings.get("custom_prompt")
    settings["custom_prompt"] = text_value
    if not _save_user_settings():
        if previous is None:
            settings.pop("custom_prompt", None)
        else:
            settings["custom_prompt"] = previous
        await message.answer("Не удалось сохранить промпт. Проверь постоянное хранилище Bothost.")
        return
    await state.clear()
    await message.answer(
        f"Пользовательский промпт сохранён ({len(text_value)} символов).",
        reply_markup=_prompt_settings_keyboard(),
    )


@router.message(AIChatStates.waiting_api_key)
async def ai_save_custom_api_key(message: types.Message, state: FSMContext):
    text_value = (message.text or "").strip()
    if text_value.lower() in {"/cancel", "отмена"}:
        await state.clear()
        await message.answer("Настройка API key отменена.", reply_markup=_root_keyboard())
        return
    if message.document or not text_value or len(text_value) > 1024 or "\n" in text_value:
        await message.answer("Отправь API key одним текстовым сообщением без переносов строк.")
        return
    if _settings_cipher() is None:
        await message.answer(
            "Для безопасного хранения API key добавь переменную AI_CHAT_SETTINGS_SECRET "
            "или GITHUB_STATE_SECRET в Bothost, затем повтори ввод."
        )
        return
    settings = _user_settings(message.from_user.id)
    previous = settings.get("api_key")
    settings["api_key"] = text_value
    if not _save_user_settings():
        if previous is None:
            settings.pop("api_key", None)
        else:
            settings["api_key"] = previous
        await message.answer("Не удалось сохранить API key. Проверь постоянное хранилище Bothost.")
        return
    available_models.pop(str(message.from_user.id), None)
    try:
        await message.delete()
    except Exception:
        logging.debug("Не удалось удалить сообщение с API key из Telegram", exc_info=True)
    await state.clear()
    await message.answer(
        "Свой API key сохранён. Значение ключа не показывается в меню.",
        reply_markup=_api_settings_keyboard(),
    )


@router.message(AIChatStates.waiting_api_url)
async def ai_save_custom_api_url(message: types.Message, state: FSMContext):
    text_value = (message.text or "").strip()
    if text_value.lower() in {"/cancel", "отмена"}:
        await state.clear()
        await message.answer("Настройка API URL отменена.", reply_markup=_root_keyboard())
        return
    try:
        normalized_url = _normalize_api_url(text_value)
    except ValueError as exc:
        await message.answer(str(exc))
        return
    settings = _user_settings(message.from_user.id)
    previous = settings.get("api_url")
    settings["api_url"] = normalized_url
    if not _save_user_settings():
        if previous is None:
            settings.pop("api_url", None)
        else:
            settings["api_url"] = previous
        await message.answer("Не удалось сохранить API URL. Проверь постоянное хранилище Bothost.")
        return
    available_models.pop(str(message.from_user.id), None)
    await state.clear()
    await message.answer("Свой API URL сохранён.", reply_markup=_api_settings_keyboard())


@router.message(AIChatStates.chatting)
async def ai_chat_message(message: types.Message, state: FSMContext):
    auto_task = _auto_tasks.get(message.from_user.id)
    if auto_task and auto_task is not asyncio.current_task() and not auto_task.done():
        await message.answer("Сейчас работает авто-запрос. Останови его в меню «Авто-запрос», чтобы писать вручную.")
        return
    await _process_ai_chat_message(message, state)


async def _process_ai_chat_message(
    message: types.Message,
    state: FSMContext,
    user_text_override: Optional[str] = None,
    skip_duplicate: bool = False,
) -> bool:
    if not skip_duplicate and _is_duplicate_ai_message(message):
        logging.warning(
            "Повторное Telegram-сообщение пропущено: chat_id=%s message_id=%s",
            message.chat.id,
            message.message_id,
        )
        return

    user_id = message.from_user.id
    active_task = _active_tasks.get(user_id)
    if active_task and active_task is not asyncio.current_task() and not active_task.done():
        await message.answer(
            "Предыдущий запрос ещё выполняется. Дождись отправки ответа и файлов, "
            "затем отправь следующее сообщение."
        )
        return

    user_text = (
        user_text_override.strip()
        if user_text_override is not None
        else (message.text or message.caption or "").strip()
    )
    uploaded_archive_text = None
    uploaded_archive_name = None
    if user_text_override is None and message.document:
        try:
            document_text = await _document_to_text(message)
        except (ValueError, RuntimeError) as exc:
            await message.answer(f"Не удалось обработать файл: {exc}")
            return
        if Path(message.document.file_name or "").suffix.lower() == ".zip":
            uploaded_archive_text = document_text
            uploaded_archive_name = message.document.file_name or "archive.zip"
            user_text = (
                f"{user_text}\n\nПользователь приложил файл: {uploaded_archive_name}. "
                "Содержимое ZIP сохранено в контексте этого диалога и доступно для следующих этапов."
            ).strip()
        else:
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
    if uploaded_archive_text is not None and uploaded_archive_name:
        try:
            _persist_chat_archive_context(
                user_id, chat, uploaded_archive_name, uploaded_archive_text
            )
        except (OSError, RuntimeError, ValueError) as exc:
            await message.answer(f"Не удалось сохранить ZIP для следующих этапов: {exc}")
            return

    model = str(chat.get("model", "неизвестная модель"))
    if _is_paid_model(model) and not is_premium(user_id):
        await state.clear()
        await message.answer("Эта модель доступна только с премиум-доступом. Выбери другую модель.")
        return False
    if _ai_quota_error(user_id, model):
        await message.answer(_ai_quota_error(user_id, model))
        await message.answer(_ai_quota_text(user_id, model), parse_mode="HTML")
        return False
    archive_followup = _is_archive_followup_request(
        user_text, chat.get("messages", [])
    )
    if user_id in _active_requests:
        await message.answer("Предыдущий запрос ещё завершается. Повтори сообщение через секунду.")
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
    try:
        record_ai_request()
    except Exception:
        logging.exception("Не удалось обновить общий счётчик запросов ИИ")
    waiting = None
    progress_message = None
    waiting_timer = None
    stream_display = None
    try:
        waiting = await message.answer("👀 ИИ думает. 0s.")
        progress_message = await message.answer("🔄 Сейчас: Подготавливает запрос")
        _waiting_messages[user_id] = waiting
        waiting_timer = asyncio.create_task(_animate_waiting_message(waiting))
        # Оставляем статус ожидания и журнал этапов отдельными сообщениями;
        # потоковый ответ отправляется ниже них и не перемешивается с журналом.
        stream_display = _StreamingReplyDisplay(message, None)

        async def on_model_delta(piece: str) -> None:
            nonlocal waiting_timer
            stream_display.append(piece)
            if stream_display.has_visible_text and waiting_timer is not None:
                await _stop_waiting_timer(waiting_timer)
                waiting_timer = None
                try:
                    await waiting.edit_text("🤖 ИИ формирует ответ")
                except Exception:
                    logging.debug("Не удалось обновить статус ожидания ИИ", exc_info=True)
            await stream_display.render()
        # Контекст берётся только из выбранного chat_id, не из других чатов
        # этой же модели или других моделей.
        # Модели получают историю только своего chat_id. Удаляем содержимое
        # старых вложений также из сохранённой истории, а не только из API-контекста.
        await _update_ai_progress(progress_message, "Собирает контекст диалога")
        stored_messages = chat.setdefault("messages", [])
        for stored_message in stored_messages:
            if (
                isinstance(stored_message, dict)
                and stored_message.get("role") == "user"
                and isinstance(stored_message.get("content"), str)
            ):
                stored_message["content"] = _history_message_without_old_files(
                    stored_message["content"]
                )
        _save_history()
        model_history = _history_without_old_files(stored_messages)
        repository_url = _extract_github_reference(user_text)
        repository_reference = repository_url or chat.get("github_repo")
        if repository_reference and not is_premium(user_id):
            await message.answer("Работа с GitHub доступна только с Premium.")
            return False
        explicit_branch_name = _extract_github_branch_name(user_text)
        if explicit_branch_name:
            chat["github_target_branch"] = explicit_branch_name

        repository_snapshot = None
        request_text = user_text
        await _update_ai_progress(progress_message, "Подключает сохранённые файлы проекта")
        saved_archive_context = _load_chat_archive_context(user_id, chat)
        if saved_archive_context:
            request_text += (
                "\n\n[СОХРАНЁННОЕ СОДЕРЖИМОЕ ZIP-АРХИВОВ ЭТОГО ДИАЛОГА]\n"
                + saved_archive_context
                + "\n\nИспользуй сохранённые архивы как исходные материалы текущего проекта. "
                "Сохраняй совместимость с ранее присланными файлами и не проси загрузить архив повторно."
            )
        if repository_reference:
            await _update_ai_progress(progress_message, "Получает исходники репозитория GitHub")
            if _github_reference_has_branch(repository_url):
                branch_override = None
            else:
                branch_override = (
                    explicit_branch_name
                    or chat.get("github_target_branch")
                    or chat.get("github_branch")
                )
            repository_snapshot = await fetch_repository_context(
                user_id, repository_reference, branch_override
            )
            chat["github_repo"] = repository_snapshot["html_url"]
            if repository_snapshot.get("branch_from_reference") or explicit_branch_name:
                chat["github_target_branch"] = repository_snapshot["branch"]
            request_text += (
                "\n\n[ЦЕЛЕВАЯ ВЕТКА GITHUB]\n"
                + repository_snapshot["branch"]
                + (" (из ссылки пользователя; изменения должны идти только сюда)"
                   if repository_snapshot.get("branch_from_reference") or explicit_branch_name
                   else "")
            )
            request_text += "\n\n[СНИМОК GITHUB-РЕПОЗИТОРИЯ]\n" + repository_snapshot["context"]
        await _update_ai_progress(progress_message, "Ожидает ответ модели")
        model_generation_started = False

        async def tracked_model_delta(piece: str) -> None:
            nonlocal model_generation_started
            if not model_generation_started:
                model_generation_started = True
                await _update_ai_progress(progress_message, "Получает и формирует ответ модели")
            await on_model_delta(piece)

        effort_level = chat.get("reasoning_effort")
        if effort_level not in {value for value, _ in _effort_levels(model)}:
            effort_level = None

        async def tracked_model_usage(usage: Dict[str, Any]) -> None:
            if is_admin(user_id):
                return
            try:
                record_ai_usage(user_id, model, usage)
            except Exception:
                logging.exception("Не удалось сохранить расход токенов для пользователя %s", user_id)

        reply = await _request_model(
            model, model_history, request_text, user_id=user_id,
            on_delta=tracked_model_delta,
            on_usage=tracked_model_usage,
            effort=effort_level,
        )

        if not is_premium(user_id):
            try:
                record_free_request(user_id)
            except Exception:
                logging.exception("Не удалось сохранить бесплатный запрос пользователя %s", user_id)
        reply_without_github_files, github_files = _extract_github_files(reply)
        visible_reply, generated_files, empty_files = _extract_generated_files(reply_without_github_files)

        # Не доверяем текстовому заявлению модели о созданном PR: GitHub-операция
        # выполняется только по разобранным GITHUB_FILE-блокам.
        if repository_snapshot and _is_github_change_request(user_text) and not github_files:
            await _update_ai_progress(progress_message, "Уточняет формат файлов для GitHub")
            logging.warning(
                "Модель %s не вернула GITHUB_FILE для запроса изменения %s",
                model, repository_snapshot["full_name"],
            )
            retry_context = [
                *_history_without_old_files(chat.get("messages", []))[-MAX_CONTEXT_MESSAGES:],
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": reply},
            ]
            if saved_archive_context:
                retry_context.append({
                    "role": "user",
                    "content": "[СОХРАНЁННЫЕ ZIP-АРХИВЫ ЭТОГО ДИАЛОГА]\n" + saved_archive_context,
                })
            retry_instruction = (
                "Предыдущий ответ не содержит ни одного корректного блока GITHUB_FILE, поэтому "
                "никакие изменения в GitHub ещё не выполнялись. Выполни исходную просьбу пользователя "
                "по приложенному снимку репозитория. Верни каждый новый или изменённый файл целиком "
                "в точном формате [[GITHUB_FILE: путь/от/корня/репозитория]] затем перевод строки, "
                "полное содержимое файла, затем перевод строки и [[/GITHUB_FILE]]. "
                "Не используй FILE-блоки, diff, Markdown-кодовые ограждения или ссылки на выдуманные PR. "
                "Не утверждай, что создал ветку, коммит или Pull Request: это сделает бот только после "
                "получения блоков. Не изменяй файлы, содержание которых отсутствует в снимке, если "
                "их точное содержимое нельзя надёжно восстановить; в таком случае попроси путь/файл. "
                "Верни только нужные GITHUB_FILE-блоки и краткое пояснение вне блоков."
            )
            try:
                await _update_ai_progress(progress_message, "Повторно генерирует файлы для GitHub")
                retry_reply = await _request_model(
                    model, retry_context, retry_instruction, user_id=user_id,
                    on_usage=tracked_model_usage, effort=effort_level,
                )
                retry_without_github_files, retry_github_files = _extract_github_files(retry_reply)
                if retry_github_files:
                    reply = retry_reply
                    github_files = retry_github_files
                    visible_reply, generated_files, empty_files = _extract_generated_files(
                        retry_without_github_files
                    )
                else:
                    logging.error(
                        "Модель %s повторно не вернула GITHUB_FILE; PR не создавался", model
                    )
                    reply = (
                        "Изменения не отправлены в GitHub: модель дважды не вернула файлы "
                        "в требуемом формате. Ветка, коммит и Pull Request не создавались. "
                        "Повтори запрос, указав конкретные файлы и требуемые изменения."
                    )
                    visible_reply = reply
                    generated_files = []
                    github_files = []
            except (RuntimeError, ValueError) as exc:
                logging.exception("Не удалось получить корректные GITHUB_FILE-блоки при повторе")
                reply = (
                    "Изменения не отправлены в GitHub: повторная генерация файлов завершилась "
                    "ошибкой (" + str(exc)[:300] + "). Ветка и Pull Request не создавались."
                )
                visible_reply = reply
                generated_files = []
                github_files = []

        if github_files:
            await _update_ai_progress(progress_message, "Применяет изменения в GitHub")
            if not repository_snapshot:
                raise RuntimeError("Для отправки изменений в GitHub сначала пришли ссылку на репозиторий.")
            target_branch = chat.get("github_target_branch")
            github_result = await create_repository_pull_request(
                user_id,
                repository_snapshot["full_name"],
                github_files,
                title="AI changes: " + _short_title(str(chat.get("title") or user_text), 70),
                body=visible_reply,
                base_branch_override=target_branch or chat.get("github_branch"),
                commit_to_target_branch=bool(target_branch),
            )
            changed_paths = github_result.get("changed_files", [])
            if github_result.get("direct_commit"):
                success_details = (
                    f"Изменения записаны непосредственно в ветку {target_branch}. "
                    f"Коммит: {github_result['commit'][:7]}. Изменено файлов: {len(changed_paths)}."
                )
                result_link_label = "Ветка"
            else:
                chat["github_branch"] = github_result["branch"]
                success_details = (
                    f"Изменения отправлены в GitHub. Обновлено файлов: {len(changed_paths)}."
                )
                result_link_label = "Pull Request"
            visible_reply = (
                (visible_reply + "\n\n" if visible_reply else "")
                + success_details + " " + result_link_label + ": " + github_result["url"]
            )

        # Если пользователь явно запросил файл, но модель не создала ни одного
        # непустого вложения, один раз просим её повторить генерацию.
        if (
            (_is_file_creation_request(user_text) or _is_archive_request(user_text) or archive_followup)
            and (not _is_large_project_request(user_text) or _is_archive_request(user_text) or archive_followup)
            and not generated_files
            and not github_files
        ):
            retry_context = [
                *_history_without_old_files(chat.get("messages", []))[-MAX_CONTEXT_MESSAGES:],
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": reply},
            ]
            if saved_archive_context:
                retry_context.append({
                    "role": "user",
                    "content": "[СОХРАНЁННЫЕ ZIP-АРХИВЫ ЭТОГО ДИАЛОГА]\n" + saved_archive_context,
                })
            retry_instruction = (
                "Предыдущая попытка не дала пригодного вложения. Пользователь просил готовый "
                "проект/архив. Не выдавай план, скрипт-сборщик, bash-команды или инструкцию "
                "запустить код вручную. Верни файлы проекта непосредственно в блоках FILE. "
                "Выполни исходную просьбу "
                "и верни полный файл в точном формате [[FILE: имя_файла.расширение]] "
                "затем перевод строки, полное непустое содержимое и [[/FILE]]. "
                "Внутри каждого блока обязателен хотя бы один непробельный символ. "
                "Не отправляй пустой блок и не заменяй файл описанием. Если запрос касается "
                "текстового файла, создай законченное содержимое прямо сейчас. "
                "Бот сам отправит это содержимое как вложение."
            )
            try:
                await _update_ai_progress(progress_message, "Повторно запрашивает недостающие файлы")
                retry_reply = await _request_model(
                    model, retry_context, retry_instruction, user_id=user_id,
                    on_usage=tracked_model_usage, effort=effort_level,
                )
                retry_visible, retry_files, retry_empty_files = _extract_generated_files(retry_reply)
                if retry_files:
                    reply = retry_reply
                    visible_reply, generated_files = retry_visible, retry_files
                    empty_files = retry_empty_files
                else:
                    logging.warning(
                        "Модель %s не вернула непустой файл после повторного запроса", model
                    )
                    if empty_files or retry_empty_files:
                        reply = (
                            "⚠️ Модель вернула пустой файл. Пустое вложение отправить нельзя, "
                            "поэтому файл не был прикреплён. Запрос завершён — можешь сразу "
                            "отправить новое сообщение. Для повторной попытки попроси модель "
                            "заполнить файл полным содержимым, а не оставлять его пустым."
                        )
                    else:
                        reply = (
                            "Не удалось получить файл от модели: она не вернула содержимое "
                            "в требуемом формате. Запрос завершён — можешь отправить новое "
                            "сообщение или повторить просьбу, указав имя и формат файла."
                        )
                    visible_reply, generated_files = reply, []
            except RuntimeError:
                logging.exception("Повторный запрос для генерации файла завершился ошибкой")
                reply = (
                    "Не удалось создать файл: повторный запрос к модели завершился ошибкой. "
                    "Запрос завершён — можешь отправить новое сообщение позже."
                )
                visible_reply, generated_files = reply, []

        messages = chat.setdefault("messages", [])
        messages.extend([
            {"role": "user", "content": _history_message_without_old_files(user_text)},
            {"role": "assistant", "content": reply},
        ])
        if chat.get("title") == "Новый чат":
            chat["title"] = _short_title(user_text, 48)
        chat["updated_at"] = _now()
        if not _save_history():
            logging.error("Ответ ИИ получен, но историю диалога не удалось сохранить.")

        await _update_ai_progress(progress_message, "Подготавливает итоговый ответ и вложения")
        await _stop_waiting_timer(waiting_timer)
        waiting_timer = None
        display_text = _compact_display_spacing(visible_reply) if visible_reply else "Файл готов."
        if stream_display is not None:
            stream_display.visible_text = display_text
            await stream_display.render(force=True)
        if generated_files and (
            len(generated_files) >= 2
            or _is_archive_request(user_text)
            or archive_followup
            or any(not file_content for _, file_content in generated_files)
        ):
            await _update_ai_progress(progress_message, "Собирает ZIP-архив с результатами")
            try:
                archive_content = _build_generated_zip(generated_files)
            except ValueError as exc:
                await message.answer(f"Не удалось собрать ZIP-архив: {exc}")
            else:
                try:
                    await message.answer_document(
                        BufferedInputFile(archive_content, filename="generated_files.zip"),
                        caption=f'Архив с {len(generated_files)} файлами от модели {model}',
                    )
                except Exception as exc:
                    logging.exception("Не удалось отправить ZIP-архив пользователю")
                    await message.answer(
                        "ZIP собран, но Telegram не смог его отправить. "
                        f"Ошибка: {type(exc).__name__}: {str(exc)[:300]}"
                    )
        elif generated_files:
            await _update_ai_progress(progress_message, "Отправляет созданный файл")
            filename, file_content = generated_files[0]
            try:
                await message.answer_document(
                    BufferedInputFile(file_content, filename=filename),
                    caption=f"Файл от модели {model}",
                )
            except Exception as exc:
                logging.exception("Не удалось отправить файл пользователю: %s", filename)
                await message.answer(
                    f"Файл {filename} создан, но Telegram не смог его отправить. "
                    f"Ошибка: {type(exc).__name__}: {str(exc)[:300]}"
                )

        await _update_ai_progress(progress_message, "Готово")
        if waiting:
            try:
                await waiting.edit_text("✅ ИИ закончил отвечать.")
            except Exception:
                logging.debug("Не удалось обновить финальный статус ИИ", exc_info=True)
        await message.answer(_ai_quota_text(user_id, model), parse_mode="HTML")
        return True
    except RuntimeError as exc:
        await _update_ai_progress(progress_message, "Завершено с ошибкой")
        await _stop_waiting_timer(waiting_timer)
        waiting_timer = None
        error_text = f'"{model}" - Ошибка запроса: {exc}'
        if stream_display is not None and stream_display.has_visible_text:
            if waiting:
                try:
                    await waiting.edit_text("⚠️ Запрос завершился с ошибкой.")
                except Exception:
                    logging.debug("Не удалось обновить статус ошибки ИИ", exc_info=True)
            await message.answer(error_text)
        elif waiting:
            await waiting.edit_text(error_text)
        return False
    except asyncio.CancelledError:
        await _update_ai_progress(progress_message, "Запрос прерван")
        if stream_display is not None and stream_display.has_visible_text:
            if waiting:
                try:
                    await waiting.edit_text("Ответ прерван.")
                except Exception:
                    logging.debug("Не удалось обновить статус прерванного запроса", exc_info=True)
            try:
                await message.answer("Ответ прерван.")
            except Exception:
                logging.debug("Не удалось отправить сообщение о прерывании", exc_info=True)
        elif waiting:
            try:
                await waiting.edit_text("Ответ прерван.")
            except Exception:
                logging.debug("Не удалось обновить сообщение прерванного запроса", exc_info=True)
        return False
    except Exception:
        await _update_ai_progress(progress_message, "Внутренняя ошибка")
        logging.exception("Непредвиденная ошибка при запросе к TokenPlan")
        error_text = f'"{model}" - Произошла внутренняя ошибка при обращении к API.'
        if stream_display is not None and stream_display.has_visible_text:
            if waiting:
                try:
                    await waiting.edit_text("⚠️ Внутренняя ошибка запроса.")
                except Exception:
                    logging.debug("Не удалось обновить статус внутренней ошибки ИИ", exc_info=True)
            await message.answer(error_text)
        elif waiting:
            await waiting.edit_text(error_text)
        return False
    finally:
        try:
            await _stop_waiting_timer(waiting_timer)
        except Exception:
            logging.debug("Не удалось корректно остановить таймер ожидания", exc_info=True)
        finally:
            # Освобождаем блокировку даже при ошибке редактирования сообщения/таймера.
            _active_requests.discard(user_id)
            _active_tasks.pop(user_id, None)
            _waiting_messages.pop(user_id, None)
