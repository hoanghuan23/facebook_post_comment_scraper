# Source management routes
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.api.auth import get_current_user
from backend.database.crud import SourceCRUD, PostCRUD, duplicate_check_source, get_source_stats
from backend.database.db import get_db
from backend.database.models import User
from backend.database.schemas import SourceCreate, SourceDetail, SourceResponse, SourceUpdate, PostResponse

router = APIRouter()


def _extract_facebook_id(url: str) -> str:
    """Best-effort extraction for Facebook ID/slug from URL."""
    cleaned = url.strip().rstrip("/")
    if not cleaned:
        return ""
    return cleaned.split("/")[-1]


@router.post("/", response_model=SourceResponse)
async def create_source(
    source_data: SourceCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new source (group/page/user) to track."""
    facebook_id = _extract_facebook_id(source_data.facebook_url)
    if not facebook_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Facebook URL",
        )

    if duplicate_check_source(db, current_user.id, facebook_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This source is already being tracked",
        )

    source = SourceCRUD.create(
        db=db,
        user_id=current_user.id,
        source_type=source_data.source_type,
        facebook_id=facebook_id,
        facebook_url=source_data.facebook_url,
        include_comments=source_data.include_comments,
        include_replies=source_data.include_replies,
        max_days_old=source_data.max_days_old,
    )
    return source


@router.get("/", response_model=List[SourceResponse])
async def list_sources(
    skip: int = 0,
    limit: int = 100,
    active_only: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all sources for current user."""
    if active_only:
        return SourceCRUD.get_active_sources(db, current_user.id)
    return SourceCRUD.get_by_user(db, current_user.id, skip=skip, limit=limit)


@router.get("/{source_id}", response_model=SourceDetail)
async def get_source(
    source_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get detailed information about a source."""
    source = SourceCRUD.get_by_id(db, source_id)
    if not source or source.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Source not found")

    stats = get_source_stats(db, source_id)
    detail = SourceDetail.model_validate(source)
    detail.post_count = stats["posts_count"]
    return detail


@router.put("/{source_id}", response_model=SourceResponse)
async def update_source(
    source_id: int,
    source_data: SourceUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update source settings."""
    source = SourceCRUD.get_by_id(db, source_id)
    if not source or source.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Source not found")

    payload = source_data.model_dump(exclude_unset=True)
    updated_source = SourceCRUD.update(db, source_id, **payload)
    if not updated_source:
        raise HTTPException(status_code=500, detail="Failed to update source")

    return updated_source


@router.delete("/{source_id}")
async def delete_source(
    source_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a source."""
    source = SourceCRUD.get_by_id(db, source_id)
    if not source or source.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Source not found")

    if not SourceCRUD.delete(db, source_id):
        raise HTTPException(status_code=500, detail="Failed to delete source")

    return {"message": f"Source {source_id} deleted successfully"}


@router.post("/{source_id}/refresh")
async def refresh_source(
    source_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Manually trigger scrape for a source."""
    source = SourceCRUD.get_by_id(db, source_id)
    if not source or source.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Source not found")

    SourceCRUD.update_scrape_info(db, source_id, next_scrape=datetime.utcnow())
    return {"message": f"Source {source_id} queued for refresh", "source_id": source_id}


@router.get("/{source_id}/posts", response_model=List[PostResponse])
async def get_source_posts(
    source_id: int,
    skip: int = 0,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get posts from a source."""
    source = SourceCRUD.get_by_id(db, source_id)
    if not source or source.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Source not found")

    return PostCRUD.get_by_source(db, source_id, skip=skip, limit=limit)
