# CRUD Operations for Database Models
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, desc, func
from datetime import datetime, timedelta
import json
from typing import Dict, List, Optional, Tuple

from backend.database import models
from backend.api.auth import hash_password

# ==================== USER OPERATIONS ====================

class UserCRUD:
    """CRUD operations for User model"""
    
    @staticmethod
    def create(db: Session, username: str, email: str, password: str) -> models.User:
        """Create a new user"""
        hashed_password = hash_password(password)
        db_user = models.User(
            username=username,
            email=email,
            password_hash=hashed_password
        )
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
        return db_user
    
    @staticmethod
    def get_by_id(db: Session, user_id: int) -> Optional[models.User]:
        """Get user by ID"""
        return db.query(models.User).filter(models.User.id == user_id).first()
    
    @staticmethod
    def get_by_username(db: Session, username: str) -> Optional[models.User]:
        """Get user by username"""
        return db.query(models.User).filter(models.User.username == username).first()
    
    @staticmethod
    def get_by_email(db: Session, email: str) -> Optional[models.User]:
        """Get user by email"""
        return db.query(models.User).filter(models.User.email == email).first()
    
    @staticmethod
    def get_all(db: Session, skip: int = 0, limit: int = 100) -> List[models.User]:
        """Get all users with pagination"""
        return db.query(models.User).offset(skip).limit(limit).all()
    
    @staticmethod
    def update(db: Session, user_id: int, **kwargs) -> Optional[models.User]:
        """Update user (exclude password_hash)"""
        user = db.query(models.User).filter(models.User.id == user_id).first()
        if not user:
            return None
        
        # Allowed fields to update
        allowed_fields = {'email', 'is_active', 'is_admin'}
        
        for key, value in kwargs.items():
            if key in allowed_fields and value is not None:
                setattr(user, key, value)
        
        db.commit()
        db.refresh(user)
        return user
    
    @staticmethod
    def update_password(db: Session, user_id: int, new_password: str) -> Optional[models.User]:
        """Update user password"""
        user = db.query(models.User).filter(models.User.id == user_id).first()
        if not user:
            return None
        
        user.password_hash = hash_password(new_password)
        db.commit()
        db.refresh(user)
        return user
    
    @staticmethod
    def update_last_login(db: Session, user_id: int) -> Optional[models.User]:
        """Update last login timestamp"""
        user = db.query(models.User).filter(models.User.id == user_id).first()
        if not user:
            return None
        
        user.last_login = datetime.utcnow()
        db.commit()
        db.refresh(user)
        return user
    
    @staticmethod
    def delete(db: Session, user_id: int) -> bool:
        """Delete user"""
        user = db.query(models.User).filter(models.User.id == user_id).first()
        if not user:
            return False
        
        db.delete(user)
        db.commit()
        return True
    
    @staticmethod
    def count(db: Session) -> int:
        """Count total users"""
        return db.query(models.User).count()


class FacebookSessionCRUD:
    """CRUD operations for FacebookSession model"""

    @staticmethod
    def get_active_by_user_id(db: Session, user_id: int) -> Optional[models.FacebookSession]:
        """Get active Facebook session for a user."""
        return db.query(models.FacebookSession).filter(
            and_(
                models.FacebookSession.user_id == user_id,
                models.FacebookSession.is_active == True,
            )
        ).order_by(desc(models.FacebookSession.created_at), desc(models.FacebookSession.id)).first()

    @staticmethod
    def get_active_sessions_bulk(db: Session, user_ids: List[int]) -> Dict[int, models.FacebookSession]:
        """Get the newest active Facebook session for each user."""
        unique_user_ids = list({user_id for user_id in user_ids if user_id is not None})
        if not unique_user_ids:
            return {}

        sessions = db.query(models.FacebookSession).filter(
            and_(
                models.FacebookSession.user_id.in_(unique_user_ids),
                models.FacebookSession.is_active == True,
            )
        ).order_by(
            models.FacebookSession.user_id,
            desc(models.FacebookSession.created_at),
            desc(models.FacebookSession.id),
        ).all()

        sessions_by_user_id = {}
        for session in sessions:
            sessions_by_user_id.setdefault(session.user_id, session)
        return sessions_by_user_id

    @staticmethod
    def upsert_active_for_user(
        db: Session,
        user_id: int,
        fb_cookies: Optional[str] = None,
        fb_dtsg: Optional[str] = None,
        fb_user_agent: Optional[str] = None,
        fb_user_id: Optional[str] = None,
    ) -> models.FacebookSession:
        """Create or update a user's active Facebook session."""
        session = FacebookSessionCRUD.get_active_by_user_id(db, user_id)
        if not session:
            session = models.FacebookSession(
                user_id=user_id,
                is_active=True,
                is_valid=True,
            )
            db.add(session)

        if fb_cookies is not None:
            session.fb_cookies = fb_cookies
        if fb_dtsg is not None:
            session.fb_dtsg = fb_dtsg
        if fb_user_agent is not None:
            session.fb_user_agent = fb_user_agent
        if fb_user_id is not None:
            session.fb_user_id = fb_user_id

        db.commit()
        db.refresh(session)
        return session

    @staticmethod
    def upsert_from_login_extraction(
        db: Session,
        user_id: int,
        fb_cookies: Optional[str] = None,
        fb_dtsg: Optional[str] = None,
        fb_user_agent: Optional[str] = None,
    ) -> models.FacebookSession:
        """Persist an extracted login session and infer `fb_user_id` from cookies."""
        fb_user_id = None
        if fb_cookies:
            raw_cookies = str(fb_cookies).strip()
            parsed = None
            if raw_cookies.startswith("{") or raw_cookies.startswith("["):
                try:
                    parsed = json.loads(raw_cookies)
                except json.JSONDecodeError:
                    parsed = None

            if isinstance(parsed, dict):
                c_user_value = parsed.get("c_user")
                fb_user_id = str(c_user_value).strip() if c_user_value is not None else None
            elif isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict) and "c_user" in item:
                        c_user_value = item.get("c_user")
                        fb_user_id = str(c_user_value).strip() if c_user_value is not None else None
                        break
                    if isinstance(item, dict) and item.get("name") == "c_user":
                        c_user_value = item.get("value")
                        fb_user_id = str(c_user_value).strip() if c_user_value is not None else None
                        break
            else:
                for cookie_part in raw_cookies.split(";"):
                    cookie_part = cookie_part.strip()
                    if not cookie_part or "=" not in cookie_part:
                        continue
                    key, value = cookie_part.split("=", 1)
                    if key.strip() == "c_user":
                        fb_user_id = value.strip() or None
                        break

        return FacebookSessionCRUD.upsert_active_for_user(
            db=db,
            user_id=user_id,
            fb_cookies=fb_cookies,
            fb_dtsg=fb_dtsg,
            fb_user_agent=fb_user_agent,
            fb_user_id=fb_user_id,
        )


