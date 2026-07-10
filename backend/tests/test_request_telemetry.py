import json

import requests

from backend.scraper import request_telemetry as telemetry
from backend.scraper import direct_post_metrics


def _event(
    *,
    request_id,
    attempt,
    classification,
    start_time,
    end_time,
    duration_ms,
    proxy_mode="none",
):
    return {
        "schema_version": 1,
        "request_id": request_id,
        "run_id": "run-1",
        "start_time": start_time,
        "end_time": end_time,
        "duration_ms": duration_ms,
        "scraper": "group_posts",
        "endpoint_label": "facebook_graphql",
        "source_id": 123,
        "facebook_id": "group-123",
        "attempt": attempt,
        "max_retries": 3,
        "classification": classification,
        "status_code": 200 if classification == "success" else 429,
        "response_size_bytes": 100,
        "current_concurrency": 1,
        "proxy_mode": proxy_mode,
        "proxy_label": None,
    }


def _operation(
    *,
    operation_type,
    success,
    start_time,
    end_time,
    failure_reason=None,
    source_id=123,
    facebook_id="group-123",
    target_id=None,
):
    return {
        "schema_version": 2,
        "event_type": "operation",
        "operation_type": operation_type,
        "run_id": "run-1",
        "start_time": start_time,
        "end_time": end_time,
        "duration_ms": 100,
        "source_id": source_id,
        "facebook_id": facebook_id,
        "target_id": target_id,
        "success": success,
        "failure_reason": failure_reason,
        "details": {},
    }


def test_classification_helpers():
    assert telemetry.classify_response(200, "{}") == "success"
    assert telemetry.classify_response(429, "too many") == "http_error"
    assert telemetry.classify_response(200, "checkpoint required") == "login_required_or_checkpoint"
    assert telemetry.classify_exception(requests.exceptions.Timeout()) == "timeout"
    assert telemetry.classify_exception(requests.exceptions.ProxyError()) == "proxy_error"
    assert telemetry.classify_exception(requests.exceptions.ConnectionError()) == "connection_error"


def test_proxy_label_strips_credentials():
    proxies = {"https": "http://user:secret@proxy.example.com:8080"}

    assert telemetry.proxy_label(proxies) == "proxy.example.com:8080"


def test_build_summary_counts_retry_and_error_metrics(tmp_path):
    raw_path = tmp_path / "facebook_requests_2026-07-09.jsonl"
    events = [
        _event(
            request_id="req-1",
            attempt=1,
            classification="success",
            start_time="2026-07-09T10:00:00.000Z",
            end_time="2026-07-09T10:00:00.100Z",
            duration_ms=100,
        ),
        _event(
            request_id="req-2",
            attempt=1,
            classification="http_error",
            start_time="2026-07-09T10:00:01.000Z",
            end_time="2026-07-09T10:00:01.200Z",
            duration_ms=200,
        ),
        _event(
            request_id="req-2",
            attempt=2,
            classification="success",
            start_time="2026-07-09T10:00:03.000Z",
            end_time="2026-07-09T10:00:03.300Z",
            duration_ms=300,
        ),
        _event(
            request_id="req-3",
            attempt=1,
            classification="timeout",
            start_time="2026-07-09T10:00:04.000Z",
            end_time="2026-07-09T10:00:04.400Z",
            duration_ms=400,
        ),
        _event(
            request_id="req-3",
            attempt=2,
            classification="http_error",
            start_time="2026-07-09T10:00:05.000Z",
            end_time="2026-07-09T10:00:05.500Z",
            duration_ms=500,
        ),
    ]
    raw_path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")

    summary = telemetry.build_summary(date="2026-07-09", path=raw_path)

    assert summary["schema_version"] == 2
    assert summary["total_requests"] == 5
    assert summary["success_requests"] == 2
    assert summary["error_requests"] == 3
    assert summary["request_level"]["success_rate"] == 0.4
    assert summary["estimated_outcome_level"]["estimated"] is True
    assert summary["total_operations"] == 0
    assert summary["success_rate"] == 0
    assert summary["error_rate"] == 0
    assert summary["first_error_request_index"] == 2
    assert summary["retry_count"] == 2
    assert summary["retry_success"] == 1
    assert summary["retry_failed"] == 1
    assert summary["max_consecutive_errors"] == 2
    assert summary["errors_by_classification"] == {"http_error": 2, "timeout": 1}
    assert summary["latency_ms"]["avg"] == 300
    assert summary["latency_ms"]["p50"] == 300
    assert summary["by_hour"]["10"]["total"] == 5
    assert summary["proxy_mode"]["none"] == {"total": 5, "success": 2, "error": 3}
    assert summary["requests_per_minute"] > 0


