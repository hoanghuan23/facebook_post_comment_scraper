import math
from datetime import datetime, timedelta
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from backend.database.models import Post, PostMetric


BOOTSTRAP = "bootstrap"
HOT = "hot"
WARM = "warm"
COLD = "cold"
EXPIRED = "expired"

TRACKING_HOURS = 24
BOOTSTRAP_MINUTES = 15
RETRY_MINUTES = 15
COLD_RECHECK_MINUTES = 120
MIN_SOURCE_BASELINE_POSTS = 10
SOURCE_BASELINE_DAYS = 7
HOT_VELOCITY_THRESHOLD = 100.0
WARM_VELOCITY_THRESHOLD = 20.0


def weighted_engagement(metric: PostMetric) -> int:
    return (
        (metric.likes_count or 0)
        + (3 * (metric.comments_count or 0))
        + (5 * (metric.shares_count or 0))
    )


def _velocity(newer: PostMetric, older: PostMetric) -> float:
    elapsed_hours = (newer.recorded_at - older.recorded_at).total_seconds() / 3600
    if elapsed_hours <= 0:
        return 0.0
    growth = max(weighted_engagement(newer) - weighted_engagement(older), 0)
    return growth / elapsed_hours


def _nearest_rank_percentile(values: list[float], percentile: float) -> Optional[float]:
    if not values:
        return None
    rank = max(1, math.ceil(percentile * len(values)))
    return sorted(values)[rank - 1]


def _latest_source_velocities(db: Session, source_id: int, now: datetime) -> list[float]:
    cutoff = now - timedelta(days=SOURCE_BASELINE_DAYS)
    rows = (
        db.query(PostMetric)
        .join(Post, PostMetric.post_id == Post.id)
        .filter(
            Post.source_id == source_id,
            Post.is_deleted.is_(False),
            PostMetric.recorded_at >= cutoff,
        )
        .order_by(PostMetric.post_id, PostMetric.recorded_at.desc(), PostMetric.id.desc())
        .all()
    )
    metrics_by_post: dict[int, list[PostMetric]] = {}
    for metric in rows:
        post_metrics = metrics_by_post.setdefault(metric.post_id, [])
        if len(post_metrics) < 2:
            post_metrics.append(metric)
    return [
        _velocity(metrics[0], metrics[1])
        for metrics in metrics_by_post.values()
        if len(metrics) >= 2
    ]


def _tracking_deadline(post: Post) -> datetime:
    return post.tracking_until or (post.posted_at + timedelta(hours=TRACKING_HOURS))


def expire_post(post: Post) -> None:
    post.metric_tier = EXPIRED
    post.is_tracked = False
    post.next_metric_update = None


def _next_interval_minutes(tier: str, age_hours: float) -> int:
    if tier == HOT:
        if age_hours < 2:
            return 5
        if age_hours < 6:
            return 10
        return 30
    if tier == WARM:
        if age_hours < 2:
            return 15
        if age_hours < 6:
            return 30
        return 60
    return COLD_RECHECK_MINUTES


def apply_metric_snapshot_schedule(
    db: Session,
    post_id: int,
    now: Optional[datetime] = None,
) -> Optional[Post]:
    """Classify and schedule a post after a new metric snapshot is stored."""
    now = now or datetime.utcnow()
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        return None

    post.tracking_until = _tracking_deadline(post)
    if now >= post.tracking_until or now >= post.posted_at + timedelta(hours=TRACKING_HOURS):
        expire_post(post)
        db.commit()
        db.refresh(post)
        return post

    latest_metrics = (
        db.query(PostMetric)
        .filter(PostMetric.post_id == post.id)
        .order_by(PostMetric.recorded_at.desc(), PostMetric.id.desc())
        .limit(2)
        .all()
    )
    post.metric_scan_miss_count = 0
    if len(latest_metrics) < 2:
        post.metric_tier = BOOTSTRAP
        post.last_engagement_velocity = None
        post.cold_check_count = 0
        snapshot_time = latest_metrics[0].recorded_at if latest_metrics else now
        post.next_metric_update = snapshot_time + timedelta(minutes=BOOTSTRAP_MINUTES)
        db.commit()
        db.refresh(post)
        return post

    velocity = _velocity(latest_metrics[0], latest_metrics[1])
    post.last_engagement_velocity = velocity
    source_velocities = _latest_source_velocities(db, post.source_id, now)
    p70 = None
    p90 = None
    if len(source_velocities) >= MIN_SOURCE_BASELINE_POSTS:
        p70 = _nearest_rank_percentile(source_velocities, 0.70)
        p90 = _nearest_rank_percentile(source_velocities, 0.90)

    is_hot = velocity >= HOT_VELOCITY_THRESHOLD or (
        velocity > 0 and p90 is not None and p90 > 0 and velocity >= p90
    )
    is_warm = velocity >= WARM_VELOCITY_THRESHOLD or (
        velocity > 0 and p70 is not None and p70 > 0 and velocity >= p70
    )
    if is_hot:
        next_tier = HOT
    elif is_warm:
        next_tier = WARM
    else:
        next_tier = COLD

    if next_tier == COLD:
        if post.metric_tier == COLD and (post.cold_check_count or 0) >= 1:
            expire_post(post)
            db.commit()
            db.refresh(post)
            return post
        post.cold_check_count = (post.cold_check_count or 0) + 1
    else:
        post.cold_check_count = 0

    post.metric_tier = next_tier
    age_hours = max((now - post.posted_at).total_seconds() / 3600, 0)
    post.next_metric_update = now + timedelta(minutes=_next_interval_minutes(next_tier, age_hours))
    db.commit()
    db.refresh(post)
    return post


def defer_metric_updates(
    db: Session,
    source_id: int,
    facebook_post_ids: Iterable[str],
    minutes: int = RETRY_MINUTES,
) -> None:
    post_ids = {str(post_id) for post_id in facebook_post_ids if post_id}
    if not post_ids:
        return
    posts = db.query(Post).filter(
        Post.source_id == source_id,
        Post.facebook_post_id.in_(post_ids),
        Post.is_tracked.is_(True),
    ).all()
    retry_at = datetime.utcnow() + timedelta(minutes=minutes)
    for post in posts:
        post.next_metric_update = retry_at
    db.commit()


def handle_max_page_misses(
    db: Session,
    source_id: int,
    facebook_post_ids: Iterable[str],
) -> None:
    post_ids = {str(post_id) for post_id in facebook_post_ids if post_id}
    if not post_ids:
        return
    posts = db.query(Post).filter(
        Post.source_id == source_id,
        Post.facebook_post_id.in_(post_ids),
        Post.is_tracked.is_(True),
    ).all()
    now = datetime.utcnow()
    for post in posts:
        post.metric_scan_miss_count = (post.metric_scan_miss_count or 0) + 1
        if post.metric_scan_miss_count >= 2:
            expire_post(post)
        else:
            post.metric_tier = COLD
            post.next_metric_update = now + timedelta(minutes=COLD_RECHECK_MINUTES)
    db.commit()
