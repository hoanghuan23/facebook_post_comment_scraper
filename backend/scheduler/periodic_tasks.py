# Periodic tasks for the scheduler
import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import func

from backend.config import settings
from backend.database.crud import AnalyticsCRUD, LogCRUD, PostCRUD, SourceCRUD
from backend.database.db import SessionLocal
from backend.database.models import AnalyticsCache, Comment, Post, Source, SourceType
from backend.scraper.facebook_service import FacebookScraperService

logger = logging.getLogger("facebook_scraper")


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
    logger.info("Running: Scrape new posts")
    db = SessionLocal()
    started_at_ts = time.time()
    scraped_count = 0
    skipped_count = 0
    errors_count = 0
    task_log = _start_task_log(db, "periodic_scrape_new_posts")

    try:
        due_sources = SourceCRUD.get_due_for_scraping(db, limit=settings.SCRAPER_MAX_WORKERS * 5)
        if not due_sources:
            logger.info("No sources due for scraping")
            _finish_task_log(db, task_log, 0, started_at_ts)
            return

        for source in due_sources:
            next_scrape = datetime.utcnow() + timedelta(seconds=settings.TASK_SCRAPE_NEW_POSTS_INTERVAL)
            try:
                if source.source_type not in {SourceType.GROUP, SourceType.PAGE, SourceType.USER}:
                    skipped_count += 1
                    logger.info(
                        "Skipping source %s: type %s not implemented yet",
                        source.id,
                        source.source_type.value,
                    )
                    SourceCRUD.update_scrape_info(db, source.id, next_scrape=next_scrape)
                    continue

                if source.is_accessible is False and source.permission_checked_at is not None:
                    skipped_count += 1
                    logger.warning("Skipping source %s: source marked inaccessible", source.id)
                    SourceCRUD.update_scrape_info(db, source.id, next_scrape=next_scrape)
                    continue

                result = FacebookScraperService.scrape_source(db, source.id, limit=20)
                scraped_count += 1
                SourceCRUD.update_scrape_info(db, source.id, next_scrape=next_scrape)
                logger.info(
                    "Source %s scrape complete: fetched=%s created=%s updated=%s",
                    source.id,
                    result.total_fetched,
                    result.created_posts,
                    result.updated_posts,
                )
            except Exception as exc:
                errors_count += 1
                logger.exception("Failed scraping source %s: %s", source.id, exc)
                SourceCRUD.update_scrape_info(db, source.id, next_scrape=next_scrape)
                LogCRUD.create_scraper_log(
                    db,
                    message=f"Failed scraping source {source.id}",
                    log_level="ERROR",
                    source_id=source.id,
                    error_type=type(exc).__name__,
                    error_details=str(exc),
                )

        logger.info(
            "Scrape new posts finished: processed=%s skipped=%s total_due=%s",
            scraped_count,
            skipped_count,
            len(due_sources),
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
    logger.info("Running: Update recent post metrics")
    db = SessionLocal()
    started_at_ts = time.time()
    refreshed_sources = set()
    updated_posts = 0
    errors_count = 0
    task_log = _start_task_log(db, "update_recent_post_metrics")

    try:
        recent_posts = PostCRUD.get_recent_posts(db, hours=24, limit=settings.SCRAPER_MAX_WORKERS * 50)
        if not recent_posts:
            logger.info("No recent posts need metric refresh")
            _finish_task_log(db, task_log, 0, started_at_ts)
            return

        source_ids = {post.source_id for post in recent_posts}
        for source_id in source_ids:
            source = SourceCRUD.get_by_id(db, source_id)
            if not source or not source.is_active:
                continue
            if source.is_accessible is False and source.permission_checked_at is not None:
                logger.warning("Skipping metric refresh for inaccessible source %s", source.id)
                continue
            if source.source_type not in {SourceType.GROUP, SourceType.PAGE, SourceType.USER}:
                logger.info("Skipping metric refresh for source %s type %s", source.id, source.source_type.value)
                continue

            try:
                result = FacebookScraperService.refresh_recent_post_metrics(db, source, limit=20)
                refreshed_sources.add(source.id)
                updated_posts += result["updated"]
                logger.info(
                    "Metric refresh complete for source %s: fetched=%s updated=%s skipped=%s",
                    source.id,
                    result["fetched"],
                    result["updated"],
                    result["skipped"],
                )
            except Exception as exc:
                errors_count += 1
                logger.exception("Failed refreshing metrics for source %s: %s", source.id, exc)
                LogCRUD.create_scraper_log(
                    db,
                    message=f"Failed refreshing metrics for source {source.id}",
                    log_level="ERROR",
                    source_id=source.id,
                    error_type=type(exc).__name__,
                    error_details=str(exc),
                )

        logger.info(
            "Update recent post metrics finished: sources=%s updated_posts=%s recent_posts=%s",
            len(refreshed_sources),
            updated_posts,
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
