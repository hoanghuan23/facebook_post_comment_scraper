from __future__ import annotations

from collections import Counter, defaultdict
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urlparse
import uuid

import requests


SCHEMA_VERSION = 2
EVENT_TYPE_REQUEST = "request"
EVENT_TYPE_OPERATION = "operation"
CLASS_SUCCESS = "success"
CLASS_HTTP_ERROR = "http_error"
CLASS_TIMEOUT = "timeout"
CLASS_CONNECTION_ERROR = "connection_error"
CLASS_PROXY_ERROR = "proxy_error"
CLASS_EMPTY_RESPONSE = "empty_response"
CLASS_PARSE_ERROR = "parse_error"
CLASS_LOGIN_REQUIRED = "login_required_or_checkpoint"
CLASS_NO_METRIC_SIGNAL = "no_metric_signal"
OP_SOURCE_SCRAPE = "source_scrape"
OP_DIRECT_POST_METRIC = "direct_post_metric"
FAIL_LOGIN_REQUIRED = "login_required"
FAIL_RATE_LIMITED = "rate_limited"
FAIL_NOT_FOUND = "not_found"
FAIL_NO_METRIC_SIGNAL = "no_metric_signal"
FAIL_EMPTY_SOURCE_RESPONSE = "empty_source_response"
FAIL_EXCEPTION = "exception"

LEGACY_REQUEST_LEVEL_KEYS = {
    "total_requests",
    "success_requests",
    "error_requests",
    "first_error_request_index",
    "retry_count",
    "retry_success",
    "retry_failed",
    "max_consecutive_errors",
    "requests_per_minute",
    "latency_ms",
    "errors_by_classification",
    "by_hour",
    "proxy_mode",
    "by_endpoint",
    "by_status_code",
}

_run_id: ContextVar[Optional[str]] = ContextVar("facebook_telemetry_run_id", default=None)
_source_id: ContextVar[Optional[Any]] = ContextVar("facebook_telemetry_source_id", default=None)
_facebook_id: ContextVar[Optional[Any]] = ContextVar("facebook_telemetry_facebook_id", default=None)
_write_lock = threading.Lock()
_concurrency_lock = threading.Lock()
_active_requests = 0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _new_run_id() -> str:
    return f"{_utc_now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def get_run_id() -> str:
    run_id = _run_id.get()
    if not run_id:
        run_id = _new_run_id()
        _run_id.set(run_id)
    return run_id


def start_run(run_id: Optional[str] = None) -> str:
    run_id = run_id or _new_run_id()
    _run_id.set(run_id)
    return run_id


def set_context(source_id: Optional[Any] = None, facebook_id: Optional[Any] = None) -> None:
    _source_id.set(source_id)
    _facebook_id.set(facebook_id)


def get_source_id(default: Optional[Any] = None) -> Optional[Any]:
    return _source_id.get() if _source_id.get() is not None else default


def get_facebook_id(default: Optional[Any] = None) -> Optional[Any]:
    return _facebook_id.get() if _facebook_id.get() is not None else default


def telemetry_dir() -> Path:
    return Path(os.getenv("FACEBOOK_TELEMETRY_DIR", "data/telemetry"))


def raw_events_path(date: Optional[str] = None) -> Path:
    date = date or _utc_now().date().isoformat()
    return telemetry_dir() / f"facebook_requests_{date}.jsonl"


def summary_path(date: Optional[str] = None) -> Path:
    date = date or _utc_now().date().isoformat()
    return telemetry_dir() / f"facebook_summary_{date}.json"


def begin_attempt() -> tuple[float, str, int]:
    global _active_requests
    start_monotonic = time.perf_counter()
    start_time = _iso(_utc_now())
    with _concurrency_lock:
        _active_requests += 1
        current_concurrency = _active_requests
    return start_monotonic, start_time, current_concurrency


def finish_attempt(start_monotonic: float) -> tuple[str, int]:
    global _active_requests
    end_time = _iso(_utc_now())
    duration_ms = int(round((time.perf_counter() - start_monotonic) * 1000))
    with _concurrency_lock:
        _active_requests = max(0, _active_requests - 1)
    return end_time, duration_ms


