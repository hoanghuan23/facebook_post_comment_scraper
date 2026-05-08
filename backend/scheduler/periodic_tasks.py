# Periodic tasks for the scheduler
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from sqlalchemy import func

from backend.config import settings
from backend.database.crud import AnalyticsCRUD, LogCRUD, PostCRUD, SourceCRUD
from backend.database.db import SessionLocal
from backend.database.models import AnalyticsCache, Comment, Post, Source, SourceType
from backend.scraper.facebook_service import FacebookScraperService

logger = logging.getLogger("facebook_scraper")


def _format_source_label(source_id: int, source_name: str = None) -> str:
    return f"{source_name or 'unknown'} (id={source_id})"


def _format_post_update_list(post_refs, max_items: int = 10) -> str:
    shown = post_refs[:max_items]
    suffix = f" ... +{len(post_refs) - max_items} more" if len(post_refs) > max_items else ""
    return ", ".join(shown) + suffix if shown else "-"


def _start_task_log(db, task_name: str):
    return LogCRUD.create_task_log(
        db,
        task_name=task_name,
        status="RUNNING",
        started_at=datetime.utcnow(),
    )


def _finish_task_log(
    db,
    task_log,
    items_processed: int,
    started_at_ts: float,
    errors_count: int = 0,
    error_message: str = None,
    status: str = "SUCCESS",
):
    LogCRUD.update_task_log(
        db,
        task_log.id,
        status=status,
        completed_at=datetime.utcnow(),
        duration_seconds=round(time.time() - started_at_ts, 3),
        items_processed=items_processed,
        errors_count=errors_count,
        error_message=error_message,
    )


async def periodic_scrape_new_posts():
    """Scrape new posts from all active sources due for refresh."""
    db = SessionLocal()
    started_at_ts = time.time()
    scraped_count = 0
    skipped_count = 0
    errors_count = 0
    total_new_posts_created = 0
    task_log = _start_task_log(db, "periodic_scrape_new_posts")

    try:
        due_sources = SourceCRUD.get_due_for_scraping(db, limit=settings.SCRAPER_MAX_WORKERS * 5)
        logger.info("periodic_scrape_new_posts START: total_due_sources=%s", len(due_sources))
        if not due_sources:
            logger.info("periodic_scrape_new_posts DONE: total_due_sources=0")
            _finish_task_log(db, task_log, 0, started_at_ts)
            return

        source_jobs = [
            {
                "id": source.id,
                "source_name": source.source_name,
                "source_type": source.source_type,
                "is_accessible": source.is_accessible,
                "permission_checked_at": source.permission_checked_at,
            }
            for source in due_sources
        ]

        def _scrape_source_job(job: dict):
            job_db = SessionLocal()
            source_id = job["id"]
            source_label = _format_source_label(source_id, job.get("source_name"))
            next_scrape = datetime.utcnow() + timedelta(seconds=settings.TASK_SCRAPE_NEW_POSTS_INTERVAL)
            try:
                if job["source_type"] not in {SourceType.GROUP, SourceType.PAGE, SourceType.USER}:
                    logger.info(
                        "periodic_scrape_new_posts SKIP: source=%s reason=unsupported_type type=%s",
                        source_label,
                        job["source_type"].value,
                    )
                    SourceCRUD.update_scrape_info(job_db, source_id, next_scrape=next_scrape)
                    return {"status": "skipped"}

                if job["is_accessible"] is False and job["permission_checked_at"] is not None:
                    logger.warning(
                        "periodic_scrape_new_posts SKIP: source=%s reason=inaccessible",
                        source_label,
                    )
                    SourceCRUD.update_scrape_info(job_db, source_id, next_scrape=next_scrape)
                    return {"status": "skipped"}

                latest_posted_at = PostCRUD.get_latest_posted_at_by_source(job_db, source_id, tracked_only=True)
                logger.info(
                    "periodic_scrape_new_posts SOURCE START: source=%s latest_cutoff=%s",
                    source_label,
                    latest_posted_at.isoformat() if latest_posted_at else None,
                )
                result = FacebookScraperService.scrape_source(
                    job_db,
                    source_id,
                    limit=10,
                    min_posted_at=latest_posted_at,
                )
                SourceCRUD.update_scrape_info(job_db, source_id, next_scrape=next_scrape)
                logger.info(
                    "periodic_scrape_new_posts SOURCE DONE: source=%s fetched=%s created_posts=%s updated_posts=%s",
                    source_label,
                    result.total_fetched,
                    result.created_posts,
                    result.updated_posts,
                )
                return {"status": "scraped", "created_posts": result.created_posts}
            except Exception as exc:
                logger.exception("Failed scraping source %s: %s", source_id, exc)
                SourceCRUD.update_scrape_info(job_db, source_id, next_scrape=next_scrape)
                LogCRUD.create_scraper_log(
                    job_db,
                    message=f"Failed scraping source {source_id}",
                    log_level="ERROR",
                    source_id=source_id,
                    error_type=type(exc).__name__,
                    error_details=str(exc),
                )
                return {"status": "error"}
            finally:
                job_db.close()

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(_scrape_source_job, job) for job in source_jobs]
            for future in as_completed(futures):
                outcome = future.result()
                if outcome["status"] == "scraped":
                    scraped_count += 1
                    total_new_posts_created += outcome.get("created_posts", 0)
                elif outcome["status"] == "skipped":
                    skipped_count += 1
                else:
                    errors_count += 1
        logger.info(
            "periodic_scrape_new_posts DONE: scraped_sources=%s skipped_sources=%s error_sources=%s total_due_sources=%s total_new_posts_created=%s",
            scraped_count,
            skipped_count,
            errors_count,
            len(due_sources),
            total_new_posts_created,
        )
        _finish_task_log(db, task_log, scraped_count, started_at_ts, errors_count=errors_count)
    except Exception as exc:
        logger.exception("periodic_scrape_new_posts failed: %s", exc)
        _finish_task_log(
            db,
            task_log,
            scraped_count,
            started_at_ts,
            errors_count=errors_count + 1,
            error_message=str(exc),
            status="FAILED",
        )
    finally:
        db.close()


