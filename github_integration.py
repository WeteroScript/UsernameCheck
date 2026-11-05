"""Per-user GitHub App installation and repository access helpers."""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

import aiohttp
import jwt

INSTALLATIONS_PATH = Path(os.getenv("GITHUB_INSTALLATIONS_FILE", "data/github_installations.json"))
_STORAGE_LOCK = threading.Lock()


def _private_key() -> str:
    key = os.getenv("GITHUB_APP_PRIVATE_KEY", "")
    key_path = os.getenv("GITHUB_APP_PRIVATE_KEY_PATH", "").strip()
    if not key and key_path:
        key = Path(key_path).read_text(encoding="utf-8")
    return key.replace("\\n", "\n").strip()


def configuration_error() -> Optional[str]:
    required = {
        "GITHUB_APP_ID": os.getenv("GITHUB_APP_ID", "").strip(),
        "GITHUB_APP_SLUG": os.getenv("GITHUB_APP_SLUG", "").strip(),
        "GITHUB_APP_PRIVATE_KEY": _private_key(),
        "GITHUB_STATE_SECRET": os.getenv("GITHUB_STATE_SECRET", "").strip(),
        "GITHUB_CALLBACK_URL": os.getenv("GITHUB_CALLBACK_URL", "").strip(),
    }
    missing = [name for name, value in required.items() if not value]
    return "Не настроены переменные GitHub: " + ", ".join(missing) if missing else None


def _state_secret() -> bytes:
    secret = os.getenv("GITHUB_STATE_SECRET", "").strip()
    if not secret:
        raise RuntimeError("Не задан GITHUB_STATE_SECRET")
    return secret.encode("utf-8")


def create_install_url(telegram_user_id: int) -> str:
    error = configuration_error()
    if error:
        raise RuntimeError(error)
    payload = {
        "uid": int(telegram_user_id),
        "exp": int(time.time()) + 600,
        "nonce": base64.urlsafe_b64encode(os.urandom(18)).decode("ascii").rstrip("="),
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = hmac.new(_state_secret(), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    state = encoded + "." + signature
    slug = os.getenv("GITHUB_APP_SLUG", "").strip()
    return "https://github.com/apps/{}/installations/new?{}".format(
        slug, urlencode({"state": state})
    )


def _decode_state(state: str) -> int:
    try:
        encoded, supplied_signature = state.split(".", 1)
        expected = hmac.new(
            _state_secret(), encoded.encode("ascii"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(supplied_signature, expected):
            raise ValueError("Подпись state не совпадает")
        padding = "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded + padding).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            raise ValueError("Срок действия ссылки истёк")
        user_id = int(payload["uid"])
        if user_id <= 0:
            raise ValueError("Некорректный Telegram user ID")
        return user_id
    except Exception as exc:
        raise ValueError("Некорректный или просроченный параметр авторизации GitHub") from exc


def _app_jwt() -> str:
    error = configuration_error()
    if error:
        raise RuntimeError(error)
    now = int(time.time())
    token = jwt.encode(
        {"iat": now - 60, "exp": now + 540, "iss": os.getenv("GITHUB_APP_ID", "").strip()},
        _private_key(),
        algorithm="RS256",
    )
    return token.decode("utf-8") if isinstance(token, bytes) else token


def _github_api_request(path: str) -> Dict[str, Any]:
    request = Request(
        "https://api.github.com" + path,
        headers={
            "Authorization": "Bearer " + _app_jwt(),
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "UsernameCheck-Telegram-Bot",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError("GitHub API HTTP {}: {}".format(exc.code, detail)) from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError("Не удалось связаться с GitHub API: {}".format(exc)) from exc


def _load_installations() -> Dict[str, Dict[str, Any]]:
    if not INSTALLATIONS_PATH.exists():
        return {}
    try:
        with INSTALLATIONS_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        logging.exception("Не удалось загрузить привязки GitHub")
        return {}


def _save_installations(data: Dict[str, Dict[str, Any]]) -> None:
    INSTALLATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = INSTALLATIONS_PATH.with_name(INSTALLATIONS_PATH.name + ".tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    try:
        os.chmod(temp_path, 0o600)
    except OSError:
        pass
    os.replace(temp_path, INSTALLATIONS_PATH)


def complete_installation(state: str, installation_id: str) -> Dict[str, Any]:
    user_id = _decode_state(state)
    if not str(installation_id).isdigit():
        raise ValueError("GitHub не передал корректный installation_id")
    installation = _github_api_request("/app/installations/{}".format(installation_id))
    account = installation.get("account") or {}
    if not account.get("login") or not account.get("id"):
        raise RuntimeError("GitHub не вернул данные аккаунта для установки приложения")
    returned_app_id = installation.get("app_id")
    app_id = os.getenv("GITHUB_APP_ID", "").strip()
    if returned_app_id is not None and str(returned_app_id) != app_id:
        raise RuntimeError("Установка принадлежит другому GitHub App")
    record = {
        "installation_id": int(installation_id),
        "account_login": str(account["login"]),
        "account_id": int(account["id"]),
        "account_type": str(account.get("type") or "User"),
        "connected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with _STORAGE_LOCK:
        data = _load_installations()
        data[str(user_id)] = record
        _save_installations(data)
    return {"telegram_user_id": user_id, **record}


def get_user_installation(telegram_user_id: int) -> Optional[Dict[str, Any]]:
    with _STORAGE_LOCK:
        return _load_installations().get(str(int(telegram_user_id)))


def remove_user_installation(telegram_user_id: int) -> bool:
    with _STORAGE_LOCK:
        data = _load_installations()
        removed = data.pop(str(int(telegram_user_id)), None) is not None
        if removed:
            _save_installations(data)
        return removed


async def _installation_token(installation_id: int) -> str:
    headers = {
        "Authorization": "Bearer " + _app_jwt(),
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = "https://api.github.com/app/installations/{}/access_tokens".format(installation_id)
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, headers=headers) as response:
            payload = await response.json(content_type=None)
            if response.status >= 400:
                raise RuntimeError(
                    "GitHub не выдал installation token (HTTP {}): {}".format(
                        response.status, str(payload)[:500]
                    )
                )
            token = payload.get("token")
            if not token:
                raise RuntimeError("GitHub вернул пустой installation token")
            return str(token)


async def list_user_repositories(telegram_user_id: int) -> List[Dict[str, Any]]:
    record = get_user_installation(telegram_user_id)
    if not record:
        raise RuntimeError("GitHub-аккаунт не подключён")
    token = await _installation_token(int(record["installation_id"]))
    url = "https://api.github.com/installation/repositories?per_page=100"
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers) as response:
            payload = await response.json(content_type=None)
            if response.status >= 400:
                raise RuntimeError(
                    "Не удалось получить репозитории (HTTP {}): {}".format(
                        response.status, str(payload)[:500]
                    )
                )
    return [
        {
            "full_name": item.get("full_name", ""),
            "html_url": item.get("html_url", ""),
            "private": bool(item.get("private")),
            "default_branch": item.get("default_branch", "main"),
        }
        for item in payload.get("repositories", [])
        if item.get("full_name")
    ]
