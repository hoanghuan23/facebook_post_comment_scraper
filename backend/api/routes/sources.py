# Source management routes
from datetime import datetime
from typing import List
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.api.auth import get_current_user
from backend.database.crud import SourceCRUD, PostCRUD, duplicate_check_source, get_source_stats
from backend.database.db import get_db
from backend.database.models import User
from backend.database.schemas import SourceCreate, SourceDetail, SourceResponse, SourceUpdate, PostResponse
from backend.utils.facebook_url_parser import FacebookURLParser, FacebookSourceType
from backend.utils.permission_checker import FacebookPermissionChecker, SourceAccessValidator

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/", response_model=SourceResponse)
async def create_source(
    source_data: SourceCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new source (group/page/user) to track."""
    
    # Parse Facebook URL
    parsed_url = FacebookURLParser.parse(source_data.facebook_url)
    
    if not parsed_url['is_valid']:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid Facebook URL: {parsed_url['error']}",
        )
    
    facebook_id = parsed_url['facebook_id']
    detected_source_type = parsed_url['source_type'].value
    
    # Use provided source_type if valid, otherwise use detected
    if source_data.source_type not in ['group', 'page', 'user']:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid source type",
        )
    
    # Check for duplicates
    if duplicate_check_source(db, current_user.id, facebook_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This source is already being tracked",
        )
    
    # Check access permissions if requested
    permission_status = None
    permission_message = None
    access_restrictions = None
    is_accessible = False
    
    if source_data.check_access:
        # Validate access before saving
        is_valid, validation_error = SourceAccessValidator.validate_before_save(
            facebook_id=facebook_id,
            source_type=source_data.source_type,
            user_id=current_user.id,
            user_cookies=None,  # Can be passed if available
            strict_mode=False
        )
        
        # Check detailed permissions
        permission_result = FacebookPermissionChecker.check_access(
            facebook_id=facebook_id,
            user_id=current_user.id,
            source_type=source_data.source_type,
            user_cookies=None
        )
        
        permission_status = permission_result['status'].value
        permission_message = permission_result['message']
        is_accessible = permission_result['accessible']
        
        if permission_result.get('restrictions'):
            access_restrictions = json.dumps(permission_result['restrictions'])
        
        # If access is denied, raise error
        if not is_valid and permission_result['status'].value == 'denied':
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: {validation_error}",
            )
    
    # Create source with permission information
    source = SourceCRUD.create(
        db=db,
        user_id=current_user.id,
        source_type=source_data.source_type,
        facebook_id=facebook_id,
        facebook_url=source_data.facebook_url,
        include_comments=source_data.include_comments,
        include_replies=source_data.include_replies,
        max_days_old=source_data.max_days_old,
        permission_status=permission_status,
        permission_message=permission_message,
        access_restrictions=access_restrictions,
        is_accessible=is_accessible,
        permission_checked_at=datetime.utcnow() if source_data.check_access else None,
    )
    
    logger.info(f"Source created: {facebook_id} by user {current_user.id}")
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
    
    # Parse restrictions if available
    if source.access_restrictions:
        try:
            detail.access_restrictions = json.loads(source.access_restrictions)
        except:
            pass
    
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


@router.post("/{source_id}/check-access")
async def check_source_access(
    source_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Check/recheck access permissions for a source."""
    source = SourceCRUD.get_by_id(db, source_id)
    if not source or source.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Source not found")
    
    # Check permissions
    permission_result = FacebookPermissionChecker.check_access(
        facebook_id=source.facebook_id,
        user_id=current_user.id,
        source_type=source.source_type.value,
        user_cookies=None
    )
    
    # Update source with new permission info
    update_data = {
        'permission_status': permission_result['status'].value,
        'permission_message': permission_result['message'],
        'is_accessible': permission_result['accessible'],
        'permission_checked_at': datetime.utcnow(),
    }
    
    if permission_result.get('restrictions'):
        update_data['access_restrictions'] = json.dumps(permission_result['restrictions'])
    
    # Use raw SQL update for permission fields
    for key, value in update_data.items():
        if hasattr(source, key):
            setattr(source, key, value)
    
    db.commit()
    db.refresh(source)
    
    return {
        "source_id": source_id,
        "permission_status": permission_result['status'].value,
        "is_accessible": permission_result['accessible'],
        "message": permission_result['message'],
        "restrictions": permission_result.get('restrictions', []),
    }


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
