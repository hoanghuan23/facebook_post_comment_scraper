# Task scheduler using APScheduler
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime
import logging

logger = logging.getLogger("facebook_scraper")

# Global scheduler instance
scheduler: AsyncIOScheduler = None


async def start_scheduler():
    """Start the task scheduler"""
    global scheduler
    
    from backend.config import settings
    from backend.scheduler.periodic_tasks import (
        periodic_scrape_new_posts,
        update_recent_post_metrics,
        cleanup_old_data,
        generate_analytics_cache,
        health_check,
    )
    
    scheduler = AsyncIOScheduler()
    
    if settings.SCHEDULER_ENABLED:
        # Add scheduled tasks
        scheduler.add_job(
            periodic_scrape_new_posts,
            'interval',
            seconds=settings.TASK_SCRAPE_NEW_POSTS_INTERVAL,
            id='scrape_new_posts',
            name='Scrape new posts',
        )
        
        scheduler.add_job(
            update_recent_post_metrics,
            'interval',
            seconds=settings.TASK_UPDATE_RECENT_METRICS_INTERVAL,
            id='update_recent_metrics',
            name='Update recent post metrics',
        )
        
        scheduler.add_job(
            cleanup_old_data,
            'interval',
            seconds=settings.TASK_CLEANUP_OLD_DATA_INTERVAL,
            id='cleanup_old_data',
            name='Cleanup old data',
        )
        
        scheduler.add_job(
            generate_analytics_cache,
            'interval',
            seconds=settings.TASK_GENERATE_ANALYTICS_INTERVAL,
            id='generate_analytics',
            name='Generate analytics cache',
        )
        
        scheduler.add_job(
            health_check,
            'interval',
            seconds=settings.TASK_HEALTH_CHECK_INTERVAL,
            id='health_check',
            name='Health check',
        )
        
        scheduler.start()
        logger.info("✅ Task scheduler started with 5 jobs")


async def stop_scheduler():
    """Stop the task scheduler"""
    global scheduler
    if scheduler and scheduler.running:
        scheduler.shutdown()
        logger.info("🛑 Task scheduler stopped")


def get_scheduler() -> AsyncIOScheduler:
    """Get the scheduler instance"""
    global scheduler
    return scheduler


def add_job(func, trigger, **kwargs):
    """Add a job to the scheduler"""
    global scheduler
    if scheduler:
        scheduler.add_job(func, trigger, **kwargs)
        logger.info(f"📌 Job added: {kwargs.get('id', func.__name__)}")


def remove_job(job_id: str):
    """Remove a job from the scheduler"""
    global scheduler
    if scheduler:
        scheduler.remove_job(job_id)
        logger.info(f"🗑️ Job removed: {job_id}")


def pause_scheduler():
    """Pause the scheduler"""
    global scheduler
    if scheduler and scheduler.running:
        scheduler.pause()
        logger.info("⏸️ Scheduler paused")


def resume_scheduler():
    """Resume the scheduler"""
    global scheduler
    if scheduler:
        scheduler.resume()
        logger.info("▶️ Scheduler resumed")
