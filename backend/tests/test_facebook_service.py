from datetime import datetime, timedelta
from types import SimpleNamespace
import json

from fastapi import BackgroundTasks
from sqlalchemy import text
from backend.database.crud import CommentCRUD, FacebookSessionCRUD, PipelineJobCRUD, PostCRUD, PostMetricCRUD, SourceCRUD, UserCRUD
from backend.database.db import SessionLocal, engine
from backend.database.models import Base, ScrapeJob, ScraperLog
from backend.database.schemas import SourceCreate, SourceUpdate
from backend.api.routes.sources import (
    _bootstrap_scrape_source_last_24h,
    create_source,
    get_source_schedule_stats,
    get_sources_ranking,
    list_sources,
    refresh_source,
    update_source,
)
from backend.scraper.facebook_service import (
    FacebookScraperService,
    _load_json_dict,
    _coerce_count,
    _coerce_datetime,
    _normalize_group_post,
    _normalize_timeline_post,
)
from backend.scheduler.periodic_tasks import generate_analytics_cache, periodic_scrape_new_posts, update_recent_post_metrics
from backend.services.schedule_service import calculate_tier
import post_scraper
import group_post_scraper_v2
import comment_scraper


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def teardown_function():
    Base.metadata.drop_all(bind=engine)


def test_facebook_session_upsert_active_for_user_persists_fb_user_id_and_updates_fields():
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="session_u1", email="session_u1@example.com", password="secret123")
        created = FacebookSessionCRUD.upsert_active_for_user(
            db=db,
            user_id=user.id,
            fb_cookies='{"c_user":"111"}',
            fb_dtsg="token-1",
            fb_user_agent="ua-1",
            fb_user_id="111",
        )

        assert created.user_id == user.id
        assert created.fb_user_id == "111"
        assert created.fb_cookies == '{"c_user":"111"}'
        assert created.fb_dtsg == "token-1"
        assert created.fb_user_agent == "ua-1"

        updated = FacebookSessionCRUD.upsert_active_for_user(
            db=db,
            user_id=user.id,
            fb_cookies='{"c_user":"222"}',
            fb_dtsg="token-2",
            fb_user_agent="ua-2",
            fb_user_id="222",
        )

        assert updated.id == created.id
        assert updated.fb_user_id == "222"
        assert updated.fb_cookies == '{"c_user":"222"}'
        assert updated.fb_dtsg == "token-2"
        assert updated.fb_user_agent == "ua-2"
    finally:
        db.close()


def test_facebook_session_upsert_from_login_extraction_parses_c_user():
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="session_u2", email="session_u2@example.com", password="secret123")
        session = FacebookSessionCRUD.upsert_from_login_extraction(
            db=db,
            user_id=user.id,
            fb_cookies="datr=abc; c_user=123456789; xs=xyz",
            fb_dtsg="token-login",
            fb_user_agent="ua-login",
        )

        assert session.user_id == user.id
        assert session.fb_user_id == "123456789"
        assert session.fb_dtsg == "token-login"
        assert session.fb_user_agent == "ua-login"
    finally:
        db.close()


def test_facebook_session_upsert_from_login_extraction_parses_c_user_from_json_dict():
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="session_u3", email="session_u3@example.com", password="secret123")
        session = FacebookSessionCRUD.upsert_from_login_extraction(
            db=db,
            user_id=user.id,
            fb_cookies='{"datr":"abc","c_user":"987654321","xs":"xyz"}',
            fb_dtsg="token-json",
            fb_user_agent="ua-json",
        )

        assert session.user_id == user.id
        assert session.fb_user_id == "987654321"
        assert session.fb_dtsg == "token-json"
        assert session.fb_user_agent == "ua-json"
    finally:
        db.close()


def test_normalize_group_post_maps_metrics_and_media_flags():
    normalized = _normalize_group_post(
        {
            "post_id": "123",
            "permalink": "https://facebook.com/posts/123",
            "message": "hello",
            "posted_at": datetime(2026, 1, 1, 10, 0, 0),
            "reaction_count": 11,
            "share_count": 2,
            "comment_count": 4,
            "photos": [{"id": "p1"}],
            "videos": [],
        }
    )

    assert normalized["facebook_post_id"] == "123"
    assert normalized["likes_count"] == 11
    assert normalized["shares_count"] == 2
    assert normalized["comments_count"] == 4
    assert normalized["media_count"] == 1
    assert normalized["has_images"] is True
    assert normalized["has_videos"] is False


def test_coerce_count_parses_compact_facebook_counts():
    assert _coerce_count("2K") == 2000
    assert _coerce_count("1.2K") == 1200
    assert _coerce_count("1,2K") == 1200
    assert _coerce_count("1,234") == 1234
    assert _coerce_count("bad-value") == 0


def test_normalize_group_post_parses_compact_metric_counts():
    normalized = _normalize_group_post(
        {
            "post_id": "compact-1",
            "reaction_count": "2K",
            "share_count": "1.2K",
            "comment_count": "1,234",
            "photos": [],
            "videos": [],
        }
    )

    assert normalized["likes_count"] == 2000
    assert normalized["shares_count"] == 1200
    assert normalized["comments_count"] == 1234


def test_normalize_timeline_post_maps_media_and_text():
    normalized = _normalize_timeline_post(
        {
            "post_id": "timeline-1",
            "permalink": "https://facebook.com/posts/timeline-1",
            "text": "timeline post",
            "posted_at": datetime(2026, 1, 1, 11, 0, 0),
            "reaction_count": 9,
            "share_count": 3,
            "comment_count": 7,
            "media": [{"type": "photo"}, {"type": "video"}],
        }
    )

    assert normalized["facebook_post_id"] == "timeline-1"
    assert normalized["content"] == "timeline post"
    assert normalized["likes_count"] == 9
    assert normalized["shares_count"] == 3
    assert normalized["comments_count"] == 7
    assert normalized["media_count"] == 2
    assert normalized["has_images"] is True
    assert normalized["has_videos"] is True


def test_coerce_datetime_parses_iso_z():
    parsed = _coerce_datetime("2026-01-01T10:00:00Z")
    assert parsed == datetime(2026, 1, 1, 10, 0, 0)


def test_coerce_datetime_parses_offset_to_utc():
    parsed = _coerce_datetime("2026-01-01T17:00:00+07:00")
    assert parsed == datetime(2026, 1, 1, 10, 0, 0)


def test_coerce_datetime_accepts_datetime_object():
    value = datetime(2026, 1, 1, 10, 0, 0)
    parsed = _coerce_datetime(value)
    assert parsed == value


def test_coerce_datetime_parses_unix_timestamp():
    parsed = _coerce_datetime(1767242400)
    assert parsed == datetime(2026, 1, 1, 4, 40, 0)


def test_coerce_datetime_returns_none_for_invalid_value():
    parsed = _coerce_datetime("not-a-date")
    assert parsed is None


def test_load_json_dict_rejects_cookie_list_name_value():
    cookies = _load_json_dict(
        '[{"name":"c_user","value":"123"},{"name":"xs","value":"abc"}]'
    )
    assert cookies == {}


def test_load_json_dict_rejects_cookie_list_key_value_strings():
    cookies = _load_json_dict('["c_user=123", "xs=abc"]')
    assert cookies == {}


def test_load_json_dict_accepts_semicolon_cookie_string():
    cookies = _load_json_dict("datr=abc; c_user=123456789; xs=xyz")
    assert cookies == {
        "datr": "abc",
        "c_user": "123456789",
        "xs": "xyz",
    }


def test_apply_source_auth_context_applies_proxy_and_user_agent(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="session_u4", email="session_u4@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_name="Group Test",
            source_type="group",
            facebook_url="https://www.facebook.com/groups/123456789012345/",
            facebook_id="123456789012345",
        )
        FacebookSessionCRUD.upsert_active_for_user(
            db=db,
            user_id=user.id,
            fb_cookies="datr=abc; c_user=123456789; xs=xyz",
            fb_dtsg="token-ctx",
            fb_user_agent="ua-ctx",
            fb_user_id="123456789",
        )

        monkeypatch.setattr(
            "backend.scraper.facebook_service.select_proxy",
            lambda has_cookies: {"http": "http://static-proxy", "https": "http://static-proxy"} if has_cookies else None,
        )

        FacebookScraperService._apply_source_auth_context(db, source)

        assert group_post_scraper_v2.COOKIES["c_user"] == "123456789"
        assert group_post_scraper_v2.FB_DTSG == "token-ctx"
        assert group_post_scraper_v2.PROXIES == {"http": "http://static-proxy", "https": "http://static-proxy"}
        assert group_post_scraper_v2.HEADERS["user-agent"] == "ua-ctx"
        assert post_scraper.PROXIES == {"http": "http://static-proxy", "https": "http://static-proxy"}
        assert post_scraper.BASE_HEADERS["user-agent"] == "ua-ctx"
        assert comment_scraper.PROXIES == {"http": "http://static-proxy", "https": "http://static-proxy"}
        assert comment_scraper.BASE_HEADERS["user-agent"] == "ua-ctx"
    finally:
        db.close()


def test_normalize_group_post_falls_back_when_invalid_posted_at():
    normalized = _normalize_group_post(
        {
            "post_id": "123",
            "permalink": "https://facebook.com/posts/123",
            "message": "hello",
            "posted_at": "invalid-datetime",
            "reaction_count": 1,
            "share_count": 1,
            "comment_count": 1,
            "photos": [],
            "videos": [],
        }
    )
    assert isinstance(normalized["posted_at"], datetime)


def test_scrape_group_source_creates_posts_and_metrics(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="alice", email="alice@example.com", password="secret123")
        FacebookSessionCRUD.upsert_active_for_user(
            db=db,
            user_id=user.id,
            fb_cookies='{"c_user":"123"}',
            fb_dtsg="token",
        )
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="361726451351144",
            facebook_url="https://www.facebook.com/groups/361726451351144",
            source_name="Initial Name",
        )

        monkeypatch.setattr(
            "backend.scraper.facebook_service.group_scraper.fetch_posts",
            lambda limit=20: [
                {
                    "post_id": "1001",
                    "group_link": "https://www.facebook.com/groups/361726451351144/",
                    "permalink": "https://facebook.com/groups/361726451351144/posts/1001",
                    "message": "Post 1",
                    "posted_at": datetime(2026, 1, 1, 9, 0, 0),
                    "reaction_count": 5,
                    "share_count": 1,
                    "comment_count": 3,
                    "group_name": "Tracked Group",
                    "photos": [],
                    "videos": [],
                }
            ],
        )

        result = FacebookScraperService.scrape_source(db, source.id, limit=5)
        created_post = PostCRUD.get_by_source_and_facebook_post_id(db, source.id, "1001")

        assert result.total_fetched == 1
        assert result.created_posts == 1
        assert result.updated_posts == 0
        assert created_post is not None
        assert created_post.current_likes == 5
        assert created_post.current_shares == 1
        assert created_post.current_comments == 3
        assert len(created_post.metrics_history) == 1

        refreshed_source = SourceCRUD.get_by_id(db, source.id)
        assert refreshed_source.source_name == "Tracked Group"
        assert refreshed_source.last_scraped is not None
    finally:
        db.close()


def test_scrape_group_source_last_24h_does_not_persist_old_or_unknown_dates(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="last24-user", email="last24@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="last24-group",
            facebook_url="https://www.facebook.com/groups/last24-group",
            source_name="Last 24 Group",
            include_comments=False,
        )
        recent_time = datetime.utcnow() - timedelta(hours=2)
        old_time = datetime.utcnow() - timedelta(hours=30)

        monkeypatch.setattr(
            "backend.scraper.facebook_service.group_scraper.fetch_posts",
            lambda **kwargs: [
                {
                    "post_id": "recent-post",
                    "permalink": "https://facebook.com/posts/recent-post",
                    "message": "Recent",
                    "posted_at": recent_time,
                    "reaction_count": 1,
                    "share_count": 0,
                    "comment_count": 0,
                    "group_name": "Last 24 Group",
                    "photos": [],
                    "videos": [],
                },
                {
                    "post_id": "old-post",
                    "permalink": "https://facebook.com/posts/old-post",
                    "message": "Old",
                    "posted_at": old_time,
                    "reaction_count": 1,
                    "share_count": 0,
                    "comment_count": 0,
                    "group_name": "Last 24 Group",
                    "photos": [],
                    "videos": [],
                },
                {
                    "post_id": "unknown-date-post",
                    "permalink": "https://facebook.com/posts/unknown-date-post",
                    "message": "Unknown date",
                    "posted_at": "not-a-date",
                    "reaction_count": 1,
                    "share_count": 0,
                    "comment_count": 0,
                    "group_name": "Last 24 Group",
                    "photos": [],
                    "videos": [],
                },
            ],
        )

        result = FacebookScraperService.scrape_source(db, source.id, last_24_hours_only=True)

        assert result.total_fetched == 3
        assert result.created_posts == 1
        assert result.filtered_by_cutoff == 2
        assert PostCRUD.get_by_source_and_facebook_post_id(db, source.id, "recent-post") is not None
        assert PostCRUD.get_by_source_and_facebook_post_id(db, source.id, "old-post") is None
        assert PostCRUD.get_by_source_and_facebook_post_id(db, source.id, "unknown-date-post") is None
    finally:
        db.close()


