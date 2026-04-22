# Database Models using SQLAlchemy
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Boolean, Float, ForeignKey,
    Enum, Index, UniqueConstraint
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
import enum

Base = declarative_base()

# ==================== USER MANAGEMENT ====================

class User(Base):
    """User account model"""
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    
    # Facebook credentials (encrypted)
    fb_cookies = Column(Text, nullable=True)  # Encrypted cookies JSON
    fb_dtsg = Column(String(255), nullable=True)  # Encrypted fb_dtsg token
    fb_user_agent = Column(String(500), nullable=True)
    
    # Account info
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)
    
    # Relationships
    sources = relationship("Source", back_populates="user", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index('idx_user_username', 'username'),
        Index('idx_user_email', 'email'),
    )


# ==================== SOURCE MANAGEMENT ====================

class SourceType(str, enum.Enum):
    """Type of source to scrape"""
    GROUP = "group"
    PAGE = "page"
    USER = "user"


class Source(Base):
    """Facebook source (group, page, or user) to track"""
    __tablename__ = "sources"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    
    # Source info
    source_type = Column(Enum(SourceType), nullable=False)  # group/page/user
    facebook_id = Column(String(50), nullable=False)
    facebook_url = Column(String(255), nullable=False)
    source_name = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    
    # Metadata
    cover_image_url = Column(String(500), nullable=True)
    member_count = Column(Integer, nullable=True)
    follower_count = Column(Integer, nullable=True)
    
    # Tracking settings
    is_active = Column(Boolean, default=True)
    include_comments = Column(Boolean, default=True)
    include_replies = Column(Boolean, default=True)
    max_days_old = Column(Integer, default=30)  # Chỉ track bài <= N ngày
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    last_scraped = Column(DateTime, nullable=True)
    next_scrape = Column(DateTime, nullable=True)
    
    # Relationships
    user = relationship("User", back_populates="sources")
    posts = relationship("Post", back_populates="source", cascade="all, delete-orphan")
    
    __table_args__ = (
        UniqueConstraint('user_id', 'facebook_id', name='uq_user_source'),
        Index('idx_source_user_active', 'user_id', 'is_active'),
        Index('idx_source_next_scrape', 'next_scrape'),
    )


# ==================== POST & METRICS ====================

class Post(Base):
    """Facebook post data"""
    __tablename__ = "posts"
    
    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey('sources.id'), nullable=False)
    
    # Post identification
    facebook_post_id = Column(String(100), nullable=False, unique=True, index=True)
    facebook_url = Column(String(500), nullable=False)
    
    # Content
    content = Column(Text, nullable=True)
    media_count = Column(Integer, default=0)
    has_images = Column(Boolean, default=False)
    has_videos = Column(Boolean, default=False)
    
    # Timing
    posted_at = Column(DateTime, nullable=False)  # Khi post được đăng
    created_at = Column(DateTime, default=datetime.utcnow)  # Khi được scrape lần đầu
    
    # Tracking
    is_tracked = Column(Boolean, default=True)
    is_deleted = Column(Boolean, default=False)
    
    # Initial metrics (snapshot lúc scrape lần đầu)
    initial_likes = Column(Integer, default=0)
    initial_shares = Column(Integer, default=0)
    initial_comments = Column(Integer, default=0)
    
    # Current metrics (updated)
    current_likes = Column(Integer, default=0)
    current_shares = Column(Integer, default=0)
    current_comments = Column(Integer, default=0)
    current_views = Column(Integer, nullable=True)
    
    # Latest update
    last_metric_update = Column(DateTime, nullable=True)
    metrics_update_count = Column(Integer, default=0)
    
    # Relationships
    source = relationship("Source", back_populates="posts")
    metrics_history = relationship("PostMetric", back_populates="post", cascade="all, delete-orphan")
    comments = relationship("Comment", back_populates="post", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index('idx_post_source', 'source_id'),
        Index('idx_post_facebook_id', 'facebook_post_id'),
        Index('idx_post_posted_at', 'posted_at'),
        Index('idx_post_last_update', 'last_metric_update'),
    )


