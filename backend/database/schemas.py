# Pydantic schemas for API request/response validation
from pydantic import BaseModel, EmailStr, HttpUrl, Field
from datetime import datetime
from typing import Optional, List, Literal
from enum import Enum

# ==================== USER SCHEMAS ====================

class UserCreate(BaseModel):
    """Schema for user registration"""
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=6)
    

class UserLogin(BaseModel):
    """Schema for user login"""
    username: str
    password: str


class UserResponse(BaseModel):
    """User response (without password)"""
    id: int
    username: str
    email: str
    is_active: bool
    created_at: datetime
    last_login: Optional[datetime]
    
    class Config:
        from_attributes = True


class UserProfileUpdate(BaseModel):
    """Update user profile"""
    email: Optional[EmailStr] = None
    username: Optional[str] = None


# ==================== AUTH RESPONSE ====================

class TokenResponse(BaseModel):
    """Authentication token response"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class TokenRefresh(BaseModel):
    """Token refresh request"""
    refresh_token: str


# ==================== SOURCE SCHEMAS ====================

class SourceCreate(BaseModel):
    """Create a new source"""
    source_type: Literal["group", "page", "user"]
    facebook_url: str
    include_comments: bool = True
    include_replies: bool = True
    max_days_old: int = 30
    check_access: bool = True  # Có kiểm tra quyền trước khi lưu không


class SourceUpdate(BaseModel):
    """Update source settings"""
    source_name: Optional[str] = None
    include_comments: Optional[bool] = None
    include_replies: Optional[bool] = None
    max_days_old: Optional[int] = None
    is_active: Optional[bool] = None


class SourceResponse(BaseModel):
    """Source response"""
    id: int
    user_id: int
    source_type: str
    facebook_id: str
    facebook_url: str
    source_name: Optional[str]
    description: Optional[str]
    is_active: bool
    created_at: datetime
    last_scraped: Optional[datetime]
    member_count: Optional[int]
    follower_count: Optional[int]
    
    # Permission fields
    permission_status: Optional[str] = None
    permission_message: Optional[str] = None
    is_accessible: bool = False
    
    class Config:
        from_attributes = True


class SourceDetail(SourceResponse):
    """Detailed source response"""
    post_count: Optional[int] = None
    latest_post_date: Optional[datetime] = None
    access_restrictions: Optional[List[str]] = None
    permission_checked_at: Optional[datetime] = None


# ==================== POST SCHEMAS ====================

class PostCreate(BaseModel):
    """Create post record (internal use)"""
    facebook_post_id: str
    facebook_url: str
    content: Optional[str]
    posted_at: datetime


class PostMetricSnapshot(BaseModel):
    """Single metric snapshot"""
    likes_count: int
    shares_count: int
    comments_count: int
    views_count: Optional[int] = None
    recorded_at: datetime
    
    class Config:
        from_attributes = True


class PostResponse(BaseModel):
    """Post response"""
    id: int
    facebook_post_id: str
    facebook_url: str
    content: Optional[str]
    posted_at: datetime
    created_at: datetime
    is_tracked: bool
    
    # Current metrics
    current_likes: int
    current_shares: int
    current_comments: int
    current_views: Optional[int]
    
    # Initial metrics for comparison
    initial_likes: int
    initial_shares: int
    initial_comments: int
    
    # Metadata
    media_count: int
    has_images: bool
    has_videos: bool
    last_metric_update: Optional[datetime]
    metrics_update_count: int
    
    class Config:
        from_attributes = True


class PostWithMetrics(PostResponse):
    """Post with full metrics history"""
    metrics_history: List[PostMetricSnapshot]


# ==================== COMMENT SCHEMAS ====================

class CommentResponse(BaseModel):
    """Comment response"""
    id: int
    facebook_comment_id: str
    commenter_name: Optional[str]
    commenter_url: Optional[str]
    comment_text: Optional[str]
    likes_count: int
    reply_count: int
    created_at: datetime
    depth_level: int
    
    class Config:
        from_attributes = True


class CommentWithReplies(CommentResponse):
    """Comment with nested replies"""
    replies: List['CommentResponse'] = []


CommentWithReplies.update_forward_refs()


# ==================== ANALYTICS SCHEMAS ====================

class DailyAnalytics(BaseModel):
    """Daily analytics snapshot"""
    date: datetime
    total_posts: int
    total_likes: int
    total_shares: int
    total_comments: int
    avg_engagement_rate: Optional[float]
    growth_rate: Optional[float]
    
    class Config:
        from_attributes = True


class AnalyticsSummary(BaseModel):
    """Overall analytics summary"""
    source_id: int
    period_days: int
    
    # Aggregated metrics
    total_posts: int
    total_engagement: int  # likes + shares + comments
    avg_likes_per_post: float
    avg_shares_per_post: float
    avg_comments_per_post: float
    avg_engagement_rate: float
    
    # Top posts
    top_posts_by_likes: List[dict]
    top_posts_by_comments: List[dict]
    top_posts_by_shares: List[dict]
    
    # Trends
    daily_analytics: List[DailyAnalytics]
    growth_trend: str  # "up", "down", "stable"


class MetricGrowth(BaseModel):
    """Metric growth for a post"""
    facebook_post_id: str
    likes_growth: int
    shares_growth: int
    comments_growth: int
    likes_growth_percent: float
    shares_growth_percent: float
    comments_growth_percent: float
    hours_elapsed: float


class TrendingPost(BaseModel):
    """Trending post"""
    id: int
    facebook_post_id: str
    facebook_url: str
    content: Optional[str]
    current_likes: int
    current_shares: int
    current_comments: int
    engagement_rate: float
    time_since_posted: str  # e.g., "2 hours ago"
    engagement_velocity: float  # engagement per hour


# ==================== EXPORT SCHEMAS ====================

class ExportRequest(BaseModel):
    """Export data request"""
    source_id: int
    format: Literal["csv", "json", "pdf"] = "csv"
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    include_comments: bool = False


class ExportResponse(BaseModel):
    """Export response"""
    filename: str
    size_bytes: int
    format: str
    created_at: datetime
    download_url: str


# ==================== ERROR SCHEMAS ====================

class ErrorResponse(BaseModel):
    """Error response"""
    error: str
    detail: Optional[str] = None
    status_code: int


# ==================== PAGINATION ====================

class PaginationParams(BaseModel):
    """Pagination parameters"""
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class PaginatedResponse(BaseModel):
    """Paginated response wrapper"""
    total_count: int
    page: int
    page_size: int
    total_pages: int
    data: List[dict]


# ==================== SCHEDULER SCHEMAS ====================

class ScraperStatus(BaseModel):
    """Scraper status"""
    status: str  # "running", "paused", "stopped"
    active_tasks: int
    queued_tasks: int
    last_heartbeat: datetime
    uptime_seconds: float
    processed_posts_today: int
    errors_count_today: int


class TaskAction(BaseModel):
    """Control scraper task"""
    action: Literal["start", "stop", "pause", "resume"]
    task_name: Optional[str] = None  # If None, apply to all tasks


# ==================== FACEBOOK CREDENTIALS ====================

class FacebookCredentials(BaseModel):
    """Facebook credentials update"""
    cookies: Optional[str] = None
    fb_dtsg: Optional[str] = None
    user_agent: Optional[str] = None