class PipelineJobCRUD:
    """CRUD operations for PipelineJob model."""

    @staticmethod
    def create_job(
        db: Session,
        job_type: str,
        source_id: Optional[int] = None,
        session_id: Optional[int] = None,
        status: str = "running",
        started_at: Optional[datetime] = None,
    ) -> models.PipelineJob:
        """Create a pipeline job row before execution starts."""
        db_job = models.PipelineJob(
            job_type=job_type,
            source_id=source_id,
            session_id=session_id,
            status=status,
            started_at=started_at or datetime.utcnow(),
        )
        db.add(db_job)
        db.commit()
        db.refresh(db_job)
        return db_job

    @staticmethod
    def mark_done(
        db: Session,
        job_id: int,
        posts_found: int = 0,
        posts_new: int = 0,
        finished_at: Optional[datetime] = None,
        **kwargs,
    ) -> Optional[models.PipelineJob]:
        """Mark pipeline job as done and update counters."""
        job = db.query(models.PipelineJob).filter(models.PipelineJob.id == job_id).first()
        if not job:
            return None

        job.status = "done"
        job.posts_found = posts_found
        job.posts_new = posts_new
        if kwargs.get("items_total") is not None:
            job.items_total = kwargs["items_total"]
        if kwargs.get("items_updated") is not None:
            job.items_updated = kwargs["items_updated"]
        if kwargs.get("items_failed") is not None:
            job.items_failed = kwargs["items_failed"]
        job.finished_at = finished_at or datetime.utcnow()
        db.commit()
        db.refresh(job)
        return job

    @staticmethod
    def mark_failed(
        db: Session,
        job_id: int,
        error_message: str,
        finished_at: Optional[datetime] = None,
    ) -> Optional[models.PipelineJob]:
        """Mark pipeline job as failed and store error message."""
        job = db.query(models.PipelineJob).filter(models.PipelineJob.id == job_id).first()
        if not job:
            return None

        job.status = "failed"
        job.error_message = error_message
        job.finished_at = finished_at or datetime.utcnow()
        db.commit()
        db.refresh(job)
        return job


ScrapeJobCRUD = PipelineJobCRUD


# ==================== SOURCE OPERATIONS ====================

