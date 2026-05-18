"""
Service layer for source scrape schedule tiers.

Tier calculation intentionally uses analytics_cache only. The sources.member_count
column remains in the schema, but it is not used to calculate suggested tiers.
"""

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session


TIER_CONFIG = [
    {
        "tier": 1,
        "min_posts": 20,
        "min_avg_likes_per_post": 500,
        "interval_minutes": 15,
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
        "min_posts": 1,
        "min_avg_likes_per_post": 0,
        "interval_minutes": 180,
        "label": "Cool",
    },
]


def calculate_tier(source_id: int, db: Session) -> dict:
    """
    Read analytics_cache for the last 7 days and return a suggested tier.

    The tier is based on:
      - avg_posts_per_day = AVG(total_posts)
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

    row = db.execute(
        text(
            """
            SELECT
                COUNT(*)                   AS data_days,
                AVG(total_posts)           AS avg_posts,
                AVG(
                    COALESCE(
                        avg_likes_per_post,
                        CAST(total_likes AS FLOAT) / NULLIF(total_posts, 0)
                    )
                )                          AS avg_likes_per_post
            FROM analytics_cache
            WHERE source_id = :source_id
              AND date >= DATE('now', '-7 days')
            """
        ),
        {"source_id": source_id},
    ).fetchone()

    if not row or row.data_days == 0:
        return {
            "tier": None,
            "interval_minutes": None,
            "label": "Unknown",
            "reason": "Chua co du lieu analytics (analytics_cache trong)",
            "avg_posts_per_day": 0,
            "avg_likes_per_post": 0,
            "data_days": 0,
        }

    avg_posts = row.avg_posts or 0
    avg_likes_per_post = row.avg_likes_per_post or 0
    data_days = row.data_days

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
        reason_parts.append("duoi nguong tier hoat dong")

    return {
        "tier": selected_tier,
        "interval_minutes": interval,
        "label": label,
        "reason": ", ".join(reason_parts),
        "avg_posts_per_day": round(avg_posts, 2),
        "avg_likes_per_post": round(avg_likes_per_post, 2),
        "data_days": data_days,
    }


def apply_schedule(source_id: int, db: Session) -> dict:
    """
    Decide the effective interval (auto or override) and update sources.

    This is the only function that writes schedule_tier and next_scrape.
    """

    source = db.execute(
        text(
            """
            SELECT id, source_name, is_active,
                   schedule_tier,
                   schedule_override_minutes
            FROM sources
            WHERE id = :source_id
            """
        ),
        {"source_id": source_id},
    ).fetchone()

    if not source:
        return {"error": f"source_id={source_id} khong ton tai"}

    is_overridden = source.schedule_override_minutes is not None
    tier_result = None
    applied_interval = None
    applied_tier = None
    reason = ""

    if is_overridden:
        applied_interval = source.schedule_override_minutes
        applied_tier = None
        reason = f"User override: {applied_interval} phut"
        action = "overridden"
    else:
        tier_result = calculate_tier(source_id, db)
        applied_tier = tier_result["tier"]
        applied_interval = tier_result["interval_minutes"]
        reason = tier_result["reason"]

        if applied_tier is None:
            db.execute(
                text(
                    """
                    UPDATE sources
                    SET schedule_tier = 4,
                        is_active     = 0
                    WHERE id = :source_id
                    """
                ),
                {"source_id": source_id},
            )

            db.execute(
                text(
                    """
                    INSERT INTO pipeline_logs (source_id, log_level, message, created_at)
                    VALUES (:source_id, 'WARNING',
                            'auto-paused: low activity - ' || :reason,
                            CURRENT_TIMESTAMP)
                    """
                ),
                {"source_id": source_id, "reason": reason},
            )

            db.commit()

            return {
                "source_id": source_id,
                "source_name": source.source_name,
                "action": "paused",
                "applied_tier": 4,
                "applied_interval_minutes": None,
                "next_scrape": None,
                "is_overridden": False,
                "reason": reason,
                "avg_posts_per_day": tier_result["avg_posts_per_day"],
                "avg_likes_per_post": tier_result["avg_likes_per_post"],
            }

        action = "scheduled"

    next_scrape = datetime.utcnow() + timedelta(minutes=applied_interval)

    db.execute(
        text(
            """
            UPDATE sources
            SET schedule_tier = :tier,
                next_scrape   = :next_scrape,
                is_active     = 1
            WHERE id = :source_id
            """
        ),
        {
            "tier": applied_tier,
            "next_scrape": next_scrape.strftime("%Y-%m-%d %H:%M:%S"),
            "source_id": source_id,
        },
    )

    db.commit()

    result = {
        "source_id": source_id,
        "source_name": source.source_name,
        "action": action,
        "applied_tier": applied_tier,
        "applied_interval_minutes": applied_interval,
        "next_scrape": next_scrape.strftime("%Y-%m-%dT%H:%M:%S"),
        "is_overridden": is_overridden,
        "reason": reason,
    }

    if tier_result:
        result.update(
            {
                "avg_posts_per_day": tier_result["avg_posts_per_day"],
                "avg_likes_per_post": tier_result["avg_likes_per_post"],
                "data_days": tier_result["data_days"],
            }
        )

    return result


def apply_schedule_all(user_id: int, db: Session) -> dict:
    """
    Run apply_schedule() for all sources of a user.

    Sources with a schedule override are skipped.
    """

    sources = db.execute(
        text(
            """
            SELECT id FROM sources
            WHERE user_id = :user_id
            """
        ),
        {"user_id": user_id},
    ).fetchall()

    summary = {
        "processed": 0,
        "skipped_override": 0,
        "changes": [],
        "no_change": 0,
        "tier_summary": {1: 0, 2: 0, 3: 0, 4: 0},
    }

    for row in sources:
        source = db.execute(
            text(
                """
                SELECT schedule_tier, schedule_override_minutes
                FROM sources
                WHERE id = :id
                """
            ),
            {"id": row.id},
        ).fetchone()

        if source.schedule_override_minutes is not None:
            summary["skipped_override"] += 1
            continue

        old_tier = source.schedule_tier
        result = apply_schedule(row.id, db)
        new_tier = result.get("applied_tier") or 4

        summary["processed"] += 1
        summary["tier_summary"][new_tier] = summary["tier_summary"].get(new_tier, 0) + 1

        if old_tier != new_tier:
            summary["changes"].append(
                {
                    "source_id": row.id,
                    "source_name": result.get("source_name"),
                    "old_tier": old_tier,
                    "new_tier": new_tier,
                    "action": result.get("action"),
                }
            )
        else:
            summary["no_change"] += 1

    return summary
