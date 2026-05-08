from datetime import datetime, timedelta
from types import SimpleNamespace

from fastapi import BackgroundTasks
from backend.database.crud import CommentCRUD, FacebookSessionCRUD, PostCRUD, SourceCRUD, UserCRUD
from backend.database.db import SessionLocal, engine
from backend.database.models import Base, ScraperLog
from backend.database.schemas import SourceCreate
from backend.api.routes.sources import _bootstrap_scrape_source_last_24h, create_source
from backend.scraper.facebook_service import (
    FacebookScraperService,
    _coerce_datetime,
    _normalize_group_post,
    _normalize_timeline_post,
)
from backend.scheduler.periodic_tasks import periodic_scrape_new_posts, update_recent_post_metrics
import post_scraper
import group_post_scraper_v2


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def teardown_function():
    Base.metadata.drop_all(bind=engine)


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
                        "reaction_count": 2,
                        "reply_count": 1,
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
        assert top_comment.reply_count == 1
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
                    "posted_at": "2026-01-03T09:00:00Z",
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


def test_get_recent_posts_uses_created_at_window():
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

        recent_posts = PostCRUD.get_recent_posts(db, hours=24, limit=50)
        recent_ids = {post.id for post in recent_posts}

        assert recent_created_old_posted.id in recent_ids
        assert old_created_new_posted.id not in recent_ids
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


def test_update_recent_post_metrics_only_refreshes_sources_with_recent_created_posts(monkeypatch):
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
            posted_at=datetime(2020, 1, 1, 0, 0, 0),
            content="Recent created",
        )
        old_post = PostCRUD.create(
            db,
            source_id=old_source.id,
            facebook_post_id="metrics-old-post",
            facebook_url="https://facebook.com/posts/metrics-old-post",
            posted_at=datetime.utcnow(),
            content="Old created",
        )

        old_post.created_at = datetime.utcnow() - timedelta(hours=30)
        db.commit()
        db.refresh(recent_post)
        db.refresh(old_post)
        recent_source_id = recent_source.id
        old_source_id = old_source.id
    finally:
        db.close()

    calls = []

    def fake_refresh_recent_post_metrics(_db, source, limit=20):
        calls.append(source.id)
        return {"fetched": 1, "updated": 1, "skipped": 0}

    monkeypatch.setattr(
        "backend.scheduler.periodic_tasks.FacebookScraperService.refresh_recent_post_metrics",
        fake_refresh_recent_post_metrics,
    )

    import asyncio
    asyncio.run(update_recent_post_metrics())

    assert recent_source_id in calls
    assert old_source_id not in calls


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
        PostCRUD.create(
            db,
            source_id=source.id,
            facebook_post_id="seed-post",
            facebook_url="https://facebook.com/posts/seed-post",
            posted_at=datetime(2026, 1, 4, 10, 0, 0),
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
                "posted_at": datetime(2026, 1, 4, 9, 0, 0),
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
                "posted_at": datetime(2026, 1, 4, 10, 1, 0),
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
                "posted_at": datetime(2026, 1, 4, 10, 1, 0),
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
    assert "periodic_scrape_new_posts SOURCE START" in logs
    assert f"Log Periodic Group (id={source.id})" in logs
    assert "periodic_scrape_new_posts SOURCE DONE" in logs
    assert "created_posts=1" in logs
    assert "periodic_scrape_new_posts DONE" in logs
    assert "total_new_posts_created=1" in logs


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
        )
    finally:
        db.close()

    def fake_refresh_recent_post_metrics(_db, _source, limit=20):
        refs = [f"post-{idx}" for idx in range(1, 13)]
        return {"fetched": 20, "updated": 12, "skipped": 8, "updated_post_refs": refs}

    monkeypatch.setattr(
        "backend.scheduler.periodic_tasks.FacebookScraperService.refresh_recent_post_metrics",
        fake_refresh_recent_post_metrics,
    )

    import asyncio
    with caplog.at_level("INFO", logger="facebook_scraper"):
        asyncio.run(update_recent_post_metrics())

    logs = caplog.text
    assert "update_recent_post_metrics SOURCE START" in logs
    assert f"Log Metrics Group (id={source_id})" in logs
    assert "update_recent_post_metrics SOURCE DONE" in logs
    assert "updated_posts=[post-1, post-2, post-3, post-4, post-5, post-6, post-7, post-8, post-9, post-10 ... +2 more]" in logs
    assert "update_recent_post_metrics DONE" in logs
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
                    "posted_at": datetime(2026, 1, 3, 9, 0, 0),
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

        created_source = asyncio.run(
            create_source(
                source_data=source_data,
                background_tasks=background_tasks,
                current_user=user,
                db=db,
            )
        )

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
        assert "Bootstrap 24h scrape failed" in log.message
        assert log.error_type == "RuntimeError"
    finally:
        verify_db.close()