class SourceCRUD:
    """CRUD operations for Source model"""
    
    @staticmethod
    def create(db: Session, user_id: int, source_type: str, facebook_id: str,
               facebook_url: str, source_name: str = None, **kwargs) -> models.Source:
        """Create a new source"""
        db_source = models.Source(
            user_id=user_id,
            source_type=source_type,
            facebook_id=facebook_id,
            facebook_url=facebook_url,
            source_name=source_name,
            include_comments=kwargs.get('include_comments', True),
            max_days_old=kwargs.get('max_days_old', 30),
            permission_status=kwargs.get('permission_status'),
            is_accessible=kwargs.get('is_accessible', False),
            permission_checked_at=kwargs.get('permission_checked_at'),
        )
        db.add(db_source)
        db.commit()
        db.refresh(db_source)
        return db_source
    
    @staticmethod
    def get_by_id(db: Session, source_id: int) -> Optional[models.Source]:
        """Get source by ID"""
        return db.query(models.Source).filter(models.Source.id == source_id).first()
    
    @staticmethod
    def get_by_user(
        db: Session,
        user_id: int,
        skip: int = 0,
        limit: int = 100,
        active_only: bool = False,
        tiers: Optional[List[int]] = None,
        sort: str = "created_at",
    ) -> List[models.Source]:
        """Get all sources for a user."""
        query = db.query(models.Source).filter(models.Source.user_id == user_id)

        if active_only:
            query = query.filter(models.Source.is_active == True)

        if tiers:
            query = query.filter(models.Source.schedule_tier.in_(tiers))

        if sort in {"posts_today", "engagement"}:
            today = datetime.utcnow().date().isoformat()
            today_metrics = (
                db.query(
                    models.AnalyticsCache.source_id.label("source_id"),
                    func.sum(models.AnalyticsCache.total_posts).label("posts_today"),
                    func.sum(
                        models.AnalyticsCache.total_likes
                        + models.AnalyticsCache.total_shares
                        + models.AnalyticsCache.total_comments
                    ).label("engagement"),
                )
                .filter(func.date(models.AnalyticsCache.date) == today)
                .group_by(models.AnalyticsCache.source_id)
                .subquery()
            )
            query = query.outerjoin(today_metrics, today_metrics.c.source_id == models.Source.id)

            if sort == "posts_today":
                query = query.order_by(
                    desc(func.coalesce(today_metrics.c.posts_today, 0)),
                    desc(models.Source.created_at),
                    desc(models.Source.id),
                )
            else:
                query = query.order_by(
                    desc(func.coalesce(today_metrics.c.engagement, 0)),
                    desc(models.Source.created_at),
                    desc(models.Source.id),
                )
        elif sort == "tier":
            query = query.order_by(
                func.coalesce(models.Source.schedule_tier, 5),
                desc(models.Source.created_at),
                desc(models.Source.id),
            )
        else:
            query = query.order_by(desc(models.Source.created_at), desc(models.Source.id))

        return query.offset(skip).limit(limit).all()
    
    @staticmethod
    def get_active_sources(db: Session, user_id: int) -> List[models.Source]:
        """Get active sources for a user"""
        return db.query(models.Source).filter(
            and_(models.Source.user_id == user_id, models.Source.is_active == True)
        ).all()
    
    @staticmethod
    def get_by_facebook_id(db: Session, facebook_id: str) -> Optional[models.Source]:
        """Get source by Facebook ID"""
        return db.query(models.Source).filter(models.Source.facebook_id == facebook_id).first()
    
    @staticmethod
    def get_by_user_and_facebook_id(db: Session, user_id: int, facebook_id: str) -> Optional[models.Source]:
        """Get source by user and Facebook ID (check for duplicates)"""
        return db.query(models.Source).filter(
            and_(models.Source.user_id == user_id, models.Source.facebook_id == facebook_id)
        ).first()
    
    @staticmethod
    def get_due_for_scraping(db: Session, limit: int = 10) -> List[models.Source]:
        """Get sources that need scraping (next_scrape < now)"""
        now = datetime.utcnow()
        return db.query(models.Source).filter(
            and_(
                models.Source.is_active == True,
                or_(
                    models.Source.next_scrape == None,
                    models.Source.next_scrape <= now
                )
            )
        ).order_by(
            models.Source.next_scrape.asc().nullsfirst(),
            models.Source.id.asc()
        ).limit(limit).all()
    
    @staticmethod
    def update(db: Session, source_id: int, **kwargs) -> Optional[models.Source]:
        """Update source"""
        source = db.query(models.Source).filter(models.Source.id == source_id).first()
        if not source:
            return None
        
        allowed_fields = {
            'source_name', 'description', 'include_comments',
            'max_days_old', 'is_active', 'member_count',
            'permission_status', 'is_accessible', 'permission_checked_at',
            'schedule_override_minutes',
        }
        nullable_fields = {'schedule_override_minutes'}
        
        for key, value in kwargs.items():
            if key in allowed_fields and (value is not None or key in nullable_fields):
                setattr(source, key, value)
        
        db.commit()
        db.refresh(source)
        return source
    
    @staticmethod
    def update_scrape_info(db: Session, source_id: int, last_scraped: datetime = None,
                          next_scrape: datetime = None) -> Optional[models.Source]:
        """Update scraping timestamps"""
        source = db.query(models.Source).filter(models.Source.id == source_id).first()
        if not source:
            return None
        
        if last_scraped:
            source.last_scraped = last_scraped
        if next_scrape:
            source.next_scrape = next_scrape
        
        db.commit()
        db.refresh(source)
        return source
    
    @staticmethod
    def delete(db: Session, source_id: int) -> bool:
        """Delete source"""
        source = db.query(models.Source).filter(models.Source.id == source_id).first()
        if not source:
            return False
        
        db.delete(source)
        db.commit()
        return True
    
    @staticmethod
    def count_by_user(db: Session, user_id: int) -> int:
        """Count sources for a user"""
        return db.query(models.Source).filter(models.Source.user_id == user_id).count()


# ==================== POST OPERATIONS ====================

