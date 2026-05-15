# Source management routes
from datetime import datetime, timedelta
from typing import Annotated, List, Literal, Union
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.api.auth import get_current_user
from backend.config import settings
from backend.database.crud import (
    FacebookSessionCRUD,
    LogCRUD,
    PostCRUD,
    PipelineJobCRUD,
    SourceCRUD,
    duplicate_check_source,
    get_source_stats,
)
from backend.database.db import SessionLocal, get_db
from backend.database.models import SourceType, User
from backend.database.schemas import (
    CreateSourceError,
    CreateSourceResult,
    SourceCreate,
    SourceDetail,
    SourceRankingResponse,
    SourceScheduleStatsResponse,
    SourceResponse,
    SourceUpdate,
    PostResponse,
)
from backend.services.schedule_service import TIER_CONFIG, apply_schedule_all, calculate_tier
from backend.scraper.facebook_service import FacebookScraperService
from backend.utils.facebook_url_parser import FacebookURLParser, FacebookSourceType
from backend.utils.permission_checker import FacebookPermissionChecker, SourceAccessValidator

logger = logging.getLogger(__name__)
router = APIRouter()

TIER_INTERVAL_MAP = {cfg["tier"]: cfg["interval_minutes"] for cfg in TIER_CONFIG}


def _format_source_label(source_id: int, source_name: str = None) -> str:
    return f"{source_name or 'unknown'} (id={source_id})"


def _bootstrap_scrape_source_last_24h(source_id: int):
    db = SessionLocal()
    started_at = datetime.utcnow()
    started_ts = datetime.utcnow().timestamp()
    pipeline_job_id = None
    try:
        source = SourceCRUD.get_by_id(db, source_id)
        if not source:
            logger.warning("Bỏ qua bootstrap scrape 24h: source_id=%s reason=not_found", source_id)
            return
        source_label = _format_source_label(source.id, source.source_name)
        if source.source_type not in {SourceType.GROUP, SourceType.PAGE, SourceType.USER}:
            logger.info(
                "Bỏ qua bootstrap scrape 24h: source=%s reason=unsupported_type type=%s",
                source_label,
                source.source_type.value,
            )
            return

        logger.info(
            "Bắt đầu bootstrap scrape 24h: source=%s thread=%s started_at=%s",
            source_label,
            "background_task",
            started_at.isoformat(),
        )
        active_session = FacebookSessionCRUD.get_active_by_user_id(db, source.user_id)
        pipeline_job = PipelineJobCRUD.create_job(
            db=db,
            job_type="scrape_24h",
            source_id=source_id,
            session_id=active_session.id if active_session else None,
            status="running",
            started_at=started_at,
        )
        pipeline_job_id = pipeline_job.id
        result = FacebookScraperService.scrape_source(
            db,
            source_id=source_id,
            last_24_hours_only=True,
        )
        PipelineJobCRUD.mark_done(
            db=db,
            job_id=pipeline_job_id,
            posts_found=result.total_fetched,
            posts_new=result.created_posts,
            finished_at=datetime.utcnow(),
        )
        next_scrape = datetime.utcnow() + timedelta(seconds=settings.TASK_SCRAPE_NEW_POSTS_INTERVAL)
        SourceCRUD.update_scrape_info(db, source_id, next_scrape=next_scrape)
        duration_seconds = round(datetime.utcnow().timestamp() - started_ts, 3)
        post_per_second = round(result.total_fetched / duration_seconds, 3) if duration_seconds > 0 else 0.0
        logger.info(
            "Kết thúc bootstrap scrape 24h: source=%s fetched=%s created_posts=%s updated_posts=%s skipped_posts=%s filtered_by_cutoff=%s duration_seconds=%s post_per_second=%s next_scrape=%s",
            source_label,
            result.total_fetched,
            result.created_posts,
            result.updated_posts,
            result.skipped_posts,
            result.filtered_by_cutoff,
            duration_seconds,
            post_per_second,
            next_scrape.isoformat(),
        )
    except Exception as exc:
        if pipeline_job_id is not None:
            PipelineJobCRUD.mark_failed(
                db=db,
                job_id=pipeline_job_id,
                error_message=str(exc),
                finished_at=datetime.utcnow(),
            )
        logger.exception("Bootstrap scrape 24h thất bại: source_id=%s error=%s", source_id, exc)
        LogCRUD.create_pipeline_log(
            db,
            message=f"Bootstrap scrape 24h thất bại cho source {source_id}",
            log_level="ERROR",
            job_id=pipeline_job_id,
            source_id=source_id,
            error_type=type(exc).__name__,
            error_details=str(exc),
        )
    finally:
        db.close()


def _create_single_source(
    source_data: SourceCreate,
    background_tasks: BackgroundTasks,
    current_user: User,
    db: Session,
):
    """Create a single source with existing validation rules."""

    # Parse Facebook URL
    parsed_url = FacebookURLParser.parse(source_data.facebook_url)

    if not parsed_url['is_valid']:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid Facebook URL: {parsed_url['error']}",
        )

    facebook_id = parsed_url['facebook_id']

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
        is_accessible = permission_result['accessible']
        
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
        max_days_old=source_data.max_days_old,
        permission_status=permission_status,
        is_accessible=is_accessible,
        permission_checked_at=datetime.utcnow() if source_data.check_access else None,
    )

    background_tasks.add_task(_bootstrap_scrape_source_last_24h, source.id)

    logger.info(f"Source created: {facebook_id} by user {current_user.id}")
    return source


