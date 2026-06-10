# Admin routes
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime
import asyncio

from backend.database.db import get_db
from backend.database.models import User, Source, Post, Comment, PostMetric, PipelineJob, PipelineLog
from backend.database.crud import PipelineJobCRUD, UserCRUD, LogCRUD, SourceCRUD, get_user_stats
from backend.api.auth import get_current_admin_user

router = APIRouter()


@router.get("/scraper-status")
async def get_scraper_status(
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    """Get current scraper status"""
    from backend.scheduler.task_scheduler import get_scheduler
    
    scheduler = get_scheduler()
    
    # Get recent task logs
    recent_logs = LogCRUD.get_task_logs(db, limit=10)
    
    # Count errors from today
    today = datetime.utcnow().date()
    errors_today = db.query(func.count(PipelineLog.id)).filter(
        PipelineLog.log_level == "ERROR",
        func.date(PipelineLog.created_at) == today
    ).scalar()
    
    return {
        "scheduler_running": scheduler.running if scheduler else False,
        "jobs_count": len(scheduler.get_jobs()) if scheduler else 0,
        "active_tasks": len(scheduler.get_jobs()) if scheduler else 0,
        "recent_tasks": [
            {
                "task": l.task_name,
                "status": l.status,
                "timestamp": l.created_at,
            }
            for l in recent_logs
        ],
        "errors_today": errors_today,
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.post("/scraper-action")
async def control_scraper(
    action: str,  # start, stop, pause, resume
    task_name: str = None,
    current_user: User = Depends(get_current_admin_user),
):
    """Control scraper (start/stop/pause/resume)"""
    from backend.scheduler.task_scheduler import (
        start_scheduler, stop_scheduler,
        pause_scheduler, resume_scheduler
    )
    
    try:
        if action == "start":
            await start_scheduler()
            return {"status": "started", "message": "Scheduler started"}
        elif action == "stop":
            await stop_scheduler()
            return {"status": "stopped", "message": "Scheduler stopped"}
        elif action == "pause":
            pause_scheduler()
            return {"status": "paused", "message": "Scheduler paused"}
        elif action == "resume":
            resume_scheduler()
            return {"status": "resumed", "message": "Scheduler resumed"}
        else:
            raise HTTPException(
                status_code=400,
                detail="Invalid action. Use: start, stop, pause, resume"
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/logs")
async def get_logs(
    limit: int = 100,
    log_level: str = None,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    """Get application logs"""

    query = db.query(PipelineLog).order_by(PipelineLog.created_at.desc())
    
    if log_level:
        query = query.filter(PipelineLog.log_level == log_level)
    
    logs = query.limit(limit).all()
    
    return {
        "count": len(logs),
        "limit": limit,
        "logs": [
            {
                "timestamp": l.created_at,
                "level": l.log_level,
                "message": l.message,
                "source_id": l.source_id,
                "job_id": l.job_id,
                "job_type": l.job.job_type if l.job else None,
            }
            for l in logs
        ]
    }


@router.get("/task-logs")
async def get_task_logs(
    limit: int = 100,
    task_name: str = None,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    """Get scheduled task execution logs."""
    logs = LogCRUD.get_task_logs(db, task_name=task_name, limit=limit)
    return {
        "count": len(logs),
        "limit": limit,
        "task_name": task_name,
        "logs": [
            {
                "id": log.id,
                "task_name": log.task_name,
                "status": log.status,
                "started_at": log.started_at,
                "completed_at": log.completed_at,
                "duration_seconds": log.duration_seconds,
                "items_processed": log.items_processed,
                "errors_count": log.errors_count,
                "error_message": log.error_message,
                "created_at": log.created_at,
            }
            for log in logs
        ],
    }


@router.get("/users")
async def list_users(
    skip: int = 0,
    limit: int = 20,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    """List all users (admin only)"""
    
    users = UserCRUD.get_all(db, skip=skip, limit=limit)
    
    user_stats = []
    for user in users:
        stats = get_user_stats(db, user.id)
        user_stats.append({
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "is_active": user.is_active,
            "is_admin": user.is_admin,
            "created_at": user.created_at,
            "sources_count": stats['sources_count'],
            "posts_count": stats['posts_count'],
            "total_engagement": stats['total_engagement'],
        })
    
    return {
        "count": len(user_stats),
        "users": user_stats,
    }


@router.get("/stats")
async def get_system_stats(
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    """Get system statistics"""
    
    user_count = db.query(func.count(User.id)).scalar()
    source_count = db.query(func.count(Source.id)).scalar()
    post_count = db.query(func.count(Post.id)).scalar()
    comment_count = db.query(func.count(Comment.id)).scalar()
    metric_count = db.query(func.count(PostMetric.id)).scalar()
    
    total_engagement = db.query(
        func.sum(Post.current_likes + Post.current_shares + Post.current_comments)
    ).scalar() or 0
    
    return {
        "total_users": user_count,
        "total_sources": source_count,
        "total_posts": post_count,
        "total_comments": comment_count,
        "total_metric_snapshots": metric_count,
        "total_engagement": total_engagement,
    }


@router.post("/tasks/{task_name}")
async def run_task_manually(
    task_name: str,
    source_id: int = None,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    """Manually run a scheduled task"""
    from backend.scheduler import periodic_tasks
    from backend.scraper.facebook_service import FacebookScraperService
    
    try:
        if source_id is not None:
            source = SourceCRUD.get_by_id(db, source_id)
            if not source:
                raise HTTPException(status_code=404, detail="Source not found")

            if task_name == "scrape_posts":
                if PipelineJobCRUD.has_running_scrape_job(db, source_id):
                    raise HTTPException(
                        status_code=409,
                        detail="A scrape job is already running for this source",
                    )

                pipeline_job = PipelineJobCRUD.create_job(
                    db=db,
                    job_type="scraper_job",
                    source_id=source_id,
                    status="running",
                    started_at=datetime.utcnow(),
                )
                try:
                    result = FacebookScraperService.scrape_source(db, source_id, limit=20, job_id=pipeline_job.id)
                    PipelineJobCRUD.mark_done(
                        db=db,
                        job_id=pipeline_job.id,
                        posts_found=result.total_fetched,
                        posts_new=result.created_posts,
                        finished_at=datetime.utcnow(),
                    )
                    return {
                        "status": "completed",
                        "task": "scrape_posts",
                        "source_id": source_id,
                        "result": {
                            "total_fetched": result.total_fetched,
                            "created_posts": result.created_posts,
                            "updated_posts": result.updated_posts,
                            "skipped_posts": result.skipped_posts,
                        },
                    }
                except Exception as exc:
                    PipelineJobCRUD.mark_failed(
                        db=db,
                        job_id=pipeline_job.id,
                        error_message=str(exc),
                        finished_at=datetime.utcnow(),
                    )
                    LogCRUD.create_pipeline_log(
                        db,
                        message=f"Manual scrape_posts failed for source {source_id}",
                        log_level="ERROR",
                        job_id=pipeline_job.id,
                        source_id=source_id,
                        error_type=type(exc).__name__,
                        error_details=str(exc),
                    )
                    raise

            if task_name == "update_metrics":
                pipeline_job = PipelineJobCRUD.create_job(
                    db=db,
                    job_type="update_metric",
                    source_id=source_id,
                    status="running",
                    started_at=datetime.utcnow(),
                )
                try:
                    result = FacebookScraperService.refresh_recent_post_metrics(db, source, limit=20, job_id=pipeline_job.id)
                    PipelineJobCRUD.mark_done(
                        db=db,
                        job_id=pipeline_job.id,
                        items_total=result.get("fetched", 0) if isinstance(result, dict) else 0,
                        items_updated=result.get("updated", 0) if isinstance(result, dict) else 0,
                        finished_at=datetime.utcnow(),
                    )
                    return {
                        "status": "completed",
                        "task": "update_metrics",
                        "source_id": source_id,
                        "result": result,
                    }
                except Exception as exc:
                    PipelineJobCRUD.mark_failed(
                        db=db,
                        job_id=pipeline_job.id,
                        error_message=str(exc),
                        finished_at=datetime.utcnow(),
                    )
                    LogCRUD.create_pipeline_log(
                        db,
                        message=f"Manual update_metrics failed for source {source_id}",
                        log_level="ERROR",
                        job_id=pipeline_job.id,
                        source_id=source_id,
                        error_type=type(exc).__name__,
                        error_details=str(exc),
                    )
                    raise

        if task_name == "scrape_posts":
            asyncio.create_task(periodic_tasks.periodic_scrape_new_posts())
            return {"status": "queued", "task": "scrape_posts"}
        
        elif task_name == "update_metrics":
            asyncio.create_task(periodic_tasks.update_recent_post_metrics())
            return {"status": "queued", "task": "update_metrics"}
        
        elif task_name == "cleanup":
            asyncio.create_task(periodic_tasks.cleanup_old_data())
            return {"status": "queued", "task": "cleanup"}
        
        elif task_name == "analytics":
            asyncio.create_task(periodic_tasks.generate_analytics_cache())
            return {"status": "queued", "task": "analytics"}
        
        else:
            raise HTTPException(
                status_code=400,
                detail="Invalid task. Use: scrape_posts, update_metrics, cleanup, analytics"
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

