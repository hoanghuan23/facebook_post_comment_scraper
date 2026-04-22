# Analytics routes
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import List

from backend.database.db import get_db
from backend.database.models import User
from backend.database.crud import (
    SourceCRUD, PostCRUD, AnalyticsCRUD, get_user_stats, get_source_stats
)
from backend.api.auth import get_current_user

router = APIRouter()


@router.get("/summary")
async def get_analytics_summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get overall analytics summary for all sources"""
    
    stats = get_user_stats(db, current_user.id)
    
    return {
        "user_id": current_user.id,
        "total_sources": stats['sources_count'],
        "total_posts": stats['posts_count'],
        "total_engagement": stats['total_engagement'],
        "total_likes": stats['total_likes'],
        "total_shares": stats['total_shares'],
        "total_comments": stats['total_comments'],
    }


@router.get("/source/{source_id}")
async def get_source_analytics(
    source_id: int,
    days: int = 30,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get analytics for a specific source"""
    
    source = SourceCRUD.get_by_id(db, source_id)
    
    if not source or source.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Source not found")
    
    stats = get_source_stats(db, source_id)
    
    # Get analytics cache for the period
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
    from backend.database.crud import PostMetricCRUD, calculate_engagement_growth
    from backend.database.models import Post, Source
    
    post = db.query(Post).join(Source).filter(
        Post.id == post_id,
        Source.user_id == current_user.id,
    ).first()
    
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    metrics = PostMetricCRUD.get_by_post(db, post_id)
    growth = calculate_engagement_growth(db, post_id)
    
    from backend.database.models import Post, Source
    from sqlalchemy import desc
    
    # Get all sources for user
    sources = SourceCRUD.get_by_user(db, current_user.id)
    source_ids = [s.id for s in sources]
    
    if not source_ids:
        return {"trending_posts": []}
    
    # Get recent posts and sort by engagement
    recent_posts = PostCRUD.get_recent_posts(db, hours=24, limit=limit * 2)
    
    # Filter by user's sources
    user_posts = [p for p in recent_posts if p.source_id in source_ids]
    
    # Sort by engagement rate
    user_posts_with_engagement = [
        {
            "post_id": p.id,
            "facebook_post_id": p.facebook_post_id,
            "url": p.facebook_url,
            "posted_at": p.posted_at,
            "likes": p.current_likes,
            "shares": p.current_shares,
            "comments": p.current_comments,
            "total_engagement": p.current_likes + p.current_shares + p.current_comments,
            "engagement_velocity": (p.current_likes + p.current_shares + p.current_comments) / max(1, (datetime.utcnow() - p.posted_at).total_seconds() / 3600),
        }
        for p in user_posts
    ]
    
    # Sort by engagement velocity (engagement per hour)
    trending = sorted(user_posts_with_engagement, key=lambda x: x['engagement_velocity'], reverse=True)[:limit]
    
    return {
        "count": len(trending),
        "trending_posts": trendingth,
        "current_metrics": {
            "likes": post.current_likes,
            "shares": post.current_shares,
            "comments": post.current_comments,
            "views": post.current_views,
        },
    stats = get_user_stats(db, current_user.id)
    
    # Get analytics for last 7 days
    start_date = datetime.utcnow() - timedelta(days=7)
    end_date = datetime.utcnow()
    
    sources = SourceCRUD.get_by_user(db, current_user.id)
    
    growth_data = []
    for source in sources:
        daily_analytics = AnalyticsCRUD.get_date_range(db, source.id, start_date, end_date)
        
        if len(daily_analytics) >= 2:
            first_day = daily_analytics[0]
            lSourceCRUD.get_by_id(db, source_id)
    
    if not source or source.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Source not found")
    
    # TODO: Generate export file based on format
    # For now, return metadata about what would be exported
    
    posts = PostCRUD.get_by_source(db, source_id, limit=1000)
    
    return {
        "status": "ready_for_export",
        "format": format,
        "source_id": source_id,
        "source_name": source.source_name,
        "posts_count": len(posts),
        "total_engagement": sum(p.current_likes + p.current_shares + p.current_comments for p in posts),
        "message": "Export functionality to be implemented"
        "period_days": 7,
        "growth_data": growth_data,
        "overall_stats": stats,
    
            "comments": post.initial_comments,
        },
    
    return {"message": "Not yet implemented"}


@router.get("/trending")
async def get_trending_posts(
    limit: int = 10,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get trending posts from user's sources"""
    
    # TODO: Calculate engagement velocity and rank posts
    
    return {
        "message": "Not yet implemented",
        "limit": limit,
    }


@router.get("/growth")
async def get_growth_analytics(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get growth rate analysis"""
    
    # TODO: Calculate growth trends over time
    
    return {"message": "Not yet implemented"}


@router.post("/export")
async def export_analytics(
    source_id: int,
    format: str = "csv",  # csv, json, pdf
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Export analytics data"""
    
    source = db.query(Source).filter(
        Source.id == source_id,
        Source.user_id == current_user.id,
    ).first()
    
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    
    # TODO: Generate export file
    
    return {
        "message": "Not yet implemented",
        "format": format,
    }


@router.get("/health")
async def analytics_health(
    current_user: User = Depends(get_current_user),
):
    """Analytics service health check"""
    
    return {
        "status": "ok",
        "service": "analytics",
        "timestamp": datetime.utcnow().isoformat(),
    }