def test_build_summary_prefers_operation_outcomes_over_raw_request_errors(tmp_path):
    raw_path = tmp_path / "facebook_requests_2026-07-09.jsonl"
    events = [
        _event(
            request_id="req-1",
            attempt=1,
            classification="http_error",
            start_time="2026-07-09T10:00:00.000Z",
            end_time="2026-07-09T10:00:00.100Z",
            duration_ms=100,
        ),
        _event(
            request_id="req-2",
            attempt=1,
            classification="http_error",
            start_time="2026-07-09T10:00:01.000Z",
            end_time="2026-07-09T10:00:01.200Z",
            duration_ms=200,
        ),
        _operation(
            operation_type="direct_post_metric",
            success=True,
            start_time="2026-07-09T10:00:00.000Z",
            end_time="2026-07-09T10:00:02.000Z",
            target_id="post-1",
        ),
        _operation(
            operation_type="source_scrape",
            success=False,
            failure_reason="empty_source_response",
            start_time="2026-07-09T10:05:00.000Z",
            end_time="2026-07-09T10:05:01.000Z",
        ),
    ]
    raw_path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")

    summary = telemetry.build_summary(date="2026-07-09", path=raw_path)

    assert summary["total_requests"] == 2
    assert summary["request_level"]["error_rate"] == 1.0
    assert summary["total_operations"] == 2
    assert summary["success_operations"] == 1
    assert summary["error_operations"] == 1
    assert summary["success_rate"] == 0.5
    assert summary["outcome_level"]["failure_reasons"] == {"empty_source_response": 1}


def test_append_event_writes_jsonl_to_configured_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("FACEBOOK_TELEMETRY_DIR", str(tmp_path))
    start_time = "2026-07-09T10:00:00.000Z"

    telemetry.append_event(
        request_id="req-1",
        run_id="run-1",
        start_time=start_time,
        end_time="2026-07-09T10:00:00.100Z",
        duration_ms=100,
        scraper="group_posts",
        endpoint_label="facebook_graphql",
        source_id=123,
        facebook_id="group-123",
        attempt=1,
        max_retries=3,
        classification="success",
        status_code=200,
        response_size_bytes=100,
        current_concurrency=1,
        proxy_mode="none",
        proxy_label=None,
    )

    raw_path = next(tmp_path.glob("facebook_requests_*.jsonl"))
    lines = raw_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["schema_version"] == 2
    assert event["event_type"] == "request"
    assert event["endpoint_label"] == "facebook_graphql"
    assert "cookie" not in lines[0].lower()
    assert "token" not in lines[0].lower()


def test_direct_post_metrics_do_get_writes_telemetry(tmp_path, monkeypatch):
    monkeypatch.setenv("FACEBOOK_TELEMETRY_DIR", str(tmp_path))
    telemetry.start_run("direct-run")
    telemetry.set_context(source_id=777, facebook_id="source-facebook-id")

    response = requests.Response()
    response.status_code = 200
    response.url = "https://www.facebook.com/posts/post-1"
    response._content = b"<html><body>Facebook</body></html>"

    class FakeSession:
        def get(self, url, timeout=None, allow_redirects=True):
            return response

    metric = direct_post_metrics.do_get(
        FakeSession(),
        "https://www.facebook.com/posts/post-1",
        "post-1",
        "www/original",
        timeout=1,
    )

    assert metric.post_id == "post-1"
    raw_path = next(tmp_path.glob("facebook_requests_*.jsonl"))
    lines = raw_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["run_id"] == "direct-run"
    assert event["scraper"] == "direct_post_metrics"
    assert event["endpoint_label"] == "facebook_direct_www/original"
    assert event["source_id"] == 777
    assert event["facebook_id"] == "source-facebook-id"
    assert event["classification"] == "success"
    assert event["event_type"] == "request"
    assert "cookie" not in lines[0].lower()
    assert "token" not in lines[0].lower()


def test_append_operation_event_writes_outcome_without_sensitive_data(tmp_path, monkeypatch):
    monkeypatch.setenv("FACEBOOK_TELEMETRY_DIR", str(tmp_path))
    telemetry.start_run("operation-run")

    telemetry.append_operation_event(
        operation_type=telemetry.OP_DIRECT_POST_METRIC,
        success=False,
        failure_reason=telemetry.FAIL_NO_METRIC_SIGNAL,
        source_id=777,
        facebook_id="post-1",
        target_id="post-1",
        details={"fetch_method": "mobile/post"},
    )

    raw_path = next(tmp_path.glob("facebook_requests_*.jsonl"))
    event = json.loads(raw_path.read_text(encoding="utf-8").splitlines()[0])
    assert event["event_type"] == "operation"
    assert event["operation_type"] == "direct_post_metric"
    assert event["success"] is False
    assert event["failure_reason"] == "no_metric_signal"
