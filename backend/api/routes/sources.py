# Source management routes
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime

from backend.database.db import get_db
from backend.database.models import User
from backend.database.schemas import SourceCreate, SourceResponse, SourceDetail
from backend.database.crud import SourceCRUD, duplicate_check_source, get_source_stats
from backend.api.auth import get_current_user

router = APIRouter()


@router.post("/", response_model=SourceResponse)
async def create_source(
    source_data: SourceCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new source (group/page/user) to track"""
    
    # TODO: Extract Facebook ID from URL using regex
    # TODO: Validate URL and test connection
    # TODO: Get source metadata (name, description, member count, etc.)
    
    # Placeholder: Extract Facebook ID (would use proper extraction in real implementation)
    facebook_id = source_data.facebook_url.split('/')[-1]
    
    # Check if source already exists for this user
    if duplicate_check_source(db, current_user.id, facebook_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This source is already being tracked"
        )
    
    new_source = SourceCRUD.create(
        db=db,
        user_id=current_user.id,
        source_type=source_data.source_type,
        facebook_id=facebook_id,
        facebook_url=source_data.facebook_url,
        source_name=None,  # Will be extracted from Facebook
        include_comments=source_data.include_comments,
        include_replies=source_data.include_replies,
        max_days_old=source_data.max_days_old,
    )
    
    return SourceResponse.from_orm(new_source)


@router.get("/", response_model=List[SourceResponse])
async def list_sources(
    skip: int = 0,
    limit: int = 100,
    active_only: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all sources for current user"""
    if active_only:
        sources = SourceCRUD.get_active_sources(db, current_user.id)
    else:
        sources = SourceCRUD.get_by_user(db, current_user.id, skip=skip, limit=limit)
    
    return sources


@router.get("/{source_id}", response_model=SourceDetail)
async def getSourceCRUD.get_by_id(db, source_id)
    
    if not source or source.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Source not found")
    
    # Get stats for the source
    stats = get_source_stats(db, source_id)
    
    detail = SourceDetail.from_orm(source)
    detail.post_count = stats['posts_count']
    
    return detaild,
    ).first()
    
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    
    # TODO: Add post count and latest post date
    return SourceDetail.from_orm(source)


@router.put("/{source_id}", response_model=SourceResponse)
async def update_source(
    source_id: int,
    source_data: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """UpdateSourceCRUD.get_by_id(db, source_id)
    
    if not source or source.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Source not found")
    
    updated_source = SourceCRUD.update(db, source_id, **source_data)
    
    return SourceResponse.from_orm(updated_
    
    return SourceResponse.from_orm(source)


@router.delete("/{source_id}")
async def delete_source(
    source_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a source"""
    
    source = db.query(Source).filter(
        Source.id == source_id,
        Source.user_id == current_user.id,
    ).first()
    
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    
    db.delete(source)
    db.commitSourceCRUD.get_by_id(db, source_id)
    
    if not source or source.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Source not found")
    
    success = SourceCRUD.delete(db, source_id)
    
    if success:
        return {"message": f"Source {source_id} deleted successfully"}
    else:
        raise HTTPException(status_code=500, detail="Failed to delete source")
    
    source = db.query(Source).filter(
        Source.id == source_id,
        Source.user_id == current_user.id,
    ).first()
    
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    
    # TODO: Add to scraper queue
    
    return {"SourceCRUD.get_by_id(db, source_id)
    
    if not source or source.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Source not found")
    
    # Update next_scrape to now to trigger immediate scraping
    SourceCRUD.update_scrape_info(
        db, 
        source_id,
        next_scrape=datetime.utcnow()
    )
    
    return {"message": f"Source {source_id} queued for refresh", "source_id": source_id
    from backend.database.crud import PostCRUD
    from backend.database.schemas import PostResponse
    
    source = SourceCRUD.get_by_id(db, source_id)
    
    if not source or source.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Source not found")
    
    posts = PostCRUD.get_by_source(db, source_id, skip=skip, limit=limit)
    
    return posts
    return {"message": "Not yet implemented"}