def test_scrape_group_source_min_posted_at_only_persists_newer_posts(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="cutoff-user", email="cutoff@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="cutoff-group",
            facebook_url="https://www.facebook.com/groups/cutoff-group",
            source_name="Cutoff Group",
            include_comments=False,
        )
        latest_seen = datetime.utcnow() - timedelta(hours=3)

        monkeypatch.setattr(
            "backend.scraper.facebook_service.group_scraper.fetch_posts",
            lambda **kwargs: [
                {
                    "post_id": "equal-cutoff",
                    "permalink": "https://facebook.com/posts/equal-cutoff",
                    "message": "Equal cutoff",
                    "posted_at": latest_seen,
                    "reaction_count": 1,
                    "share_count": 0,
                    "comment_count": 0,
                    "group_name": "Cutoff Group",
                    "photos": [],
                    "videos": [],
                },
                {
                    "post_id": "newer-than-cutoff",
                    "permalink": "https://facebook.com/posts/newer-than-cutoff",
                    "message": "Newer",
                    "posted_at": latest_seen + timedelta(minutes=5),
                    "reaction_count": 1,
                    "share_count": 0,
                    "comment_count": 0,
                    "group_name": "Cutoff Group",
                    "photos": [],
                    "videos": [],
                },
            ],
        )

        result = FacebookScraperService.scrape_source(
            db,
            source.id,
            last_24_hours_only=True,
            min_posted_at=latest_seen,
        )

        assert result.created_posts == 1
        assert result.filtered_by_cutoff == 1
        assert PostCRUD.get_by_source_and_facebook_post_id(db, source.id, "equal-cutoff") is None
        assert PostCRUD.get_by_source_and_facebook_post_id(db, source.id, "newer-than-cutoff") is not None
    finally:
        db.close()


def test_scrape_group_source_updates_existing_post_without_duplicate_metric(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="bob", email="bob@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="361726451351144",
            facebook_url="https://www.facebook.com/groups/361726451351144",
            source_name="Tracked Group",
        )
        first_post, _ = PostCRUD.upsert_for_source(
            db,
            source_id=source.id,
            facebook_post_id="1002",
            facebook_url="https://facebook.com/posts/1002",
            posted_at=datetime(2026, 1, 1, 8, 0, 0),
            content="Old",
            likes_count=1,
            shares_count=0,
            comments_count=0,
            media_count=0,
            has_images=False,
            has_videos=False,
        )

        monkeypatch.setattr(
            "backend.scraper.facebook_service.group_scraper.fetch_posts",
            lambda limit=20: [
                {
                    "post_id": "1002",
                    "group_link": "https://www.facebook.com/groups/361726451351144/",
                    "permalink": "https://facebook.com/posts/1002-new",
                    "message": "New",
                    "posted_at": datetime(2026, 1, 1, 8, 30, 0),
                    "reaction_count": 10,
                    "share_count": 4,
                    "comment_count": 6,
                    "group_name": "Tracked Group",
                    "photos": [{"id": "photo"}],
                    "videos": [],
                }
            ],
        )

        result = FacebookScraperService.scrape_source(db, source.id, limit=5)
        updated_post = PostCRUD.get_by_id(db, first_post.id)

        assert result.created_posts == 0
        assert result.updated_posts == 1
        assert updated_post.facebook_url == "https://facebook.com/posts/1002-new"
        assert updated_post.content == "New"
        assert updated_post.media_count == 1
        assert len(updated_post.metrics_history) == 1
    finally:
        db.close()


def test_scrape_page_source_creates_post(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="carol", email="carol@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="page",
            facebook_id="page-123",
            facebook_url="https://www.facebook.com/page-123",
            source_name="Page Source",
        )

        monkeypatch.setattr(
            "backend.scraper.facebook_service.timeline_scraper.fetch_posts",
            lambda limit=20, base_folder="page_post": [
                {
                    "post_id": "page-post-1",
                    "permalink": "https://facebook.com/page/posts/page-post-1",
                    "text": "Page post",
                    "posted_at": datetime(2026, 1, 2, 9, 0, 0),
                    "reaction_count": 12,
                    "share_count": 5,
                    "comment_count": 8,
                    "page_name": "Fetched Page",
                    "media": [{"type": "photo"}],
                }
            ],
        )

        result = FacebookScraperService.scrape_source(db, source.id, limit=5)
        created_post = PostCRUD.get_by_source_and_facebook_post_id(db, source.id, "page-post-1")

        assert result.created_posts == 1
        assert result.updated_posts == 0
        assert created_post is not None
        assert created_post.current_likes == 12
        assert len(created_post.metrics_history) == 1
        assert SourceCRUD.get_by_id(db, source.id).source_name == "Fetched Page"
    finally:
        db.close()

def test_scrape_page_source_resolves_slug_id_before_fetch(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="page-slug-user", email="page-slug-user@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="page",
            facebook_id="page-slug",
            facebook_url="https://www.facebook.com/page-slug",
            source_name="Page Slug",
        )
        FacebookSessionCRUD.upsert_active_for_user(
            db=db,
            user_id=user.id,
            fb_cookies='{"c_user":"123"}',
            fb_dtsg="token",
            fb_user_agent="browser-agent-from-api-session",
        )
        calls = []

        monkeypatch.setattr("main.extract_user_id_from_url", lambda url, cookies=None: "123456789")

        def fake_fetch_posts(limit=20, **kwargs):
            calls.append({"limit": limit, **kwargs})
            assert post_scraper.USER_ID == "123456789"
            assert post_scraper.PAGE_NAME is None
            assert post_scraper.BASE_HEADERS["user-agent"] == "Mozilla/5.0"
            assert post_scraper.BASE_HEADERS["referer"] == "https://www.facebook.com/profile.php?id=123456789"
            return [
                {
                    "post_id": "resolved-page-post",
                    "permalink": "https://facebook.com/page/posts/resolved-page-post",
                    "text": "Resolved page post",
                    "posted_at": datetime.utcnow(),
                    "reaction_count": 1,
                    "share_count": 0,
                    "comment_count": 0,
                    "page_name": "Resolved Page",
                    "media": [],
                }
            ]

        monkeypatch.setattr("backend.scraper.facebook_service.timeline_scraper.fetch_posts", fake_fetch_posts)

        result = FacebookScraperService.scrape_source(db, source.id, last_24_hours_only=True)
        refreshed_source = SourceCRUD.get_by_id(db, source.id)

        assert result.created_posts == 1
        assert refreshed_source.facebook_id == "123456789"
        assert calls[0]["limit"] is None
    finally:
        db.close()


def test_timeline_fetch_posts_handles_standalone_story_without_timeline_page_info(monkeypatch):
    creation_time = int(datetime.utcnow().timestamp())
    story = {
        "__typename": "Story",
        "post_id": "standalone-story-1",
        "creation_time": creation_time,
        "comet_sections": {
            "content": {
                "story": {
                    "message": {"text": "Standalone story"},
                    "actors": [{"name": "Standalone Page", "__typename": "Page"}],
                }
            }
        },
        "feedback": {"id": "feedback-standalone-story-1"},
        "attachments": [],
    }

    class FakeResponse:
        status_code = 200
        text = json.dumps({"data": {"node": story}})

    monkeypatch.setattr(post_scraper, "PAGE_NAME", None)
    monkeypatch.setattr(post_scraper, "WRITE_DEBUG_FILES", False)
    monkeypatch.setattr(post_scraper, "retry_request", lambda *args, **kwargs: FakeResponse())

    posts = post_scraper.fetch_posts(
        limit=None,
        last_24_hours_only=True,
        download_media=False,
        skip_existing_posts=False,
    )

    assert [post["post_id"] for post in posts] == ["standalone-story-1"]


def test_timeline_fetch_posts_includes_video_stories_by_default(monkeypatch):
    creation_time = int(datetime.utcnow().timestamp())
    story = {
        "__typename": "Story",
        "post_id": "video-story-1",
        "creation_time": creation_time,
        "comet_sections": {
            "content": {
                "story": {
                    "message": {"text": "Video story"},
                    "actors": [{"name": "Video Page", "__typename": "Page"}],
                }
            }
        },
        "feedback": {"id": "feedback-video-story-1"},
        "attachments": [
            {
                "styles": {
                    "attachment": {
                        "media": {
                            "__typename": "Video",
                            "playable_url": "https://example.test/video.mp4",
                        }
                    }
                }
            }
        ],
    }

    class FakeResponse:
        status_code = 200
        text = json.dumps({"data": {"node": story}})

    monkeypatch.setattr(post_scraper, "PAGE_NAME", None)
    monkeypatch.setattr(post_scraper, "WRITE_DEBUG_FILES", False)
    monkeypatch.setattr(post_scraper, "retry_request", lambda *args, **kwargs: FakeResponse())

    posts = post_scraper.fetch_posts(
        limit=None,
        last_24_hours_only=True,
        download_media=False,
        skip_existing_posts=False,
    )

    assert [post["post_id"] for post in posts] == ["video-story-1"]
    assert posts[0]["media"] == [{"type": "video", "url": "https://example.test/video.mp4"}]


def test_refresh_recent_post_metrics_updates_existing_group_post(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="dave", email="dave@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-123",
            facebook_url="https://www.facebook.com/groups/group-123",
            source_name="Tracked Group",
        )
        post = PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="post-1",
            facebook_url="https://facebook.com/posts/post-1",
            posted_at=datetime.utcnow(),
            content="Old content",
            likes_count=1,
            shares_count=1,
            comments_count=1,
        )

        monkeypatch.setattr(
            "backend.scraper.facebook_service.group_scraper.fetch_posts",
            lambda limit=20: [
                {
                    "post_id": "post-1",
                    "group_link": "https://www.facebook.com/groups/group-123/",
                    "permalink": "https://facebook.com/posts/post-1",
                    "message": "Old content",
                    "posted_at": datetime.utcnow(),
                    "reaction_count": 15,
                    "share_count": 4,
                    "comment_count": 9,
                    "group_name": "Tracked Group",
                    "photos": [],
                    "videos": [],
                }
            ],
        )

        result = FacebookScraperService.refresh_recent_post_metrics(db, source, limit=5)
        refreshed_post = PostCRUD.get_by_id(db, post.id)

        assert result["updated"] == 1
        assert refreshed_post.current_likes == 15
        assert refreshed_post.current_shares == 4
        assert refreshed_post.current_comments == 9
        assert len(refreshed_post.metrics_history) == 1
        assert refreshed_post.last_metric_update is not None
    finally:
        db.close()


def test_scrape_group_source_saves_only_top_level_comments_when_replies_enabled(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="erin", email="erin@example.com", password="secret123")
        FacebookSessionCRUD.upsert_active_for_user(
            db=db,
            user_id=user.id,
            fb_cookies='{"c_user":"123"}',
            fb_dtsg="token",
        )
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-456",
            facebook_url="https://www.facebook.com/groups/group-456",
            source_name="Comment Group",
            include_comments=True,
            include_replies=True,
        )

        monkeypatch.setattr(
            "backend.scraper.facebook_service.group_scraper.fetch_posts",
            lambda limit=20: [
                {
                    "post_id": "post-comments-1",
                    "group_link": "https://www.facebook.com/groups/group-456/",
                    "permalink": "https://facebook.com/posts/post-comments-1",
                    "message": "With comments",
                    "posted_at": datetime(2026, 1, 3, 9, 0, 0),
                    "reaction_count": 3,
                    "share_count": 0,
                    "comment_count": 1,
                    "group_name": "Comment Group",
                    "photos": [],
                    "videos": [],
                }
            ],
        )
        monkeypatch.setattr(
            "backend.scraper.facebook_service.comment_scraper.fetch_comments",
            lambda feedback_id, cookies=None: (
                [
                    {
                        "comment_id": "c1",
                        "author_id": "u1",
                        "author_name": "User One",
                        "author_url": "https://facebook.com/u1",
                        "text": "Top level",
                        "reaction_count": "2K",
                        "reply_count": "1.2K",
                        "_feedback_id": "feedback-c1",
                        "_expansion_token": "token-c1",
                    }
                ],
                {"is_active": True},
            ),
        )
        fetch_replies_called = {"called": False}

        def _unexpected_fetch_replies(*args, **kwargs):
            fetch_replies_called["called"] = True
            return []

        monkeypatch.setattr(
            "backend.scraper.facebook_service.comment_scraper.fetch_replies",
            _unexpected_fetch_replies,
        )

        FacebookScraperService.scrape_source(db, source.id, limit=5)
        post = PostCRUD.get_by_source_and_facebook_post_id(db, source.id, "post-comments-1")
        top_comment = CommentCRUD.get_by_facebook_id(db, "c1")

        assert post is not None
        assert top_comment is not None
        assert top_comment.post_id == post.id
        assert top_comment.depth_level == 0
        assert top_comment.likes_count == 2000
        assert top_comment.reply_count == 1200
        assert fetch_replies_called["called"] is False
        assert CommentCRUD.get_replies(db, "c1") == []
        assert CommentCRUD.count_by_post(db, post.id) == 1
    finally:
        db.close()