class PostMetric(Base):
    """Time-series metrics for a post (snapshot per update)"""
    __tablename__ = "post_metrics"
    
    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey('posts.id'), nullable=False)
    
    # Metrics
    likes_count = Column(Integer, default=0)
    shares_count = Column(Integer, default=0)
    comments_count = Column(Integer, default=0)
    views_count = Column(Integer, nullable=True)
    
    # Calculated fields
    engagement_rate = Column(Float, nullable=True)  # (likes + shares + comments) / views
    comment_ratio = Column(Float, nullable=True)    # comments / (likes + shares + comments)
    
    # Timestamp
    recorded_at = Column(DateTime, default=datetime.utcnow, index=True)
    
    # Relationships
    post = relationship("Post", back_populates="metrics_history")
    
    __table_args__ = (
        Index('idx_metric_post_date', 'post_id', 'recorded_at'),
    )


# ==================== COMMENTS ====================

class Comment(Base):
    """Comments on posts"""
    __tablename__ = "comments"
    
    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey('posts.id'), nullable=False)
    
    # Comment identification
    facebook_comment_id = Column(String(100), nullable=False, unique=True)
    
    # Commenter info
    commenter_id = Column(String(50), nullable=True)
    commenter_name = Column(String(255), nullable=True)
    commenter_url = Column(String(500), nullable=True)
    
    # Content
    comment_text = Column(Text, nullable=True)
    
    # Metadata
    likes_count = Column(Integer, default=0)
    reply_count = Column(Integer, default=0)
    
    # Timing
    created_at = Column(DateTime, default=datetime.utcnow)
    last_updated = Column(DateTime, nullable=True)
    
    # Hierarchy (for nested replies)
    parent_comment_id = Column(String(100), nullable=True)  # If reply to another comment
    depth_level = Column(Integer, default=0)  # 0 = top-level comment
    
    # Relationships
    post = relationship("Post", back_populates="comments")
    
    __table_args__ = (
        Index('idx_comment_post', 'post_id'),
        Index('idx_comment_facebook_id', 'facebook_comment_id'),
    )


# ==================== ANALYTICS & REPORTS ====================

class AnalyticsCache(Base):
    """Cached analytics data for faster queries"""
    __tablename__ = "analytics_cache"
    
    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey('sources.id'), nullable=False)
    
    # Time period
    date = Column(DateTime, nullable=False)
    
    # Aggregated metrics
    total_posts = Column(Integer, default=0)
    total_likes = Column(Integer, default=0)
    total_shares = Column(Integer, default=0)
    total_comments = Column(Integer, default=0)
    total_views = Column(Integer, nullable=True)
    
    # Engagement
    avg_engagement_rate = Column(Float, nullable=True)
    avg_likes_per_post = Column(Float, nullable=True)
    top_post_id = Column(String(100), nullable=True)  # Post ID with most engagement
    
    # Calculated
    growth_rate = Column(Float, nullable=True)  # Day-over-day growth
    
    # Timestamp
    cached_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        UniqueConstraint('source_id', 'date', name='uq_analytics_cache'),
        Index('idx_analytics_source_date', 'source_id', 'date'),
    )


# ==================== SYSTEM LOGS ====================

class ScraperLog(Base):
    """Logs for scraper execution and errors"""
    __tablename__ = "scraper_logs"
    
    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey('sources.id'), nullable=True)
    
    # Log info
    log_level = Column(String(20))  # INFO, WARNING, ERROR, CRITICAL
    message = Column(Text, nullable=False)
    
    # Error details
    error_type = Column(String(100), nullable=True)
    error_details = Column(Text, nullable=True)
    
    # Timing
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    
    __table_args__ = (
        Index('idx_log_level_date', 'log_level', 'created_at'),
    )


class TaskLog(Base):
    """Logs for scheduled task execution"""
    __tablename__ = "task_logs"
    
    id = Column(Integer, primary_key=True)
    
    # Task info
    task_name = Column(String(100), nullable=False)
    status = Column(String(20))  # PENDING, RUNNING, SUCCESS, FAILED
    
    # Execution details
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    
    # Result
    items_processed = Column(Integer, default=0)
    errors_count = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_task_name_date', 'task_name', 'created_at'),
    )
