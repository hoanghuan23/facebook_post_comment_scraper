from datetime import datetime, timedelta
from typing import List

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.database.models import Post, PostMetric, Source


def _weighted_engagement(likes: int, shares: int, comments: int) -> int:
    return (likes or 0) + ((comments or 0) * 3) + ((shares or 0) * 5)


def _percentile_ranks(values: List[float]) -> List[float]:
    """Return percentile ranks in [0, 1], using the same rank for ties."""
    if not values:
        return []
    if len(values) == 1:
        return [1.0]

    sorted_values = sorted(values)
    denominator = len(values) - 1
    less_than_counts = {}
    for index, value in enumerate(sorted_values):
        if value not in less_than_counts:
            less_than_counts[value] = index

    return [less_than_counts[value] / denominator for value in values]


def get_trending_posts_for_user(
    db: Session,
    user_id: int,
    limit: int = 10,
    window_hours: int = 24,
    max_post_age_hours: int = 168,
    min_baseline_gap_hours: int = 6,
) -> dict:
    """Rank currently trending posts across all sources owned by a user."""
    if window_hours > max_post_age_hours:
        raise HTTPException(
            status_code=400,
            detail="window_hours must be less than or equal to max_post_age_hours",
        )
    if min_baseline_gap_hours > window_hours:
        raise HTTPException(
            status_code=400,
            detail="min_baseline_gap_hours must be less than or equal to window_hours",
        )

    now = datetime.utcnow()
    post_cutoff = now - timedelta(hours=max_post_age_hours)

    latest_metric_ranked = db.query(
        PostMetric.id.label("metric_id"),
        PostMetric.post_id.label("post_id"),
        PostMetric.likes_count.label("likes_count"),
        PostMetric.shares_count.label("shares_count"),
        PostMetric.comments_count.label("comments_count"),
        PostMetric.recorded_at.label("recorded_at"),
        func.row_number()
        .over(
            partition_by=PostMetric.post_id,
            order_by=[PostMetric.recorded_at.desc(), PostMetric.id.desc()],
        )
        .label("rn"),
    ).subquery()
    latest_metric_sq = (
        db.query(
            latest_metric_ranked.c.metric_id,
            latest_metric_ranked.c.post_id,
            latest_metric_ranked.c.likes_count,
            latest_metric_ranked.c.shares_count,
            latest_metric_ranked.c.comments_count,
            latest_metric_ranked.c.recorded_at,
        )
        .filter(latest_metric_ranked.c.rn == 1)
        .subquery()
    )

    metric_count_sq = (
        db.query(
            PostMetric.post_id.label("post_id"),
            func.count(PostMetric.id).label("metrics_count"),
        )
        .group_by(PostMetric.post_id)
        .subquery()
    )

    candidate_rows = (
        db.query(
            Post,
            Source.id.label("source_id"),
            Source.source_name.label("source_name"),
            latest_metric_sq.c.likes_count.label("latest_likes"),
            latest_metric_sq.c.shares_count.label("latest_shares"),
            latest_metric_sq.c.comments_count.label("latest_comments"),
            latest_metric_sq.c.recorded_at.label("latest_recorded_at"),
            metric_count_sq.c.metrics_count.label("metrics_count"),
        )
        .join(Source, Post.source_id == Source.id)
        .join(latest_metric_sq, latest_metric_sq.c.post_id == Post.id)
        .join(metric_count_sq, metric_count_sq.c.post_id == Post.id)
        .filter(
            Source.user_id == user_id,
            Post.is_tracked.is_(True),
            Post.is_deleted.is_(False),
            Post.posted_at >= post_cutoff,
        )
        .all()
    )

    if not candidate_rows:
        return {"count": 0, "trending_posts": []}

    post_ids = [row.Post.id for row in candidate_rows]
    metrics_by_post = {post_id: [] for post_id in post_ids}
    metric_rows = (
        db.query(PostMetric)
        .filter(PostMetric.post_id.in_(post_ids))
        .order_by(PostMetric.post_id, PostMetric.recorded_at, PostMetric.id)
        .all()
    )
    for metric in metric_rows:
        metrics_by_post.setdefault(metric.post_id, []).append(metric)

    ranked = []
    for row in candidate_rows:
        post = row.Post
        latest_likes = row.latest_likes or 0
        latest_shares = row.latest_shares or 0
        latest_comments = row.latest_comments or 0
        latest_recorded_at = row.latest_recorded_at
        latest_weighted = _weighted_engagement(
            latest_likes, latest_shares, latest_comments
        )

        baseline_target = latest_recorded_at - timedelta(hours=window_hours)
        baseline_latest_allowed = latest_recorded_at - timedelta(
            hours=min_baseline_gap_hours
        )
        eligible_baselines = [
            metric
            for metric in metrics_by_post.get(post.id, [])
            if metric.recorded_at <= baseline_latest_allowed
        ]
        baseline = None
        if eligible_baselines:
            baseline = min(
                eligible_baselines,
                key=lambda metric: (
                    abs((metric.recorded_at - baseline_target).total_seconds()),
                    -metric.recorded_at.timestamp(),
                ),
            )

        if baseline:
            baseline_likes = baseline.likes_count or 0
            baseline_shares = baseline.shares_count or 0
            baseline_comments = baseline.comments_count or 0
            baseline_recorded_at = baseline.recorded_at
            hours_elapsed = max(
                (latest_recorded_at - baseline_recorded_at).total_seconds() / 3600,
                1,
            )
        else:
            baseline_likes = 0
            baseline_shares = 0
            baseline_comments = 0
            baseline_recorded_at = None
            hours_elapsed = max((now - post.posted_at).total_seconds() / 3600, 1)

        growth_likes = max(latest_likes - baseline_likes, 0)
        growth_shares = max(latest_shares - baseline_shares, 0)
        growth_comments = max(latest_comments - baseline_comments, 0)
        baseline_weighted = _weighted_engagement(
            baseline_likes,
            baseline_shares,
            baseline_comments,
        )
        weighted_growth = max(latest_weighted - baseline_weighted, 0)
        engagement_velocity = weighted_growth / hours_elapsed
        age_hours = max((now - post.posted_at).total_seconds() / 3600, 0)

        ranked.append(
            {
                "post_id": post.id,
                "facebook_post_id": post.facebook_post_id,
                "facebook_url": post.facebook_url,
                "content": post.content,
                "posted_at": post.posted_at,
                "source_id": row.source_id,
                "source_name": row.source_name,
                "latest_likes": latest_likes,
                "latest_shares": latest_shares,
                "latest_comments": latest_comments,
                "growth_likes": growth_likes,
                "growth_shares": growth_shares,
                "growth_comments": growth_comments,
                "latest_weighted": latest_weighted,
                "weighted_growth": weighted_growth,
                "engagement_velocity": engagement_velocity,
                "age_hours": age_hours,
                "baseline_recorded_at": baseline_recorded_at,
                "latest_recorded_at": latest_recorded_at,
                "metrics_count": row.metrics_count or 0,
            }
        )

    velocity_ranks = _percentile_ranks([item["engagement_velocity"] for item in ranked])
    growth_ranks = _percentile_ranks([item["weighted_growth"] for item in ranked])
    total_ranks = _percentile_ranks([item["latest_weighted"] for item in ranked])
    age_ranks = _percentile_ranks([item["age_hours"] for item in ranked])

    for index, item in enumerate(ranked):
        item["velocity_rank"] = velocity_ranks[index]
        item["growth_rank"] = growth_ranks[index]
        item["total_rank"] = total_ranks[index]
        item["freshness_rank"] = 1 - age_ranks[index]
        item["trending_score"] = (
            (0.55 * item["velocity_rank"])
            + (0.25 * item["growth_rank"])
            + (0.15 * item["total_rank"])
            + (0.05 * item["freshness_rank"])
        )

    trending = sorted(
        ranked,
        key=lambda item: (
            item["trending_score"],
            item["engagement_velocity"],
            item["weighted_growth"],
            item["posted_at"],
        ),
        reverse=True,
    )[:limit]

    return {"count": len(trending), "trending_posts": trending}
