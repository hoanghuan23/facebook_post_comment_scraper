TIER_CONFIG = {
    1: {"min_posts": 20, "min_engagement": 0.05, "interval_minutes": 15},
    2: {"min_posts": 5, "min_engagement": 0.01, "interval_minutes": 60},
    3: {"min_posts": 1, "min_engagement": 0, "interval_minues": 180} 
}

def calculate_tier(source_id, db) -> dict:
    """Đọc analytics_cache, trả dict. Không ghi DB."""
    ...

def apply_schedule(source_id, db) -> dict:
    """Gọi calculate_tier nếu cần, ghi sources. Trả dict."""
    ...

def apply_schedule_all(db, user_id) -> dict:
    """Vòng lặp apply_schedule cho toàn bộ sources của user.
    Dùng bởi POST /sources/auto-schedule."""
    ...