from datetime import datetime, timedelta

from sqlalchemy import text

from backend.database.crud import PostCRUD, PostMetricCRUD, SourceCRUD, UserCRUD
from backend.database.db import SessionLocal, engine
from backend.database.migrations import (
    migrate_pipeline_job_type_update_metric,
    migrate_post_metric_job_id_column,
    migrate_post_metric_scheduling_columns,
)
from backend.database.models import Base, Post
from backend.database.schemas import PostResponse
from backend.services.post_metric_schedule_service import (
    COLD,
    EXPIRED,
    HOT,
    apply_metric_snapshot_schedule,
    handle_max_page_misses,
)


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def teardown_function():
    Base.metadata.drop_all(bind=engine)


def _post(db, facebook_post_id: str, posted_at: datetime) -> Post:
    user = UserCRUD.create(
        db,
        username=f"user-{facebook_post_id}",
        email=f"{facebook_post_id}@example.com",
        password="secret123",
    )
    source = SourceCRUD.create(
        db,
        user_id=user.id,
        source_type="group",
        facebook_id=f"source-{facebook_post_id}",
        facebook_url=f"https://facebook.com/groups/source-{facebook_post_id}",
        source_name="Metric Source",
    )
    return PostCRUD.create(
        db,
        source_id=source.id,
        facebook_post_id=facebook_post_id,
        facebook_url=f"https://facebook.com/posts/{facebook_post_id}",
        posted_at=posted_at,
    )


def test_snapshot_scheduling_bootstraps_then_marks_fast_growth_hot():
    db = SessionLocal()
    try:
        base = datetime.utcnow().replace(microsecond=0)
        post = _post(db, "hot-post", base - timedelta(minutes=30))
        PostMetricCRUD.create(db, post.id, likes=0, shares=0, comments=0, recorded_at=base)

        bootstrap = apply_metric_snapshot_schedule(db, post.id, now=base)

        assert bootstrap.metric_tier == "bootstrap"
        assert bootstrap.next_metric_update == base + timedelta(minutes=15)
        assert bootstrap.last_engagement_velocity is None

        PostMetricCRUD.create(
            db,
            post.id,
            likes=100,
            shares=0,
            comments=0,
            recorded_at=base + timedelta(minutes=30),
        )
        hot = apply_metric_snapshot_schedule(db, post.id, now=base + timedelta(minutes=30))

        assert hot.metric_tier == HOT
        assert hot.last_engagement_velocity == 200
        assert hot.next_metric_update == base + timedelta(minutes=35)
    finally:
        db.close()


def test_cold_post_gets_one_recheck_then_expires():
    db = SessionLocal()
    try:
        base = datetime.utcnow().replace(microsecond=0)
        post = _post(db, "cold-post", base - timedelta(minutes=10))
        PostMetricCRUD.create(db, post.id, likes=1, shares=0, comments=0, recorded_at=base)
        PostMetricCRUD.create(db, post.id, likes=1, shares=0, comments=0, recorded_at=base + timedelta(minutes=15))

        cold = apply_metric_snapshot_schedule(db, post.id, now=base + timedelta(minutes=15))

        assert cold.metric_tier == COLD
        assert cold.cold_check_count == 1
        assert cold.next_metric_update == base + timedelta(minutes=135)

        PostMetricCRUD.create(db, post.id, likes=1, shares=0, comments=0, recorded_at=base + timedelta(minutes=135))
        expired = apply_metric_snapshot_schedule(db, post.id, now=base + timedelta(minutes=135))

        assert expired.metric_tier == EXPIRED
        assert expired.is_tracked is False
        assert expired.next_metric_update is None
    finally:
        db.close()