class PostCRUD:
    """CRUD operations for Post model"""
    
    @staticmethod
    def create(db: Session, source_id: int, facebook_post_id: str, facebook_url: str,
               posted_at: datetime, content: str = None, **kwargs) -> models.Post:
        """Create a new post"""
        db_post = models.Post(
            source_id=source_id,
            facebook_post_id=facebook_post_id,
            facebook_url=facebook_url,
            posted_at=posted_at,
            content=content,
            media_count=kwargs.get('media_count', 0),
            has_images=kwargs.get('has_images', False),
            has_videos=kwargs.get('has_videos', False),
            last_metric_update=kwargs.get('last_metric_update'),
        )
        db.add(db_post)
        db.commit()
        db.refresh(db_post)
        return db_post
    
    @staticmethod
    def get_by_id(db: Session, post_id: int) -> Optional[models.Post]:
        """Get post by ID"""
        return db.query(models.Post).filter(models.Post.id == post_id).first()
    
    @staticmethod
    def get_by_facebook_post_id(db: Session, facebook_post_id: str) -> Optional[models.Post]:
        """Get post by Facebook post ID"""
        return db.query(models.Post).filter(models.Post.facebook_post_id == facebook_post_id).first()

    @staticmethod
    def get_by_source_and_facebook_post_id(
        db: Session, source_id: int, facebook_post_id: str
    ) -> Optional[models.Post]:
        """Get post by source and Facebook post ID."""
        return db.query(models.Post).filter(
            and_(
                models.Post.source_id == source_id,
                models.Post.facebook_post_id == facebook_post_id,
            )
        ).first()
    
    @staticmethod
    def get_by_source(db: Session, source_id: int, skip: int = 0, limit: int = 50,
                      tracked_only: bool = True) -> List[models.Post]:
        """Get posts from a source"""
        query = db.query(models.Post).filter(models.Post.source_id == source_id)
        
        if tracked_only:
            query = query.filter(models.Post.is_tracked == True)
        
        return query.order_by(desc(models.Post.posted_at)).offset(skip).limit(limit).all()

    @staticmethod
    def get_latest_posted_at_by_source(db: Session, source_id: int, tracked_only: bool = True) -> Optional[datetime]:
        """Get latest posted_at timestamp for a source."""
        query = db.query(func.max(models.Post.posted_at)).filter(models.Post.source_id == source_id)
        if tracked_only:
            query = query.filter(models.Post.is_tracked == True)
        return query.scalar()

    @staticmethod
    def get_latest_posted_at_bulk(
        db: Session,
        source_ids: List[int],
        tracked_only: bool = True,
    ) -> Dict[int, Optional[datetime]]:
        """Get latest posted_at timestamp for multiple sources."""
        unique_source_ids = list({source_id for source_id in source_ids if source_id is not None})
        if not unique_source_ids:
            return {}

        query = db.query(
            models.Post.source_id,
            func.max(models.Post.posted_at),
        ).filter(models.Post.source_id.in_(unique_source_ids))

        if tracked_only:
            query = query.filter(models.Post.is_tracked == True)

        rows = query.group_by(models.Post.source_id).all()
        return {source_id: latest_posted_at for source_id, latest_posted_at in rows}
    
    @staticmethod
    def get_recent_posts(db: Session, hours: int = 24, limit: int = 100) -> List[models.Post]:
        """Get tracked posts posted in the last N hours."""
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)
        return db.query(models.Post).filter(
            and_(
                models.Post.posted_at >= cutoff_time,
                models.Post.is_tracked == True,
                models.Post.is_deleted == False,
            )
        ).order_by(
            models.Post.last_metric_update.isnot(None),
            models.Post.last_metric_update.asc(),
            desc(models.Post.posted_at),
            models.Post.id.asc(),
        ).limit(limit).all()

    @staticmethod
    def untrack_posts_older_than(db: Session, hours: int = 24) -> int:
        """Stop tracking non-deleted posts once they age out of the tracking window."""
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)
        posts = db.query(models.Post).filter(
            and_(
                models.Post.posted_at < cutoff_time,
                models.Post.is_tracked == True,
                models.Post.is_deleted == False,
            )
        ).all()

        for post in posts:
            post.is_tracked = False

        db.commit()
        return len(posts)
    
    @staticmethod
    def get_old_posts(db: Session, days: int = 30) -> List[models.Post]:
        """Get posts older than N days"""
        cutoff_time = datetime.utcnow() - timedelta(days=days)
        return db.query(models.Post).filter(
            models.Post.posted_at < cutoff_time
        ).all()
    
    @staticmethod
    def get_posts_needing_update(db: Session, hours: int = 6, limit: int = 100) -> List[models.Post]:
        """Get posts that need metric update (haven't been updated in N hours)"""
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)
        return db.query(models.Post).filter(
            and_(
                models.Post.is_tracked == True,
                or_(
                    models.Post.last_metric_update == None,
                    models.Post.last_metric_update < cutoff_time
                )
            )
        ).order_by(models.Post.last_metric_update).limit(limit).all()
    
    @staticmethod
    def update_metrics(db: Session, post_id: int, likes: int, shares: int,
                      comments: int, views: int = None) -> Optional[models.Post]:
        """Update post metrics"""
        post = db.query(models.Post).filter(models.Post.id == post_id).first()
        if not post:
            return None
        post.last_metric_update = datetime.utcnow()
        
        db.commit()
        db.refresh(post)
        return post
    
    @staticmethod
    def update(db: Session, post_id: int, **kwargs) -> Optional[models.Post]:
        """Update post"""
        post = db.query(models.Post).filter(models.Post.id == post_id).first()
        if not post:
            return None
        
        allowed_fields = {
            'content', 'is_tracked', 'is_deleted', 'media_count', 'has_images', 'has_videos',
            'facebook_url', 'posted_at'
        }
        
        for key, value in kwargs.items():
            if key in allowed_fields and value is not None:
                setattr(post, key, value)
        
        db.commit()
        db.refresh(post)
        return post

    @staticmethod
    def upsert_for_source(
        db: Session,
        source_id: int,
        facebook_post_id: str,
        facebook_url: str,
        posted_at: datetime,
        content: str = None,
        **kwargs,
    ) -> Tuple[models.Post, bool]:
        """Create post if missing, otherwise refresh its latest core fields."""
        existing_post = PostCRUD.get_by_source_and_facebook_post_id(db, source_id, facebook_post_id)
        if existing_post:
            existing_post.facebook_url = facebook_url
            existing_post.posted_at = posted_at
            existing_post.content = content
            existing_post.media_count = kwargs.get('media_count', existing_post.media_count)
            existing_post.has_images = kwargs.get('has_images', existing_post.has_images)
            existing_post.has_videos = kwargs.get('has_videos', existing_post.has_videos)
            existing_post.is_tracked = True
            existing_post.is_deleted = False
            db.commit()
            db.refresh(existing_post)
            return existing_post, False

        created_post = PostCRUD.create(
            db=db,
            source_id=source_id,
            facebook_post_id=facebook_post_id,
            facebook_url=facebook_url,
            posted_at=posted_at,
            content=content,
            **kwargs,
        )
        return created_post, True
    
    @staticmethod
    def delete(db: Session, post_id: int) -> bool:
        """Delete post (soft delete)"""
        post = db.query(models.Post).filter(models.Post.id == post_id).first()
        if not post:
            return False
        
        post.is_tracked = False
        post.is_deleted = True
        db.commit()
        return True
    
    @staticmethod
    def count_by_source(db: Session, source_id: int) -> int:
        """Count posts from a source"""
        return db.query(models.Post).filter(models.Post.source_id == source_id).count()
    
    @staticmethod
    def count_recent(db: Session, source_id: int, hours: int = 24) -> int:
        """Count recent posts from a source"""
        cutoff_time = datetime.utcnow() - timedelta(hours=hours)
        return db.query(models.Post).filter(
            and_(
                models.Post.source_id == source_id,
                models.Post.posted_at >= cutoff_time
            )
        ).count()