def test_refresh_recent_post_metrics_syncs_comments_when_enabled(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="frank", email="frank@example.com", password="secret123")
        FacebookSessionCRUD.upsert_active_for_user(
            db=db,
            user_id=user.id,
            fb_cookies='{"c_user":"123"}',
            fb_dtsg="token",
        )
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-789",
            facebook_url="https://www.facebook.com/groups/group-789",
            source_name="Refresh Group",
            include_comments=True,
            include_replies=False,
        )
        post = PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="refresh-post-1",
            facebook_url="https://facebook.com/posts/refresh-post-1",
            posted_at=datetime.utcnow(),
            content="Existing post",
            likes_count=1,
            shares_count=0,
            comments_count=0,
        )

        monkeypatch.setattr(
            "backend.scraper.facebook_service.group_scraper.fetch_posts",
            lambda limit=20: [
                {
                    "post_id": "refresh-post-1",
                    "group_link": "https://www.facebook.com/groups/group-789/",
                    "permalink": "https://facebook.com/posts/refresh-post-1",
                    "message": "Existing post",
                    "posted_at": datetime.utcnow(),
                    "reaction_count": 4,
                    "share_count": 1,
                    "comment_count": 2,
                    "group_name": "Refresh Group",
                    "photos": [],
                    "videos": [],
                }
            ],
        )
        monkeypatch.setattr(
            "backend.scraper.facebook_service.comment_scraper.fetch_comments",
            lambda feedback_id, cookies=None: (
                [
                    {
                        "comment_id": "refresh-c1",
                        "author_id": "u3",
                        "author_name": "User Three",
                        "author_url": "https://facebook.com/u3",
                        "text": "Fresh comment",
                        "reaction_count": 5,
                        "reply_count": 0,
                        "_feedback_id": "feedback-refresh-c1",
                        "_expansion_token": "token-refresh-c1",
                    }
                ],
                {"is_active": True},
            ),
        )

        result = FacebookScraperService.refresh_recent_post_metrics(db, source, limit=5)
        refreshed_post = PostCRUD.get_by_id(db, post.id)
        synced_comment = CommentCRUD.get_by_facebook_id(db, "refresh-c1")

        assert result["updated"] == 1
        assert refreshed_post.current_likes == 4
        assert refreshed_post.current_comments == 2
        assert synced_comment is not None
        assert synced_comment.post_id == post.id
        assert synced_comment.depth_level == 0
    finally:
        db.close()


def test_periodic_scrape_new_posts_accepts_string_posted_at(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="greg", email="greg@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-999",
            facebook_url="https://www.facebook.com/groups/group-999",
            source_name="Group 999",
        )

        monkeypatch.setattr(
            "backend.scraper.facebook_service.group_scraper.fetch_posts",
            lambda limit=20: [
                {
                    "post_id": "string-datetime-post",
                    "group_link": "https://www.facebook.com/groups/group-999/",
                    "permalink": "https://facebook.com/posts/string-datetime-post",
                    "message": "Post with string datetime",
                    "posted_at": (datetime.utcnow() - timedelta(hours=1)).isoformat() + "Z",
                    "reaction_count": 3,
                    "share_count": 1,
                    "comment_count": 2,
                    "group_name": "Group 999",
                    "photos": [],
                    "videos": [],
                }
            ],
        )

        import asyncio
        asyncio.run(periodic_scrape_new_posts())

        post = PostCRUD.get_by_source_and_facebook_post_id(db, source.id, "string-datetime-post")
        assert post is not None
        assert isinstance(post.posted_at, datetime)
    finally:
        db.close()


def test_scrape_group_source_handles_none_attachment_media_without_crash(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="none-media", email="none-media@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-none-media",
            facebook_url="https://www.facebook.com/groups/group-none-media",
            source_name="None Media Group",
        )

        monkeypatch.setattr(
            "backend.scraper.facebook_service.group_scraper.fetch_posts",
            lambda limit=20: [
                {
                    "post_id": "none-media-post",
                    "group_link": "https://www.facebook.com/groups/group-none-media/",
                    "permalink": "https://facebook.com/posts/none-media-post",
                    "message": "Post with none media",
                    "posted_at": "2026-01-03T09:00:00Z",
                    "reaction_count": 3,
                    "share_count": 1,
                    "comment_count": 2,
                    "group_name": "None Media Group",
                    "photos": [],
                    "videos": [],
                    "attachments": [
                        {"media": None, "styles": {"attachment": {"media": None}}},
                    ],
                }
            ],
        )

        result = FacebookScraperService.scrape_source(db, source.id, limit=5)
        post = PostCRUD.get_by_source_and_facebook_post_id(db, source.id, "none-media-post")
        assert result.created_posts == 1
        assert post is not None
    finally:
        db.close()


def test_get_latest_posted_at_by_source_returns_latest_and_none_for_empty():
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="latest-ts", email="latest-ts@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-latest-ts",
            facebook_url="https://www.facebook.com/groups/group-latest-ts",
            source_name="Group Latest TS",
        )

        assert PostCRUD.get_latest_posted_at_by_source(db, source.id) is None

        PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="latest-post-1",
            facebook_url="https://facebook.com/posts/latest-post-1",
            posted_at=datetime(2026, 1, 1, 9, 0, 0),
            content="First",
        )
        PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="latest-post-2",
            facebook_url="https://facebook.com/posts/latest-post-2",
            posted_at=datetime(2026, 1, 1, 10, 0, 0),
            content="Second",
        )

        latest = PostCRUD.get_latest_posted_at_by_source(db, source.id)
        assert latest == datetime(2026, 1, 1, 10, 0, 0)
    finally:
        db.close()


def test_get_recent_posts_uses_posted_at_window_and_excludes_deleted_posts():
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="recent-created-at", email="recent-created-at@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-recent-created-at",
            facebook_url="https://www.facebook.com/groups/group-recent-created-at",
            source_name="Recent Created At Group",
        )

        recent_created_old_posted = PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="recent-created-old-posted",
            facebook_url="https://facebook.com/posts/recent-created-old-posted",
            posted_at=datetime(2020, 1, 1, 0, 0, 0),
            content="Recent created, old posted",
        )
        old_created_new_posted = PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="old-created-new-posted",
            facebook_url="https://facebook.com/posts/old-created-new-posted",
            posted_at=datetime.utcnow(),
            content="Old created, new posted",
        )

        old_created_new_posted.created_at = datetime.utcnow() - timedelta(hours=30)
        db.commit()
        db.refresh(old_created_new_posted)
        deleted_post = PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="deleted-recent",
            facebook_url="https://facebook.com/posts/deleted-recent",
            posted_at=datetime.utcnow(),
            content="Deleted recent",
        )
        PostCRUD.update(db, deleted_post.id, is_deleted=True, is_tracked=True)

        recent_posts = PostCRUD.get_recent_posts(db, hours=24, limit=50)
        recent_ids = {post.id for post in recent_posts}

        assert recent_created_old_posted.id not in recent_ids
        assert old_created_new_posted.id in recent_ids
        assert deleted_post.id not in recent_ids
    finally:
        db.close()


def test_get_recent_posts_prioritizes_stale_metric_updates():
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="recent-stale", email="recent-stale@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-recent-stale",
            facebook_url="https://www.facebook.com/groups/group-recent-stale",
            source_name="Recent Stale Group",
        )

        now = datetime.utcnow()
        updated_newer = PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="updated-newer",
            facebook_url="https://facebook.com/posts/updated-newer",
            posted_at=now,
            content="Updated newer",
            last_metric_update=now - timedelta(minutes=5),
        )
        never_updated = PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="never-updated",
            facebook_url="https://facebook.com/posts/never-updated",
            posted_at=now - timedelta(hours=1),
            content="Never updated",
        )
        stale_updated = PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="stale-updated",
            facebook_url="https://facebook.com/posts/stale-updated",
            posted_at=now - timedelta(hours=2),
            content="Stale updated",
            last_metric_update=now - timedelta(hours=3),
        )

        recent_posts = PostCRUD.get_recent_posts(db, hours=24, limit=2)

        assert [post.id for post in recent_posts] == [never_updated.id, stale_updated.id]
        assert updated_newer.id not in {post.id for post in recent_posts}
    finally:
        db.close()


def test_get_due_for_scraping_prioritizes_never_scraped_then_oldest_due():
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="due-source-order", email="due-source-order@example.com", password="secret123")
        now = datetime.utcnow()

        newer_due = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="newer-due-source",
            facebook_url="https://www.facebook.com/groups/newer-due-source",
            source_name="Newer Due Source",
        )
        older_due = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="older-due-source",
            facebook_url="https://www.facebook.com/groups/older-due-source",
            source_name="Older Due Source",
        )
        never_scraped = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="never-scraped-source",
            facebook_url="https://www.facebook.com/groups/never-scraped-source",
            source_name="Never Scraped Source",
        )
        future_source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="future-source",
            facebook_url="https://www.facebook.com/groups/future-source",
            source_name="Future Source",
        )

        newer_due.next_scrape = now - timedelta(minutes=10)
        older_due.next_scrape = now - timedelta(hours=2)
        future_source.next_scrape = now + timedelta(hours=1)
        db.commit()

        due_sources = SourceCRUD.get_due_for_scraping(db, limit=3)

        assert [source.id for source in due_sources] == [never_scraped.id, older_due.id, newer_due.id]
        assert future_source.id not in {source.id for source in due_sources}
    finally:
        db.close()


def test_scrape_source_min_posted_at_filters_old_and_equal_posts(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="cutoff-user", email="cutoff-user@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-cutoff",
            facebook_url="https://www.facebook.com/groups/group-cutoff",
            source_name="Cutoff Group",
        )

        cutoff = datetime(2026, 1, 3, 9, 0, 0)
        monkeypatch.setattr(
            "backend.scraper.facebook_service.group_scraper.fetch_posts",
            lambda limit=20: [
                {
                    "post_id": "old-post",
                    "group_link": "https://www.facebook.com/groups/group-cutoff/",
                    "permalink": "https://facebook.com/posts/old-post",
                    "message": "Old",
                    "posted_at": datetime(2026, 1, 3, 8, 59, 0),
                    "reaction_count": 1,
                    "share_count": 0,
                    "comment_count": 0,
                    "group_name": "Cutoff Group",
                    "photos": [],
                    "videos": [],
                },
                {
                    "post_id": "equal-post",
                    "group_link": "https://www.facebook.com/groups/group-cutoff/",
                    "permalink": "https://facebook.com/posts/equal-post",
                    "message": "Equal",
                    "posted_at": cutoff,
                    "reaction_count": 2,
                    "share_count": 0,
                    "comment_count": 0,
                    "group_name": "Cutoff Group",
                    "photos": [],
                    "videos": [],
                },
                {
                    "post_id": "new-post",
                    "group_link": "https://www.facebook.com/groups/group-cutoff/",
                    "permalink": "https://facebook.com/posts/new-post",
                    "message": "New",
                    "posted_at": datetime(2026, 1, 3, 9, 1, 0),
                    "reaction_count": 3,
                    "share_count": 1,
                    "comment_count": 1,
                    "group_name": "Cutoff Group",
                    "photos": [],
                    "videos": [],
                },
            ],
        )

        result = FacebookScraperService.scrape_source(
            db,
            source.id,
            limit=10,
            min_posted_at=cutoff,
        )

        assert result.total_fetched == 3
        assert result.created_posts == 1
        assert PostCRUD.get_by_source_and_facebook_post_id(db, source.id, "old-post") is None
        assert PostCRUD.get_by_source_and_facebook_post_id(db, source.id, "equal-post") is None
        created = PostCRUD.get_by_source_and_facebook_post_id(db, source.id, "new-post")
        assert created is not None
        assert len(created.metrics_history) == 1
    finally:
        db.close()


def test_update_recent_post_metrics_only_refreshes_sources_with_recent_posted_posts(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="metrics-created-at", email="metrics-created-at@example.com", password="secret123")
        recent_source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-metrics-recent",
            facebook_url="https://www.facebook.com/groups/group-metrics-recent",
            source_name="Metrics Recent Group",
        )
        old_source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-metrics-old",
            facebook_url="https://www.facebook.com/groups/group-metrics-old",
            source_name="Metrics Old Group",
        )

        recent_post = PostCRUD.create(
            db,
            source_id=recent_source.id,
            facebook_post_id="metrics-recent-post",
            facebook_url="https://facebook.com/posts/metrics-recent-post",
            posted_at=datetime.utcnow(),
            content="Recent posted",
            next_metric_update=datetime.utcnow() - timedelta(minutes=1),
        )
        old_post = PostCRUD.create(
            db,
            source_id=old_source.id,
            facebook_post_id="metrics-old-post",
            facebook_url="https://facebook.com/posts/metrics-old-post",
            posted_at=datetime.utcnow() - timedelta(hours=30),
            content="Old posted",
        )
        db.commit()
        db.refresh(recent_post)
        db.refresh(old_post)
        recent_source_id = recent_source.id
        old_source_id = old_source.id
    finally:
        db.close()

    calls = []

    def fake_refresh_target_post_metrics(
        _db,
        source,
        target_post_ids,
        max_pages=20,
        stop_when_all_found=True,
        last_24_hours_only=True,
        download_media=False,
        job_id=None,
    ):
        calls.append((source.id, target_post_ids))
        return {
            "fetched": 1,
            "updated": 1,
            "skipped": 0,
            "matched_target_count": len(target_post_ids),
            "target_posts_count": len(target_post_ids),
            "pages_scanned": 1,
            "stop_reason": "all_targets_found",
        }

    monkeypatch.setattr(
        "backend.scheduler.periodic_tasks.FacebookScraperService.refresh_target_post_metrics",
        fake_refresh_target_post_metrics,
    )

    import asyncio
    asyncio.run(update_recent_post_metrics())

    called_source_ids = {source_id for source_id, _target_post_ids in calls}
    assert recent_source_id in called_source_ids
    assert old_source_id not in called_source_ids


def test_update_recent_post_metrics_untracks_posts_older_than_24h():
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="metrics-untrack-old", email="metrics-untrack-old@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-metrics-untrack-old",
            facebook_url="https://www.facebook.com/groups/group-metrics-untrack-old",
            source_name="Metrics Untrack Old Group",
        )
        old_post = PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="metrics-old-untracked",
            facebook_url="https://facebook.com/posts/metrics-old-untracked",
            posted_at=datetime.utcnow() - timedelta(hours=30),
            content="Old tracked post",
        )
        old_post_id = old_post.id
    finally:
        db.close()

    import asyncio
    asyncio.run(update_recent_post_metrics())

    verify_db = SessionLocal()
    try:
        refreshed_old_post = PostCRUD.get_by_id(verify_db, old_post_id)
        assert refreshed_old_post.is_tracked is False
        assert refreshed_old_post.is_deleted is False
    finally:
        verify_db.close()


