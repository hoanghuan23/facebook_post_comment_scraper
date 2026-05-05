from datetime import datetime
import enum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import column_property, relationship

Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    fb_cookies = Column(Text, nullable=True)
    fb_dtsg = Column(String(255), nullable=True)
    fb_user_agent = Column(String(500), nullable=True)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)

    sources = relationship("Source", back_populates="user", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_user_username", "username"),
        Index("idx_user_email", "email"),
    )


class SourceType(str, enum.Enum):
    GROUP = "group"
    PAGE = "page"
    USER = "user"


class PermissionStatus(str, enum.Enum):
    GRANTED = "granted"
    DENIED = "denied"
    RESTRICTED = "restricted"
    NOT_CHECKED = "not_checked"
    ERROR = "error"


class Source(Base):
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    source_type = Column(Enum(SourceType), nullable=False)
    facebook_id = Column(String(50), nullable=False)
    facebook_url = Column(String(255), nullable=False)
    source_name = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    cover_image_url = Column(String(500), nullable=True)
    member_count = Column(Integer, nullable=True)
    follower_count = Column(Integer, nullable=True)
    is_active = Column(Boolean, default=True)
    include_comments = Column(Boolean, default=True)
    include_replies = Column(Boolean, default=True)
    max_days_old = Column(Integer, default=30)
    permission_status = Column(Enum(PermissionStatus), default=PermissionStatus.NOT_CHECKED)
    permission_message = Column(Text, nullable=True)
    access_restrictions = Column(Text, nullable=True)
    permission_checked_at = Column(DateTime, nullable=True)
    is_accessible = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_scraped = Column(DateTime, nullable=True)
    next_scrape = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="sources")
    posts = relationship("Post", back_populates="source", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("user_id", "facebook_id", name="uq_user_source"),
        Index("idx_source_user_active", "user_id", "is_active"),
        Index("idx_source_next_scrape", "next_scrape"),
        Index("idx_source_permission", "permission_status"),
        Index("idx_source_accessible", "is_accessible"),
    )


class Post(Base):
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=False)
    facebook_post_id = Column(String(100), nullable=False, unique=True, index=True)
    facebook_url = Column(String(500), nullable=False)
    content = Column(Text, nullable=True)
    media_count = Column(Integer, default=0)
    has_images = Column(Boolean, default=False)
    has_videos = Column(Boolean, default=False)
    posted_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_tracked = Column(Boolean, default=True)
    tracking_until = Column(DateTime, nullable=True)
    is_deleted = Column(Boolean, default=False)
    last_metric_update = Column(DateTime, nullable=True)

    source = relationship("Source", back_populates="posts")
    metrics_history = relationship("PostMetric", back_populates="post", cascade="all, delete-orphan")
    comments = relationship("Comment", back_populates="post", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_post_source", "source_id"),
        Index("idx_post_facebook_id", "facebook_post_id"),
        Index("idx_post_posted_at", "posted_at"),
        Index("idx_post_last_update", "last_metric_update"),
    )


class PostMetric(Base):
    __tablename__ = "post_metrics"

    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=False)
    likes_count = Column(Integer, default=0)
    shares_count = Column(Integer, default=0)
    comments_count = Column(Integer, default=0)
    views_count = Column(Integer, nullable=True)
    recorded_at = Column(DateTime, default=datetime.utcnow, index=True)

    post = relationship("Post", back_populates="metrics_history")

    __table_args__ = (
        Index("idx_metric_post_date", "post_id", "recorded_at"),
    )

    @property
    def engagement_rate(self):
        total = (self.likes_count or 0) + (self.shares_count or 0) + (self.comments_count or 0)
        return ((total / self.views_count) * 100) if self.views_count else None

    @property
    def comment_ratio(self):
        total = (self.likes_count or 0) + (self.shares_count or 0) + (self.comments_count or 0)
        return ((self.comments_count or 0) / total) if total else None