def proxy_mode(proxies: Any, has_cookies: bool = False) -> str:
    if not proxies:
        return "none"
    return "static" if has_cookies else "rotating"


def proxy_label(proxies: Any) -> Optional[str]:
    if not proxies:
        return None
    proxy_url = None
    if isinstance(proxies, dict):
        proxy_url = proxies.get("https") or proxies.get("http")
    elif isinstance(proxies, str):
        proxy_url = proxies
    if not proxy_url:
        return None
    try:
        parsed = urlparse(proxy_url)
        if parsed.hostname:
            return f"{parsed.hostname}:{parsed.port}" if parsed.port else parsed.hostname
    except Exception:
        return None
    return None


def response_size_bytes(response: Any) -> Optional[int]:
    try:
        content = getattr(response, "content", None)
        if content is not None:
            return len(content)
        text = getattr(response, "text", None)
        if text is not None:
            return len(text.encode("utf-8", errors="replace"))
    except Exception:
        return None
    return None


def classify_exception(exc: BaseException) -> str:
    if isinstance(exc, requests.exceptions.Timeout):
        return CLASS_TIMEOUT
    if isinstance(exc, requests.exceptions.ProxyError):
        return CLASS_PROXY_ERROR
    if isinstance(exc, requests.exceptions.ConnectionError):
        return CLASS_CONNECTION_ERROR
    message = str(exc).lower()
    if "timeout" in message:
        return CLASS_TIMEOUT
    if "proxy" in message or "407" in message or "tunnel" in message:
        return CLASS_PROXY_ERROR
    return CLASS_CONNECTION_ERROR


def has_login_wall(response_text: Optional[str]) -> bool:
    text = (response_text or "").lower()
    if not text:
        return False
    markers = (
        "login_required",
        "checkpoint",
        "www.facebook.com/login",
        "/login/?",
        "you must log in",
        "please log in",
        "temporarily blocked",
    )
    return any(marker in text for marker in markers)


def classify_response(status_code: Optional[int], response_text: Optional[str] = None) -> str:
    if has_login_wall(response_text):
        return CLASS_LOGIN_REQUIRED
    if status_code == 200:
        return CLASS_SUCCESS
    return CLASS_HTTP_ERROR


def append_event(
    *,
    request_id: str,
    run_id: Optional[str],
    start_time: str,
    end_time: str,
    duration_ms: int,
    scraper: str,
    endpoint_label: str,
    source_id: Optional[Any],
    facebook_id: Optional[Any],
    attempt: int,
    max_retries: int,
    classification: Optional[str],
    status_code: Optional[int],
    response_size_bytes: Optional[int],
    current_concurrency: int,
    proxy_mode: str,
    proxy_label: Optional[str],
    success: Optional[bool] = None,
) -> None:
    if success is None:
        success = classification == CLASS_SUCCESS
    event = {
        "schema_version": SCHEMA_VERSION,
        "event_type": EVENT_TYPE_REQUEST,
        "request_id": request_id,
        "run_id": run_id or get_run_id(),
        "start_time": start_time,
        "end_time": end_time,
        "duration_ms": duration_ms,
        "scraper": scraper,
        "endpoint_label": endpoint_label,
        "source_id": source_id,
        "facebook_id": facebook_id,
        "attempt": attempt,
        "max_retries": max_retries,
        "success": bool(success),
        "classification": classification,
        "status_code": status_code,
        "response_size_bytes": response_size_bytes,
        "current_concurrency": current_concurrency,
        "proxy_mode": proxy_mode,
        "proxy_label": proxy_label,
    }
    try:
        path = raw_events_path(_date_from_iso(start_time))
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        with _write_lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:
        return


