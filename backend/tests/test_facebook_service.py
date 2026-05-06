from datetime import datetime

from backend.database.crud import CommentCRUD, PostCRUD, SourceCRUD, UserCRUD
from backend.database.db import SessionLocal, engine
from backend.database.models import Base
from backend.scraper.facebook_service import (
    FacebookScraperService,
    _coerce_datetime,
    _normalize_group_post,
    _normalize_timeline_post,
)
from backend.scheduler.periodic_tasks import periodic_scrape_new_posts


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
    assert parsed == datetime(2026, 1, 1, 10, 0, 0)


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
        user = UserCRUD.update(
            db,
            user.id,
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


def test_scrape_group_source_saves_comments_and_replies_when_enabled(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="erin", email="erin@example.com", password="secret123")
        user = UserCRUD.update(
            db,
            user.id,
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
        monkeypatch.setattr(
            "backend.scraper.facebook_service.comment_scraper.fetch_replies",
            lambda comment, cookies=None: [
                {
                    "comment_id": "r1",
                    "author_id": "u2",
                    "author_name": "User Two",
                    "author_url": "https://facebook.com/u2",
                    "text": "Reply",
                    "reaction_count": 1,
                }
            ],
        )

        FacebookScraperService.scrape_source(db, source.id, limit=5)
        post = PostCRUD.get_by_source_and_facebook_post_id(db, source.id, "post-comments-1")
        top_comment = CommentCRUD.get_by_facebook_id(db, "c1")
        reply_comment = CommentCRUD.get_by_facebook_id(db, "r1")

        assert post is not None
        assert top_comment is not None
        assert top_comment.post_id == post.id
        assert top_comment.depth_level == 0
        assert top_comment.reply_count == 1
        assert reply_comment is not None
        assert reply_comment.parent_comment_id == "c1"
        assert reply_comment.depth_level == 1
    finally:
        db.close()


def test_refresh_recent_post_metrics_syncs_comments_when_enabled(monkeypatch):
    db = SessionLocal()
    try:
        user = UserCRUD.create(db, username="frank", email="frank@example.com", password="secret123")
        user = UserCRUD.update(
            db,
            user.id,
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
            "backend.scheduler.periodic_tasks.FacebookScraperService.scrape_source",
            lambda db_session, source_id, limit=20: FacebookScraperService.scrape_source(db_session, source_id, limit),
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