def test_update_recent_post_metrics_records_active_session_id(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(
            db,
            username="metrics-session",
            email="metrics-session@example.com",
            password="secret123",
        )
        session = FacebookSessionCRUD.upsert_active_for_user(
            db=db,
            user_id=user.id,
            fb_cookies='{"c_user":"metrics-session"}',
            fb_dtsg="metrics-token",
            fb_user_agent="metrics-agent",
        )
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-metrics-session",
            facebook_url="https://www.facebook.com/groups/group-metrics-session",
            source_name="Metrics Session Group",
        )
        PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="metrics-session-post",
            facebook_url="https://facebook.com/posts/metrics-session-post",
            posted_at=datetime.utcnow(),
            content="Session metric",
            next_metric_update=datetime.utcnow() - timedelta(minutes=1),
        )
        source_id = source.id
        session_id = session.id
    finally:
        db.close()

    def fake_refresh_target_post_metrics(
        _db,
        _source,
        target_post_ids,
        max_pages=20,
        stop_when_all_found=True,
        last_24_hours_only=True,
        download_media=False,
        job_id=None,
    ):
        return {
            "fetched": 1,
            "updated": 1,
            "skipped": 0,
            "matched_target_count": len(target_post_ids),
            "target_posts_count": len(target_post_ids),
            "pages_scanned": 1,
            "stop_reason": "all_targets_found",
            "updated_post_refs": target_post_ids,
        }

    monkeypatch.setattr(
        "backend.scheduler.periodic_tasks.FacebookScraperService.refresh_target_post_metrics",
        fake_refresh_target_post_metrics,
    )

    import asyncio
    asyncio.run(update_recent_post_metrics())

    verify_db = SessionLocal()
    try:
        job = (
            verify_db.query(ScrapeJob)
            .filter(ScrapeJob.job_type == "post_metric", ScrapeJob.source_id == source_id)
            .order_by(ScrapeJob.id.desc())
            .first()
        )
        assert job is not None
        assert job.session_id == session_id
    finally:
        verify_db.close()


def test_periodic_scrape_new_posts_uses_latest_db_post_as_cutoff(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="periodic-cutoff", email="periodic-cutoff@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-periodic-cutoff",
            facebook_url="https://www.facebook.com/groups/group-periodic-cutoff",
            source_name="Periodic Cutoff Group",
        )
        latest_seen = datetime.utcnow() - timedelta(hours=2)
        PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="seed-post",
            facebook_url="https://facebook.com/posts/seed-post",
            posted_at=latest_seen,
            content="Seed",
        )
        source_id = source.id
    finally:
        db.close()

    monkeypatch.setattr(
        "backend.scraper.facebook_service.group_scraper.fetch_posts",
        lambda limit=20: [
            {
                "post_id": "periodic-old",
                "group_link": "https://www.facebook.com/groups/group-periodic-cutoff/",
                "permalink": "https://facebook.com/posts/periodic-old",
                "message": "Old periodic",
                "posted_at": latest_seen - timedelta(minutes=1),
                "reaction_count": 1,
                "share_count": 0,
                "comment_count": 0,
                "group_name": "Periodic Cutoff Group",
                "photos": [],
                "videos": [],
            },
            {
                "post_id": "periodic-new",
                "group_link": "https://www.facebook.com/groups/group-periodic-cutoff/",
                "permalink": "https://facebook.com/posts/periodic-new",
                "message": "New periodic",
                "posted_at": latest_seen + timedelta(minutes=1),
                "reaction_count": 5,
                "share_count": 1,
                "comment_count": 2,
                "group_name": "Periodic Cutoff Group",
                "photos": [],
                "videos": [],
            },
        ],
    )
    monkeypatch.setattr(
        "backend.scraper.facebook_service.comment_scraper.fetch_comments",
        lambda feedback_id, cookies=None: ([], {"is_active": True}),
    )

    import asyncio
    asyncio.run(periodic_scrape_new_posts())

    verify_db = SessionLocal()
    try:
        assert PostCRUD.get_by_source_and_facebook_post_id(verify_db, source_id, "periodic-old") is None
        assert PostCRUD.get_by_source_and_facebook_post_id(verify_db, source_id, "periodic-new") is not None
    finally:
        verify_db.close()


def test_periodic_scrape_new_posts_logs_source_name_and_summary(monkeypatch, caplog):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="log-periodic", email="log-periodic@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-log-periodic",
            facebook_url="https://www.facebook.com/groups/group-log-periodic",
            source_name="Log Periodic Group",
        )
    finally:
        db.close()

    monkeypatch.setattr(
        "backend.scraper.facebook_service.group_scraper.fetch_posts",
        lambda limit=20: [
            {
                "post_id": "log-periodic-new",
                "group_link": "https://www.facebook.com/groups/group-log-periodic/",
                "permalink": "https://facebook.com/posts/log-periodic-new",
                "message": "New periodic",
                "posted_at": datetime.utcnow() - timedelta(hours=1),
                "reaction_count": 5,
                "share_count": 1,
                "comment_count": 2,
                "group_name": "Log Periodic Group",
                "photos": [],
                "videos": [],
            },
        ],
    )
    monkeypatch.setattr(
        "backend.scraper.facebook_service.comment_scraper.fetch_comments",
        lambda feedback_id, cookies=None: ([], {"is_active": True}),
    )

    import asyncio
    with caplog.at_level("INFO", logger="facebook_scraper"):
        asyncio.run(periodic_scrape_new_posts())

    logs = caplog.text
    assert "Bắt đầu scrape source" in logs
    assert f"Log Periodic Group (id={source.id})" in logs
    assert "Kết thúc scrape source" in logs
    assert "created_posts=1" in logs
    assert "Kết thúc periodic_scrape_new_posts" in logs
    assert "total_new_posts_created=1" in logs


def test_scrape_source_combines_due_metric_targets_with_new_post_discovery(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="combined-scan", email="combined-scan@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-combined-scan",
            facebook_url="https://www.facebook.com/groups/group-combined-scan",
            source_name="Combined Scan Group",
        )
        now = datetime.utcnow()
        latest_seen = now - timedelta(minutes=10)
        PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="latest-seen",
            facebook_url="https://facebook.com/posts/latest-seen",
            posted_at=latest_seen,
        )
        target = PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="due-target",
            facebook_url="https://facebook.com/posts/due-target",
            posted_at=now - timedelta(hours=1),
            next_metric_update=now - timedelta(minutes=1),
        )
        unrelated = PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="old-not-due",
            facebook_url="https://facebook.com/posts/old-not-due",
            posted_at=now - timedelta(minutes=40),
            next_metric_update=now + timedelta(hours=1),
        )
        PostMetricCRUD.create(db, target.id, likes=1, shares=0, comments=0)
        PostMetricCRUD.create(db, unrelated.id, likes=1, shares=0, comments=0)

        calls = []

        def fake_fetch_posts(limit=20, **kwargs):
            calls.append({"limit": limit, **kwargs})
            return [
                {
                    "post_id": "brand-new",
                    "permalink": "https://facebook.com/posts/brand-new",
                    "message": "New",
                    "posted_at": now,
                    "reaction_count": 1,
                    "share_count": 0,
                    "comment_count": 0,
                    "photos": [],
                    "videos": [],
                },
                {
                    "post_id": "due-target",
                    "permalink": "https://facebook.com/posts/due-target",
                    "message": "Due",
                    "posted_at": now - timedelta(hours=1),
                    "reaction_count": 10,
                    "share_count": 0,
                    "comment_count": 0,
                    "photos": [],
                    "videos": [],
                },
                {
                    "post_id": "old-not-due",
                    "permalink": "https://facebook.com/posts/old-not-due",
                    "message": "Not due",
                    "posted_at": now - timedelta(minutes=40),
                    "reaction_count": 20,
                    "share_count": 0,
                    "comment_count": 0,
                    "photos": [],
                    "videos": [],
                },
            ]

        monkeypatch.setattr("backend.scraper.facebook_service.group_scraper.fetch_posts", fake_fetch_posts)
        monkeypatch.setattr(
            "backend.scraper.facebook_service.comment_scraper.fetch_comments",
            lambda feedback_id, cookies=None: ([], {"is_active": True}),
        )

        result = FacebookScraperService.scrape_source(
            db,
            source.id,
            last_24_hours_only=True,
            min_posted_at=latest_seen,
            metric_target_post_ids=["due-target"],
        )

        assert calls[0]["min_posted_at"] is None
        assert result.created_posts == 1
        assert result.matched_metric_target_ids == ["due-target"]
        assert len(PostMetricCRUD.get_by_post(db, target.id)) == 2
        assert len(PostMetricCRUD.get_by_post(db, unrelated.id)) == 1
        assert PostCRUD.get_by_source_and_facebook_post_id(db, source.id, "brand-new") is not None
    finally:
        db.close()


def test_update_recent_post_metrics_logs_updated_post_list_capped(monkeypatch, caplog):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="log-metrics", email="log-metrics@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-log-metrics",
            facebook_url="https://www.facebook.com/groups/group-log-metrics",
            source_name="Log Metrics Group",
        )
        source_id = source.id
        PostCRUD.create(
            db,
            source_id=source_id,
            facebook_post_id="seed-metrics-post",
            facebook_url="https://facebook.com/posts/seed-metrics-post",
            posted_at=datetime.utcnow(),
            content="Seed",
            next_metric_update=datetime.utcnow() - timedelta(minutes=1),
        )
    finally:
        db.close()

    def fake_refresh_target_post_metrics(
        _db,
        _source,
        target_post_ids,
        max_pages=20,
        stop_when_all_found=True,
        last_24_hours_only=True,
        download_media=False,
        job_id=None,
    ):
        refs = [f"post-{idx}" for idx in range(1, 13)]
        return {
            "fetched": 20,
            "updated": 12,
            "skipped": 8,
            "matched_target_count": len(target_post_ids),
            "target_posts_count": len(target_post_ids),
            "pages_scanned": 7,
            "stop_reason": "all_targets_found",
            "updated_post_refs": refs,
        }

    monkeypatch.setattr(
        "backend.scheduler.periodic_tasks.FacebookScraperService.refresh_target_post_metrics",
        fake_refresh_target_post_metrics,
    )

    import asyncio
    with caplog.at_level("INFO", logger="facebook_scraper"):
        asyncio.run(update_recent_post_metrics())

    logs = caplog.text
    assert "stop_reason=all_targets_found" in logs
    assert "pages_scanned=7" in logs
    assert "fetch_to_update_ratio=1.667" in logs
    assert "Bắt đầu cập nhật metric source" in logs
    assert f"Log Metrics Group (id={source_id})" in logs
    assert "Kết thúc cập nhật metric source" in logs
    assert "updated_posts=[post-1, post-2, post-3, post-4, post-5, post-6, post-7, post-8, post-9, post-10 ... +2 more]" in logs
    assert "Kết thúc update_recent_post_metrics" in logs
    assert "updated_posts_total=12" in logs


def test_scrape_source_last_24_hours_uses_unbounded_recent_fetch(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="henry", email="henry@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-24h",
            facebook_url="https://www.facebook.com/groups/group-24h",
            source_name="Group 24h",
        )

        calls = []

        def fake_fetch_posts(limit=10, **kwargs):
            calls.append({"limit": limit, **kwargs})
            return [
                {
                    "post_id": "recent-1",
                    "group_link": "https://www.facebook.com/groups/group-24h/",
                    "permalink": "https://facebook.com/posts/recent-1",
                    "message": "Recent post",
                    "posted_at": datetime.utcnow() - timedelta(hours=1),
                    "reaction_count": 1,
                    "share_count": 0,
                    "comment_count": 0,
                    "group_name": "Group 24h",
                    "photos": [],
                    "videos": [],
                }
            ]

        monkeypatch.setattr(
            "backend.scraper.facebook_service.group_scraper.fetch_posts",
            fake_fetch_posts,
        )

        result = FacebookScraperService.scrape_source(
            db,
            source.id,
            last_24_hours_only=True,
        )
        created_post = PostCRUD.get_by_source_and_facebook_post_id(db, source.id, "recent-1")

        assert result.total_fetched == 1
        assert created_post is not None
        assert created_post.current_likes == 1
        assert len(created_post.metrics_history) == 1
        assert created_post.metrics_history[0].likes_count == 1
        assert calls
        assert calls[0]["limit"] is None
        assert calls[0]["last_24_hours_only"] is True
    finally:
        db.close()


def test_scrape_source_passes_download_media_flag_to_group_fetch_posts(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="group-media-flag", email="group-media-flag@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-media-flag",
            facebook_url="https://www.facebook.com/groups/group-media-flag",
            source_name="Group Media Flag",
        )

        calls = []

        def fake_fetch_posts(limit=10, **kwargs):
            calls.append({"limit": limit, **kwargs})
            return []

        monkeypatch.setattr("backend.scraper.facebook_service.group_scraper.fetch_posts", fake_fetch_posts)
        monkeypatch.setattr("backend.scraper.facebook_service.settings.SCRAPER_DOWNLOAD_MEDIA", False)

        FacebookScraperService.scrape_source(db, source.id, limit=5)

        assert calls
        assert calls[0]["download_media"] is False
    finally:
        db.close()


