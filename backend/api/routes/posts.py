# Posts management routes
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.api.auth import get_current_user
from backend.database.crud import (
    CommentCRUD,
    PostCRUD,
    PostMetricCRUD,
    SourceCRUD,
    calculate_engagement_growth,
)
from backend.database.db import get_db
from backend.database.models import Post, Source, User
from backend.database.schemas import PostWithMetrics, PostResponse

router = APIRouter()


@router.get("/", response_model=List[PostResponse])
async def list_posts(
    skip: int = 0,
    limit: int = 20,
    source_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List posts for current user."""
    if source_id is not None:
        source = SourceCRUD.get_by_id(db, source_id)
        if not source or source.user_id != current_user.id:
            raise HTTPException(status_code=404, detail="Source not found")
        return PostCRUD.get_by_source(db, source_id, skip=skip, limit=limit)

    return (
        db.query(Post)
        .join(Source, Post.source_id == Source.id)
        .filter(Source.user_id == current_user.id, Post.is_tracked.is_(True))
        .order_by(Post.posted_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


@router.get("/{post_id}", response_model=PostWithMetrics)
async def get_post(
    post_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get post detail with metrics history."""
    post = (
        db.query(Post)
        .join(Source, Post.source_id == Source.id)
        .filter(Post.id == post_id, Source.user_id == current_user.id)
        .first()
    )
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    metrics = PostMetricCRUD.get_by_post(db, post_id, limit=100)
    payload = PostResponse.model_validate(post).model_dump()
    payload["metrics_history"] = metrics
    return payload


@router.get("/{post_id}/metrics")
async def get_post_metrics(
    post_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get metrics history for a post."""
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
        "metrics_count": len(metrics),
        "metrics": metrics,
        "growth": growth,
    }


@router.get("/{post_id}/comments")
async def get_post_comments(
    post_id: int,
    skip: int = 0,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get comments for a post."""
    post = (
        db.query(Post)
        .join(Source, Post.source_id == Source.id)
        .filter(Post.id == post_id, Source.user_id == current_user.id)
        .first()
    )
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    comments = CommentCRUD.get_by_post(db, post_id, skip=skip, limit=limit, top_level_only=True)
    return {
        "post_id": post_id,
        "comments_count": CommentCRUD.count_by_post(db, post_id),
        "comments": comments,
    }


@router.put("/{post_id}", response_model=PostResponse)
async def update_post(
    post_id: int,
    post_data: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update post settings."""
    post = (
        db.query(Post)
        .join(Source, Post.source_id == Source.id)
        .filter(Post.id == post_id, Source.user_id == current_user.id)
        .first()
    )
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    updated_post = PostCRUD.update(db, post_id, **post_data)
    if not updated_post:
        raise HTTPException(status_code=500, detail="Failed to update post")

    return updated_post


@router.delete("/{post_id}")
async def delete_post(
    post_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Untrack a post (soft delete)."""
    post = (
        db.query(Post)
        .join(Source, Post.source_id == Source.id)
        .filter(Post.id == post_id, Source.user_id == current_user.id)
        .first()
    )
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if not PostCRUD.delete(db, post_id):
        raise HTTPException(status_code=500, detail="Failed to untrack post")

    return {"message": "Post untracked successfully"}