# ==================== POST METRIC OPERATIONS ====================

class PostMetricCRUD:
    """CRUD operations for PostMetric model"""
    
    @staticmethod
    def create(db: Session, post_id: int, likes: int, shares: int, comments: int) -> models.PostMetric:
        """Create a metric snapshot"""
        db_metric = models.PostMetric(
            post_id=post_id,
            likes_count=likes,
            shares_count=shares,
            comments_count=comments,
        )
        db.add(db_metric)
        db.commit()
        db.refresh(db_metric)
        return db_metric
    
    @staticmethod
    def get_by_post(db: Session, post_id: int, limit: int = None) -> List[models.PostMetric]:
        """Get all metrics for a post (ordered by time)"""
        query = db.query(models.PostMetric).filter(
            models.PostMetric.post_id == post_id
        ).order_by(models.PostMetric.recorded_at)
        
        if limit:
            query = query.limit(limit)
        
        return query.all()
    
    @staticmethod
    def get_latest_metric(db: Session, post_id: int) -> Optional[models.PostMetric]:
        """Get latest metric for a post"""
        return db.query(models.PostMetric).filter(
            models.PostMetric.post_id == post_id
        ).order_by(desc(models.PostMetric.recorded_at)).first()
    
    @staticmethod
    def get_metrics_between(db: Session, post_id: int, start_time: datetime,
                           end_time: datetime) -> List[models.PostMetric]:
        """Get metrics within a time range"""
        return db.query(models.PostMetric).filter(
            and_(
                models.PostMetric.post_id == post_id,
                models.PostMetric.recorded_at >= start_time,
                models.PostMetric.recorded_at <= end_time
            )
        ).order_by(models.PostMetric.recorded_at).all()
    
    @staticmethod
    def delete_old_metrics(db: Session, post_id: int, keep_days: int = 90) -> int:
        """Delete metrics older than N days"""
        cutoff_time = datetime.utcnow() - timedelta(days=keep_days)
        
        old_metrics = db.query(models.PostMetric).filter(
            and_(
                models.PostMetric.post_id == post_id,
                models.PostMetric.recorded_at < cutoff_time
            )
        ).all()
        
        count = len(old_metrics)
        for metric in old_metrics:
            db.delete(metric)
        
        db.commit()
        return count


# ==================== COMMENT OPERATIONS ====================

