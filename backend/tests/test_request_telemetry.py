import json

import requests

from backend.scraper import request_telemetry as telemetry
from backend.scraper import direct_post_metrics


REQUEST_LEVEL_ONLY_KEYS = {
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


def _metric_signal_html(post_id):
    return f"""
    <html><body><script>
    {{"post_id":"{post_id}","comet_ufi_summary_and_actions_renderer":{{}},
     "reaction_count":{{"count":17}},
     "comments":{{"total_count":4}},
     "share_count":{{"count":2}}}}
    </script></body></html>
    """.encode("utf-8")


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
    assert REQUEST_LEVEL_ONLY_KEYS.isdisjoint(summary.keys())
    request_level = summary["request_level"]
    assert request_level["total_requests"] == 5
    assert request_level["success_requests"] == 2
    assert request_level["error_requests"] == 3
    assert request_level["success_rate"] == 0.4
    assert summary["estimated_outcome_level"]["estimated"] is True
    assert summary["total_operations"] == 0
    assert summary["success_rate"] == 0
    assert summary["error_rate"] == 0
    assert request_level["first_error_request_index"] == 2
    assert request_level["retry_count"] == 2
    assert request_level["retry_success"] == 1
    assert request_level["retry_failed"] == 1
    assert request_level["max_consecutive_errors"] == 2
    assert request_level["errors_by_classification"] == {"http_error": 2, "timeout": 1}
    assert request_level["latency_ms"]["avg"] == 300
    assert request_level["latency_ms"]["p50"] == 300
    assert request_level["by_hour"]["10"]["total"] == 5
    assert request_level["proxy_mode"]["none"] == {"total": 5, "success": 2, "error": 3}
    assert request_level["requests_per_minute"] > 0


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

    assert "total_requests" not in summary
    assert summary["request_level"]["total_requests"] == 2
    assert summary["request_level"]["error_rate"] == 1.0
    assert summary["total_operations"] == 2
    assert summary["success_operations"] == 1
    assert summary["error_operations"] == 1
    assert summary["estimated_outcome_level"] is None
    assert summary["success_rate"] == 0.5
    assert summary["error_rate"] == summary["outcome_level"]["error_rate"] == 0.5
    assert summary["outcome_level"]["failure_reasons"] == {"empty_source_response": 1}


def test_normalize_summary_preserves_legacy_request_fields_under_request_level():
    legacy = {
        "schema_version": 2,
        "date": "2026-07-09",
        "success_rate": 0.2,
        "error_rate": 0.8,
        "outcome_level": {
            "total_operations": 2,
            "success_operations": 1,
            "error_operations": 1,
            "success_rate": 0.5,
            "error_rate": 0.5,
        },
        "total_requests": 10,
        "success_requests": 2,
        "error_requests": 8,
        "latency_ms": {"avg": 123},
        "by_endpoint": {"direct": {"total": 10}},
    }

    normalized = telemetry.normalize_summary(legacy)

    assert REQUEST_LEVEL_ONLY_KEYS.isdisjoint(normalized.keys())
    assert normalized["success_rate"] == 0.5
    assert normalized["error_rate"] == 0.5
    assert normalized["total_operations"] == 2
    assert normalized["request_level"]["total_requests"] == 10
    assert normalized["request_level"]["error_requests"] == 8
    assert normalized["request_level"]["latency_ms"] == {"avg": 123}
    assert normalized["request_level"]["by_endpoint"] == {"direct": {"total": 10}}


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
    assert event["success"] is True
    assert "cookie" not in lines[0].lower()
    assert "token" not in lines[0].lower()


def test_direct_post_metrics_do_get_writes_success_after_metric_signal(tmp_path, monkeypatch):
    monkeypatch.setenv("FACEBOOK_TELEMETRY_DIR", str(tmp_path))
    telemetry.start_run("direct-run")
    telemetry.set_context(source_id=777, facebook_id="source-facebook-id")

    response = requests.Response()
    response.status_code = 200
    response.url = "https://www.facebook.com/posts/direct-target-1"
    response._content = _metric_signal_html("direct-target-1")

    class FakeSession:
        def get(self, url, timeout=None, allow_redirects=True):
            return response

    metric = direct_post_metrics.do_get(
        FakeSession(),
        "https://www.facebook.com/posts/direct-target-1",
        "direct-target-1",
        "www/original",
        timeout=1,
    )

    assert metric.has_metric_signal is True
    raw_path = next(tmp_path.glob("facebook_requests_*.jsonl"))
    lines = raw_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["run_id"] == "direct-run"
    assert event["scraper"] == "direct_post_metrics"
    assert event["endpoint_label"] == "facebook_direct_www/original"
    assert event["source_id"] == 777
    assert event["facebook_id"] == "source-facebook-id"
    assert event["success"] is True
    assert event["classification"] is None
    assert event["event_type"] == "request"

    summary = telemetry.build_summary(path=raw_path)
    assert summary["request_level"]["success_requests"] == 1
    assert summary["request_level"]["error_requests"] == 0
    assert summary["request_level"]["errors_by_classification"] == {}
    assert "cookie" not in lines[0].lower()
    assert "token" not in lines[0].lower()


def test_direct_post_metrics_do_get_writes_no_metric_signal(tmp_path, monkeypatch):
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

    assert metric.has_metric_signal is False
    raw_path = next(tmp_path.glob("facebook_requests_*.jsonl"))
    lines = raw_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["success"] is False
    assert event["classification"] == "no_metric_signal"
    assert event["event_type"] == "request"
    assert "cookie" not in lines[0].lower()
    assert "token" not in lines[0].lower()


def test_direct_post_metrics_do_get_keeps_http_error_for_status_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("FACEBOOK_TELEMETRY_DIR", str(tmp_path))
    telemetry.start_run("direct-run")
    telemetry.set_context(source_id=777, facebook_id="source-facebook-id")

    response = requests.Response()
    response.status_code = 404
    response.url = "https://www.facebook.com/posts/post-1"
    response._content = b"not found"

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

    assert metric.is_not_found is True
    raw_path = next(tmp_path.glob("facebook_requests_*.jsonl"))
    event = json.loads(raw_path.read_text(encoding="utf-8").splitlines()[0])
    assert event["success"] is False
    assert event["classification"] == "http_error"


def test_record_response_is_idempotent_for_same_response(tmp_path, monkeypatch):
    monkeypatch.setenv("FACEBOOK_TELEMETRY_DIR", str(tmp_path))
    response = requests.Response()
    response.status_code = 200
    response._content = _metric_signal_html("post-1")
    telemetry.attach_response_metadata(
        response,
        request_id="dup-1",
        run_id="run-1",
        start_time="2026-07-09T10:00:00.000Z",
        end_time="2026-07-09T10:00:00.100Z",
        duration_ms=100,
        scraper="direct_post_metrics",
        endpoint_label="facebook_direct_www/original",
        source_id=123,
        facebook_id="post-1",
        attempt=1,
        max_retries=1,
        current_concurrency=1,
        proxy_mode="none",
        proxy_label=None,
    )

    telemetry.record_response(response, success=True, classification=None)
    telemetry.record_response(response, success=False, classification=telemetry.CLASS_NO_METRIC_SIGNAL)

    raw_path = next(tmp_path.glob("facebook_requests_*.jsonl"))
    events = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines()]
    assert len(events) == 1
    assert events[0]["success"] is True
    assert events[0]["classification"] is None