async def update_recent_post_metrics():
    """Update metrics for recent posts (< 24 hours)."""
    db = SessionLocal()
    started_at_ts = time.time()
    refreshed_sources = set()
    updated_posts = 0
    errors_count = 0
    task_log = _start_task_log(db, "update_recent_post_metrics")

    try:
        recent_posts = PostCRUD.get_recent_posts(db, hours=24, limit=settings.SCRAPER_MAX_WORKERS * 50)
        source_ids = list({post.source_id for post in recent_posts})
        logger.info(
            "update_recent_post_metrics START: recent_posts_count=%s candidate_source_count=%s",
            len(recent_posts),
            len(source_ids),
        )
        if not recent_posts:
            logger.info("update_recent_post_metrics DONE: recent_posts_count=0 candidate_source_count=0")
            _finish_task_log(db, task_log, 0, started_at_ts)
            return

        def _refresh_metrics_job(source_id: int):
            job_db = SessionLocal()
            try:
                source = SourceCRUD.get_by_id(job_db, source_id)
                if not source or not source.is_active:
                    return {"status": "ignored"}
                source_label = _format_source_label(source.id, source.source_name)
                if source.is_accessible is False and source.permission_checked_at is not None:
                    logger.warning(
                        "update_recent_post_metrics SKIP: source=%s reason=inaccessible",
                        source_label,
                    )
                    return {"status": "skipped"}
                if source.source_type not in {SourceType.GROUP, SourceType.PAGE, SourceType.USER}:
                    logger.info(
                        "update_recent_post_metrics SKIP: source=%s reason=unsupported_type type=%s",
                        source_label,
                        source.source_type.value,
                    )
                    return {"status": "skipped"}

                logger.info("update_recent_post_metrics SOURCE START: source=%s", source_label)
                result = FacebookScraperService.refresh_recent_post_metrics(job_db, source, limit=None)
                updated_post_refs = result.get("updated_post_refs", [])
                logger.info(
                    "update_recent_post_metrics SOURCE DONE: source=%s fetched=%s updated_count=%s skipped=%s updated_posts=[%s]",
                    source_label,
                    result["fetched"],
                    result["updated"],
                    result["skipped"],
                    _format_post_update_list(updated_post_refs, max_items=10),
                )
                return {"status": "refreshed", "source_id": source.id, "updated": result["updated"]}
            except Exception as exc:
                logger.exception("Failed refreshing metrics for source %s: %s", source_id, exc)
                LogCRUD.create_scraper_log(
                    job_db,
                    message=f"Failed refreshing metrics for source {source_id}",
                    log_level="ERROR",
                    source_id=source_id,
                    error_type=type(exc).__name__,
                    error_details=str(exc),
                )
                return {"status": "error"}
            finally:
                job_db.close()

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(_refresh_metrics_job, source_id) for source_id in source_ids]
            for future in as_completed(futures):
                outcome = future.result()
                if outcome["status"] == "refreshed":
                    refreshed_sources.add(outcome["source_id"])
                    updated_posts += outcome["updated"]
                elif outcome["status"] == "error":
                    errors_count += 1

        logger.info(
            "update_recent_post_metrics DONE: refreshed_sources=%s updated_posts_total=%s error_sources=%s recent_posts_count=%s",
            len(refreshed_sources),
            updated_posts,
            errors_count,
            len(recent_posts),
        )
        _finish_task_log(db, task_log, updated_posts, started_at_ts, errors_count=errors_count)
    except Exception as exc:
        logger.exception("update_recent_post_metrics failed: %s", exc)
        _finish_task_log(
            db,
            task_log,
            updated_posts,
            started_at_ts,
            errors_count=errors_count + 1,
            error_message=str(exc),
            status="FAILED",
        )
    finally:
        db.close()