class CommentCRUD:
    """CRUD operations for Comment model"""
    
    @staticmethod
    def create(db: Session, post_id: int, facebook_comment_id: str, comment_text: str,
               **kwargs) -> models.Comment:
        """Create a new comment"""
        db_comment = models.Comment(
            post_id=post_id,
            facebook_comment_id=facebook_comment_id,
            comment_text=comment_text,
            commenter_id=kwargs.get('commenter_id'),
            commenter_name=kwargs.get('commenter_name'),
            commenter_url=kwargs.get('commenter_url'),
            likes_count=kwargs.get('likes_count', 0),
            reply_count=kwargs.get('reply_count', 0),
            parent_id=kwargs.get('parent_id'),
            depth_level=kwargs.get('depth_level', 0),
        )
        db.add(db_comment)
        db.commit()
        db.refresh(db_comment)
        return db_comment
    
    @staticmethod
    def get_by_id(db: Session, comment_id: int) -> Optional[models.Comment]:
        """Get comment by ID"""
        return db.query(models.Comment).filter(models.Comment.id == comment_id).first()
    
    @staticmethod
    def get_by_facebook_id(db: Session, facebook_comment_id: str) -> Optional[models.Comment]:
        """Get comment by Facebook ID"""
        return db.query(models.Comment).filter(
            models.Comment.facebook_comment_id == facebook_comment_id
        ).first()

    @staticmethod
    def upsert(db: Session, post_id: int, facebook_comment_id: str, comment_text: str,
               **kwargs) -> models.Comment:
        """Create comment if missing, otherwise refresh mutable fields."""
        existing_comment = CommentCRUD.get_by_facebook_id(db, facebook_comment_id)
        if existing_comment:
            existing_comment.post_id = post_id
            existing_comment.comment_text = comment_text
            existing_comment.commenter_id = kwargs.get('commenter_id')
            existing_comment.commenter_name = kwargs.get('commenter_name')
            existing_comment.commenter_url = kwargs.get('commenter_url')
            existing_comment.likes_count = kwargs.get('likes_count', existing_comment.likes_count)
            existing_comment.reply_count = kwargs.get('reply_count', existing_comment.reply_count)
            existing_comment.parent_id = kwargs.get('parent_id')
            existing_comment.depth_level = kwargs.get('depth_level', existing_comment.depth_level)
            existing_comment.last_updated = datetime.utcnow()
            db.commit()
            db.refresh(existing_comment)
            return existing_comment

        return CommentCRUD.create(
            db=db,
            post_id=post_id,
            facebook_comment_id=facebook_comment_id,
            comment_text=comment_text,
            **kwargs,
        )
    
    @staticmethod
    def get_by_post(db: Session, post_id: int, skip: int = 0, limit: int = 100,
                    top_level_only: bool = False) -> List[models.Comment]:
        """Get comments for a post"""
        query = db.query(models.Comment).filter(models.Comment.post_id == post_id)
        
        if top_level_only:
            query = query.filter(models.Comment.depth_level == 0)
        
        return query.order_by(desc(models.Comment.created_at)).offset(skip).limit(limit).all()
    
    @staticmethod
    def get_replies(db: Session, parent_comment_id: str) -> List[models.Comment]:
        """Get replies to a comment"""
        parent_comment = CommentCRUD.get_by_facebook_id(db, parent_comment_id)
        if not parent_comment:
            return []
        return db.query(models.Comment).filter(
            models.Comment.parent_id == parent_comment.id
        ).order_by(models.Comment.created_at).all()
    
    @staticmethod
    def update_metrics(db: Session, comment_id: int, likes: int, replies: int) -> Optional[models.Comment]:
        """Update comment metrics"""
        comment = db.query(models.Comment).filter(models.Comment.id == comment_id).first()
        if not comment:
            return None
        
        comment.likes_count = likes
        comment.reply_count = replies
        comment.last_updated = datetime.utcnow()
        
        db.commit()
        db.refresh(comment)
        return comment
    
    @staticmethod
    def delete(db: Session, comment_id: int) -> bool:
        """Delete comment"""
        comment = db.query(models.Comment).filter(models.Comment.id == comment_id).first()
        if not comment:
            return False
        
        db.delete(comment)
        db.commit()
        return True
    
    @staticmethod
    def count_by_post(db: Session, post_id: int) -> int:
        """Count comments on a post"""
        return db.query(models.Comment).filter(models.Comment.post_id == post_id).count()
    
    @staticmethod
    def delete_old_comments(db: Session, post_id: int, keep_days: int = 90) -> int:
        """Delete comments older than N days"""
        cutoff_time = datetime.utcnow() - timedelta(days=keep_days)
        
        old_comments = db.query(models.Comment).filter(
            and_(
                models.Comment.post_id == post_id,
                models.Comment.created_at < cutoff_time
            )
        ).all()
        
        count = len(old_comments)
        for comment in old_comments:
            db.delete(comment)
        
        db.commit()
        return count


# ==================== ANALYTICS CACHE OPERATIONS ====================