class Comment(Base):
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=False)
    parent_id = Column(Integer, ForeignKey("comments.id"), nullable=True)
    facebook_comment_id = Column(String(100), nullable=False, unique=True)
    commenter_id = Column(String(50), nullable=True)
    commenter_name = Column(String(255), nullable=True)
    commenter_url = Column(String(500), nullable=True)
    comment_text = Column(Text, nullable=True)
    likes_count = Column(Integer, default=0)
    reply_count = Column(Integer, default=0)
    depth_level = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_updated = Column(DateTime, nullable=True)

    post = relationship("Post", back_populates="comments")
    parent = relationship("Comment", remote_side=[id], backref="replies")

    __table_args__ = (
        Index("idx_comment_post", "post_id"),
        Index("idx_comment_facebook_id", "facebook_comment_id"),
    )

    @property
    def parent_comment_id(self):
        return self.parent.facebook_comment_id if self.parent else None


Post.current_likes = column_property(
    func.coalesce(
        select(PostMetric.likes_count)
        .where(PostMetric.post_id == Post.id)
        .order_by(PostMetric.recorded_at.desc(), PostMetric.id.desc())
        .limit(1)
        .correlate_except(PostMetric)
        .scalar_subquery(),
        0,
    )
)
Post.current_shares = column_property(
    func.coalesce(
        select(PostMetric.shares_count)
        .where(PostMetric.post_id == Post.id)
        .order_by(PostMetric.recorded_at.desc(), PostMetric.id.desc())
        .limit(1)
        .correlate_except(PostMetric)
        .scalar_subquery(),
        0,
    )
)
Post.current_comments = column_property(
    func.coalesce(
        select(PostMetric.comments_count)
        .where(PostMetric.post_id == Post.id)
        .order_by(PostMetric.recorded_at.desc(), PostMetric.id.desc())
        .limit(1)
        .correlate_except(PostMetric)
        .scalar_subquery(),
        0,
    )
)
Post.current_views = column_property(
    select(PostMetric.views_count)
    .where(PostMetric.post_id == Post.id)
    .order_by(PostMetric.recorded_at.desc(), PostMetric.id.desc())
    .limit(1)
    .correlate_except(PostMetric)
    .scalar_subquery()
)
Post.initial_likes = column_property(
    func.coalesce(
        select(PostMetric.likes_count)
        .where(PostMetric.post_id == Post.id)
        .order_by(PostMetric.recorded_at.asc(), PostMetric.id.asc())
        .limit(1)
        .correlate_except(PostMetric)
        .scalar_subquery(),
        0,
    )
)
Post.initial_shares = column_property(
    func.coalesce(
        select(PostMetric.shares_count)
        .where(PostMetric.post_id == Post.id)
        .order_by(PostMetric.recorded_at.asc(), PostMetric.id.asc())
        .limit(1)
        .correlate_except(PostMetric)
        .scalar_subquery(),
        0,
    )
)
Post.initial_comments = column_property(
    func.coalesce(
        select(PostMetric.comments_count)
        .where(PostMetric.post_id == Post.id)
        .order_by(PostMetric.recorded_at.asc(), PostMetric.id.asc())
        .limit(1)
        .correlate_except(PostMetric)
        .scalar_subquery(),
        0,
    )
)
Post.metrics_update_count = column_property(
    select(func.count(PostMetric.id))
    .where(PostMetric.post_id == Post.id)
    .correlate_except(PostMetric)
    .scalar_subquery()
)


class AnalyticsCache(Base):
    __tablename__ = "analytics_cache"

    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=False)
    date = Column(DateTime, nullable=False)
    total_posts = Column(Integer, default=0)
    total_likes = Column(Integer, default=0)
    total_shares = Column(Integer, default=0)
    total_comments = Column(Integer, default=0)
    total_views = Column(Integer, nullable=True)
    avg_engagement_rate = Column(Float, nullable=True)
    avg_likes_per_post = Column(Float, nullable=True)
    top_post_id = Column(String(100), nullable=True)
    growth_rate = Column(Float, nullable=True)
    cached_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("source_id", "date", name="uq_analytics_cache"),
        Index("idx_analytics_source_date", "source_id", "date"),
    )


class ScraperLog(Base):
    __tablename__ = "scraper_logs"

    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=True)
    log_level = Column(String(20))
    message = Column(Text, nullable=False)
    error_type = Column(String(100), nullable=True)
    error_details = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        Index("idx_log_level_date", "log_level", "created_at"),
    )


class TaskLog(Base):
    __tablename__ = "task_logs"

    id = Column(Integer, primary_key=True)
    task_name = Column(String(100), nullable=False)
    status = Column(String(20))
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    items_processed = Column(Integer, default=0)
    errors_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_task_name_date", "task_name", "created_at"),
    )