def append_operation_event(
    *,
    operation_type: str,
    success: bool,
    failure_reason: Optional[str] = None,
    run_id: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    duration_ms: Optional[int] = None,
    source_id: Optional[Any] = None,
    facebook_id: Optional[Any] = None,
    target_id: Optional[Any] = None,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    now = _iso(_utc_now())
    start_time = start_time or now
    end_time = end_time or now
    event = {
        "schema_version": SCHEMA_VERSION,
        "event_type": EVENT_TYPE_OPERATION,
        "operation_type": operation_type,
        "run_id": run_id or get_run_id(),
        "start_time": start_time,
        "end_time": end_time,
        "duration_ms": duration_ms,
        "source_id": source_id,
        "facebook_id": facebook_id,
        "target_id": target_id,
        "success": bool(success),
        "failure_reason": None if success else (failure_reason or FAIL_EXCEPTION),
        "details": details or {},
    }
    try:
        path = raw_events_path(_date_from_iso(start_time))
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        with _write_lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:
        return


def attach_response_metadata(
    response: Any,
    *,
    request_id: str,
    run_id: str,
    start_time: str,
    end_time: str,
    duration_ms: int,
    scraper: str,
    endpoint_label: str,
    source_id: Optional[Any],
    facebook_id: Optional[Any],
    attempt: int,
    max_retries: int,
    current_concurrency: int,
    proxy_mode: str,
    proxy_label: Optional[str],
) -> None:
    try:
        response._facebook_telemetry = {
            "request_id": request_id,
            "run_id": run_id,
            "start_time": start_time,
            "end_time": end_time,
            "duration_ms": duration_ms,
            "scraper": scraper,
            "endpoint_label": endpoint_label,
            "source_id": source_id,
            "facebook_id": facebook_id,
            "attempt": attempt,
            "max_retries": max_retries,
            "current_concurrency": current_concurrency,
            "proxy_mode": proxy_mode,
            "proxy_label": proxy_label,
            "logged": False,
        }
    except Exception:
        return


def record_response(response: Any, *, success: bool, classification: Optional[str]) -> None:
    meta = getattr(response, "_facebook_telemetry", None)
    if not meta or meta.get("logged"):
        return
    append_event(
        request_id=meta["request_id"],
        run_id=meta["run_id"],
        start_time=meta["start_time"],
        end_time=meta["end_time"],
        duration_ms=meta["duration_ms"],
        scraper=meta["scraper"],
        endpoint_label=meta["endpoint_label"],
        source_id=meta["source_id"],
        facebook_id=meta["facebook_id"],
        attempt=meta["attempt"],
        max_retries=meta["max_retries"],
        classification=classification,
        success=success,
        status_code=getattr(response, "status_code", None),
        response_size_bytes=response_size_bytes(response),
        current_concurrency=meta["current_concurrency"],
        proxy_mode=meta["proxy_mode"],
        proxy_label=meta["proxy_label"],
    )
    meta["logged"] = True


def _date_from_iso(value: str) -> str:
    return value[:10] if value else _utc_now().date().isoformat()


def iter_events(date: Optional[str] = None, path: Optional[Path] = None) -> Iterable[Dict[str, Any]]:
    path = path or raw_events_path(date)
    if not path.exists():
        return []
    events = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
    return events


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _percentile(values: list[int], percentile: float) -> Optional[int]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = round((len(ordered) - 1) * percentile)
    return ordered[int(index)]


def _request_events(events: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
    return [event for event in events if event.get("event_type", EVENT_TYPE_REQUEST) != EVENT_TYPE_OPERATION]


def _operation_events(events: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
    return [event for event in events if event.get("event_type") == EVENT_TYPE_OPERATION]


def _request_success(event: Dict[str, Any]) -> bool:
    if isinstance(event.get("success"), bool):
        return bool(event["success"])
    return event.get("classification") == CLASS_SUCCESS


def _build_request_level(events: list[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(events)
    success = sum(1 for event in events if _request_success(event))
    error = total - success
    latencies = [int(event["duration_ms"]) for event in events if isinstance(event.get("duration_ms"), int)]
    errors_by_classification = Counter(
        event.get("classification")
        for event in events
        if not _request_success(event) and event.get("classification")
    )

    first_error_request_index = None
    consecutive = 0
    max_consecutive_errors = 0
    for index, event in enumerate(events, start=1):
        if _request_success(event):
            consecutive = 0
            continue
        if first_error_request_index is None:
            first_error_request_index = index
        consecutive += 1
        max_consecutive_errors = max(max_consecutive_errors, consecutive)

    retry_count = sum(1 for event in events if int(event.get("attempt") or 1) > 1)
    grouped_by_request: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
    for event in events:
        if event.get("request_id"):
            grouped_by_request[str(event["request_id"])].append(event)
    retry_success = 0
    retry_failed = 0
    for request_events in grouped_by_request.values():
        if max(int(event.get("attempt") or 1) for event in request_events) <= 1:
            continue
        final_event = sorted(request_events, key=lambda item: int(item.get("attempt") or 1))[-1]
        if _request_success(final_event):
            retry_success += 1
        else:
            retry_failed += 1

    start_times = [_parse_time(event.get("start_time")) for event in events]
    end_times = [_parse_time(event.get("end_time")) for event in events]
    start_times = [value for value in start_times if value]
    end_times = [value for value in end_times if value]
    elapsed_seconds = None
    if start_times and end_times:
        elapsed_seconds = max((max(end_times) - min(start_times)).total_seconds(), 1)
    requests_per_minute = round(total / elapsed_seconds * 60, 4) if elapsed_seconds else 0

    by_hour_raw: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
    for event in events:
        start_time = _parse_time(event.get("start_time"))
        if start_time:
            by_hour_raw[start_time.strftime("%H")].append(event)
    by_hour = {}
    for hour, hour_events in sorted(by_hour_raw.items()):
        hour_total = len(hour_events)
        hour_success = sum(1 for event in hour_events if _request_success(event))
        hour_start = [_parse_time(event.get("start_time")) for event in hour_events]
        hour_end = [_parse_time(event.get("end_time")) for event in hour_events]
        hour_start = [value for value in hour_start if value]
        hour_end = [value for value in hour_end if value]
        hour_elapsed = max((max(hour_end) - min(hour_start)).total_seconds(), 1) if hour_start and hour_end else None
        by_hour[hour] = {
            "total": hour_total,
            "success": hour_success,
            "error": hour_total - hour_success,
            "success_rate": round(hour_success / hour_total, 4) if hour_total else 0,
            "requests_per_minute": round(hour_total / hour_elapsed * 60, 4) if hour_elapsed else 0,
        }

    proxy_mode_summary: Dict[str, Dict[str, int]] = {}
    for mode in sorted({str(event.get("proxy_mode") or "none") for event in events}):
        mode_events = [event for event in events if str(event.get("proxy_mode") or "none") == mode]
        mode_success = sum(1 for event in mode_events if _request_success(event))
        proxy_mode_summary[mode] = {
            "total": len(mode_events),
            "success": mode_success,
            "error": len(mode_events) - mode_success,
        }

    by_endpoint: Dict[str, Dict[str, int]] = {}
    endpoint_keys = sorted(
        {
            f"{event.get('scraper') or 'unknown'}:{event.get('endpoint_label') or 'unknown'}"
            for event in events
        }
    )
    for key in endpoint_keys:
        scraper, endpoint_label = key.split(":", 1)
        endpoint_events = [
            event
            for event in events
            if str(event.get("scraper") or "unknown") == scraper
            and str(event.get("endpoint_label") or "unknown") == endpoint_label
        ]
        endpoint_success = sum(1 for event in endpoint_events if _request_success(event))
        by_endpoint[key] = {
            "total": len(endpoint_events),
            "success": endpoint_success,
            "error": len(endpoint_events) - endpoint_success,
            "success_rate": round(endpoint_success / len(endpoint_events), 4) if endpoint_events else 0,
        }

    by_status_code = Counter(str(event.get("status_code")) for event in events)

    return {
        "total_requests": total,
        "success_requests": success,
        "error_requests": error,
        "success_rate": round(success / total, 4) if total else 0,
        "error_rate": round(error / total, 4) if total else 0,
        "first_error_request_index": first_error_request_index,
        "retry_count": retry_count,
        "retry_success": retry_success,
        "retry_failed": retry_failed,
        "max_consecutive_errors": max_consecutive_errors,
        "requests_per_minute": requests_per_minute,
        "latency_ms": {
            "avg": round(sum(latencies) / len(latencies), 2) if latencies else None,
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "p99": _percentile(latencies, 0.99),
        },
        "errors_by_classification": dict(errors_by_classification),
        "by_hour": by_hour,
        "proxy_mode": proxy_mode_summary,
        "by_endpoint": by_endpoint,
        "by_status_code": dict(by_status_code),
    }


def _build_operation_level(events: list[Dict[str, Any]], *, estimated: bool = False) -> Dict[str, Any]:
    total = len(events)
    success = sum(1 for event in events if bool(event.get("success")))
    error = total - success
    by_operation: Dict[str, Dict[str, int]] = {}
    for operation_type in sorted({str(event.get("operation_type") or "unknown") for event in events}):
        operation_events = [
            event for event in events if str(event.get("operation_type") or "unknown") == operation_type
        ]
        operation_success = sum(1 for event in operation_events if bool(event.get("success")))
        by_operation[operation_type] = {
            "total": len(operation_events),
            "success": operation_success,
            "error": len(operation_events) - operation_success,
            "success_rate": round(operation_success / len(operation_events), 4) if operation_events else 0,
        }

    failure_reasons = Counter(
        str(event.get("failure_reason") or "unknown")
        for event in events
        if not bool(event.get("success"))
    )

    by_hour_raw: Dict[str, list[Dict[str, Any]]] = defaultdict(list)
    for event in events:
        start_time = _parse_time(event.get("start_time"))
        if start_time:
            by_hour_raw[start_time.strftime("%H")].append(event)
    by_hour = {}
    for hour, hour_events in sorted(by_hour_raw.items()):
        hour_success = sum(1 for event in hour_events if bool(event.get("success")))
        by_hour[hour] = {
            "total": len(hour_events),
            "success": hour_success,
            "error": len(hour_events) - hour_success,
            "success_rate": round(hour_success / len(hour_events), 4) if hour_events else 0,
        }

    return {
        "estimated": estimated,
        "total_operations": total,
        "success_operations": success,
        "error_operations": error,
        "success_rate": round(success / total, 4) if total else 0,
        "error_rate": round(error / total, 4) if total else 0,
        "failure_reasons": dict(failure_reasons),
        "by_operation": by_operation,
        "by_hour": by_hour,
    }


def _failure_reason_from_classification(classification: Optional[str]) -> str:
    if classification == CLASS_LOGIN_REQUIRED:
        return FAIL_LOGIN_REQUIRED
    if classification == CLASS_NO_METRIC_SIGNAL:
        return FAIL_NO_METRIC_SIGNAL
    if classification in {CLASS_TIMEOUT, CLASS_CONNECTION_ERROR, CLASS_PROXY_ERROR, CLASS_HTTP_ERROR}:
        return str(classification)
    return "unknown"


def _build_estimated_outcome_level(request_events: list[Dict[str, Any]]) -> Dict[str, Any]:
    grouped: Dict[tuple[str, str], list[Dict[str, Any]]] = defaultdict(list)
    for event in request_events:
        scraper = str(event.get("scraper") or "unknown")
        if scraper == "direct_post_metrics":
            operation_type = OP_DIRECT_POST_METRIC
            key = str(event.get("facebook_id") or event.get("run_id") or event.get("request_id") or "unknown")
        else:
            operation_type = OP_SOURCE_SCRAPE
            key = str(event.get("run_id") or event.get("source_id") or event.get("request_id") or "unknown")
        grouped[(operation_type, key)].append(event)

    estimated_events: list[Dict[str, Any]] = []
    for (operation_type, key), group in grouped.items():
        success = any(_request_success(event) for event in group)
        first_event = group[0]
        last_event = group[-1]
        failure_reason = None
        if not success:
            classification = last_event.get("classification")
            failure_reason = _failure_reason_from_classification(classification) if classification else "unknown"
        estimated_events.append(
            {
                "operation_type": operation_type,
                "operation_key": key,
                "start_time": first_event.get("start_time"),
                "end_time": last_event.get("end_time"),
                "source_id": first_event.get("source_id"),
                "facebook_id": first_event.get("facebook_id"),
                "success": success,
                "failure_reason": failure_reason,
            }
        )

    return _build_operation_level(estimated_events, estimated=True)


def build_summary(date: Optional[str] = None, path: Optional[Path] = None) -> Dict[str, Any]:
    events = list(iter_events(date=date, path=path))
    date = date or (events[0]["start_time"][:10] if events else _utc_now().date().isoformat())
    request_events = _request_events(events)
    operation_events = _operation_events(events)
    request_level = _build_request_level(request_events)
    outcome_level = _build_operation_level(operation_events) if operation_events else None
    estimated_outcome_level = None if operation_events else (
        _build_estimated_outcome_level(request_events) if request_events else None
    )
    primary_outcome = outcome_level or _build_operation_level([], estimated=True)

    summary = {
        "schema_version": SCHEMA_VERSION,
        "date": date,
        "generated_at": _iso(_utc_now()),
        "total_operations": primary_outcome["total_operations"],
        "success_operations": primary_outcome["success_operations"],
        "error_operations": primary_outcome["error_operations"],
        "success_rate": primary_outcome["success_rate"],
        "error_rate": primary_outcome["error_rate"],
        "outcome_level": outcome_level,
        "estimated_outcome_level": estimated_outcome_level,
        "request_level": request_level,
        "notes": [
            "Top-level success_rate/error_rate use only operation outcome events. Legacy request-only logs are not promoted to final outcome metrics.",
            "request_level keeps raw HTTP attempt metrics for diagnosis and is not directly comparable to pipeline_logs.",
            "estimated_outcome_level is diagnostic only because request-only logs cannot distinguish no-new-posts from a failed business outcome.",
            "generated_at is the snapshot time; if raw JSONL receives more events later, this summary is not a full-day report.",
        ],
    }
    return summary


def normalize_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(summary)

    request_level = normalized.get("request_level")
    if not isinstance(request_level, dict):
        request_level = {}
    else:
        request_level = dict(request_level)

    for key in LEGACY_REQUEST_LEVEL_KEYS:
        if key in normalized and key not in request_level:
            request_level[key] = normalized[key]
        normalized.pop(key, None)

    if "success_rate" not in request_level and "total_requests" in request_level and "error_requests" in request_level:
        total = int(request_level.get("total_requests") or 0)
        error = int(request_level.get("error_requests") or 0)
        success = max(total - error, 0)
        request_level["success_rate"] = round(success / total, 4) if total else 0
    if "error_rate" not in request_level and "total_requests" in request_level and "error_requests" in request_level:
        total = int(request_level.get("total_requests") or 0)
        error = int(request_level.get("error_requests") or 0)
        request_level["error_rate"] = round(error / total, 4) if total else 0

    if request_level:
        normalized["request_level"] = request_level

    outcome_level = normalized.get("outcome_level")
    if isinstance(outcome_level, dict):
        if "success_rate" in outcome_level:
            normalized["success_rate"] = outcome_level["success_rate"]
        if "error_rate" in outcome_level:
            normalized["error_rate"] = outcome_level["error_rate"]
        for source_key, target_key in (
            ("total_operations", "total_operations"),
            ("success_operations", "success_operations"),
            ("error_operations", "error_operations"),
        ):
            if source_key in outcome_level:
                normalized[target_key] = outcome_level[source_key]

    return normalized


def read_summary(date: Optional[str] = None, path: Optional[Path] = None) -> Dict[str, Any]:
    input_path = path or summary_path(date)
    with input_path.open("r", encoding="utf-8") as fh:
        summary = json.load(fh)
    return normalize_summary(summary) if isinstance(summary, dict) else {}


def write_summary(date: Optional[str] = None, path: Optional[Path] = None) -> Dict[str, Any]:
    summary = build_summary(date=date)
    output_path = path or summary_path(summary["date"])
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return summary