def test_source_percentile_can_mark_post_hot_below_absolute_threshold():
    db = SessionLocal()
    try:
        base = datetime.utcnow().replace(microsecond=0)
        user = UserCRUD.create(db, username="percentile", email="percentile@example.com", password="secret123")
        source = SourceCRUD.create(
            db,
            user_id=user.id,
            source_type="group",
            facebook_id="percentile-source",
            facebook_url="https://facebook.com/groups/percentile-source",
            source_name="Percentile Source",
        )
        target = None
        for index in range(10):
            post = PostCRUD.create(
                db,
                source_id=source.id,
                facebook_post_id=f"percentile-{index}",
                facebook_url=f"https://facebook.com/posts/percentile-{index}",
                posted_at=base - timedelta(hours=1),
            )
            PostMetricCRUD.create(db, post.id, likes=0, shares=0, comments=0, recorded_at=base)
            PostMetricCRUD.create(
                db,
                post.id,
                likes=10 if index == 9 else 1,
                shares=0,
                comments=0,
                recorded_at=base + timedelta(hours=1),
            )
            if index == 9:
                target = post

        scheduled = apply_metric_snapshot_schedule(db, target.id, now=base + timedelta(hours=1))

        assert scheduled.last_engagement_velocity == 10
        assert scheduled.metric_tier == HOT
    finally:
        db.close()


def test_due_metric_query_returns_only_posts_whose_schedule_arrived():
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        due = _post(db, "due-post", now - timedelta(minutes=30))
        future = _post(db, "future-post", now - timedelta(minutes=30))
        PostCRUD.update(db, due.id, next_metric_update=now - timedelta(minutes=1))
        PostCRUD.update(db, future.id, next_metric_update=now + timedelta(minutes=1))

        selected = PostCRUD.get_due_metric_updates(db, hours=24, limit=10)

        assert [post.id for post in selected] == [due.id]
    finally:
        db.close()


def test_post_api_schema_exposes_metric_schedule_fields():
    db = SessionLocal()
    try:
        post = _post(db, "api-fields", datetime.utcnow() - timedelta(minutes=10))
        payload = PostResponse.model_validate(post).model_dump()

        assert payload["metric_tier"] == "bootstrap"
        assert payload["next_metric_update"] is not None
        assert payload["last_engagement_velocity"] is None
        assert payload["cold_check_count"] == 0
    finally:
        db.close()


def test_three_max_page_misses_stop_tracking_post():
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        post = _post(db, "missed-post", now - timedelta(minutes=30))

        handle_max_page_misses(db, post.source_id, [post.facebook_post_id])
        once = PostCRUD.get_by_id(db, post.id)
        assert once.metric_tier == COLD
        assert once.metric_scan_miss_count == 1
        assert once.is_tracked is True

        handle_max_page_misses(db, post.source_id, [post.facebook_post_id])
        twice = PostCRUD.get_by_id(db, post.id)
        assert twice.metric_tier == COLD
        assert twice.metric_scan_miss_count == 2
        assert twice.is_tracked is True

        handle_max_page_misses(db, post.source_id, [post.facebook_post_id])
        third = PostCRUD.get_by_id(db, post.id)
        assert third.metric_tier == EXPIRED
        assert third.metric_scan_miss_count == 3
        assert third.is_tracked is False
    finally:
        db.close()


