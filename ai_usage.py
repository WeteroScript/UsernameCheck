"""Persistent weekly AI request and per-model token quotas."""
import json
import html
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict

from storage import data_file
from access_control import is_admin, is_premium

USAGE_PATH = Path(data_file(os.getenv("AI_USAGE_FILE", "ai_usage.json")))
FREE_REQUESTS_PER_WEEK = 5
PREMIUM_TOKENS_PER_5H = 1_500_000
PREMIUM_TOKENS_PER_WEEK = 5_000_000
WINDOW_5H_SECONDS = 5 * 60 * 60
WINDOW_WEEK_SECONDS = 7 * 24 * 60 * 60

_LOCK = threading.RLock()


def _load() -> Dict[str, Any]:
    try:
        with USAGE_PATH.open("r", encoding="utf-8") as source:
            data = json.load(source)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        logging.exception("Не удалось загрузить статистику использования ИИ")
        return {}


_DATA: Dict[str, Any] = _load()


def _save() -> None:
    USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = USAGE_PATH.with_name(USAGE_PATH.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as target:
        json.dump(_DATA, target, ensure_ascii=False, separators=(",", ":"))
    os.replace(temporary, USAGE_PATH)


def _user_data(user_id: int) -> Dict[str, Any]:
    user = _DATA.setdefault(str(int(user_id)), {})
    if not isinstance(user, dict):
        user = {}
        _DATA[str(int(user_id))] = user
    requests = user.setdefault("free_requests", [])
    models = user.setdefault("models", {})
    if not isinstance(requests, list):
        user["free_requests"] = []
    if not isinstance(models, dict):
        user["models"] = {}
    return user


def _prune(user: Dict[str, Any], now: float) -> bool:
    changed = False
    cutoff = now - WINDOW_WEEK_SECONDS
    requests = user.get("free_requests", [])
    fresh_requests = [
        float(ts) for ts in requests
        if isinstance(ts, (int, float)) and float(ts) > cutoff
    ]
    if fresh_requests != requests:
        user["free_requests"] = fresh_requests
        changed = True

    models = user.get("models", {})
    for model, events in list(models.items()):
        if not isinstance(events, list):
            models[model] = []
            changed = True
            continue
        fresh = [
            event for event in events
            if isinstance(event, dict)
            and isinstance(event.get("at"), (int, float))
            and float(event["at"]) > cutoff
        ]
        if fresh != events:
            models[model] = fresh
            changed = True
        if not fresh:
            models.pop(model, None)
            changed = True
    return changed


def record_ai_usage(user_id: int, model: str, usage: Dict[str, Any]) -> None:
    """Record quota usage; Premium users consume twice the recorded token allowance."""
    total = max(0, int(usage.get("total_tokens") or 0))
    prompt = max(0, int(usage.get("prompt_tokens") or 0))
    completion = max(0, int(usage.get("completion_tokens") or 0))
    if total <= 0:
        total = prompt + completion
    if total <= 0:
        return

    # Administrators have unlimited access and are not charged against a quota.
    if is_premium(user_id) and not is_admin(user_id):
        total *= 2
        prompt *= 2
        completion *= 2
    now = time.time()
    with _LOCK:
        user = _user_data(user_id)
        _prune(user, now)
        events = user["models"].setdefault(str(model), [])
        events.append({
            "at": now,
            "total": total,
            "prompt": prompt,
            "completion": completion,
            "estimated": bool(usage.get("estimated")),
        })
        _save()


def record_ai_request() -> None:
    """Increment the lifetime counter once for each user-initiated AI request."""
    with _LOCK:
        meta = _DATA.setdefault("_meta", {})
        if not isinstance(meta, dict):
            meta = {}
            _DATA["_meta"] = meta
        meta["ai_requests_total"] = max(0, int(meta.get("ai_requests_total") or 0)) + 1
        _save()


def get_ai_request_total() -> int:
    with _LOCK:
        meta = _DATA.get("_meta", {})
        if not isinstance(meta, dict):
            return 0
        try:
            return max(0, int(meta.get("ai_requests_total") or 0))
        except (TypeError, ValueError):
            return 0


def record_free_request(user_id: int) -> None:
    now = time.time()
    with _LOCK:
        user = _user_data(user_id)
        _prune(user, now)
        user["free_requests"].append(now)
        _save()


def get_ai_quota(user_id: int, model: str, premium: bool) -> Dict[str, Any]:
    now = time.time()
    with _LOCK:
        user = _user_data(user_id)
        changed = _prune(user, now)
        if changed:
            _save()
        if not premium:
            events = sorted(float(ts) for ts in user.get("free_requests", []))
            reset_at = events[0] + WINDOW_WEEK_SECONDS if events else None
            return {
                "premium": False,
                "used": len(events),
                "limit": FREE_REQUESTS_PER_WEEK,
                "reset_at": reset_at,
            }

        events = user.get("models", {}).get(str(model), [])
        five_hour = [event for event in events if float(event["at"]) > now - WINDOW_5H_SECONDS]
        week = [event for event in events if float(event["at"]) > now - WINDOW_WEEK_SECONDS]
        reset_5h = min((float(event["at"]) for event in five_hour), default=None)
        reset_week = min((float(event["at"]) for event in week), default=None)
        return {
            "premium": True,
            "model": str(model),
            "used_5h": sum(int(event.get("total", 0)) for event in five_hour),
            "used_week": sum(int(event.get("total", 0)) for event in week),
            "limit_5h": PREMIUM_TOKENS_PER_5H,
            "limit_week": PREMIUM_TOKENS_PER_WEEK,
            "reset_5h": reset_5h + WINDOW_5H_SECONDS if reset_5h is not None else None,
            "reset_week": reset_week + WINDOW_WEEK_SECONDS if reset_week is not None else None,
            "estimated": any(bool(event.get("estimated")) for event in five_hour + week),
        }


def get_ai_usage_models(user_id: int, limit: int = 10) -> list[str]:
    """Return recently used model ids, newest usage first."""
    now = time.time()
    with _LOCK:
        user = _user_data(user_id)
        changed = _prune(user, now)
        if changed:
            _save()
        model_events = [
            (str(model), max(float(event["at"]) for event in events))
            for model, events in user.get("models", {}).items()
            if isinstance(events, list)
            and any(
                isinstance(event, dict) and isinstance(event.get("at"), (int, float))
                for event in events
            )
        ]
        model_events.sort(key=lambda item: item[1], reverse=True)
        return [model for model, _ in model_events[:max(1, int(limit))]]


def quota_block_reason(user_id: int, model: str, premium: bool) -> str | None:
    quota = get_ai_quota(user_id, model, premium)
    if not premium:
        if quota["used"] >= quota["limit"]:
            return "Недельный бесплатный лимит исчерпан (5 из 5 запросов). Сброс: {}.".format(
                format_reset(quota["reset_at"])
            )
        return None
    if quota["used_5h"] >= quota["limit_5h"]:
        return "Лимит этой модели на 5 часов исчерпан (1,5 млн токенов). Сброс: {}.".format(
            format_reset(quota["reset_5h"])
        )
    if quota["used_week"] >= quota["limit_week"]:
        return "Недельный лимит этой модели исчерпан (5 млн токенов). Сброс: {}.".format(
            format_reset(quota["reset_week"])
        )
    return None


def format_reset(timestamp: float | None) -> str:
    if timestamp is None:
        return "нет данных"
    remaining = max(0, int(timestamp - time.time()))
    days, remaining = divmod(remaining, 86400)
    hours, remaining = divmod(remaining, 3600)
    minutes, seconds = divmod(remaining, 60)
    parts = []
    if days:
        parts.append(f"{days} дн.")
    if hours or days:
        parts.append(f"{hours} ч.")
    if minutes or hours or days:
        parts.append(f"{minutes} мин.")
    if not parts:
        parts.append(f"{seconds} сек.")
    return " ".join(parts)


def format_ai_quota(user_id: int, model: str, premium: bool) -> str:
    quota = get_ai_quota(user_id, model, premium)
    if not premium:
        return (
            "📊 <b>Бесплатный лимит</b>\n"
            f"Запросы за 7 дней: <b>{quota['used']}/{quota['limit']}</b>\n"
            f"Сброс ближайшего запроса: <b>{format_reset(quota['reset_at'])}</b>"
        )
    marker = " (оценка, API не вернул usage)" if quota["estimated"] else ""
    return (
        f"📊 <b>Лимиты модели {html.escape(str(model))}</b>{marker}\n"
        f"За 5 часов: <b>{quota['used_5h']:,}/{quota['limit_5h']:,}</b> токенов"
        f" · сброс через <b>{format_reset(quota['reset_5h'])}</b>\n"
        f"За 7 дней: <b>{quota['used_week']:,}/{quota['limit_week']:,}</b> токенов"
        f" · сброс через <b>{format_reset(quota['reset_week'])}</b>"
    )