async def cleanup_old_data():
    """Cleanup old data (delete posts older than retention period)"""
    logger.info("Running: Cleanup old data")
    db = SessionLocal()
    started_at_ts = time.time()
    task_log = _start_task_log(db, "cleanup_old_data")

    try:
        old_posts = PostCRUD.get_old_posts(db, days=settings.DATA_RETENTION_DAYS)
        deleted_posts = 0
        deleted_metrics = 0
        deleted_comments = 0

        for post in old_posts:
            deleted_metrics += len(post.metrics_history)
            deleted_comments += len(post.comments)
            db.delete(post)
            deleted_posts += 1

        scraper_logs_deleted, task_logs_deleted = LogCRUD.delete_old_logs(db, keep_days=settings.KEEP_DELETED_POSTS_DAYS)
        db.commit()

        items_processed = deleted_posts + deleted_metrics + deleted_comments + scraper_logs_deleted + task_logs_deleted
        logger.info(
            "Cleanup finished: posts=%s metrics=%s comments=%s scraper_logs=%s task_logs=%s",
            deleted_posts,
            deleted_metrics,
            deleted_comments,
            scraper_logs_deleted,
            task_logs_deleted,
        )
        _finish_task_log(db, task_log, items_processed, started_at_ts)
    except Exception as exc:
        db.rollback()
        logger.exception("cleanup_old_data failed: %s", exc)
        _finish_task_log(
            db,
            task_log,
            0,
            started_at_ts,
            errors_count=1,
            error_message=str(exc),
            status="FAILED",
        )
    finally:
        db.close()


async def generate_analytics_cache():
    """Generate analytics cache for faster queries"""
    logger.info("Running: Generate analytics cache")
    db = SessionLocal()
    started_at_ts = time.time()
    task_log = _start_task_log(db, "generate_analytics_cache")

    try:
        cache_date = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        processed_sources = 0

        active_sources = db.query(Source).filter(Source.is_active == True).all()

        for source in active_sources:
            posts = PostCRUD.get_by_source(db, source.id, skip=0, limit=1000, tracked_only=True)
            total_posts = len(posts)
            total_likes = sum(post.current_likes for post in posts)
            total_shares = sum(post.current_shares for post in posts)
            total_comments = sum(post.current_comments for post in posts)
            total_views = sum(post.current_views or 0 for post in posts) if posts else None
            total_engagement = total_likes + total_shares + total_comments
            avg_likes_per_post = (total_likes / total_posts) if total_posts else 0
            avg_engagement_rate = ((total_engagement / total_views) * 100) if total_views else None

            top_post = None
            if posts:
                top_post = max(posts, key=lambda post: post.current_likes + post.current_shares + post.current_comments)

            previous_cache = (
                db.query(AnalyticsCache)
                .filter(
                    AnalyticsCache.source_id == source.id,
                    AnalyticsCache.date < cache_date,
                )
                .order_by(AnalyticsCache.date.desc())
                .first()
            )
            previous_total = 0 if not previous_cache else (
                previous_cache.total_likes + previous_cache.total_shares + previous_cache.total_comments
            )
            growth_rate = None
            if previous_total > 0:
                growth_rate = ((total_engagement - previous_total) / previous_total) * 100

            AnalyticsCRUD.update(
                db,
                source.id,
                cache_date,
                total_posts=total_posts,
                total_likes=total_likes,
                total_shares=total_shares,
                total_comments=total_comments,
                total_views=total_views,
                avg_engagement_rate=avg_engagement_rate,
                avg_likes_per_post=avg_likes_per_post,
                top_post_id=top_post.facebook_post_id if top_post else None,
                growth_rate=growth_rate,
            )
            processed_sources += 1

        logger.info("Generated analytics cache for %s sources", processed_sources)
        _finish_task_log(db, task_log, processed_sources, started_at_ts)
    except Exception as exc:
        logger.exception("generate_analytics_cache failed: %s", exc)
        _finish_task_log(
            db,
            task_log,
            0,
            started_at_ts,
            errors_count=1,
            error_message=str(exc),
            status="FAILED",
        )
    finally:
        db.close()


async def health_check():
    """Periodic health check"""
    logger.info("Health check: OK")
    db = SessionLocal()
    started_at_ts = time.time()
    task_log = _start_task_log(db, "health_check")

    try:
        total_sources = db.query(func.count()).select_from(Source).scalar() or 0
        total_posts = db.query(func.count()).select_from(Post).scalar() or 0
        total_comments = db.query(func.count()).select_from(Comment).scalar() or 0
        logger.info(
            "Health check: OK - sources=%s posts=%s comments=%s",
            total_sources,
            total_posts,
            total_comments,
        )
        _finish_task_log(db, task_log, total_sources + total_posts + total_comments, started_at_ts)
    except Exception as exc:
        logger.exception("health_check failed: %s", exc)
        _finish_task_log(
            db,
            task_log,
            0,
            started_at_ts,
            errors_count=1,
            error_message=str(exc),
            status="FAILED",
        )
    finally:
        db.close()

