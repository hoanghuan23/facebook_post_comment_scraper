# Periodic tasks for the scheduler
import logging
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from sqlalchemy import func

from backend.config import settings
from backend.database.crud import AnalyticsCRUD, FacebookSessionCRUD, LogCRUD, PipelineJobCRUD, PostCRUD, SourceCRUD
from backend.database.db import SessionLocal
from backend.database.models import AnalyticsCache, Comment, Post, Source, SourceType
from backend.scraper.facebook_service import FacebookScraperService

logger = logging.getLogger("facebook_scraper")


def _format_source_label(source_id: int, source_name: str = None) -> str:
    return f"{source_name or 'unknown'} (id={source_id})"


def _current_thread_label() -> str:
    thread = threading.current_thread()
    return f"{thread.name}/{thread.ident}"


def _format_post_update_list(post_refs, max_items: int = 10) -> str:
    shown = post_refs[:max_items]
    suffix = f" ... +{len(post_refs) - max_items} more" if len(post_refs) > max_items else ""
    return ", ".join(shown) + suffix if shown else "-"


def _safe_rate(count: int, duration_seconds: float) -> float:
    if duration_seconds <= 0:
        return 0.0
    return round(count / duration_seconds, 3)


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
        # due_sources = SourceCRUD.get_due_for_scraping(db, limit=settings.SCRAPER_MAX_WORKERS * 5)
        due_sources = SourceCRUD.get_due_for_scraping(db, limit=settings.SCRAPER_SOURCE_BATCH_LIMIT) # giới hạn số source mỗi lần chạy scrape_new_posts
        total_due_sources = len(due_sources)
        logger.info("Bắt đầu periodic_scrape_new_posts: total_due_sources=%s", total_due_sources)
        if not due_sources:
            logger.info("Kết thúc periodic_scrape_new_posts: total_due_sources=0")
            _finish_task_log(db, task_log, 0, started_at_ts)
            return

        source_ids = [source.id for source in due_sources]
        user_ids = [source.user_id for source in due_sources]
        latest_posted_at_map = PostCRUD.get_latest_posted_at_bulk(db, source_ids, tracked_only=True)
        active_sessions_map = FacebookSessionCRUD.get_active_sessions_bulk(db, user_ids)

        source_jobs = [
            {
                "id": source.id,
                "user_id": source.user_id,
                "source_name": source.source_name,
                "source_type": source.source_type,
                "is_accessible": source.is_accessible,
                "permission_checked_at": source.permission_checked_at,
                "latest_posted_at": latest_posted_at_map.get(source.id),
                "active_session_id": (
                    active_sessions_map[source.user_id].id
                    if source.user_id in active_sessions_map
                    else None
                ),
            }
            for source in due_sources
        ]

        source_index_map = {job["id"]: idx for idx, job in enumerate(source_jobs, start=1)}

        def _scrape_source_job(job: dict):
            job_db = SessionLocal()
            source_id = job["id"]
            pipeline_job_id = None
            progress_index = source_index_map.get(source_id, 0)
            source_label = _format_source_label(source_id, job.get("source_name"))
            thread_label = _current_thread_label()
            next_scrape = datetime.utcnow() + timedelta(seconds=settings.TASK_SCRAPE_NEW_POSTS_INTERVAL)
            source_started_at = time.time()
            try:
                if job["source_type"] not in {SourceType.GROUP, SourceType.PAGE, SourceType.USER}:
                    logger.info(
                        "Bỏ qua periodic_scrape_new_posts: thread=%s source=%s progress=%s/%s reason=unsupported_type type=%s",
                        thread_label,
                        source_label,
                        progress_index,
                        total_due_sources,
                        job["source_type"].value,
                    )
                    SourceCRUD.update_scrape_info(job_db, source_id, next_scrape=next_scrape)
                    return {"status": "skipped"}

                if job["is_accessible"] is False and job["permission_checked_at"] is not None:
                    logger.warning(
                        "Bỏ qua periodic_scrape_new_posts: thread=%s source=%s progress=%s/%s reason=inaccessible",
                        thread_label,
                        source_label,
                        progress_index,
                        total_due_sources,
                    )
                    SourceCRUD.update_scrape_info(job_db, source_id, next_scrape=next_scrape)
                    return {"status": "skipped"}

                latest_posted_at = job.get("latest_posted_at")
                pipeline_job = PipelineJobCRUD.create_job(
                    db=job_db,
                    job_type="scraper_job",
                    source_id=source_id,
                    session_id=job.get("active_session_id"),
                    status="running",
                    started_at=datetime.utcnow(),
                )
                pipeline_job_id = pipeline_job.id
                logger.info(
                    "Bắt đầu scrape source: thread=%s source=%s progress=%s/%s latest_cutoff=%s",
                    thread_label,
                    source_label,
                    progress_index,
                    total_due_sources,
                    latest_posted_at.isoformat() if latest_posted_at else None,
                )
                result = FacebookScraperService.scrape_source(
                    job_db,
                    source_id,
                    limit=10,
                    min_posted_at=latest_posted_at,
                    consecutive_old_limit=settings.SCRAPER_CONSECUTIVE_OLD_LIMIT,
                )
                PipelineJobCRUD.mark_done(
                    db=job_db,
                    job_id=pipeline_job_id,
                    posts_found=result.total_fetched,
                    posts_new=result.created_posts,
                    finished_at=datetime.utcnow(),
                )
                SourceCRUD.update_scrape_info(job_db, source_id, next_scrape=next_scrape)
                source_duration = round(time.time() - source_started_at, 3)
                logger.info(
                    "Kết thúc scrape source: thread=%s source=%s progress=%s/%s fetched=%s created_posts=%s skipped_posts=%s filtered_by_cutoff=%s duration_seconds=%s",
                    thread_label,
                    source_label,
                    progress_index,
                    total_due_sources,
                    result.total_fetched,
                    result.created_posts,
                    result.skipped_posts,
                    result.filtered_by_cutoff,
                    source_duration,
                )
                return {
                    "status": "scraped",
                    "created_posts": result.created_posts,
                    "updated_posts": result.updated_posts,
                    "fetched_posts": result.total_fetched,
                }
            except Exception as exc:
                if pipeline_job_id is not None:
                    PipelineJobCRUD.mark_failed(
                        db=job_db,
                        job_id=pipeline_job_id,
                        error_message=str(exc),
                        finished_at=datetime.utcnow(),
                    )
                logger.exception(
                    "Lỗi periodic_scrape_new_posts: thread=%s source=%s progress=%s/%s error=%s",
                    thread_label,
                    source_label,
                    progress_index,
                    total_due_sources,
                    exc,
                )
                SourceCRUD.update_scrape_info(job_db, source_id, next_scrape=next_scrape)
                LogCRUD.create_pipeline_log(
                    job_db,
                    message=f"Failed scraping source {source_id}",
                    log_level="ERROR",
                    job_id=pipeline_job_id,
                    source_id=source_id,
                    error_type=type(exc).__name__,
                    error_details=str(exc),
                )
                return {"status": "error"}
            finally:
                job_db.close()

        with ThreadPoolExecutor(max_workers=settings.SCRAPER_MAX_WORKERS) as executor:
            futures = [executor.submit(_scrape_source_job, job) for job in source_jobs]
            total_updated_posts = 0
            total_fetched_posts = 0
            for future in as_completed(futures):
                outcome = future.result()
                if outcome["status"] == "scraped":
                    scraped_count += 1
                    total_new_posts_created += outcome.get("created_posts", 0)
                    total_updated_posts += outcome.get("updated_posts", 0)
                    total_fetched_posts += outcome.get("fetched_posts", 0)
                elif outcome["status"] == "skipped":
                    skipped_count += 1
                else:
                    errors_count += 1
        total_duration = round(time.time() - started_at_ts, 3)
        logger.info(
            "Kết thúc periodic_scrape_new_posts: scraped_sources=%s skipped_sources=%s error_sources=%s total_due_sources=%s total_fetched_posts=%s total_new_posts_created=%s total_updated_posts=%s duration_seconds=%s source_per_second=%s post_per_second=%s",
            scraped_count,
            skipped_count,
            errors_count,
            total_due_sources,
            total_fetched_posts,
            total_new_posts_created,
            total_updated_posts,
            total_duration,
            _safe_rate(scraped_count + skipped_count + errors_count, total_duration),
            _safe_rate(total_fetched_posts, total_duration),
        )
        _finish_task_log(db, task_log, scraped_count, started_at_ts, errors_count=errors_count)
    except Exception as exc:
        logger.exception("periodic_scrape_new_posts thất bại: %s", exc)
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
        untracked_old_posts = PostCRUD.untrack_posts_older_than(db, hours=24)
        # recent_posts = PostCRUD.get_recent_posts(db, hours=24, limit=settings.SCRAPER_MAX_WORKERS * 50)
        recent_posts = PostCRUD.get_recent_posts(db, hours=24, limit=settings.SCRAPER_POSTS_BATCH_LIMIT)
        source_ids = list({post.source_id for post in recent_posts})
        target_posts_by_source = {}
        for post in recent_posts:
            target_posts_by_source.setdefault(post.source_id, []).append(str(post.facebook_post_id))
        logger.info(
            "Bắt đầu update_recent_post_metrics: recent_posts_count=%s candidate_source_count=%s untracked_old_posts=%s",
            len(recent_posts),
            len(source_ids),
            untracked_old_posts,
        )
        if not recent_posts:
            logger.info("Kết thúc update_recent_post_metrics: recent_posts_count=0 candidate_source_count=0")
            _finish_task_log(db, task_log, 0, started_at_ts)
            return

        total_sources = len(source_ids)
        source_index_map = {sid: idx for idx, sid in enumerate(source_ids, start=1)}

        def _refresh_metrics_job(source_id: int):
            job_db = SessionLocal()
            source_started_at = time.time()
            pipeline_job_id = None
            try:
                source = SourceCRUD.get_by_id(job_db, source_id)
                if not source or not source.is_active:
                    return {"status": "ignored"}
                source_label = _format_source_label(source.id, source.source_name)
                thread_label = _current_thread_label()
                progress_index = source_index_map.get(source_id, 0)
                if source.is_accessible is False and source.permission_checked_at is not None:
                    logger.warning(
                        "Bỏ qua update_recent_post_metrics: thread=%s source=%s progress=%s/%s reason=inaccessible",
                        thread_label,
                        source_label,
                        progress_index,
                        total_sources,
                    )
                    return {"status": "skipped"}
                if source.source_type not in {SourceType.GROUP, SourceType.PAGE, SourceType.USER}:
                    logger.info(
                        "Bỏ qua update_recent_post_metrics: thread=%s source=%s progress=%s/%s reason=unsupported_type type=%s",
                        thread_label,
                        source_label,
                        progress_index,
                        total_sources,
                        source.source_type.value,
                    )
                    return {"status": "skipped"}

                target_post_ids = target_posts_by_source.get(source.id, [])
                active_session = FacebookSessionCRUD.get_active_by_user_id(job_db, source.user_id)
                pipeline_job = PipelineJobCRUD.create_job(
                    db=job_db,
                    job_type="post_metric",
                    source_id=source.id,
                    session_id=active_session.id if active_session else None,
                    status="running",
                    started_at=datetime.utcnow(),
                )
                pipeline_job_id = pipeline_job.id
                logger.info(
                    "Bắt đầu cập nhật metric source: thread=%s source=%s progress=%s/%s target_posts_count=%s max_pages=%s use_24h_window=%s",
                    thread_label,
                    source_label,
                    progress_index,
                    total_sources,
                    len(target_post_ids),
                    settings.METRIC_REFRESH_MAX_PAGES,
                    settings.METRIC_REFRESH_USE_24H_WINDOW,
                )
                result = FacebookScraperService.refresh_target_post_metrics(
                    job_db,
                    source,
                    target_post_ids=target_post_ids,
                    max_pages=settings.METRIC_REFRESH_MAX_PAGES,
                    stop_when_all_found=True,
                    last_24_hours_only=settings.METRIC_REFRESH_USE_24H_WINDOW,
                    download_media=settings.METRIC_REFRESH_DOWNLOAD_MEDIA,
                )
                updated_post_refs = result.get("updated_post_refs", [])
                source_duration = round(time.time() - source_started_at, 3)
                fetched = result["fetched"]
                updated = result["updated"]
                fetch_to_update_ratio = round((fetched / updated), 3) if updated > 0 else (float(fetched) if fetched > 0 else 0.0)
                PipelineJobCRUD.mark_done(
                    db=job_db,
                    job_id=pipeline_job_id,
                    posts_found=fetched,
                    posts_new=updated,
                    items_total=fetched,
                    items_updated=updated,
                    items_failed=result.get("failed", 0),
                    finished_at=datetime.utcnow(),
                )
                logger.info(
                    "Kết thúc cập nhật metric source: thread=%s source=%s progress=%s/%s fetched=%s updated_count=%s skipped=%s target_posts_count=%s matched_target_count=%s pages_scanned=%s stop_reason=%s fetch_to_update_ratio=%s duration_seconds=%s",
                    thread_label,
                    source_label,
                    progress_index,
                    total_sources,
                    fetched,
                    updated,
                    result["skipped"],
                    result.get("target_posts_count", 0),
                    result.get("matched_target_count", 0),
                    result.get("pages_scanned", 0),
                    result.get("stop_reason", "unknown"),
                    fetch_to_update_ratio,
                    source_duration,
                )
                return {
                    "status": "refreshed",
                    "source_id": source.id,
                    "updated": updated,
                    "fetched": fetched,
                }
            except Exception as exc:
                if pipeline_job_id is not None:
                    PipelineJobCRUD.mark_failed(
                        db=job_db,
                        job_id=pipeline_job_id,
                        error_message=str(exc),
                        finished_at=datetime.utcnow(),
                    )
                logger.exception(
                    "Lỗi update_recent_post_metrics: thread=%s source_id=%s progress=%s/%s error=%s",
                    _current_thread_label(),
                    source_id,
                    source_index_map.get(source_id, 0),
                    total_sources,
                    exc,
                )
                LogCRUD.create_pipeline_log(
                    job_db,
                    message=f"Failed refreshing metrics for source {source_id}",
                    log_level="ERROR",
                    job_id=pipeline_job_id,
                    source_id=source_id,
                    error_type=type(exc).__name__,
                    error_details=str(exc),
                )
                return {"status": "error"}
            finally:
                job_db.close()

        with ThreadPoolExecutor(max_workers=settings.SCRAPER_MAX_WORKERS) as executor:
            futures = [executor.submit(_refresh_metrics_job, source_id) for source_id in source_ids]
            total_fetched = 0
            for future in as_completed(futures):
                outcome = future.result()
                if outcome["status"] == "refreshed":
                    refreshed_sources.add(outcome["source_id"])
                    updated_posts += outcome["updated"]
                    total_fetched += outcome.get("fetched", 0)
                elif outcome["status"] == "error":
                    errors_count += 1

        total_duration = round(time.time() - started_at_ts, 3)
        logger.info(
            "Kết thúc update_recent_post_metrics: refreshed_sources=%s updated_posts_total=%s error_sources=%s recent_posts_count=%s candidate_source_count=%s fetched_posts_total=%s duration_seconds=%s source_per_second=%s post_per_second=%s",
            len(refreshed_sources),
            updated_posts,
            errors_count,
            len(recent_posts),
            total_sources,
            total_fetched,
            total_duration,
            _safe_rate(len(refreshed_sources), total_duration),
            _safe_rate(updated_posts, total_duration),
        )
        _finish_task_log(db, task_log, updated_posts, started_at_ts, errors_count=errors_count)
    except Exception as exc:
        logger.exception("update_recent_post_metrics thất bại: %s", exc)
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

        pipeline_logs_deleted, pipeline_jobs_deleted, task_logs_deleted = LogCRUD.delete_old_logs(
            db,
            keep_days=settings.KEEP_DELETED_POSTS_DAYS,
        )
        db.commit()

        items_processed = (
            deleted_posts
            + deleted_metrics
            + deleted_comments
            + pipeline_logs_deleted
            + pipeline_jobs_deleted
            + task_logs_deleted
        )
        logger.info(
            "Cleanup finished: posts=%s metrics=%s comments=%s pipeline_logs=%s pipeline_jobs=%s task_logs=%s",
            deleted_posts,
            deleted_metrics,
            deleted_comments,
            pipeline_logs_deleted,
            pipeline_jobs_deleted,
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
    pipeline_job = PipelineJobCRUD.create_job(
        db=db,
        job_type="analytics",
        source_id=None,
        status="running",
        started_at=datetime.utcnow(),
    )

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
            total_engagement = total_likes + total_shares + total_comments
            avg_likes_per_post = (total_likes / total_posts) if total_posts else 0

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
                avg_likes_per_post=avg_likes_per_post,
                top_post_id=top_post.facebook_post_id if top_post else None,
                growth_rate=growth_rate,
            )
            processed_sources += 1

        logger.info("Generated analytics cache for %s sources", processed_sources)
        PipelineJobCRUD.mark_done(
            db=db,
            job_id=pipeline_job.id,
            items_total=processed_sources,
            items_updated=processed_sources,
            finished_at=datetime.utcnow(),
        )
        _finish_task_log(db, task_log, processed_sources, started_at_ts)
    except Exception as exc:
        logger.exception("generate_analytics_cache failed: %s", exc)
        PipelineJobCRUD.mark_failed(
            db=db,
            job_id=pipeline_job.id,
            error_message=str(exc),
            finished_at=datetime.utcnow(),
        )
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