class AnalyticsCRUD:
    """CRUD operations for AnalyticsCache model"""
    
    @staticmethod
    def create(db: Session, source_id: int, date: datetime, **metrics) -> models.AnalyticsCache:
        """Create analytics cache entry"""
        db_cache = models.AnalyticsCache(
            source_id=source_id,
            date=date,
            total_posts=metrics.get('total_posts', 0),
            total_likes=metrics.get('total_likes', 0),
            total_shares=metrics.get('total_shares', 0),
            total_comments=metrics.get('total_comments', 0),
            avg_likes_per_post=metrics.get('avg_likes_per_post'),
            top_post_id=metrics.get('top_post_id'),
            growth_rate=metrics.get('growth_rate'),
        )
        db.add(db_cache)
        db.commit()
        db.refresh(db_cache)
        return db_cache
    
    @staticmethod
    def get_by_source_and_date(db: Session, source_id: int, date: datetime) -> Optional[models.AnalyticsCache]:
        """Get analytics for source on specific date"""
        return db.query(models.AnalyticsCache).filter(
            and_(
                models.AnalyticsCache.source_id == source_id,
                models.AnalyticsCache.date == date
            )
        ).first()
    
    @staticmethod
    def get_by_source(db: Session, source_id: int, skip: int = 0, limit: int = 90) -> List[models.AnalyticsCache]:
        """Get analytics history for a source"""
        return db.query(models.AnalyticsCache).filter(
            models.AnalyticsCache.source_id == source_id
        ).order_by(desc(models.AnalyticsCache.date)).offset(skip).limit(limit).all()
    
    @staticmethod
    def get_date_range(db: Session, source_id: int, start_date: datetime,
                      end_date: datetime) -> List[models.AnalyticsCache]:
        """Get analytics for date range"""
        return db.query(models.AnalyticsCache).filter(
            and_(
                models.AnalyticsCache.source_id == source_id,
                models.AnalyticsCache.date >= start_date,
                models.AnalyticsCache.date <= end_date
            )
        ).order_by(models.AnalyticsCache.date).all()
    
    @staticmethod
    def update(db: Session, source_id: int, date: datetime, **metrics) -> Optional[models.AnalyticsCache]:
        """Update analytics cache"""
        cache = db.query(models.AnalyticsCache).filter(
            and_(
                models.AnalyticsCache.source_id == source_id,
                models.AnalyticsCache.date == date
            )
        ).first()
        
        if not cache:
            return AnalyticsCRUD.create(db, source_id, date, **metrics)
        
        allowed_fields = {
            'total_posts', 'total_likes', 'total_shares', 'total_comments',
            'avg_likes_per_post', 'top_post_id', 'growth_rate'
        }
        
        for key, value in metrics.items():
            if key in allowed_fields and value is not None:
                setattr(cache, key, value)
        
        cache.cached_at = datetime.utcnow()
        db.commit()
        db.refresh(cache)
        return cache
    
    @staticmethod
    def delete(db: Session, source_id: int, date: datetime) -> bool:
        """Delete analytics cache entry"""
        cache = db.query(models.AnalyticsCache).filter(
            and_(
                models.AnalyticsCache.source_id == source_id,
                models.AnalyticsCache.date == date
            )
        ).first()
        
        if not cache:
            return False
        
        db.delete(cache)
        db.commit()
        return True


# ==================== LOG OPERATIONS ====================

class LogCRUD:
    """CRUD operations for Logs"""
    
    @staticmethod
    def create_pipeline_log(
        db: Session,
        message: str,
        log_level: str = "INFO",
        job_id: int = None,
        source_id: int = None,
        error_type: str = None,
        error_details: str = None,
    ) -> models.PipelineLog:
        """Create pipeline log entry"""
        db_log = models.PipelineLog(
            job_id=job_id,
            source_id=source_id,
            log_level=log_level,
            message=message,
            error_type=error_type,
            error_details=error_details,
        )
        db.add(db_log)
        db.commit()
        db.refresh(db_log)
        return db_log

    @staticmethod
    def create_scraper_log(
        db: Session,
        message: str,
        log_level: str = "INFO",
        source_id: int = None,
        error_type: str = None,
        error_details: str = None,
        job_id: int = None,
    ) -> models.PipelineLog:
        """Compatibility wrapper for the old scraper log API."""
        return LogCRUD.create_pipeline_log(
            db,
            message=message,
            log_level=log_level,
            job_id=job_id,
            source_id=source_id,
            error_type=error_type,
            error_details=error_details,
        )
    
    @staticmethod
    def create_task_log(db: Session, task_name: str, status: str = "PENDING",
                       **kwargs) -> models.TaskLog:
        """Create task execution log"""
        db_log = models.TaskLog(
            task_name=task_name,
            status=status,
            started_at=kwargs.get('started_at'),
            completed_at=kwargs.get('completed_at'),
            duration_seconds=kwargs.get('duration_seconds'),
            items_processed=kwargs.get('items_processed', 0),
            errors_count=kwargs.get('errors_count', 0),
            error_message=kwargs.get('error_message'),
        )
        db.add(db_log)
        db.commit()
        db.refresh(db_log)
        return db_log
    
    @staticmethod
    def update_task_log(db: Session, log_id: int, **kwargs) -> Optional[models.TaskLog]:
        """Update task log"""
        log = db.query(models.TaskLog).filter(models.TaskLog.id == log_id).first()
        if not log:
            return None
        
        allowed_fields = {
            'status', 'completed_at', 'duration_seconds', 'items_processed',
            'errors_count', 'error_message'
        }
        
        for key, value in kwargs.items():
            if key in allowed_fields and value is not None:
                setattr(log, key, value)
        
        db.commit()
        db.refresh(log)
        return log
    
    @staticmethod
    def get_pipeline_logs(
        db: Session,
        log_level: str = None,
        job_type: str = None,
        source_id: int = None,
        limit: int = 100,
    ) -> List[models.PipelineLog]:
        """Get pipeline logs"""
        query = db.query(models.PipelineLog).outerjoin(models.PipelineJob, models.PipelineLog.job_id == models.PipelineJob.id)
        
        if log_level:
            query = query.filter(models.PipelineLog.log_level == log_level)
        if job_type:
            query = query.filter(models.PipelineJob.job_type == job_type)
        if source_id is not None:
            query = query.filter(models.PipelineLog.source_id == source_id)
        
        return query.order_by(desc(models.PipelineLog.created_at)).limit(limit).all()

    @staticmethod
    def get_logs(db: Session, log_level: str = None, limit: int = 100) -> List[models.PipelineLog]:
        """Compatibility wrapper for the old log API."""
        return LogCRUD.get_pipeline_logs(db, log_level=log_level, limit=limit)
    
    @staticmethod
    def get_task_logs(db: Session, task_name: str = None, limit: int = 100) -> List[models.TaskLog]:
        """Get task logs"""
        query = db.query(models.TaskLog)
        
        if task_name:
            query = query.filter(models.TaskLog.task_name == task_name)
        
        return query.order_by(desc(models.TaskLog.created_at)).limit(limit).all()
    
    @staticmethod
    def delete_old_logs(db: Session, keep_days: int = 30) -> Tuple[int, int, int]:
        """Delete logs older than N days"""
        cutoff_time = datetime.utcnow() - timedelta(days=keep_days)
        
        # Delete pipeline logs
        old_pipeline_logs = db.query(models.PipelineLog).filter(
            models.PipelineLog.created_at < cutoff_time
        ).all()
        pipeline_log_count = len(old_pipeline_logs)
        for log in old_pipeline_logs:
            db.delete(log)

        # Delete pipeline jobs
        old_pipeline_jobs = db.query(models.PipelineJob).filter(
            func.coalesce(models.PipelineJob.finished_at, models.PipelineJob.started_at) < cutoff_time
        ).all()
        pipeline_job_count = len(old_pipeline_jobs)
        for job in old_pipeline_jobs:
            db.delete(job)
        
        # Delete task logs
        old_task_logs = db.query(models.TaskLog).filter(
            models.TaskLog.created_at < cutoff_time
        ).all()
        task_count = len(old_task_logs)
        for log in old_task_logs:
            db.delete(log)
        
        db.commit()
        return pipeline_log_count, pipeline_job_count, task_count