def test_scrape_group_source_persists_empty_page_diagnostic_pipeline_log(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="empty-diagnostic", email="empty-diagnostic@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="empty-diagnostic-group",
            facebook_url="https://www.facebook.com/groups/empty-diagnostic-group",
            source_name="Empty Diagnostic Group",
        )
        job = PipelineJobCRUD.create_job(db, job_type="scraper_job", source_id=source.id, status="running")

        def fake_fetch_posts(limit=10, **kwargs):
            kwargs["on_page_diagnostic"](
                {
                    "page_num": 1,
                    "posts_found": 0,
                    "has_next_page": False,
                    "next_cursor": False,
                    "response_summary": {
                        "total_blocks": 1,
                        "group_nodes": 0,
                        "group_feed_edges": 0,
                        "timeline_edges": 0,
                        "story_nodes": 0,
                        "page_info_blocks": 0,
                    },
                    "stop_reason": "no_next_page",
                }
            )
            return []

        monkeypatch.setattr("backend.scraper.facebook_service.group_scraper.fetch_posts", fake_fetch_posts)

        result = FacebookScraperService.scrape_source(db, source.id, limit=5, job_id=job.id)
        log = db.query(ScraperLog).filter(ScraperLog.source_id == source.id).one()

        assert result.total_fetched == 0
        assert log.job_id == job.id
        assert log.log_level == "WARNING"
        assert "Trang 1 không chứa post trong response." in log.message
        assert "blocks=1, group_nodes=0, group_feed_edges=0" in log.message
        assert "reason=no_next_page" in log.message
    finally:
        db.close()


def test_scrape_group_source_does_not_persist_filtered_old_posts_diagnostic(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="old-diagnostic", email="old-diagnostic@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="old-diagnostic-group",
            facebook_url="https://www.facebook.com/groups/old-diagnostic-group",
            source_name="Old Diagnostic Group",
        )
        job = PipelineJobCRUD.create_job(db, job_type="scraper_job", source_id=source.id, status="running")

        def fake_fetch_posts(limit=10, **kwargs):
            kwargs["on_page_diagnostic"](
                {
                    "page_num": 27,
                    "posts_found": 0,
                    "received_posts": 3,
                    "filtered_by_latest_cutoff": 3,
                    "consecutive_old_count": 3,
                    "consecutive_old_limit": 3,
                    "has_next_page": False,
                    "next_cursor": False,
                    "response_summary": {
                        "total_blocks": 4,
                        "group_nodes": 1,
                        "group_feed_edges": 1,
                        "timeline_edges": 0,
                        "story_nodes": 2,
                        "page_info_blocks": 1,
                    },
                    "stop_reason": "consecutive_old",
                }
            )
            return []

        monkeypatch.setattr("backend.scraper.facebook_service.group_scraper.fetch_posts", fake_fetch_posts)

        FacebookScraperService.scrape_source(db, source.id, limit=5, job_id=job.id)

        assert db.query(ScraperLog).filter(ScraperLog.source_id == source.id).count() == 0
    finally:
        db.close()


def test_refresh_recent_post_metrics_passes_download_media_flag_to_timeline_fetch_posts(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="timeline-media-flag", email="timeline-media-flag@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="page",
            facebook_id="page-media-flag",
            facebook_url="https://www.facebook.com/page-media-flag",
            source_name="Page Media Flag",
        )

        calls = []

        def fake_fetch_posts(limit=10, **kwargs):
            calls.append({"limit": limit, **kwargs})
            return []

        monkeypatch.setattr("backend.scraper.facebook_service.timeline_scraper.fetch_posts", fake_fetch_posts)
        monkeypatch.setattr("backend.scraper.facebook_service.settings.SCRAPER_DOWNLOAD_MEDIA", False)

        FacebookScraperService.refresh_recent_post_metrics(db, source, limit=5)

        assert calls
        assert calls[0]["download_media"] is False
        assert calls[0]["skip_existing_posts"] is False
    finally:
        db.close()


def test_group_fetch_posts_emits_empty_no_next_page_diagnostic(monkeypatch):
    events = []

    class FakeResponse:
        text = json.dumps({"extensions": {"is_final": True}})

        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setattr(group_post_scraper_v2, "retry_request", lambda *args, **kwargs: FakeResponse())

    posts = group_post_scraper_v2.fetch_posts(
        limit=5,
        group_id="empty-group",
        cookies={},
        download_media=False,
        on_page_diagnostic=events.append,
    )

    assert posts == []
    assert len(events) == 1
    assert events[0]["posts_found"] == 0
    assert events[0]["has_next_page"] is False
    assert events[0]["next_cursor"] is False
    assert events[0]["stop_reason"] == "no_next_page"
    assert events[0]["response_summary"]["total_blocks"] == 1
    assert events[0]["response_summary"]["story_nodes"] == 0


def test_group_fetch_posts_reports_old_filtered_posts_without_empty_diagnostic(monkeypatch, capsys):
    events = []
    story_nodes = [
        {"__typename": "Story", "post_id": f"old-{index}", "posted_at": f"2026-05-25T06:1{index}:00+00:00"}
        for index in range(3)
    ]

    class FakeResponse:
        text = json.dumps(
            {
                "data": {
                    "node": {
                        "__typename": "Group",
                        "group_feed": {
                            "edges": [{"node": story} for story in story_nodes],
                            "page_info": {"has_next_page": True, "end_cursor": "unused"},
                        },
                    }
                }
            }
        )

        @staticmethod
        def raise_for_status():
            return None

    monkeypatch.setattr(group_post_scraper_v2, "retry_request", lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr(group_post_scraper_v2, "extract_posted_at", lambda node: node["posted_at"])

    posts = group_post_scraper_v2.fetch_posts(
        limit=5,
        group_id="old-group",
        cookies={},
        min_posted_at="2026-05-25T07:00:00+00:00",
        consecutive_old_limit=3,
        download_media=False,
        on_page_diagnostic=events.append,
    )
    output = capsys.readouterr().out

    assert posts == []
    assert events == []
    assert "Response chứa 3 post nhưng 0 post được giữ lại sau bộ lọc" in output
    assert "Chuẩn đoán response:" not in output
    assert "Đã gặp đủ số post cũ liên tiếp theo latest cutoff." in output


def test_refresh_target_post_metrics_only_updates_target_posts(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="target-metrics", email="target-metrics@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-target-metrics",
            facebook_url="https://www.facebook.com/groups/group-target-metrics",
            source_name="Target Metrics Group",
        )
        target_post = PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="target-1",
            facebook_url="https://facebook.com/posts/target-1",
            posted_at=datetime.utcnow(),
            content="Target",
            likes_count=1,
            shares_count=0,
            comments_count=0,
        )
        PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="other-1",
            facebook_url="https://facebook.com/posts/other-1",
            posted_at=datetime.utcnow(),
            content="Other",
            likes_count=2,
            shares_count=0,
            comments_count=0,
        )

        monkeypatch.setattr(
            "backend.scraper.facebook_service.group_scraper.fetch_posts",
            lambda limit=20, **kwargs: [
                {"post_id": "other-1", "permalink": "https://facebook.com/posts/other-1", "message": "Other", "posted_at": datetime.utcnow(), "reaction_count": 5, "share_count": 0, "comment_count": 0, "photos": [], "videos": []},
                {"post_id": "target-1", "permalink": "https://facebook.com/posts/target-1", "message": "Target", "posted_at": datetime.utcnow(), "reaction_count": 9, "share_count": 1, "comment_count": 2, "photos": [], "videos": []},
            ],
        )

        result = FacebookScraperService.refresh_target_post_metrics(
            db,
            source,
            target_post_ids=["target-1"],
            max_pages=10,
            stop_when_all_found=True,
            last_24_hours_only=True,
            download_media=False,
        )
        refreshed_target = PostCRUD.get_by_id(db, target_post.id)

        assert result["updated"] == 1
        assert result["matched_target_count"] == 1
        assert result["target_posts_count"] == 1
        assert refreshed_target.current_likes == 9
    finally:
        db.close()


def test_refresh_target_post_metrics_stops_when_all_targets_found(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="target-stop", email="target-stop@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-target-stop",
            facebook_url="https://www.facebook.com/groups/group-target-stop",
            source_name="Target Stop Group",
        )
        PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="target-stop-1",
            facebook_url="https://facebook.com/posts/target-stop-1",
            posted_at=datetime.utcnow(),
            content="Target stop",
            likes_count=0,
            shares_count=0,
            comments_count=0,
        )

        monkeypatch.setattr(
            "backend.scraper.facebook_service.group_scraper.fetch_posts",
            lambda limit=20, **kwargs: [
                {"post_id": "target-stop-1", "permalink": "https://facebook.com/posts/target-stop-1", "message": "Target stop", "posted_at": datetime.utcnow(), "reaction_count": 3, "share_count": 0, "comment_count": 0, "photos": [], "videos": []},
                {"post_id": "not-needed", "permalink": "https://facebook.com/posts/not-needed", "message": "not needed", "posted_at": datetime.utcnow(), "reaction_count": 99, "share_count": 0, "comment_count": 0, "photos": [], "videos": []},
            ],
        )

        result = FacebookScraperService.refresh_target_post_metrics(
            db,
            source,
            target_post_ids=["target-stop-1"],
            stop_when_all_found=True,
        )

        assert result["stop_reason"] == "all_targets_found"
        assert result["fetched"] == 1
    finally:
        db.close()


def test_refresh_target_post_metrics_returns_max_pages_stop_reason(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="target-max-pages", email="target-max-pages@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-target-max-pages",
            facebook_url="https://www.facebook.com/groups/group-target-max-pages",
            source_name="Target Max Pages Group",
        )
        late_post = PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="late-target",
            facebook_url="https://facebook.com/posts/late-target",
            posted_at=datetime.utcnow(),
            content="Late target",
            likes_count=0,
            shares_count=0,
            comments_count=0,
        )

        monkeypatch.setattr(
            "backend.scraper.facebook_service.group_scraper.fetch_posts",
            lambda limit=20, **kwargs: [{"post_id": f"other-{idx}", "permalink": f"https://facebook.com/posts/other-{idx}", "message": "Other", "posted_at": datetime.utcnow(), "reaction_count": idx, "share_count": 0, "comment_count": 0, "photos": [], "videos": []} for idx in range(1, 8)] + [{"post_id": "late-target", "permalink": "https://facebook.com/posts/late-target", "message": "Late target", "posted_at": datetime.utcnow(), "reaction_count": 7, "share_count": 0, "comment_count": 0, "photos": [], "videos": []}],
        )

        result = FacebookScraperService.refresh_target_post_metrics(
            db,
            source,
            target_post_ids=["late-target"],
            max_pages=2,
            stop_when_all_found=True,
        )
        refreshed_late_post = PostCRUD.get_by_id(db, late_post.id)

        assert result["stop_reason"] == "max_pages_reached"
        assert result["matched_target_count"] == 0
        assert result["deleted"] == 0
        assert result["deleted_post_refs"] == []
        assert refreshed_late_post.is_tracked is True
        assert refreshed_late_post.is_deleted is False
    finally:
        db.close()


def test_refresh_target_post_metrics_marks_recent_missing_targets_deleted(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="target-deleted", email="target-deleted@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-target-deleted",
            facebook_url="https://www.facebook.com/groups/group-target-deleted",
            source_name="Target Deleted Group",
        )
        missing_post = PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="missing-target",
            facebook_url="https://facebook.com/posts/missing-target",
            posted_at=datetime.utcnow(),
            content="Missing target",
            likes_count=3,
            shares_count=0,
            comments_count=0,
        )

        monkeypatch.setattr(
            "backend.scraper.facebook_service.group_scraper.fetch_posts",
            lambda limit=20, **kwargs: [
                {
                    "post_id": "other-visible",
                    "permalink": "https://facebook.com/posts/other-visible",
                    "message": "Other visible",
                    "posted_at": datetime.utcnow(),
                    "reaction_count": 1,
                    "share_count": 0,
                    "comment_count": 0,
                    "photos": [],
                    "videos": [],
                }
            ],
        )

        result = FacebookScraperService.refresh_target_post_metrics(
            db,
            source,
            target_post_ids=["missing-target"],
            max_pages=10,
            stop_when_all_found=True,
        )
        refreshed_missing_post = PostCRUD.get_by_id(db, missing_post.id)

        assert result["stop_reason"] == "source_exhausted"
        assert result["matched_target_count"] == 0
        assert result["deleted"] == 1
        assert result["deleted_post_refs"] == ["missing-target"]
        assert refreshed_missing_post.is_tracked is False
        assert refreshed_missing_post.is_deleted is True
    finally:
        db.close()


def test_refresh_target_post_metrics_untracks_old_missing_targets_without_deleting(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="target-old-missing", email="target-old-missing@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-target-old-missing",
            facebook_url="https://www.facebook.com/groups/group-target-old-missing",
            source_name="Target Old Missing Group",
        )
        old_missing_post = PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="old-missing-target",
            facebook_url="https://facebook.com/posts/old-missing-target",
            posted_at=datetime.utcnow() - timedelta(hours=30),
            content="Old missing target",
        )

        monkeypatch.setattr(
            "backend.scraper.facebook_service.group_scraper.fetch_posts",
            lambda limit=20, **kwargs: [
                {
                    "post_id": "other-visible",
                    "permalink": "https://facebook.com/posts/other-visible",
                    "message": "Other visible",
                    "posted_at": datetime.utcnow(),
                    "reaction_count": 1,
                    "share_count": 0,
                    "comment_count": 0,
                    "photos": [],
                    "videos": [],
                }
            ],
        )

        result = FacebookScraperService.refresh_target_post_metrics(
            db,
            source,
            target_post_ids=["old-missing-target"],
            max_pages=10,
            stop_when_all_found=True,
        )
        refreshed_old_post = PostCRUD.get_by_id(db, old_missing_post.id)

        assert result["deleted"] == 0
        assert result["deleted_post_refs"] == []
        assert refreshed_old_post.is_tracked is False
        assert refreshed_old_post.is_deleted is False
        assert refreshed_old_post.metric_tier == "expired"
        assert refreshed_old_post.next_metric_update is None
    finally:
        db.close()


