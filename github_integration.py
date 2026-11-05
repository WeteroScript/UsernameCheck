"""Per-user GitHub App installation and repository access helpers."""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote

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
        "GITHUB_APP_CLIENT_ID": os.getenv("GITHUB_APP_CLIENT_ID", "").strip(),
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


def _decode_state_payload(state: str) -> Dict[str, Any]:
    try:
        encoded, supplied_signature = state.split(".", 1)
        expected = hmac.new(_state_secret(), encoded.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(supplied_signature, expected):
            raise ValueError("Подпись state не совпадает")
        padding = "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded + padding).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            raise ValueError("Срок действия ссылки истёк")
        if int(payload["uid"]) <= 0:
            raise ValueError("Некорректный Telegram user ID")
        return payload
    except Exception as exc:
        raise ValueError("Некорректный или просроченный параметр авторизации GitHub") from exc


def _signed_state(payload: Dict[str, Any]) -> str:
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = hmac.new(_state_secret(), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return encoded + "." + signature


async def request_device_code() -> Dict[str, Any]:
    """Start GitHub App Device Flow; no client secret is required."""
    client_id = os.getenv("GITHUB_APP_CLIENT_ID", "").strip()
    if not client_id:
        raise RuntimeError("Не задан GITHUB_APP_CLIENT_ID")
    url = "https://github.com/login/device/code"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "UsernameCheck-Telegram-Bot",
    }
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, data={"client_id": client_id}, headers=headers) as response:
            data = await response.json(content_type=None)
            if response.status >= 400 or data.get("error"):
                detail = data.get("error_description") or data.get("error") or str(data)
                raise RuntimeError("GitHub не выдал код авторизации: " + str(detail)[:300])
    required = ("device_code", "user_code", "verification_uri")
    if any(not data.get(key) for key in required):
        raise RuntimeError("GitHub вернул неполные данные Device Flow")
    verification_uri = str(data["verification_uri"])
    if not verification_uri.startswith("https://github.com/"):
        raise RuntimeError("GitHub вернул неожиданный адрес подтверждения")
    return {
        "device_code": str(data["device_code"]),
        "user_code": str(data["user_code"]),
        "verification_uri": verification_uri,
        "expires_in": max(60, int(data.get("expires_in", 900))),
        "interval": max(5, int(data.get("interval", 5))),
    }


async def complete_device_flow(
    telegram_user_id: int,
    device_code: str,
    interval: int = 5,
    expires_in: int = 900,
) -> str:
    """Poll GitHub Device Flow, verify the user, then return the App install URL."""
    client_id = os.getenv("GITHUB_APP_CLIENT_ID", "").strip()
    if not client_id:
        raise RuntimeError("Не задан GITHUB_APP_CLIENT_ID")
    deadline = time.monotonic() + max(1, int(expires_in))
    poll_interval = max(5, int(interval))
    token_url = "https://github.com/login/oauth/access_token"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "UsernameCheck-Telegram-Bot",
    }
    timeout = aiohttp.ClientTimeout(total=20)
    access_token = None
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while time.monotonic() < deadline:
            await asyncio.sleep(poll_interval)
            if time.monotonic() >= deadline:
                break
            payload = {
                "client_id": client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            }
            async with session.post(token_url, data=payload, headers=headers) as response:
                token_data = await response.json(content_type=None)
            error = str(token_data.get("error", ""))
            if token_data.get("access_token"):
                access_token = str(token_data["access_token"])
                break
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                poll_interval = max(poll_interval + 5, int(token_data.get("interval", poll_interval + 5)))
                continue
            if error in ("expired_token", "token_expired"):
                raise RuntimeError("Срок действия кода GitHub истёк. Запусти подключение заново.")
            if error == "access_denied":
                raise RuntimeError("Авторизация GitHub отменена.")
            detail = token_data.get("error_description") or error or str(token_data)
            raise RuntimeError("GitHub отклонил авторизацию: " + str(detail)[:300])
    if not access_token:
        raise RuntimeError("Время ожидания авторизации GitHub истекло. Запусти подключение заново.")

    user_headers = {
        "Authorization": "Bearer " + access_token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "UsernameCheck-Telegram-Bot",
    }
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get("https://api.github.com/user", headers=user_headers) as response:
            github_user = await response.json(content_type=None)
            if response.status >= 400:
                detail = github_user.get("message", str(github_user))
                raise RuntimeError("Не удалось проверить GitHub-аккаунт: " + str(detail)[:300])

    account_id = github_user.get("id")
    account_login = github_user.get("login")
    if not account_id or not account_login:
        raise RuntimeError("GitHub не вернул идентификатор аккаунта")
    install_state = _signed_state({
        "uid": int(telegram_user_id),
        "account_id": int(account_id),
        "account_login": str(account_login),
        "exp": int(time.time()) + 600,
        "nonce": base64.urlsafe_b64encode(os.urandom(18)).decode("ascii").rstrip("="),
    })
    slug = os.getenv("GITHUB_APP_SLUG", "").strip()
    return "https://github.com/apps/{}/installations/new?{}".format(
        slug, urlencode({"state": install_state})
    )


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
    state_payload = _decode_state_payload(state)
    user_id = int(state_payload["uid"])
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
    if int(account["id"]) != int(state_payload.get("account_id", -1)):
        raise RuntimeError("Установка принадлежит другому GitHub-аккаунту")
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


