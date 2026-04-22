# Posts management routes
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from backend.database.db import get_db
from backend.database.models import User
from backend.database.schemas import PostResponse, PostWithMetrics
from backend.database.crud import PostCRUD, CommentCRUD, PostMetricCRUD, SourceCRUD, calculate_engagement_growth
from backend.api.auth import get_current_user

router = APIRouter()


@router.get("/", response_model=List[PostResponse])
async def list_posts(
    skip: int = 0,
    limit: int = 20,
    source_id: int = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all posts for current user"""
    
    if source_id:
        # Verify user owns the source
        source = SourceCRUD.get_by_id(db, source_id)
        if not source or source.user_id != current_user.id:
            raise HTTPException(status_code=404, detail="Source not found")
        
        posts = PostCRUD.get_by_source(db, source_id, skip=skip, limit=limit)
    else:
        # Get all posts from all user's sources
        sources = SourceCRUD.get_by_user(db, current_user.id)
        source_ids = [s.id for s in sources]
        
        if not source_ids:
            return []
        
        # Get posts from all sources
        from sqlalchemy import and_
        from backend.database.models import Post
        posts = db.query(Post).filter(
            and_(Post.source_id.in_(source_ids), Post.is_tracked == True)
        ).offset(skip).limit(limit).all()
    
    return posts


@router.get("/{post_id}", response_model=PostWithMetrics)
async def get_post(
    post_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get detailed post information with metrics history"""
    from backend.database.models import Post, Source
    
    post = db.query(Post).join(Source).filter(
        Post.id == post_id,
        Source.user_id == current_user.id,
    ).first()
    
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    # Get metrics history
    metrics = PostMetricCRUD.get_by_post(db, post_id, limit=100)
    
    response = PostWithMetrics.from_orm(post)
    response.metrics_history = [PostMetricCRUD.get_by_post.__dict__ for m in metrics]
    
    return response


@roufrom backend.database.models import Post, Source
    
    post = db.query(Post).join(Source).filter(
        Post.id == post_id,
        Source.user_id == current_user.id,
    ).first()
    
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
    
    
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    # TODO: Return metrics history with timestamps
    
    return {"message": "Not yet implemented"}


@router.get("/{post_id}/comments")
asynfrom backend.database.models import Post, Source
    
    post = db.query(Post).join(Source).filter(
        Post.id == post_id,
        Source.user_id == current_user.id,
    ).first()
    
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    comments = CommentCRUD.get_by_post(db, post_id, skip=skip, limit=limit, top_level_only=True)
    
    return {
        "post_id": post_id,
        "comments_count": CommentCRUD.count_by_post(db, post_id),
        "comments": comments,
    
    ).first()
    
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    # TODO: Return comments
    
    return {"message": "Not yet implemented"}


@router.put("/{post_id}")
async def update_post(
    post_id: int,
    from backend.database.models import Post, Source
    
    post = db.query(Post).join(Source).filter(
        Post.id == post_id,
        Source.user_id == current_user.id,
    ).first()
    
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    updated_post = PostCRUD.update(db, post_id, **post_data)
    
    return PostResponse.from_orm(updated_lue)
    
    db.commit()
    db.refresh(post)
    
    return PostResponse.from_orm(post)


@router.delete("/{post_id}")
async def delete_post(
    post_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete/untrack a post"""
    
    from backend.database.models import Post, Source
    
    post = db.query(Post).join(Source).filter(
        Post.id == post_id,
        Source.user_id == current_user.id,
    ).first()
    
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    success = PostCRUD.delete(db, post_id)
    
    if success:
        return {"message": "Post untracked successfully"}
    else:
        raise HTTPException(status_code=500, detail="Failed to untrack post")