# Analytics routes
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.api.auth import get_current_user
from backend.database.crud import (
    AnalyticsCRUD,
    PostCRUD,
    PostMetricCRUD,
    SourceCRUD,
    calculate_engagement_growth,
    get_source_stats,
    get_user_stats,
)
from backend.database.db import get_db
from backend.database.models import Post, Source, User
from backend.services.trending_service import get_trending_posts_for_user

router = APIRouter()


@router.get("/summary")
async def get_analytics_summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get overall analytics summary for all sources."""
    stats = get_user_stats(db, current_user.id)
    return {
        "user_id": current_user.id,
        "total_sources": stats["sources_count"],
        "total_posts": stats["posts_count"],
        "total_engagement": stats["total_engagement"],
        "total_likes": stats["total_likes"],
        "total_shares": stats["total_shares"],
        "total_comments": stats["total_comments"],
    }


@router.get("/source/{source_id}")
async def get_source_analytics(
    source_id: int,
    days: int = 30,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get analytics for a specific source."""
    source = SourceCRUD.get_by_id(db, source_id)
    if not source or source.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Source not found")

    stats = get_source_stats(db, source_id)
    start_date = datetime.utcnow() - timedelta(days=days)
    end_date = datetime.utcnow()
    daily_analytics = AnalyticsCRUD.get_date_range(db, source_id, start_date, end_date)

    return {
        "source_id": source_id,
        "source_name": source.source_name,
        "period_days": days,
        "statistics": stats,
        "daily_analytics_count": len(daily_analytics),
        "daily_analytics": daily_analytics,
    }


@router.get("/posts/{post_id}")
async def get_post_analytics(
    post_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get metric growth for a specific post."""
    post = (
        db.query(Post)
        .join(Source, Post.source_id == Source.id)
        .filter(Post.id == post_id, Source.user_id == current_user.id)
        .first()
    )
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    metrics = PostMetricCRUD.get_by_post(db, post_id)
    growth = calculate_engagement_growth(db, post_id)

    return {
        "post_id": post_id,
        "facebook_post_id": post.facebook_post_id,
        "posted_at": post.posted_at,
        "metrics_snapshots": len(metrics),
        "growth": growth,
        "current_metrics": {
            "likes": post.current_likes,
            "shares": post.current_shares,
            "comments": post.current_comments,
        },
        "initial_metrics": {
            "likes": post.initial_likes,
            "shares": post.initial_shares,
            "comments": post.initial_comments,
        },
    }


@router.get("/trending")
async def get_trending_posts(
    limit: int = Query(default=10, ge=1, le=100),
    window_hours: int = Query(default=24, ge=1, le=168),
    max_post_age_hours: int = Query(default=168, ge=1, le=720),
    min_baseline_gap_hours: int = Query(default=6, ge=1, le=48),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get trending posts from user's sources."""
    return get_trending_posts_for_user(
        db=db,
        user_id=current_user.id,
        limit=limit,
        window_hours=window_hours,
        max_post_age_hours=max_post_age_hours,
        min_baseline_gap_hours=min_baseline_gap_hours,
    )


@router.get("/growth")
async def get_growth_analytics(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get 7-day growth snapshot across sources."""
    stats = get_user_stats(db, current_user.id)
    start_date = datetime.utcnow() - timedelta(days=7)
    end_date = datetime.utcnow()

    growth_data = []
    for source in SourceCRUD.get_by_user(db, current_user.id):
        daily = AnalyticsCRUD.get_date_range(db, source.id, start_date, end_date)
        if len(daily) < 2:
            continue

        first_day = daily[0]
        last_day = daily[-1]
        growth_data.append(
            {
                "source_id": source.id,
                "source_name": source.source_name,
                "likes_growth": last_day.total_likes - first_day.total_likes,
                "shares_growth": last_day.total_shares - first_day.total_shares,
                "comments_growth": last_day.total_comments - first_day.total_comments,
                "growth_rate": last_day.growth_rate,
            }
        )

    return {"period_days": 7, "growth_data": growth_data, "overall_stats": stats}


@router.post("/export")
async def export_analytics(
    source_id: int,
    format: str = "csv",  # csv, json, pdf
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Export analytics data (metadata response for now)."""
    source = SourceCRUD.get_by_id(db, source_id)
    if not source or source.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Source not found")

    posts = PostCRUD.get_by_source(db, source_id, limit=1000)
    total_engagement = sum(
        p.current_likes + p.current_shares + p.current_comments for p in posts
    )

    return {
        "status": "ready_for_export",
        "format": format,
        "source_id": source_id,
        "source_name": source.source_name,
        "posts_count": len(posts),
        "total_engagement": total_engagement,
        "message": "Export functionality to be implemented",
    }


@router.get("/health")
async def analytics_health(
    current_user: User = Depends(get_current_user),
):
    """Analytics service health check."""
    return {
        "status": "ok",
        "service": "analytics",
        "timestamp": datetime.utcnow().isoformat(),
    }