def test_metric_scheduling_migration_backfills_legacy_posts_idempotently():
    Base.metadata.drop_all(bind=engine)
    now = datetime.utcnow().replace(microsecond=0)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE posts (
                    id INTEGER PRIMARY KEY,
                    source_id INTEGER NOT NULL,
                    facebook_post_id VARCHAR(100) NOT NULL UNIQUE,
                    facebook_url VARCHAR(500) NOT NULL,
                    content TEXT,
                    media_count INTEGER,
                    has_images BOOLEAN,
                    has_videos BOOLEAN,
                    posted_at DATETIME NOT NULL,
                    created_at DATETIME,
                    is_tracked BOOLEAN,
                    tracking_until DATETIME,
                    is_deleted BOOLEAN,
                    last_metric_update DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE post_metrics (
                    id INTEGER PRIMARY KEY,
                    post_id INTEGER NOT NULL,
                    likes_count INTEGER,
                    shares_count INTEGER,
                    comments_count INTEGER,
                    recorded_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO posts (
                    id, source_id, facebook_post_id, facebook_url, posted_at,
                    is_tracked, is_deleted
                ) VALUES
                    (1, 1, 'recent', 'https://facebook.com/recent', :recent, 1, 0),
                    (2, 1, 'old', 'https://facebook.com/old', :old, 1, 0)
                """
            ),
            {"recent": now - timedelta(hours=1), "old": now - timedelta(hours=25)},
        )

    migrate_post_metric_scheduling_columns()
    migrate_post_metric_scheduling_columns()

    db = SessionLocal()
    try:
        recent = db.query(Post).filter(Post.id == 1).one()
        old = db.query(Post).filter(Post.id == 2).one()
        assert recent.metric_tier == "bootstrap"
        assert recent.is_tracked is True
        assert recent.tracking_until is not None
        assert recent.next_metric_update is not None
        assert old.metric_tier == EXPIRED
        assert old.is_tracked is False
        assert old.next_metric_update is None
    finally:
        db.close()


def test_migrate_pipeline_job_type_update_metric_rebuilds_sqlite_constraint():
    Base.metadata.drop_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE pipeline_jobs (
                    id INTEGER PRIMARY KEY,
                    job_type VARCHAR(20) NOT NULL DEFAULT 'scraper_job'
                        CHECK (job_type IN ('scrape_24h', 'scraper_job', 'post_metric', 'analytics')),
                    source_id INTEGER,
                    session_id INTEGER,
                    status VARCHAR(10) NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'running', 'done', 'failed')),
                    posts_found INTEGER NOT NULL DEFAULT 0,
                    posts_new INTEGER NOT NULL DEFAULT 0,
                    items_total INTEGER NOT NULL DEFAULT 0,
                    items_updated INTEGER NOT NULL DEFAULT 0,
                    items_failed INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT,
                    started_at DATETIME,
                    finished_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO pipeline_jobs (
                    id, job_type, status, posts_found, posts_new,
                    items_total, items_updated, items_failed
                ) VALUES
                    (1, 'post_metric', 'done', 3, 2, 3, 2, 0),
                    (2, 'scraper_job', 'running', 0, 0, 0, 0, 0)
                """
            )
        )

    migrate_pipeline_job_type_update_metric()
    migrate_pipeline_job_type_update_metric()

    with engine.begin() as conn:
        job_types = conn.execute(text("SELECT id, job_type FROM pipeline_jobs ORDER BY id")).all()
        create_sql = conn.execute(
            text(
                """
                SELECT sql
                FROM sqlite_master
                WHERE type = 'table'
                  AND name = 'pipeline_jobs'
                """
            )
        ).scalar()
        conn.execute(
            text(
                """
                INSERT INTO pipeline_jobs (
                    id, job_type, status, posts_found, posts_new,
                    items_total, items_updated, items_failed
                ) VALUES
                    (3, 'update_metric', 'pending', 0, 0, 0, 0, 0)
                """
            )
        )

    assert job_types == [(1, "update_metric"), (2, "scraper_job")]
    assert "update_metric" in create_sql
    assert "post_metric" not in create_sql


def test_migrate_post_metric_job_id_column_adds_nullable_job_link_idempotently():
    Base.metadata.drop_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE pipeline_jobs (
                    id INTEGER PRIMARY KEY,
                    job_type VARCHAR(20) NOT NULL DEFAULT 'scraper_job',
                    status VARCHAR(10) NOT NULL DEFAULT 'pending'
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE post_metrics (
                    id INTEGER PRIMARY KEY,
                    post_id INTEGER NOT NULL,
                    likes_count INTEGER,
                    shares_count INTEGER,
                    comments_count INTEGER,
                    recorded_at DATETIME
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO post_metrics (
                    id, post_id, likes_count, shares_count, comments_count, recorded_at
                ) VALUES (
                    1, 10, 1, 2, 3, CURRENT_TIMESTAMP
                )
                """
            )
        )

    migrate_post_metric_job_id_column()
    migrate_post_metric_job_id_column()

    with engine.begin() as conn:
        columns = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(post_metrics)")).all()
        }
        indexes = {
            row[1]
            for row in conn.execute(text("PRAGMA index_list(post_metrics)")).all()
        }
        legacy_job_id = conn.execute(
            text("SELECT job_id FROM post_metrics WHERE id = 1")
        ).scalar()

    assert "job_id" in columns
    assert "idx_post_metrics_job_time" in indexes
    assert legacy_job_id is None