def _repository_full_name(reference: str) -> str:
    from urllib.parse import urlparse

    value = reference.strip()
    if value.startswith("https://") or value.startswith("http://"):
        parsed = urlparse(value)
        if parsed.netloc.lower() not in ("github.com", "www.github.com"):
            raise ValueError("Поддерживаются только ссылки github.com")
        parts = [part for part in parsed.path.split("/") if part]
    else:
        parts = [part for part in value.split("/") if part]
    if len(parts) < 2:
        raise ValueError("Укажи ссылку вида https://github.com/owner/repository")
    owner, name = parts[0], parts[1]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError("Некорректное имя GitHub-репозитория")
    return owner + "/" + name.removesuffix(".git")


async def _api_json(
    session: aiohttp.ClientSession,
    method: str,
    path: str,
    token: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    url = "https://api.github.com" + path
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with session.request(method, url, headers=headers, json=payload) as response:
        data = await response.json(content_type=None)
        if response.status >= 400:
            message = data.get("message", str(data)) if isinstance(data, dict) else str(data)
            raise RuntimeError("GitHub API HTTP {}: {}".format(response.status, message[:500]))
        return data


async def fetch_repository_context(telegram_user_id: int, reference: str, branch_override: Optional[str] = None) -> Dict[str, str]:
    full_name = _repository_full_name(reference)
    record = get_user_installation(telegram_user_id)
    if not record:
        raise RuntimeError("Сначала подключи свой GitHub в меню бота.")
    token = await _installation_token(int(record["installation_id"]))
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        repo_data = await _api_json(session, "GET", "/repos/" + full_name, token)
        branch = branch_override or repo_data.get("default_branch") or "main"
        encoded_branch = quote(branch, safe="")
        branch_ref = await _api_json(
            session, "GET",
            "/repos/{}/git/ref/heads/{}".format(full_name, encoded_branch),
            token,
        )
        head_commit = await _api_json(
            session, "GET",
            "/repos/{}/git/commits/{}".format(full_name, branch_ref["object"]["sha"]),
            token,
        )
        tree_data = await _api_json(
            session, "GET",
            "/repos/{}/git/trees/{}?recursive=1".format(full_name, head_commit["tree"]["sha"]),
            token,
        )
        entries = [
            item for item in tree_data.get("tree", [])
            if item.get("type") == "blob" and isinstance(item.get("path"), str)
        ]
        excluded_names = {
            ".env", ".env.local", ".env.production", ".npmrc", ".pypirc",
            ".netrc", "id_rsa", "id_ed25519", "credentials.json", "secrets.json",
        }
        allowed_names = {"dockerfile", "makefile", "procfile", ".gitignore", "license", "readme"}
        allowed_extensions = {
            ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".html", ".htm",
            ".css", ".scss", ".json", ".md", ".txt", ".yml", ".yaml", ".toml",
            ".ini", ".cfg", ".sh", ".sql", ".go", ".rs", ".java", ".kt", ".php",
            ".rb", ".swift", ".c", ".cpp", ".h", ".hpp", ".cs", ".xml", ".vue",
            ".svelte", ".gradle", ".properties",
        }

        def safe_entry(item: Dict[str, Any]) -> bool:
            path = item["path"]
            lower = path.lower()
            basename = lower.rsplit("/", 1)[-1]
            extension = Path(lower).suffix
            if basename in excluded_names or basename.startswith(".env."):
                return False
            excluded_dirs = (".git/", "node_modules/", "vendor/", "dist/", "build/", ".venv/")
            if any(lower.startswith(directory) or ("/" + directory) in lower for directory in excluded_dirs):
                return False
            if any(term in basename for term in ("secret", "credential", "private_key", "token")):
                return False
            return (basename in allowed_names or extension in allowed_extensions) and int(item.get("size", 0)) <= 20000

        candidates = [item for item in entries if safe_entry(item)]
        priority_names = (
            "readme", "readme.md", "dockerfile", "makefile", "pyproject.toml", "requirements.txt", "package.json",
            "dockerfile", "compose.yaml", "compose.yml", "tsconfig.json",
        )
        candidates.sort(key=lambda item: (
            0 if item["path"].lower().rsplit("/", 1)[-1] in priority_names else 1,
            item["path"].count("/"),
            item["path"],
        ))
        files = []
        total_bytes = 0
        for item in candidates:
            if len(files) >= 30 or total_bytes >= 100000:
                break
            try:
                blob = await _api_json(
                    session, "GET",
                    "/repos/{}/git/blobs/{}".format(full_name, item["sha"]),
                    token,
                )
                raw = base64.b64decode(blob.get("content", ""))
                text_content = raw.decode("utf-8")
            except (UnicodeDecodeError, ValueError, RuntimeError):
                continue
            if total_bytes + len(raw) > 100000:
                continue
            files.append((item["path"], text_content))
            total_bytes += len(raw)

    if not files:
        raise RuntimeError("В репозитории не найдено доступных текстовых исходников.")
    sections = [
        "GitHub repository: " + full_name,
        "Default branch: " + branch,
        "Repository URL: " + repo_data.get("html_url", "https://github.com/" + full_name),
        "Файлы ниже — ограниченный снимок репозитория. Если нужного файла нет, попроси пользователя указать путь.",
    ]
    for path, file_content in files:
        sections.append("\n--- FILE: " + path + " ---\n" + file_content)
    return {
        "full_name": full_name,
        "branch": branch,
        "html_url": repo_data.get("html_url", "https://github.com/" + full_name),
        "context": "\n".join(sections),
    }


async def create_repository_pull_request(
    telegram_user_id: int,
    full_name: str,
    files: List[tuple],
    title: str,
    body: str = "",
    base_branch_override: Optional[str] = None,
) -> Dict[str, str]:
    full_name = _repository_full_name(full_name)
    if not files:
        raise ValueError("Нет файлов для отправки в GitHub")
    if len(files) > 100:
        raise ValueError("За один Pull Request можно изменить не более 100 файлов. Разбей изменения на несколько этапов.")
    normalized = []
    total_size = 0
    for path, file_content in files:
        clean_path = str(path).replace("\\", "/").strip()
        pure_path = Path(clean_path)
        if not clean_path or clean_path.startswith("/") or any(part in ("", ".", "..") for part in clean_path.split("/")):
            raise ValueError("Недопустимый путь файла: " + clean_path[:120])
        if pure_path.is_absolute() or len(clean_path) > 240:
            raise ValueError("Недопустимый путь файла: " + clean_path[:120])
        raw = file_content.encode("utf-8")
        if len(raw) > 10 * 1024 * 1024:
            raise ValueError("Файл {} превышает 10 МБ".format(clean_path))
        total_size += len(raw)
        normalized.append((clean_path, raw))
    if total_size > 30 * 1024 * 1024:
        raise ValueError("Общий размер изменений превышает 30 МБ")

    record = get_user_installation(telegram_user_id)
    if not record:
        raise RuntimeError("GitHub-аккаунт не подключён")
    token = await _installation_token(int(record["installation_id"]))
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        repo_data = await _api_json(session, "GET", "/repos/" + full_name, token)
        base_branch = base_branch_override or repo_data.get("default_branch") or "main"
        encoded_branch = quote(base_branch, safe="")
        ref_data = await _api_json(
            session, "GET", "/repos/{}/git/ref/heads/{}".format(full_name, encoded_branch), token
        )
        base_sha = ref_data["object"]["sha"]
        base_commit = await _api_json(
            session, "GET", "/repos/{}/git/commits/{}".format(full_name, base_sha), token
        )
        tree_entries = []
        for path, raw in normalized:
            blob = await _api_json(
                session, "POST", "/repos/{}/git/blobs".format(full_name), token,
                {"content": base64.b64encode(raw).decode("ascii"), "encoding": "base64"},
            )
            tree_entries.append({
                "path": path,
                "mode": "100644",
                "type": "blob",
                "sha": blob["sha"],
            })
        tree = await _api_json(
            session, "POST", "/repos/{}/git/trees".format(full_name), token,
            {"base_tree": base_commit["tree"]["sha"], "tree": tree_entries},
        )
        safe_title = (title or "AI project changes").strip().replace("\n", " ")[:120]
        commit = await _api_json(
            session, "POST", "/repos/{}/git/commits".format(full_name), token,
            {"message": safe_title, "tree": tree["sha"], "parents": [base_sha]},
        )
        branch_name = "ai/telegram-" + uuid.uuid4().hex[:10]
        await _api_json(
            session, "POST", "/repos/{}/git/refs".format(full_name), token,
            {"ref": "refs/heads/" + branch_name, "sha": commit["sha"]},
        )
        pull = await _api_json(
            session, "POST", "/repos/{}/pulls".format(full_name), token,
            {
                "title": safe_title,
                "head": branch_name,
                "base": base_branch,
                "body": (body or "Изменения подготовлены через Telegram-бота. Проверьте diff и тесты перед слиянием.")[:5000],
            },
        )
    return {
        "url": pull.get("html_url", ""),
        "branch": branch_name,
        "base_branch": base_branch,
        "commit": commit["sha"],
        "repository": full_name,
    }
