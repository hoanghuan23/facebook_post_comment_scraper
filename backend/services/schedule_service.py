"""
Service layer for source scrape schedule tiers.

Tier calculation uses post timestamps for posting frequency and analytics_cache
for engagement. The sources.member_count column remains in the schema, but it is
not used to calculate suggested tiers.
"""

from datetime import datetime, timedelta

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from backend.config import settings
from backend.database.models import AnalyticsCache, Post


TIER_CONFIG = [
    {
        "tier": 1,
        "min_posts": 20,
        "min_avg_likes_per_post": 500,
        "interval_minutes": 30,
        "label": "Hot",
    },
    {
        "tier": 2,
        "min_posts": 5,
        "min_avg_likes_per_post": 100,
        "interval_minutes": 60,
        "label": "Warm",
    },
    {
        "tier": 3,
        "min_posts": 3,
        "min_avg_likes_per_post": 0,
        "interval_minutes": 360,
        "label": "Cool",
    },
    {
        "tier": 4,
        "min_posts": 1,
        "min_avg_likes_per_post": 0,
        "interval_minutes": 720,
        "label": "Frozen",
    }
]

TIER_INTERVAL_MINUTES = {
    config["tier"]: config["interval_minutes"] for config in TIER_CONFIG
}


def calculate_tier(source_id: int, db: Session) -> dict:
    """
    Read recent posts and analytics_cache for the last 7 days and return a suggested tier.

    The tier is based on:
      - avg_posts_per_day = count(posts posted in the last 7 days) / 7
      - avg_likes_per_post = AVG(avg_likes_per_post)

    Returns dict:
        {
            "tier": int | None,
            "interval_minutes": int | None,
            "label": str,
            "reason": str,
            "avg_posts_per_day": float,
            "avg_likes_per_post": float,
            "data_days": int,
        }
    """

    now = datetime.utcnow()
    post_cutoff = now - timedelta(days=7)
    analytics_cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=7)

    recent_posts = db.query(func.count(Post.id)).filter(
        Post.source_id == source_id,
        Post.posted_at >= post_cutoff,
    ).scalar() or 0
    analytics_rows = db.query(AnalyticsCache).filter(
        AnalyticsCache.source_id == source_id,
        AnalyticsCache.date >= analytics_cutoff,
    ).all()

    data_days = len(analytics_rows)
    avg_posts = recent_posts / 7
    if data_days == 0:
        return {
            "tier": None,
            "interval_minutes": None,
            "label": "Unknown",
            "reason": "Chua co du lieu analytics (analytics_cache trong)",
            "avg_posts_per_day": round(avg_posts, 2),
            "avg_likes_per_post": 0,
            "data_days": 0,
        }

    likes_per_post_values = []
    for cache in analytics_rows:
        if cache.avg_likes_per_post is not None:
            likes_per_post_values.append(cache.avg_likes_per_post)
        elif cache.total_posts:
            likes_per_post_values.append(cache.total_likes / cache.total_posts)
    avg_likes_per_post = (
        sum(likes_per_post_values) / len(likes_per_post_values)
        if likes_per_post_values
        else 0
    )

    selected_tier = None
    interval = None
    label = "Frozen"
    reason_parts = [
        f"avg {avg_posts:.1f} posts/ngay ({data_days} ngay gan nhat)",
        f"avg_likes_per_post={avg_likes_per_post:.0f}",
    ]

    for cfg in TIER_CONFIG:
        if (
            avg_posts >= cfg["min_posts"]
            and avg_likes_per_post >= cfg["min_avg_likes_per_post"]
        ):
            selected_tier = cfg["tier"]
            interval = cfg["interval_minutes"]
            label = cfg["label"]
            break

    if selected_tier is None:
        selected_tier = 4
        interval = TIER_INTERVAL_MINUTES[selected_tier]
        reason_parts.append("duoi nguong hoat dong; giu theo doi o tier 4")

    return {
        "tier": selected_tier,
        "interval_minutes": interval,
        "label": label,
        "reason": ", ".join(reason_parts),
        "avg_posts_per_day": round(avg_posts, 2),
        "avg_likes_per_post": round(avg_likes_per_post, 2),
        "data_days": data_days,
    }


def effective_interval_seconds(schedule_tier: int | None, override_minutes: int | None) -> int:
    """Return the scrape interval selected by override, tier, or bootstrap default."""
    if override_minutes is not None:
        return override_minutes * 60
    if schedule_tier in TIER_INTERVAL_MINUTES:
        return TIER_INTERVAL_MINUTES[schedule_tier] * 60
    return settings.TASK_SCRAPE_NEW_POSTS_INTERVAL


def effective_interval_minutes(schedule_tier: int | None, override_minutes: int | None) -> float:
    """Return the effective scrape interval in minutes for API responses."""
    return effective_interval_seconds(schedule_tier, override_minutes) / 60


def schedule_next_scrape(source_id: int, db: Session) -> dict:
    """Schedule the next scrape without calculating or changing analytics tier."""
    source = db.execute(
        text(
            """
            SELECT id, source_name, schedule_tier, schedule_override_minutes
            FROM sources
            WHERE id = :source_id
            """
        ),
        {"source_id": source_id},
    ).fetchone()
    if not source:
        return {"error": f"source_id={source_id} khong ton tai"}

    interval_seconds = effective_interval_seconds(
        source.schedule_tier,
        source.schedule_override_minutes,
    )
    next_scrape = datetime.utcnow() + timedelta(seconds=interval_seconds)
    db.execute(
        text(
            """
            UPDATE sources
            SET next_scrape = :next_scrape
            WHERE id = :source_id
            """
        ),
        {
            "source_id": source_id,
            "next_scrape": next_scrape.strftime("%Y-%m-%d %H:%M:%S"),
        },
    )
    db.commit()

    return {
        "source_id": source_id,
        "source_name": source.source_name,
        "applied_tier": source.schedule_tier,
        "applied_interval_minutes": interval_seconds / 60,
        "next_scrape": next_scrape.strftime("%Y-%m-%dT%H:%M:%S"),
        "is_overridden": source.schedule_override_minutes is not None,
    }


def apply_analytics_schedule(source_id: int, db: Session) -> dict:
    """Persist the latest analytics tier and reschedule automatic sources."""
    source = db.execute(
        text(
            """
            SELECT id, source_name, schedule_override_minutes
            FROM sources
            WHERE id = :source_id
            """
        ),
        {"source_id": source_id},
    ).fetchone()
    if not source:
        return {"error": f"source_id={source_id} khong ton tai"}

    tier_result = calculate_tier(source_id, db)
    applied_tier = tier_result["tier"]
    db.execute(
        text(
            """
            UPDATE sources
            SET schedule_tier = :tier
            WHERE id = :source_id
            """
        ),
        {"tier": applied_tier, "source_id": source_id},
    )
    db.commit()

    schedule_result = None
    if source.schedule_override_minutes is None:
        schedule_result = schedule_next_scrape(source_id, db)

    return {
        "source_id": source_id,
        "source_name": source.source_name,
        "applied_tier": applied_tier,
        "applied_interval_minutes": (
            schedule_result["applied_interval_minutes"]
            if schedule_result
            else source.schedule_override_minutes
        ),
        "next_scrape": schedule_result["next_scrape"] if schedule_result else None,
        "is_overridden": source.schedule_override_minutes is not None,
        "reason": tier_result["reason"],
        "avg_posts_per_day": tier_result["avg_posts_per_day"],
        "avg_likes_per_post": tier_result["avg_likes_per_post"],
        "data_days": tier_result["data_days"],
    }