# ==================== HELPER FUNCTIONS ====================

def duplicate_check_user(db: Session, username: str = None, email: str = None) -> bool:
    """Check if user already exists"""
    if username:
        if db.query(models.User).filter(models.User.username == username).first():
            return True
    if email:
        if db.query(models.User).filter(models.User.email == email).first():
            return True
    return False


def duplicate_check_source(db: Session, user_id: int, facebook_id: str) -> bool:
    """Check if source already exists for user"""
    return SourceCRUD.get_by_user_and_facebook_id(db, user_id, facebook_id) is not None


def duplicate_check_post(db: Session, facebook_post_id: str) -> bool:
    """Check if post already exists"""
    return PostCRUD.get_by_facebook_post_id(db, facebook_post_id) is not None


def duplicate_check_comment(db: Session, facebook_comment_id: str) -> bool:
    """Check if comment already exists"""
    return CommentCRUD.get_by_facebook_id(db, facebook_comment_id) is not None


def get_user_stats(db: Session, user_id: int) -> dict:
    """Get statistics for a user"""
    sources_count = SourceCRUD.count_by_user(db, user_id)
    
    user_posts = db.query(models.Post).join(models.Source).filter(
        models.Source.user_id == user_id
    ).all()
    posts_count = len(user_posts)
    
    total_likes = sum([p.current_likes for p in user_posts])
    total_comments = sum([p.current_comments for p in user_posts])
    total_shares = sum([p.current_shares for p in user_posts])
    
    return {
        'sources_count': sources_count,
        'posts_count': posts_count,
        'total_likes': total_likes,
        'total_comments': total_comments,
        'total_shares': total_shares,
        'total_engagement': total_likes + total_comments + total_shares,
    }


def get_source_stats(db: Session, source_id: int) -> dict:
    """Get statistics for a source"""
    posts = db.query(models.Post).filter(models.Post.source_id == source_id).all()
    posts_count = len(posts)
    
    total_likes = sum([p.current_likes for p in posts])
    total_comments = sum([p.current_comments for p in posts])
    total_shares = sum([p.current_shares for p in posts])
    total_engagement = total_likes + total_comments + total_shares
    
    avg_likes = total_likes / posts_count if posts_count > 0 else 0
    avg_comments = total_comments / posts_count if posts_count > 0 else 0
    avg_shares = total_shares / posts_count if posts_count > 0 else 0
    
    return {
        'posts_count': posts_count,
        'total_likes': total_likes,
        'total_comments': total_comments,
        'total_shares': total_shares,
        'total_engagement': total_engagement,
        'avg_likes': avg_likes,
        'avg_comments': avg_comments,
        'avg_shares': avg_shares,
    }


def calculate_engagement_growth(db: Session, post_id: int) -> dict:
    """Calculate engagement growth for a post"""
    post = PostCRUD.get_by_id(db, post_id)
    if not post:
        return {}
    
    initial_total = post.initial_likes + post.initial_shares + post.initial_comments
    current_total = post.current_likes + post.current_shares + post.current_comments
    
    return {
        'likes_growth': post.current_likes - post.initial_likes,
        'shares_growth': post.current_shares - post.initial_shares,
        'comments_growth': post.current_comments - post.initial_comments,
        'total_growth': current_total - initial_total,
        'likes_growth_percent': ((post.current_likes - post.initial_likes) / post.initial_likes * 100) if post.initial_likes > 0 else 0,
        'shares_growth_percent': ((post.current_shares - post.initial_shares) / post.initial_shares * 100) if post.initial_shares > 0 else 0,
        'comments_growth_percent': ((post.current_comments - post.initial_comments) / post.initial_comments * 100) if post.initial_comments > 0 else 0,
    }