def test_refresh_target_post_metrics_disables_skip_existing_posts(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="target-skip-existing", email="target-skip-existing@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-target-skip-existing",
            facebook_url="https://www.facebook.com/groups/group-target-skip-existing",
            source_name="Target Skip Existing Group",
        )
        PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="target-existing-1",
            facebook_url="https://facebook.com/posts/target-existing-1",
            posted_at=datetime.utcnow(),
            content="Target existing",
            likes_count=0,
            shares_count=0,
            comments_count=0,
        )

        calls = []

        def fake_fetch_posts(limit=20, **kwargs):
            calls.append({"limit": limit, **kwargs})
            return [
                {
                    "post_id": "target-existing-1",
                    "permalink": "https://facebook.com/posts/target-existing-1",
                    "message": "Target existing",
                    "posted_at": datetime.utcnow(),
                    "reaction_count": 4,
                    "share_count": 0,
                    "comment_count": 0,
                    "photos": [],
                    "videos": [],
                }
            ]

        monkeypatch.setattr("backend.scraper.facebook_service.group_scraper.fetch_posts", fake_fetch_posts)

        result = FacebookScraperService.refresh_target_post_metrics(
            db,
            source,
            target_post_ids=["target-existing-1"],
            stop_when_all_found=True,
        )

        assert result["matched_target_count"] == 1
        assert calls
        assert calls[0]["limit"] == 60
        assert calls[0]["skip_existing_posts"] is False
        assert calls[0]["target_post_ids"] == ["target-existing-1"]
        assert calls[0]["stop_when_targets_found"] is True
    finally:
        db.close()


def test_post_extract_media_skips_download_when_flag_disabled(monkeypatch):
    called = {"download": False}

    def fake_download_image(*args, **kwargs):
        called["download"] = True
        return "file.jpg"

    monkeypatch.setattr(post_scraper, "download_image", fake_download_image)

    node = {
        "attachments": [
            {
                "styles": {
                    "attachment": {
                        "media": {
                            "id": "m1",
                            "photo_image": {"uri": "https://example.com/a.jpg"},
                        }
                    }
                }
            }
        ]
    }

    media = post_scraper.extract_media(node, "post-1", download_media=False)

    assert called["download"] is False
    assert media
    assert media[0]["type"] == "photo"
    assert media[0]["saved_as"] is None


def test_group_extract_media_skips_download_when_flag_disabled(monkeypatch):
    called = {"download": False}

    def fake_download_image(*args, **kwargs):
        called["download"] = True
        return "file.jpg"

    monkeypatch.setattr(group_post_scraper_v2, "download_image", fake_download_image)

    node = {
        "attachments": [
            {
                "styles": {
                    "attachment": {
                        "media": {
                            "id": "m1",
                            "photo_image": {"uri": "https://example.com/a.jpg", "width": 1200, "height": 800},
                        }
                    }
                }
            }
        ]
    }

    media = group_post_scraper_v2.extract_media(node, "post-1", download_media=False)

    assert called["download"] is False
    assert media["photos"]
    assert media["photos"][0]["saved_as"] is None


def test_group_extract_media_handles_video_with_null_preferred_thumbnail():
    node = {
        "attachments": [
            {
                "styles": {
                    "attachment": {
                        "media": {
                            "id": "background-video",
                            "__typename": "Video",
                            "playable_url": "https://example.com/background.mp4",
                            "preferred_thumbnail": None,
                        }
                    }
                }
            }
        ]
    }

    media = group_post_scraper_v2.extract_media(node, "post-background", download_media=False)

    assert media["videos"] == [
        {
            "id": "background-video",
            "url": "https://example.com/background.mp4",
            "thumbnail": None,
        }
    ]


def test_group_reaction_lookup_parses_detached_feedback_count_nested():
    feedback_node = {
        "id": "ZmVlZGJhY2s6abc",
        "reaction_count": {"count": {"count": "42"}},
    }

    parsed = group_post_scraper_v2._extract_feedback_reaction_count_from_node(feedback_node)

    assert parsed == 42


def test_group_coerce_int_parses_reduced_count_strings():
    assert group_post_scraper_v2._coerce_int(42) == 42
    assert group_post_scraper_v2._coerce_int("42") == 42
    assert group_post_scraper_v2._coerce_int("1,234") == 1234
    assert group_post_scraper_v2._coerce_int("1.2K") == 1200
    assert group_post_scraper_v2._coerce_int("3K") == 3000
    assert group_post_scraper_v2._coerce_int("1.5M") == 1500000


def test_group_feedback_metric_lookup_scans_nested_detached_feedback():
    data = [
        {
            "node": {
                "__typename": "Group",
                "group_feed": {
                    "edges": [
                        {
                            "node": {
                                "__typename": "Story",
                                "post_id": "post-nested",
                            }
                        }
                    ]
                },
                "detached": {
                    "feedback": {
                        "id": "ZmVlZGJhY2s6nested",
                        "reaction_count": {"count": {"count": "1.2K"}},
                    }
                },
            }
        }
    ]

    reaction_by_id, _ = group_post_scraper_v2._build_feedback_metric_lookups(data)

    assert reaction_by_id["ZmVlZGJhY2s6nested"] == 1200


def test_group_reaction_lookup_uses_nested_story_feedback_id():
    story_node = {
        "__typename": "Story",
        "post_id": "post-with-nested-feedback-id",
        "comet_sections": {
            "feedback": {
                "story": {
                    "story_ufi_container": {
                        "story": {
                            "feedback_context": {
                                "feedback_target_with_context": {
                                    "id": "ZmVlZGJhY2s6story-nested",
                                }
                            }
                        }
                    }
                }
            }
        },
    }
    detached_feedback_map = {"ZmVlZGJhY2s6story-nested": 27}

    resolved = group_post_scraper_v2._resolve_story_reaction_count(story_node, detached_feedback_map)

    assert resolved == 27


def test_group_reaction_lookup_prefers_story_when_non_zero():
    story_reaction = 7
    detached_feedback_map = {"ZmVlZGJhY2s6post-1": 99}
    feedback_id = "ZmVlZGJhY2s6post-1"

    resolved = group_post_scraper_v2._coerce_int(story_reaction)
    if resolved in (None, 0):
        resolved = detached_feedback_map.get(feedback_id, 0)

    assert resolved == 7


def test_group_reaction_lookup_fallbacks_when_story_zero():
    story_reaction = 0
    detached_feedback_map = {"ZmVlZGJhY2s6post-2": 15}
    feedback_id = "ZmVlZGJhY2s6post-2"

    resolved = group_post_scraper_v2._coerce_int(story_reaction)
    if resolved in (None, 0):
        resolved = detached_feedback_map.get(feedback_id, 0)

    assert resolved == 15


def test_refresh_target_post_metrics_group_uses_reaction_count_output_for_likes(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="group-reaction-fallback", email="group-reaction-fallback@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-reaction-fallback",
            facebook_url="https://www.facebook.com/groups/group-reaction-fallback",
            source_name="Group Reaction Fallback",
        )
        target_post = PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="target-rx-1",
            facebook_url="https://facebook.com/posts/target-rx-1",
            posted_at=datetime.utcnow(),
            content="Target reaction",
            likes_count=1,
            shares_count=0,
            comments_count=0,
        )

        # Service layer should persist scraper reaction_count into likes_count.
        # This value represents output after group scraper fallback merge.
        monkeypatch.setattr(
            "backend.scraper.facebook_service.group_scraper.fetch_posts",
            lambda limit=20, **kwargs: [
                {
                    "post_id": "target-rx-1",
                    "permalink": "https://facebook.com/posts/target-rx-1",
                    "message": "Target reaction",
                    "posted_at": datetime.utcnow(),
                    "reaction_count": 15,
                    "share_count": 0,
                    "comment_count": 0,
                    "photos": [],
                    "videos": [],
                }
            ],
        )

        result = FacebookScraperService.refresh_target_post_metrics(
            db,
            source,
            target_post_ids=["target-rx-1"],
            stop_when_all_found=True,
        )
        refreshed_target = PostCRUD.get_by_id(db, target_post.id)

        assert result["updated"] == 1
        assert refreshed_target.current_likes == 15
    finally:
        db.close()


def test_create_source_enqueues_background_bootstrap_scrape(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="ivy", email="ivy@example.com", password="secret123")

        monkeypatch.setattr(
            "backend.api.routes.sources.FacebookURLParser.parse",
            lambda _url: {
                "is_valid": True,
                "facebook_id": "new-group-1",
                "source_type": SimpleNamespace(value="group"),
            },
        )

        source_data = SourceCreate(
            source_type="group",
            facebook_url="https://www.facebook.com/groups/new-group-1",
            include_comments=True,
            include_replies=True,
            max_days_old=30,
            check_access=False,
        )

        background_tasks = BackgroundTasks()
        added_tasks = []

        def fake_add_task(func, *args, **kwargs):
            added_tasks.append((func, args, kwargs))

        monkeypatch.setattr(background_tasks, "add_task", fake_add_task)

        import asyncio

        result = asyncio.run(
            create_source(
                source_data=source_data,
                background_tasks=background_tasks,
                current_user=user,
                db=db,
            )
        )

        assert result.mode == "single"
        assert result.total == 1
        assert result.success_count == 1
        assert result.error_count == 0
        assert len(result.created) == 1
        created_source = result.created[0]
        assert created_source.id is not None
        assert len(added_tasks) == 1
        task_func, task_args, task_kwargs = added_tasks[0]
        assert task_func.__name__ == "_bootstrap_scrape_source_last_24h"
        assert task_args == (created_source.id,)
        assert task_kwargs == {}
    finally:
        db.close()


def test_bootstrap_scrape_logs_error_without_crashing(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="jack", email="jack@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-bootstrap-fail",
            facebook_url="https://www.facebook.com/groups/group-bootstrap-fail",
            source_name="Bootstrap Fail",
        )
    finally:
        db.close()

    monkeypatch.setattr(
        "backend.api.routes.sources.FacebookScraperService.scrape_source",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("bootstrap failed")),
    )

    _bootstrap_scrape_source_last_24h(source.id)

    verify_db = SessionLocal()
    try:
        log = (
            verify_db.query(ScraperLog)
            .filter(ScraperLog.source_id == source.id)
            .order_by(ScraperLog.id.desc())
            .first()
        )
        assert log is not None
        assert "Bootstrap scrape 24h thất bại" in log.message
        assert log.error_type == "RuntimeError"
    finally:
        verify_db.close()


def test_create_source_batch_partial_success_returns_multi_status(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="batch-ivy", email="batch-ivy@example.com", password="secret123")

        def fake_parse(url):
            if "invalid" in url:
                return {"is_valid": False, "error": "not facebook url"}
            return {
                "is_valid": True,
                "facebook_id": url.split("/")[-1],
                "source_type": SimpleNamespace(value="group"),
            }

        monkeypatch.setattr("backend.api.routes.sources.FacebookURLParser.parse", fake_parse)

        source_data = [
            SourceCreate(
                source_type="group",
                facebook_url="https://www.facebook.com/groups/batch-ok-1",
                check_access=False,
            ),
            SourceCreate(
                source_type="group",
                facebook_url="https://www.facebook.com/groups/invalid",
                check_access=False,
            ),
            SourceCreate(
                source_type="group",
                facebook_url="https://www.facebook.com/groups/batch-ok-2",
                check_access=False,
            ),
        ]

        background_tasks = BackgroundTasks()
        added_tasks = []

        def fake_add_task(func, *args, **kwargs):
            added_tasks.append((func, args, kwargs))

        monkeypatch.setattr(background_tasks, "add_task", fake_add_task)

        import asyncio

        response = asyncio.run(
            create_source(
                source_data=source_data,
                background_tasks=background_tasks,
                current_user=user,
                db=db,
            )
        )

        assert response.status_code == 207
        payload = json.loads(response.body)
        assert payload["mode"] == "batch"
        assert payload["total"] == 3
        assert payload["success_count"] == 2
        assert payload["error_count"] == 1
        assert len(payload["created"]) == 2
        assert len(payload["errors"]) == 1
        assert payload["errors"][0]["index"] == 1
        assert len(added_tasks) == 2
    finally:
        db.close()


def test_create_source_batch_all_success(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="batch-all-ok", email="batch-all-ok@example.com", password="secret123")

        monkeypatch.setattr(
            "backend.api.routes.sources.FacebookURLParser.parse",
            lambda url: {
                "is_valid": True,
                "facebook_id": url.split("/")[-1],
                "source_type": SimpleNamespace(value="group"),
            },
        )

        source_data = [
            SourceCreate(
                source_type="group",
                facebook_url="https://www.facebook.com/groups/batch-all-1",
                check_access=False,
            ),
            SourceCreate(
                source_type="group",
                facebook_url="https://www.facebook.com/groups/batch-all-2",
                check_access=False,
            ),
        ]

        background_tasks = BackgroundTasks()
        added_tasks = []

        def fake_add_task(func, *args, **kwargs):
            added_tasks.append((func, args, kwargs))

        monkeypatch.setattr(background_tasks, "add_task", fake_add_task)

        import asyncio

        result = asyncio.run(
            create_source(
                source_data=source_data,
                background_tasks=background_tasks,
                current_user=user,
                db=db,
            )
        )

        assert result.mode == "batch"
        assert result.total == 2
        assert result.success_count == 2
        assert result.error_count == 0
        assert len(result.created) == 2
        assert len(result.errors) == 0
        assert len(added_tasks) == 2
    finally:
        db.close()


