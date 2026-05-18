import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

from backend.api.routes.analytics import get_trending_posts
from backend.api.routes.posts import router as posts_router
from backend.database.crud import PostCRUD, SourceCRUD, UserCRUD
from backend.database.db import SessionLocal, engine
from backend.database.models import Base, PostMetric


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def teardown_function():
    Base.metadata.drop_all(bind=engine)


def _create_user_source(db, username="user"):
    user = UserCRUD.create(
        db,
        username=username,
        email=f"{username}@example.com",
        password="secret123",
    )
    source = SourceCRUD.create(
        db,
        user_id=user.id,
        source_type="group",
        facebook_id=f"group-{username}",
        facebook_url=f"https://facebook.com/groups/group-{username}",
        source_name=f"Group {username}",
    )
    return user, source


def _create_post(db, source_id, post_id, posted_at):
    return PostCRUD.create(
        db,
        source_id=source_id,
        facebook_post_id=post_id,
        facebook_url=f"https://facebook.com/posts/{post_id}",
        posted_at=posted_at,
        content=f"Post {post_id}",
    )


def _add_metric(db, post_id, likes, shares, comments, recorded_at):
    metric = PostMetric(
        post_id=post_id,
        likes_count=likes,
        shares_count=shares,
        comments_count=comments,
        recorded_at=recorded_at,
    )
    db.add(metric)
    db.commit()
    db.refresh(metric)
    return metric


def test_posts_router_no_longer_exposes_trending_route():
    assert "/trending" not in {route.path for route in posts_router.routes}


def test_trending_validates_window_parameters():
    db = SessionLocal()
    try:
        user, _ = _create_user_source(db)

        with pytest.raises(HTTPException) as window_error:
            asyncio.run(
                get_trending_posts(
                    limit=10,
                    window_hours=25,
                    max_post_age_hours=24,
                    min_baseline_gap_hours=6,
                    current_user=user,
                    db=db,
                )
            )
        assert window_error.value.status_code == 400

        with pytest.raises(HTTPException) as gap_error:
            asyncio.run(
                get_trending_posts(
                    limit=10,
                    window_hours=6,
                    max_post_age_hours=24,
                    min_baseline_gap_hours=7,
                    current_user=user,
                    db=db,
                )
            )
        assert gap_error.value.status_code == 400
    finally:
        db.close()


def test_trending_selects_baseline_near_window_and_respects_min_gap():
    db = SessionLocal()
    try:
        user, source = _create_user_source(db)
        now = datetime.utcnow()
        post = _create_post(db, source.id, "baseline-post", now - timedelta(hours=2))
        latest_at = now - timedelta(minutes=5)
        expected_baseline_at = latest_at - timedelta(hours=23)

        _add_metric(
            db, post.id, likes=1, shares=0, comments=0, recorded_at=expected_baseline_at
        )
        _add_metric(
            db,
            post.id,
            likes=50,
            shares=0,
            comments=0,
            recorded_at=latest_at - timedelta(hours=2),
        )
        _add_metric(db, post.id, likes=100, shares=0, comments=0, recorded_at=latest_at)

        result = asyncio.run(
            get_trending_posts(
                limit=10,
                window_hours=24,
                max_post_age_hours=168,
                min_baseline_gap_hours=6,
                current_user=user,
                db=db,
            )
        )

        item = result["trending_posts"][0]
        assert item["baseline_recorded_at"] == expected_baseline_at
        assert item["growth_likes"] == 99
    finally:
        db.close()


def test_trending_ranks_growth_over_stale_total_engagement():
    db = SessionLocal()
    try:
        user, source = _create_user_source(db)
        now = datetime.utcnow()
        baseline_at = now - timedelta(hours=24)
        latest_at = now - timedelta(minutes=1)

        stale = _create_post(db, source.id, "stale-big", now - timedelta(hours=12))
        _add_metric(
            db, stale.id, likes=5000, shares=0, comments=0, recorded_at=baseline_at
        )
        _add_metric(
            db, stale.id, likes=5000, shares=0, comments=0, recorded_at=latest_at
        )

        growing = _create_post(
            db, source.id, "growing-small", now - timedelta(hours=12)
        )
        _add_metric(
            db, growing.id, likes=0, shares=0, comments=0, recorded_at=baseline_at
        )
        _add_metric(
            db, growing.id, likes=150, shares=5, comments=10, recorded_at=latest_at
        )

        result = asyncio.run(
            get_trending_posts(
                limit=10,
                window_hours=24,
                max_post_age_hours=168,
                min_baseline_gap_hours=6,
                current_user=user,
                db=db,
            )
        )

        assert result["trending_posts"][0]["facebook_post_id"] == "growing-small"
        assert result["trending_posts"][0]["engagement_velocity"] > 0
        assert result["trending_posts"][1]["facebook_post_id"] == "stale-big"
    finally:
        db.close()


def test_trending_excludes_deleted_posts():
    db = SessionLocal()
    try:
        user, source = _create_user_source(db)
        now = datetime.utcnow()
        baseline_at = now - timedelta(hours=24)
        latest_at = now - timedelta(minutes=1)

        deleted = _create_post(db, source.id, "deleted-high", now - timedelta(hours=2))
        PostCRUD.update(db, deleted.id, is_deleted=True, is_tracked=True)
        _add_metric(
            db, deleted.id, likes=0, shares=0, comments=0, recorded_at=baseline_at
        )
        _add_metric(
            db,
            deleted.id,
            likes=10000,
            shares=1000,
            comments=1000,
            recorded_at=latest_at,
        )

        active = _create_post(db, source.id, "active-low", now - timedelta(hours=2))
        _add_metric(
            db, active.id, likes=0, shares=0, comments=0, recorded_at=baseline_at
        )
        _add_metric(db, active.id, likes=1, shares=0, comments=0, recorded_at=latest_at)

        result = asyncio.run(
            get_trending_posts(
                limit=10,
                window_hours=24,
                max_post_age_hours=168,
                min_baseline_gap_hours=6,
                current_user=user,
                db=db,
            )
        )

        assert [item["facebook_post_id"] for item in result["trending_posts"]] == [
            "active-low"
        ]
    finally:
        db.close()


def test_trending_only_returns_posts_owned_by_current_user():
    db = SessionLocal()
    try:
        user, source = _create_user_source(db, username="owner")
        other_user, other_source = _create_user_source(db, username="other")
        now = datetime.utcnow()

        owned = _create_post(db, source.id, "owned", now - timedelta(hours=3))
        _add_metric(
            db,
            owned.id,
            likes=0,
            shares=0,
            comments=0,
            recorded_at=now - timedelta(hours=24),
        )
        _add_metric(db, owned.id, likes=10, shares=0, comments=0, recorded_at=now)

        other = _create_post(db, other_source.id, "not-owned", now - timedelta(hours=3))
        _add_metric(
            db,
            other.id,
            likes=0,
            shares=0,
            comments=0,
            recorded_at=now - timedelta(hours=24),
        )
        _add_metric(db, other.id, likes=9999, shares=999, comments=999, recorded_at=now)

        result = asyncio.run(
            get_trending_posts(
                limit=10,
                window_hours=24,
                max_post_age_hours=168,
                min_baseline_gap_hours=6,
                current_user=user,
                db=db,
            )
        )

        assert [item["facebook_post_id"] for item in result["trending_posts"]] == [
            "owned"
        ]
        assert other_user.id != user.id
    finally:
        db.close()
