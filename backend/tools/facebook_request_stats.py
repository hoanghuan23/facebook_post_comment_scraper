from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import json
from pathlib import Path
from typing import Any


SUCCESS_CLASSIFICATION = "success"


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _hour_key(event: dict[str, Any]) -> str:
    parsed = _parse_time(event.get("start_time")) or _parse_time(event.get("end_time"))
    if not parsed:
        return "unknown"
    return parsed.strftime("%Y-%m-%d %H:00")


def _is_request_event(event: dict[str, Any]) -> bool:
    return event.get("event_type", "request") == "request"


def _is_operation_event(event: dict[str, Any]) -> bool:
    return event.get("event_type") == "operation"


def _is_success(event: dict[str, Any]) -> bool:
    if isinstance(event.get("success"), bool):
        return bool(event["success"])
    return event.get("classification") == SUCCESS_CLASSIFICATION


def _rate(part: int, total: int) -> float:
    return round(part / total, 4) if total else 0.0


def _rate_percent(part: int, total: int) -> float:
    return round(_rate(part, total) * 100, 2)


def _build_bucket(events: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(events)
    success = sum(1 for event in events if _is_success(event))
    error = total - success

    errors_by_classification = Counter(
        str(event.get("classification") or "unknown")
        for event in events
        if not _is_success(event)
    )
    by_status_code = Counter(str(event.get("status_code") or "unknown") for event in events)
    by_scraper = Counter(str(event.get("scraper") or "unknown") for event in events)
    by_endpoint = Counter(str(event.get("endpoint_label") or "unknown") for event in events)

    return {
        "total_requests": total,
        "success_requests": success,
        "error_requests": error,
        "success_rate": _rate(success, total),
        "success_rate_percent": _rate_percent(success, total),
        "error_rate": _rate(error, total),
        "error_rate_percent": _rate_percent(error, total),
        "errors_by_classification": dict(sorted(errors_by_classification.items())),
        "by_status_code": dict(sorted(by_status_code.items())),
        "by_scraper": dict(sorted(by_scraper.items())),
        "by_endpoint": dict(sorted(by_endpoint.items())),
    }


def _build_http_status_bucket(events: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(events)
    status_2xx = sum(
        1
        for event in events
        if isinstance(event.get("status_code"), int) and 200 <= event["status_code"] <= 299
    )
    status_non_2xx = total - status_2xx

    return {
        "total_requests": total,
        "status_2xx_requests": status_2xx,
        "status_non_2xx_requests": status_non_2xx,
        "status_2xx_rate": _rate(status_2xx, total),
        "status_2xx_rate_percent": _rate_percent(status_2xx, total),
        "status_non_2xx_rate": _rate(status_non_2xx, total),
        "status_non_2xx_rate_percent": _rate_percent(status_non_2xx, total),
        "by_status_code": dict(
            sorted(Counter(str(event.get("status_code") or "unknown") for event in events).items())
        ),
    }


def _build_operation_bucket(events: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(events)
    success = sum(1 for event in events if bool(event.get("success")))
    error = total - success
    failure_reasons = Counter(
        str(event.get("failure_reason") or "unknown")
        for event in events
        if not bool(event.get("success"))
    )
    by_operation = Counter(str(event.get("operation_type") or "unknown") for event in events)

    return {
        "total_operations": total,
        "success_operations": success,
        "error_operations": error,
        "success_rate": _rate(success, total),
        "success_rate_percent": _rate_percent(success, total),
        "error_rate": _rate(error, total),
        "error_rate_percent": _rate_percent(error, total),
        "failure_reasons": dict(sorted(failure_reasons.items())),
        "by_operation": dict(sorted(by_operation.items())),
    }


def load_request_events(path: Path) -> tuple[list[dict[str, Any]], int]:
    events: list[dict[str, Any]] = []
    invalid_lines = 0

    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                invalid_lines += 1
                continue
            if isinstance(event, dict) and _is_request_event(event):
                events.append(event)

    return events, invalid_lines


def load_events(path: Path) -> tuple[list[dict[str, Any]], int]:
    events: list[dict[str, Any]] = []
    invalid_lines = 0

    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                invalid_lines += 1
                continue
            if isinstance(event, dict):
                events.append(event)

    return events, invalid_lines


def build_stats(path: Path) -> dict[str, Any]:
    events, invalid_lines = load_events(path)
    request_events = [event for event in events if _is_request_event(event)]
    operation_events = [event for event in events if _is_operation_event(event)]

    by_hour_raw: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in request_events:
        by_hour_raw[_hour_key(event)].append(event)

    return {
        "source_file": str(path),
        "invalid_lines": invalid_lines,
        "http_status_level": _build_http_status_bucket(request_events),
        "request_classification_level": _build_bucket(request_events),
        "operation_outcome_level": _build_operation_bucket(operation_events),
        "by_hour": {
            hour: _build_bucket(hour_events)
            for hour, hour_events in sorted(by_hour_raw.items())
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Calculate Facebook request success/error stats from a JSONL telemetry file."
    )
    parser.add_argument(
        "input",
        nargs="?",
        default="data/telemetry/facebook_requests_2026-07-10.jsonl",
        help="Path to facebook_requests_YYYY-MM-DD.jsonl",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Optional JSON output path. If omitted, stats are printed to stdout.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")

    stats = build_stats(input_path)
    rendered = json.dumps(stats, ensure_ascii=False, indent=2)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
        print(f"Wrote stats to {output_path}")
    else:
        print(rendered)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