def test_get_source_schedule_stats_returns_latest_post_metrics_totals():
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="source-stats-user", email="source-stats-user@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-source-stats",
            facebook_url="https://www.facebook.com/groups/group-source-stats",
            source_name="Source Stats",
        )

        source.member_count = 1000
        source.schedule_tier = 1
        source.schedule_override_minutes = None
        source.next_scrape = datetime(2026, 5, 14, 10, 35, 0)
        db.commit()

        first_post = PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="source-stats-post-1",
            facebook_url="https://www.facebook.com/groups/group-source-stats/posts/1",
            posted_at=datetime(2026, 5, 15, 9, 0, 0),
            content="first",
        )
        second_post = PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="source-stats-post-2",
            facebook_url="https://www.facebook.com/groups/group-source-stats/posts/2",
            posted_at=datetime(2026, 5, 15, 8, 0, 0),
            content="second",
        )
        ignored_post = PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="source-stats-untracked",
            facebook_url="https://www.facebook.com/groups/group-source-stats/posts/3",
            posted_at=datetime(2026, 5, 15, 7, 0, 0),
            content="ignored",
        )
        ignored_post.is_tracked = False

        PostMetricCRUD.create(db, first_post.id, likes=10, shares=1, comments=2)
        PostMetricCRUD.create(db, first_post.id, likes=30, shares=3, comments=4)
        PostMetricCRUD.create(db, second_post.id, likes=5, shares=2, comments=1)
        PostMetricCRUD.create(db, second_post.id, likes=7, shares=4, comments=6)
        PostMetricCRUD.create(db, ignored_post.id, likes=100, shares=100, comments=100)
        db.execute(
            text(
                """
                INSERT INTO analytics_cache (
                    source_id, date, total_posts, total_likes, total_shares,
                    total_comments, avg_likes_per_post, cached_at
                )
                VALUES (
                    :source_id, :date, 99, 999, 999, 999, 999, :cached_at
                )
                """
            ),
            {
                "source_id": source.id,
                "date": datetime(2026, 5, 15, 0, 0, 0),
                "cached_at": datetime.utcnow(),
            },
        )
        db.commit()

        import asyncio

        result = asyncio.run(
            get_source_schedule_stats(
                source_id=source.id,
                current_user=user,
                db=db,
            )
        )

        assert result.total_posts == 2
        assert result.total_likes == 37
        assert result.total_shares == 7
        assert result.total_comments == 10
        assert result.total_engagement == 54
        assert result.avg_likes_per_post == 18.5
        assert [post.facebook_post_id for post in result.posts] == [
            "source-stats-post-1",
            "source-stats-post-2",
        ]
        assert result.posts[0].latest_likes == 30
        assert result.posts[0].latest_shares == 3
        assert result.posts[0].latest_comments == 4
        assert result.posts[1].latest_likes == 7
        assert result.posts[1].latest_shares == 4
        assert result.posts[1].latest_comments == 6
    finally:
        db.close()


def test_calculate_tier_counts_recent_posts_by_posted_at_even_when_untracked():
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="tier-post-time-user", email="tier-post-time-user@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="tier-post-time-group",
            facebook_url="https://www.facebook.com/groups/tier-post-time-group",
            source_name="Tier Posted At Source",
        )
        recent_time = datetime.utcnow() - timedelta(hours=1)

        for index in range(21):
            post = PostCRUD.create(
                db,
                source_id=source.id,
                facebook_post_id=f"tier-recent-{index}",
                facebook_url=f"https://www.facebook.com/posts/tier-recent-{index}",
                posted_at=recent_time,
            )
            post.is_tracked = False

        PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="tier-too-old",
            facebook_url="https://www.facebook.com/posts/tier-too-old",
            posted_at=datetime.utcnow() - timedelta(days=8),
        )
        db.execute(
            text(
                """
                INSERT INTO analytics_cache (
                    source_id, date, total_posts, total_likes, total_shares,
                    total_comments, avg_likes_per_post, cached_at
                )
                VALUES (:source_id, :date, 0, 0, 0, 0, 0, :cached_at)
                """
            ),
            {
                "source_id": source.id,
                "date": datetime.utcnow(),
                "cached_at": datetime.utcnow(),
            },
        )
        db.commit()

        result = calculate_tier(source.id, db)

        assert result["avg_posts_per_day"] == 3.0
        assert result["tier"] == 3
    finally:
        db.close()


def test_calculate_tier_without_analytics_does_not_assign_a_tier():
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="tier-new-user", email="tier-new-user@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="tier-new-group",
            facebook_url="https://www.facebook.com/groups/tier-new-group",
        )

        result = calculate_tier(source.id, db)

        assert result["tier"] is None
        assert result["interval_minutes"] is None
        assert source.schedule_tier is None
    finally:
        db.close()


def test_generate_analytics_cache_assigns_tier_4_without_pausing_low_activity_source():
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="analytics-tier-user", email="analytics-tier-user@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="analytics-tier-group",
            facebook_url="https://www.facebook.com/groups/analytics-tier-group",
        )
        source_id = source.id
    finally:
        db.close()

    before = datetime.utcnow()
    import asyncio
    asyncio.run(generate_analytics_cache())

    verify_db = SessionLocal()
    try:
        refreshed = SourceCRUD.get_by_id(verify_db, source_id)
        assert refreshed.schedule_tier == 4
        assert refreshed.is_active is True
        assert refreshed.next_scrape >= before + timedelta(minutes=719)
        assert refreshed.next_scrape <= datetime.utcnow() + timedelta(minutes=721)
    finally:
        verify_db.close()


def test_generate_analytics_cache_updates_tier_but_preserves_override_schedule():
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="analytics-override-user", email="analytics-override-user@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="analytics-override-group",
            facebook_url="https://www.facebook.com/groups/analytics-override-group",
        )
        existing_next_scrape = datetime(2026, 5, 28, 12, 0, 0)
        source.schedule_tier = 1
        source.schedule_override_minutes = 45
        source.next_scrape = existing_next_scrape
        source_id = source.id
        db.commit()
    finally:
        db.close()

    import asyncio
    asyncio.run(generate_analytics_cache())

    verify_db = SessionLocal()
    try:
        refreshed = SourceCRUD.get_by_id(verify_db, source_id)
        assert refreshed.schedule_tier == 4
        assert refreshed.schedule_override_minutes == 45
        assert refreshed.next_scrape == existing_next_scrape
    finally:
        verify_db.close()


def test_get_sources_ranking_returns_ranked_user_sources_and_tier_distribution():
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="ranking-user", email="ranking-user@example.com", password="secret123")
        other_user = UserCRUD.create(db, username="ranking-other", email="ranking-other@example.com", password="secret123")
        hot_source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="ranking-hot",
            facebook_url="https://www.facebook.com/groups/ranking-hot",
            source_name="Hot Source",
        )
        warm_source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="ranking-warm",
            facebook_url="https://www.facebook.com/groups/ranking-warm",
            source_name="Warm Source",
        )
        frozen_source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="ranking-frozen",
            facebook_url="https://www.facebook.com/groups/ranking-frozen",
            source_name="Frozen Source",
        )
        other_source = SourceCRUD.create(
            db,
            user_id=other_user.id,
            source_type="group",
            facebook_id="ranking-other-hot",
            facebook_url="https://www.facebook.com/groups/ranking-other-hot",
            source_name="Other Hot Source",
        )

        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        fixtures = [
            (hot_source, 20, 10000, 5, 5, 1, None),
            (warm_source, 10, 1000, 2, 2, 2, 30),
            (other_source, 50, 25000, 10, 10, 1, None),
        ]
        for source, posts, likes, shares, comments, current_tier, override_minutes in fixtures:
            source.schedule_tier = current_tier
            source.schedule_override_minutes = override_minutes
            if source.id in {hot_source.id, warm_source.id}:
                for index in range(posts * 7):
                    db.execute(
                        text(
                            """
                            INSERT INTO posts (
                                source_id, facebook_post_id, facebook_url, posted_at,
                                is_tracked, is_deleted
                            )
                            VALUES (
                                :source_id, :facebook_post_id, :facebook_url, :posted_at,
                                0, 0
                            )
                            """
                        ),
                        {
                            "source_id": source.id,
                            "facebook_post_id": f"ranking-{source.id}-{index}",
                            "facebook_url": f"https://www.facebook.com/posts/ranking-{source.id}-{index}",
                            "posted_at": datetime.utcnow(),
                        },
                    )
            db.execute(
                text(
                    """
                    INSERT INTO analytics_cache (
                        source_id, date, total_posts, total_likes, total_shares,
                        total_comments, avg_likes_per_post, cached_at
                    )
                    VALUES (
                        :source_id, :date, :total_posts, :total_likes, :total_shares,
                        :total_comments, :avg_likes_per_post, :cached_at
                    )
                    """
                ),
                {
                    "source_id": source.id,
                    "date": today,
                    "total_posts": posts,
                    "total_likes": likes,
                    "total_shares": shares,
                    "total_comments": comments,
                    "avg_likes_per_post": likes / posts,
                    "cached_at": datetime.utcnow(),
                },
            )
        db.commit()

        import asyncio

        result = asyncio.run(
            get_sources_ranking(
                sort="posts_per_day",
                limit=2,
                current_user=user,
                db=db,
            )
        )

        assert result.total_sources == 3
        assert result.tier_distribution == {"tier_1": 1, "tier_2": 1, "tier_3": 0, "tier_4": 1}
        assert [item.source_id for item in result.sources] == [hot_source.id, warm_source.id]
        assert [item.rank for item in result.sources] == [1, 2]
        assert result.sources[0].avg_posts_per_day == 20.0
        assert result.sources[0].avg_likes_per_post == 500.0
        assert result.sources[0].suggested_tier == 1
        assert result.sources[0].current_tier == 1
        assert result.sources[0].is_overridden is False
        assert result.sources[1].suggested_tier == 2
        assert result.sources[1].is_overridden is True

        tier_sorted = asyncio.run(
            get_sources_ranking(
                sort="tier",
                current_user=user,
                db=db,
            )
        )
        assert [item.source_id for item in tier_sorted.sources] == [
            hot_source.id,
            warm_source.id,
            frozen_source.id,
        ]
    finally:
        db.close()


def test_list_sources_filters_sorts_and_optionally_includes_schedule_stats():
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="list-user", email="list-user@example.com", password="secret123")
        other_user = UserCRUD.create(db, username="list-other", email="list-other@example.com", password="secret123")
        tier_1_source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="list-tier-1",
            facebook_url="https://www.facebook.com/groups/list-tier-1",
            source_name="Tier 1 Source",
        )
        tier_2_source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="list-tier-2",
            facebook_url="https://www.facebook.com/groups/list-tier-2",
            source_name="Tier 2 Source",
        )
        tier_3_source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="list-tier-3",
            facebook_url="https://www.facebook.com/groups/list-tier-3",
            source_name="Tier 3 Source",
        )
        other_source = SourceCRUD.create(
            db,
            user_id=other_user.id,
            source_type="group",
            facebook_id="list-other-tier-1",
            facebook_url="https://www.facebook.com/groups/list-other-tier-1",
            source_name="Other Tier 1 Source",
        )

        next_scrape = datetime(2026, 5, 14, 10, 35, 0)
        fixtures = [
            (tier_1_source, 1, None, 4, 80, 8, 2),
            (tier_2_source, 2, 30, 8, 10, 1, 1),
            (tier_3_source, 3, None, 2, 20, 2, 2),
            (other_source, 1, None, 99, 999, 99, 99),
        ]
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        for source, schedule_tier, override_minutes, posts, likes, shares, comments in fixtures:
            source.schedule_tier = schedule_tier
            source.schedule_override_minutes = override_minutes
            source.next_scrape = next_scrape
            db.execute(
                text(
                    """
                    INSERT INTO analytics_cache (
                        source_id, date, total_posts, total_likes, total_shares,
                        total_comments, cached_at
                    )
                    VALUES (
                        :source_id, :date, :total_posts, :total_likes, :total_shares,
                        :total_comments, :cached_at
                    )
                    """
                ),
                {
                    "source_id": source.id,
                    "date": today,
                    "total_posts": posts,
                    "total_likes": likes,
                    "total_shares": shares,
                    "total_comments": comments,
                    "cached_at": datetime.utcnow(),
                },
            )
        db.commit()

        import asyncio

        without_stats = asyncio.run(
            list_sources(
                current_user=user,
                db=db,
            )
        )
        assert "schedule_tier" not in without_stats[0]
        assert "schedule_override_minutes" not in without_stats[0]
        assert "next_scrape" not in without_stats[0]

        filtered = asyncio.run(
            list_sources(
                sort="tier",
                tier=[1, 2],
                include_stats=True,
                current_user=user,
                db=db,
            )
        )
        assert [item["id"] for item in filtered] == [tier_1_source.id, tier_2_source.id]
        assert filtered[0]["schedule_tier"] == 1
        assert filtered[0]["schedule_override_minutes"] is None
        assert filtered[0]["next_scrape"] == next_scrape.isoformat()
        assert filtered[1]["schedule_tier"] == 2
        assert filtered[1]["schedule_override_minutes"] == 30

        engagement_sorted = asyncio.run(
            list_sources(
                sort="engagement",
                include_stats=True,
                current_user=user,
                db=db,
            )
        )
        assert [item["id"] for item in engagement_sorted] == [
            tier_1_source.id,
            tier_3_source.id,
            tier_2_source.id,
        ]

        posts_today_sorted = asyncio.run(
            list_sources(
                sort="posts_today",
                current_user=user,
                db=db,
            )
        )
        assert [item["id"] for item in posts_today_sorted] == [
            tier_2_source.id,
            tier_1_source.id,
            tier_3_source.id,
        ]
    finally:
        db.close()