def test_direct_smart_fetch_fallback_success_has_one_success_request_and_operation(tmp_path, monkeypatch):
    monkeypatch.setenv("FACEBOOK_TELEMETRY_DIR", str(tmp_path))
    telemetry.start_run("fallback-run")
    telemetry.set_context(source_id=777, facebook_id="source-facebook-id")
    monkeypatch.setattr(direct_post_metrics.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(direct_post_metrics.random, "uniform", lambda start, end: 0)

    responses = []
    first = requests.Response()
    first.status_code = 200
    first.url = "https://www.facebook.com/groups/g/posts/direct-target-1"
    first._content = b"<html><body>Facebook</body></html>"
    responses.append(first)

    second = requests.Response()
    second.status_code = 200
    second.url = "https://m.facebook.com/groups/g/posts/direct-target-1"
    second._content = _metric_signal_html("direct-target-1")
    responses.append(second)

    class FakeSession:
        def get(self, url, timeout=None, allow_redirects=True):
            response = responses.pop(0)
            response.url = url
            return response

    monkeypatch.setattr(direct_post_metrics, "build_session", lambda cookies_raw, mobile=False: FakeSession())

    result = direct_post_metrics.smart_fetch(
        None,
        "direct-target-1",
        original_url="https://www.facebook.com/groups/g/posts/direct-target-1",
        jitter_min_seconds=0,
        jitter_max_seconds=0,
    )
    operation_success = (
        result.has_metric_signal
        and not result.is_rate_limited
        and not result.is_login_required
        and not result.is_not_found
        and not result.error_message
    )
    telemetry.append_operation_event(
        operation_type=telemetry.OP_DIRECT_POST_METRIC,
        success=operation_success,
        failure_reason=None,
        run_id=telemetry.get_run_id(),
        source_id=777,
        facebook_id="direct-target-1",
        target_id="direct-target-1",
    )

    raw_path = next(tmp_path.glob("facebook_requests_*.jsonl"))
    events = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines()]
    request_events = [event for event in events if event["event_type"] == "request"]
    operation_events = [event for event in events if event["event_type"] == "operation"]
    assert len(request_events) == 2
    assert len({event["request_id"] for event in request_events}) == 2
    assert sum(1 for event in request_events if event["success"] is True) == 1
    assert [event["classification"] for event in request_events] == ["no_metric_signal", None]
    assert operation_events[0]["success"] is True


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