@router.post("/", response_model=CreateSourceResult)
async def create_source(
    source_data: Union[SourceCreate, List[SourceCreate]],
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create one or many sources (group/page/user) to track."""
    is_batch = isinstance(source_data, list)
    payload_items = source_data if is_batch else [source_data]
    mode = "batch" if is_batch else "single"

    created_sources = []
    errors = []

    for index, item in enumerate(payload_items):
        try:
            source = _create_single_source(
                source_data=item,
                background_tasks=background_tasks,
                current_user=current_user,
                db=db,
            )
            created_sources.append(source)
        except HTTPException as exc:
            error_code = "create_source_error"
            if exc.status_code == status.HTTP_400_BAD_REQUEST:
                error_code = "bad_request"
            elif exc.status_code == status.HTTP_403_FORBIDDEN:
                error_code = "access_denied"
            errors.append(
                CreateSourceError(
                    index=index,
                    facebook_url=item.facebook_url,
                    code=error_code,
                    message=str(exc.detail),
                )
            )

    result = CreateSourceResult(
        mode=mode,
        total=len(payload_items),
        success_count=len(created_sources),
        error_count=len(errors),
        created=[SourceResponse.model_validate(source) for source in created_sources],
        errors=errors,
    )

    if result.error_count == 0:
        return result

    status_code = status.HTTP_207_MULTI_STATUS if result.success_count > 0 else status.HTTP_400_BAD_REQUEST
    return JSONResponse(
        status_code=status_code,
        content=result.model_dump(mode="json"),
    )


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


@router.get("/ranking", response_model=SourceRankingResponse)
async def get_sources_ranking(
    sort: Literal["posts_per_day", "engagement", "tier"] = "posts_per_day",
    limit: Annotated[int, Query(ge=1)] = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Rank current user's sources by calculated activity, engagement, or tier."""
    sources = SourceCRUD.get_by_user(db, current_user.id, limit=100000)
    tier_distribution = {f"tier_{tier}": 0 for tier in range(1, 5)}
    ranked_sources = []

    for source in sources:
        suggested = calculate_tier(source.id, db)
        suggested_tier = suggested["tier"] or 4
        tier_distribution[f"tier_{suggested_tier}"] += 1
        ranked_sources.append(
            {
                "source_id": source.id,
                "source_name": source.source_name,
                "avg_posts_per_day": suggested["avg_posts_per_day"],
                "avg_engagement_rate": suggested["avg_engagement_rate"],
                "engagement_available": suggested["engagement_available"],
                "data_days": suggested["data_days"],
                "suggested_tier": suggested_tier,
                "current_tier": source.schedule_tier,
                "is_overridden": source.schedule_override_minutes is not None,
            }
        )

    if sort == "engagement":
        ranked_sources.sort(
            key=lambda item: (
                item["avg_engagement_rate"] is not None,
                item["avg_engagement_rate"] or 0,
                item["avg_posts_per_day"],
            ),
            reverse=True,
        )
    elif sort == "tier":
        ranked_sources.sort(
            key=lambda item: (
                item["suggested_tier"],
                -item["avg_posts_per_day"],
                -(item["avg_engagement_rate"] or 0),
            )
        )
    else:
        ranked_sources.sort(
            key=lambda item: (
                item["avg_posts_per_day"],
                item["avg_engagement_rate"] or 0,
            ),
            reverse=True,
        )

    limited_sources = ranked_sources[:limit]
    for rank, item in enumerate(limited_sources, start=1):
        item["rank"] = rank

    return SourceRankingResponse(
        total_sources=len(sources),
        tier_distribution=tier_distribution,
        sources=limited_sources,
    )


@router.post("/auto-schedule")
async def auto_schedule_sources(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Apply automatic schedule tiers to all sources for current user."""
    return apply_schedule_all(current_user.id, db)


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


@router.get("/{source_id}/stats", response_model=SourceScheduleStatsResponse)
async def get_source_schedule_stats(
    source_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return suggested schedule tier and current applied schedule state for a source."""
    source = SourceCRUD.get_by_id(db, source_id)
    if not source or source.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Source not found")

    suggested = calculate_tier(source_id, db)
    current_state = db.execute(
        text(
            """
            SELECT schedule_tier, schedule_override_minutes, next_scrape
            FROM sources
            WHERE id = :source_id
            """
        ),
        {"source_id": source_id},
    ).fetchone()

    if not current_state:
        raise HTTPException(status_code=404, detail="Source not found")

    override_minutes = current_state.schedule_override_minutes
    is_overridden = override_minutes is not None
    current_tier = current_state.schedule_tier
    current_interval = override_minutes if is_overridden else TIER_INTERVAL_MAP.get(current_tier)

    return SourceScheduleStatsResponse(
        suggested_tier=suggested["tier"],
        suggested_interval_minutes=suggested["interval_minutes"],
        engagement_available=suggested["engagement_available"],
        data_days=suggested["data_days"],
        avg_posts_per_day=suggested["avg_posts_per_day"],
        avg_engagement_rate=suggested["avg_engagement_rate"],
        tier_reason=suggested["reason"],
        current_tier=current_tier,
        current_interval_minutes=current_interval,
        is_overridden=is_overridden,
        override_minutes=override_minutes,
        next_scrape=current_state.next_scrape,
    )


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
        'is_accessible': permission_result['accessible'],
        'permission_checked_at': datetime.utcnow(),
    }
    
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