def test_update_source_sets_schedule_override_and_refreshes_next_scrape(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="update-override-user", email="update-override-user@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="update-override-group",
            facebook_url="https://www.facebook.com/groups/update-override-group",
            source_name="Update Override Group",
        )
        source.schedule_tier = 2
        db.commit()
        next_scrape = datetime(2026, 5, 14, 10, 50, 0)
        calls = []

        def fake_schedule_next_scrape(source_id, session):
            calls.append((source_id, session))
            refreshed = SourceCRUD.get_by_id(session, source_id)
            refreshed.next_scrape = next_scrape
            session.commit()
            return {"next_scrape": next_scrape.strftime("%Y-%m-%dT%H:%M:%S")}

        monkeypatch.setattr("backend.api.routes.sources.schedule_next_scrape", fake_schedule_next_scrape)

        import asyncio

        result = asyncio.run(
            update_source(
                source_id=source.id,
                source_data=SourceUpdate(schedule_override_minutes=30),
                current_user=user,
                db=db,
            )
        )

        updated_source = SourceCRUD.get_by_id(db, source.id)
        assert updated_source.schedule_override_minutes == 30
        assert updated_source.next_scrape == next_scrape
        assert calls == [(source.id, db)]
        assert result.is_overridden is True
        assert result.override_minutes == 30
        assert result.suggested_tier == 2
        assert result.next_scrape == next_scrape
    finally:
        db.close()


def test_update_source_clears_schedule_override_with_null_and_refreshes_schedule(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="clear-override-user", email="clear-override-user@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="clear-override-group",
            facebook_url="https://www.facebook.com/groups/clear-override-group",
            source_name="Clear Override Group",
        )
        source.schedule_override_minutes = 30
        source.schedule_tier = 2
        db.commit()

        next_scrape = datetime(2026, 5, 14, 11, 20, 0)
        calls = []

        def fake_schedule_next_scrape(source_id, session):
            calls.append((source_id, session))
            refreshed = SourceCRUD.get_by_id(session, source_id)
            refreshed.next_scrape = next_scrape
            session.commit()
            return {"next_scrape": next_scrape.strftime("%Y-%m-%dT%H:%M:%S")}

        monkeypatch.setattr("backend.api.routes.sources.schedule_next_scrape", fake_schedule_next_scrape)

        import asyncio

        result = asyncio.run(
            update_source(
                source_id=source.id,
                source_data=SourceUpdate(schedule_override_minutes=None),
                current_user=user,
                db=db,
            )
        )

        updated_source = SourceCRUD.get_by_id(db, source.id)
        assert updated_source.schedule_override_minutes is None
        assert updated_source.next_scrape == next_scrape
        assert calls == [(source.id, db)]
        assert result.is_overridden is False
        assert result.override_minutes is None
        assert result.suggested_tier == 2
        assert result.next_scrape == next_scrape
    finally:
        db.close()


def test_refresh_source_only_marks_source_due_without_recalculating_schedule():
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="refresh-schedule-user", email="refresh-schedule-user@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="refresh-schedule-group",
            facebook_url="https://www.facebook.com/groups/refresh-schedule-group",
            source_name="Refresh Schedule Group",
        )
        source.schedule_tier = 1
        db.commit()

        import asyncio

        result = asyncio.run(refresh_source(source_id=source.id, current_user=user, db=db))

        assert result == {
            "message": "Scrape scheduled",
            "source_id": source.id,
            "next_scrape": result["next_scrape"],
            "current_tier": 1,
            "applied_interval_minutes": 30.0,
            "next_auto_scrape": None,
            "avg_likes_per_post": None,
            "data_days": None,
        }
        assert datetime.fromisoformat(result["next_scrape"])
        refreshed_source = SourceCRUD.get_by_id(db, source.id)
        assert refreshed_source.next_scrape == datetime.fromisoformat(result["next_scrape"])
    finally:
        db.close()


def test_periodic_scrape_new_posts_uses_24h_window_and_latest_post_even_untracked(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="periodic-cutoff-user", email="periodic-cutoff@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="periodic-cutoff-group",
            facebook_url="https://www.facebook.com/groups/periodic-cutoff-group",
            source_name="Periodic Cutoff Group",
        )
        latest_posted_at = datetime.utcnow() - timedelta(days=3)
        existing_post = PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="existing-untracked",
            facebook_url="https://facebook.com/posts/existing-untracked",
            posted_at=latest_posted_at,
        )
        existing_post.is_tracked = False
        db.commit()
        source_id = source.id
    finally:
        db.close()

    calls = []

    def fake_scrape_source(session, passed_source_id, **kwargs):
        calls.append((passed_source_id, kwargs))
        return SimpleNamespace(
            total_fetched=0,
            created_posts=0,
            updated_posts=0,
            skipped_posts=0,
            filtered_by_cutoff=0,
        )

    monkeypatch.setattr(
        "backend.scheduler.periodic_tasks.FacebookScraperService.scrape_source",
        fake_scrape_source,
    )

    import asyncio
    asyncio.run(periodic_scrape_new_posts())

    assert len(calls) == 1
    assert calls[0][0] == source_id
    assert calls[0][1]["last_24_hours_only"] is True
    assert calls[0][1]["min_posted_at"] == latest_posted_at


def test_periodic_scrape_new_posts_includes_due_metric_targets_for_same_source(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="periodic-combined", email="periodic-combined@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="periodic-combined-group",
            facebook_url="https://www.facebook.com/groups/periodic-combined-group",
            source_name="Periodic Combined Group",
        )
        source.next_scrape = datetime.utcnow() - timedelta(minutes=1)
        due_posted_at = datetime.utcnow() - timedelta(minutes=30)
        PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="periodic-due-target",
            facebook_url="https://facebook.com/posts/periodic-due-target",
            posted_at=due_posted_at,
            next_metric_update=datetime.utcnow() - timedelta(minutes=1),
        )
        db.commit()
        source_id = source.id
    finally:
        db.close()

    calls = []

    def fake_scrape_source(session, passed_source_id, **kwargs):
        calls.append((passed_source_id, kwargs))
        return SimpleNamespace(
            total_fetched=1,
            created_posts=0,
            updated_posts=1,
            skipped_posts=0,
            filtered_by_cutoff=0,
            matched_metric_target_ids=["periodic-due-target"],
        )

    monkeypatch.setattr(
        "backend.scheduler.periodic_tasks.FacebookScraperService.scrape_source",
        fake_scrape_source,
    )

    import asyncio
    asyncio.run(periodic_scrape_new_posts())

    assert len(calls) == 1
    assert calls[0][0] == source_id
    assert calls[0][1]["last_24_hours_only"] is True
    assert calls[0][1]["min_posted_at"] == due_posted_at
    assert calls[0][1]["metric_target_post_ids"] == ["periodic-due-target"]


def test_periodic_scrape_new_posts_sets_next_scrape_from_stored_tier(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="periodic-tier-user", email="periodic-tier@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="periodic-tier-group",
            facebook_url="https://www.facebook.com/groups/periodic-tier-group",
        )
        source.schedule_tier = 1
        source.next_scrape = datetime.utcnow() - timedelta(minutes=1)
        source_id = source.id
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(
        "backend.scheduler.periodic_tasks.FacebookScraperService.scrape_source",
        lambda *args, **kwargs: SimpleNamespace(
            total_fetched=0,
            created_posts=0,
            updated_posts=0,
            skipped_posts=0,
            filtered_by_cutoff=0,
        ),
    )

    before = datetime.utcnow()
    import asyncio
    asyncio.run(periodic_scrape_new_posts())

    verify_db = SessionLocal()
    try:
        refreshed = SourceCRUD.get_by_id(verify_db, source_id)
        assert refreshed.next_scrape >= before + timedelta(minutes=29)
        assert refreshed.next_scrape <= datetime.utcnow() + timedelta(minutes=31)
    finally:
        verify_db.close()


def test_periodic_scrape_new_posts_creates_done_scrape_job_without_session(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="periodic-job-no-session", email="periodic-job-no-session@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-periodic-job-no-session",
            facebook_url="https://www.facebook.com/groups/group-periodic-job-no-session",
            source_name="Periodic Job No Session",
        )
        source_id = source.id
    finally:
        db.close()

    monkeypatch.setattr(
        "backend.scraper.facebook_service.group_scraper.fetch_posts",
        lambda limit=20: [
            {
                "post_id": "periodic-job-post-1",
                "group_link": "https://www.facebook.com/groups/group-periodic-job-no-session/",
                "permalink": "https://facebook.com/posts/periodic-job-post-1",
                "message": "Periodic job post",
                "posted_at": datetime.utcnow(),
                "reaction_count": 2,
                "share_count": 1,
                "comment_count": 1,
                "group_name": "Periodic Job No Session",
                "photos": [],
                "videos": [],
            },
        ],
    )
    monkeypatch.setattr(
        "backend.scraper.facebook_service.comment_scraper.fetch_comments",
        lambda feedback_id, cookies=None: ([], {"is_active": True}),
    )

    import asyncio
    asyncio.run(periodic_scrape_new_posts())

    verify_db = SessionLocal()
    try:
        job = (
            verify_db.query(ScrapeJob)
            .filter(ScrapeJob.source_id == source_id)
            .order_by(ScrapeJob.id.desc())
            .first()
        )
        assert job is not None
        assert job.status == "done"
        assert job.session_id is None
        assert job.posts_found == 1
        assert job.posts_new == 1
        assert job.started_at is not None
        assert job.finished_at is not None
    finally:
        verify_db.close()


def test_periodic_scrape_new_posts_marks_failed_scrape_job_and_keeps_counters_default(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="periodic-job-failed", email="periodic-job-failed@example.com", password="secret123")
        session = FacebookSessionCRUD.upsert_active_for_user(
            db=db,
            user_id=user.id,
            fb_cookies='{"c_user":"123"}',
            fb_dtsg="token",
        )
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-periodic-job-failed",
            facebook_url="https://www.facebook.com/groups/group-periodic-job-failed",
            source_name="Periodic Job Failed",
        )
        source_id = source.id
        session_id = session.id
    finally:
        db.close()

    monkeypatch.setattr(
        "backend.scheduler.periodic_tasks.FacebookScraperService.scrape_source",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("periodic scrape failed")),
    )

    import asyncio
    asyncio.run(periodic_scrape_new_posts())

    verify_db = SessionLocal()
    try:
        job = (
            verify_db.query(ScrapeJob)
            .filter(ScrapeJob.source_id == source_id)
            .order_by(ScrapeJob.id.desc())
            .first()
        )
        assert job is not None
        assert job.status == "failed"
        assert job.session_id == session_id
        assert job.error_message == "periodic scrape failed"
        assert job.posts_found == 0
        assert job.posts_new == 0
        assert job.started_at is not None
        assert job.finished_at is not None
    finally:
        verify_db.close()


def test_bootstrap_scrape_creates_done_scrape_job_with_session(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="bootstrap-job-done", email="bootstrap-job-done@example.com", password="secret123")
        session = FacebookSessionCRUD.upsert_active_for_user(
            db=db,
            user_id=user.id,
            fb_cookies='{"c_user":"123"}',
            fb_dtsg="token",
        )
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-bootstrap-job-done",
            facebook_url="https://www.facebook.com/groups/group-bootstrap-job-done",
            source_name="Bootstrap Job Done",
        )
        source_id = source.id
        session_id = session.id
    finally:
        db.close()

    monkeypatch.setattr(
        "backend.api.routes.sources.FacebookScraperService.scrape_source",
        lambda *args, **kwargs: SimpleNamespace(
            total_fetched=3,
            created_posts=2,
            updated_posts=1,
            skipped_posts=0,
            filtered_by_cutoff=0,
        ),
    )

    _bootstrap_scrape_source_last_24h(source_id)

    verify_db = SessionLocal()
    try:
        job = (
            verify_db.query(ScrapeJob)
            .filter(ScrapeJob.source_id == source_id)
            .order_by(ScrapeJob.id.desc())
            .first()
        )
        assert job is not None
        assert job.status == "done"
        assert job.session_id == session_id
        assert job.posts_found == 3
        assert job.posts_new == 2
        assert job.started_at is not None
        assert job.finished_at is not None
    finally:
        verify_db.close()


def test_bootstrap_scrape_marks_failed_scrape_job_and_keeps_counters_default(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="bootstrap-job-failed", email="bootstrap-job-failed@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="group-bootstrap-job-failed",
            facebook_url="https://www.facebook.com/groups/group-bootstrap-job-failed",
            source_name="Bootstrap Job Failed",
        )
        source_id = source.id
    finally:
        db.close()

    monkeypatch.setattr(
        "backend.api.routes.sources.FacebookScraperService.scrape_source",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("bootstrap failed job")),
    )

    _bootstrap_scrape_source_last_24h(source_id)

    verify_db = SessionLocal()
    try:
        job = (
            verify_db.query(ScrapeJob)
            .filter(ScrapeJob.source_id == source_id)
            .order_by(ScrapeJob.id.desc())
            .first()
        )
        assert job is not None
        assert job.status == "failed"
        assert job.error_message == "bootstrap failed job"
        assert job.posts_found == 0
        assert job.posts_new == 0
        assert job.started_at is not None
        assert job.finished_at is not None
    finally:
        verify_db.close()

