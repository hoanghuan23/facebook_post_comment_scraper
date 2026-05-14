"""
Service layer tính toán tier và lịch scrape cho từng Facebook source.
 
Dữ liệu thực tế từ analytics_cache:
  - total_posts, total_likes, total_shares, total_comments  → có
  - total_views = 0 (không lấy được từ Facebook)
  - avg_engagement_rate = NULL (tính từ views nên vô dụng)
  - avg_likes_per_post  → có (VD: 532.84)
 
Công thức engagement thay thế:
  engagement = (total_likes + total_shares + total_comments) / member_count
  Fallback nếu member_count NULL/0: dùng avg_likes_per_post để ước lượng tier.
"""

from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import text
 
 
# ---------------------------------------------------------------------------
# Config ngưỡng tier — chỉnh ở đây, không hardcode trong if-else
# ---------------------------------------------------------------------------
TIER_CONFIG = [
    {
        "tier": 1,
        "min_posts": 20,
        "min_engagement": 0.05,   # 5%  — tính từ likes+shares+comments / member_count
        "interval_minutes": 15,
        "label": "Hot",
    },
    {
        "tier": 2,
        "min_posts": 5,
        "min_engagement": 0.01,   # 1%
        "interval_minutes": 60,
        "label": "Warm",
    },
    {
        "tier": 3,
        "min_posts": 1,
        "min_engagement": 0,      # không yêu cầu engagement
        "interval_minutes": 180,
        "label": "Cool",
    },
]
# Tier 4 = không đạt bất kỳ điều kiện nào trên → pause
 
# Ngưỡng fallback khi không có member_count
# Dùng avg_likes_per_post để ước lượng mức độ hoạt động
FALLBACK_LIKES_TIER1 = 500   # avg_likes/post >= 500 → coi như tier 1
FALLBACK_LIKES_TIER2 = 100   # avg_likes/post >= 100 → coi như tier 2
 
 
# ---------------------------------------------------------------------------
# calculate_tier()
# ---------------------------------------------------------------------------
def calculate_tier(source_id: int, db: Session) -> dict:
    """
    Đọc analytics_cache 7 ngày gần nhất → tính avg → phân tier.
    KHÔNG ghi gì vào DB.
 
    Returns dict:
        {
            "tier": int | None,
            "interval_minutes": int | None,
            "label": str,
            "reason": str,
            "avg_posts_per_day": float,
            "avg_engagement_rate": float | None,
            "engagement_available": bool,
            "data_days": int,
        }
    """
 
    # 1. Query analytics_cache + join sources để lấy member_count
    row = db.execute(text("""
        SELECT
            COUNT(*)                            AS data_days,
            AVG(ac.total_posts)                 AS avg_posts,
            SUM(ac.total_likes)                 AS sum_likes,
            SUM(ac.total_shares)                AS sum_shares,
            SUM(ac.total_comments)              AS sum_comments,
            AVG(ac.avg_likes_per_post)          AS avg_likes_per_post,
            s.member_count                      AS member_count
        FROM analytics_cache ac
        JOIN sources s ON s.id = ac.source_id
        WHERE ac.source_id = :source_id
          AND ac.date >= DATE('now', '-7 days')
        GROUP BY s.member_count
    """), {"source_id": source_id}).fetchone()
 
    # Không có data analytics nào cả
    if not row or row.data_days == 0:
        return {
            "tier": None,
            "interval_minutes": None,
            "label": "Unknown",
            "reason": "Chưa có dữ liệu analytics (analytics_cache trống)",
            "avg_posts_per_day": 0,
            "avg_engagement_rate": None,
            "engagement_available": False,
            "data_days": 0,
        }
 
    avg_posts        = row.avg_posts or 0
    data_days        = row.data_days
    member_count     = row.member_count
    avg_likes_per_post = row.avg_likes_per_post or 0
 
    # 2. Tính engagement_rate từ likes + shares + comments / member_count
    engagement_available = False
    avg_engagement = None
 
    if member_count and member_count > 0:
        total_interactions = (row.sum_likes or 0) + (row.sum_shares or 0) + (row.sum_comments or 0)
        # Chia cho (member_count * data_days) để ra rate trung bình mỗi ngày
        avg_engagement = total_interactions / (member_count * data_days)
        engagement_available = True
 
    # 3. So ngưỡng → chọn tier
    #    Ưu tiên từ tier 1 xuống, lấy tier cao nhất đạt được
    selected_tier   = None
    interval        = None
    label           = "Frozen"
    reason_parts    = [f"avg {avg_posts:.1f} posts/ngày ({data_days} ngày gần nhất)"]
 
    if engagement_available:
        reason_parts.append(f"engagement {avg_engagement*100:.2f}%")
 
        for cfg in TIER_CONFIG:
            if avg_posts >= cfg["min_posts"] and avg_engagement >= cfg["min_engagement"]:
                selected_tier = cfg["tier"]
                interval      = cfg["interval_minutes"]
                label         = cfg["label"]
                break
 
    else:
        # Fallback: member_count không có → dùng avg_likes_per_post
        reason_parts.append(
            f"member_count không có, ước lượng qua avg_likes_per_post={avg_likes_per_post:.0f}"
        )
 
        if avg_posts >= TIER_CONFIG[0]["min_posts"] and avg_likes_per_post >= FALLBACK_LIKES_TIER1:
            selected_tier = 1
            interval      = TIER_CONFIG[0]["interval_minutes"]
            label         = TIER_CONFIG[0]["label"]
        elif avg_posts >= TIER_CONFIG[1]["min_posts"] and avg_likes_per_post >= FALLBACK_LIKES_TIER2:
            selected_tier = 2
            interval      = TIER_CONFIG[1]["interval_minutes"]
            label         = TIER_CONFIG[1]["label"]
        elif avg_posts >= TIER_CONFIG[2]["min_posts"]:
            selected_tier = 3
            interval      = TIER_CONFIG[2]["interval_minutes"]
            label         = TIER_CONFIG[2]["label"]
        # else → tier 4, selected_tier = None
 
    # Tier 4: avg_posts quá thấp (< 1 post/ngày)
    if selected_tier is None and avg_posts >= 1:
        # Có posts nhưng không đạt engagement → vẫn tier 3
        selected_tier = 3
        interval      = TIER_CONFIG[2]["interval_minutes"]
        label         = TIER_CONFIG[2]["label"]
 
    if selected_tier is None:
        reason_parts.append("dưới 1 post/ngày → dừng scrape")
 
    reason = ", ".join(reason_parts)
 
    return {
        "tier": selected_tier,                  # None = tier 4 (pause)
        "interval_minutes": interval,           # None nếu pause
        "label": label,
        "reason": reason,
        "avg_posts_per_day": round(avg_posts, 2),
        "avg_engagement_rate": round(avg_engagement, 4) if avg_engagement is not None else None,
        "engagement_available": engagement_available,
        "data_days": data_days,
    }
 
 
# ---------------------------------------------------------------------------
# apply_schedule()
# ---------------------------------------------------------------------------
def apply_schedule(source_id: int, db: Session) -> dict:
    """
    Quyết định interval thực tế (auto hoặc override), ghi vào bảng sources.
    Là hàm DUY NHẤT được phép ghi schedule_tier và next_scrape.
 
    Returns dict để endpoint trả thẳng trong response.
    """
 
    # 1. Đọc override và trạng thái hiện tại từ sources
    source = db.execute(text("""
        SELECT id, source_name, is_active,
               schedule_tier,
               schedule_override_minutes
        FROM sources
        WHERE id = :source_id
    """), {"source_id": source_id}).fetchone()
 
    if not source:
        return {"error": f"source_id={source_id} không tồn tại"}
 
    is_overridden    = source.schedule_override_minutes is not None
    tier_result      = None
    applied_interval = None
    applied_tier     = None
    reason           = ""
 
    # 2. Nếu user đang override → dùng luôn, không gọi calculate_tier
    if is_overridden:
        applied_interval = source.schedule_override_minutes
        applied_tier     = None   # override không gắn với tier cụ thể
        reason           = f"User override: {applied_interval} phút"
        action           = "overridden"
 
    else:
        # 3. Tính tier tự động
        tier_result   = calculate_tier(source_id, db)
        applied_tier  = tier_result["tier"]
        applied_interval = tier_result["interval_minutes"]
        reason        = tier_result["reason"]
 
        # 4. Tier 4 → pause source
        if applied_tier is None:
            db.execute(text("""
                UPDATE sources
                SET schedule_tier = 4,
                    is_active     = 0
                WHERE id = :source_id
            """), {"source_id": source_id})
 
            # Ghi log vào pipeline_logs
            db.execute(text("""
                INSERT INTO pipeline_logs (source_id, log_level, message, created_at)
                VALUES (:source_id, 'WARNING',
                        'auto-paused: low activity — ' || :reason,
                        CURRENT_TIMESTAMP)
            """), {"source_id": source_id, "reason": reason})
 
            db.commit()
 
            return {
                "source_id"        : source_id,
                "source_name"      : source.source_name,
                "action"           : "paused",
                "applied_tier"     : 4,
                "applied_interval_minutes": None,
                "next_scrape"      : None,
                "is_overridden"    : False,
                "reason"           : reason,
                "engagement_available": tier_result["engagement_available"],
                "avg_posts_per_day": tier_result["avg_posts_per_day"],
                "avg_engagement_rate": tier_result["avg_engagement_rate"],
            }
 
        action = "scheduled"
 
    # 5. Tính next_scrape và ghi vào sources
    next_scrape = datetime.utcnow() + timedelta(minutes=applied_interval)
 
    db.execute(text("""
        UPDATE sources
        SET schedule_tier = :tier,
            next_scrape   = :next_scrape,
            is_active     = 1
        WHERE id = :source_id
    """), {
        "tier"       : applied_tier,
        "next_scrape": next_scrape.strftime("%Y-%m-%d %H:%M:%S"),
        "source_id"  : source_id,
    })
 
    db.commit()
 
    result = {
        "source_id"               : source_id,
        "source_name"             : source.source_name,
        "action"                  : action,
        "applied_tier"            : applied_tier,
        "applied_interval_minutes": applied_interval,
        "next_scrape"             : next_scrape.strftime("%Y-%m-%dT%H:%M:%S"),
        "is_overridden"           : is_overridden,
        "reason"                  : reason,
    }
 
    # Thêm thông tin từ calculate_tier nếu không phải override
    if tier_result:
        result.update({
            "engagement_available": tier_result["engagement_available"],
            "avg_posts_per_day"   : tier_result["avg_posts_per_day"],
            "avg_engagement_rate" : tier_result["avg_engagement_rate"],
            "data_days"           : tier_result["data_days"],
        })
 
    return result
 
 
# ---------------------------------------------------------------------------
# apply_schedule_all()
# ---------------------------------------------------------------------------
def apply_schedule_all(user_id: int, db: Session) -> dict:
    """
    Chạy apply_schedule() cho toàn bộ sources của user.
    Dùng bởi POST /api/sources/auto-schedule.
    Sources đang override sẽ bị skip (không tính lại tier).
    """
 
    sources = db.execute(text("""
        SELECT id FROM sources
        WHERE user_id  = :user_id
    """), {"user_id": user_id}).fetchall()
 
    summary = {
        "processed"       : 0,
        "skipped_override": 0,
        "changes"         : [],   # source thay đổi tier
        "no_change"       : 0,
        "tier_summary"    : {1: 0, 2: 0, 3: 0, 4: 0},
    }
 
    for row in sources:
        source = db.execute(text("""
            SELECT schedule_tier, schedule_override_minutes
            FROM sources WHERE id = :id
        """), {"id": row.id}).fetchone()
 
        if source.schedule_override_minutes is not None:
            summary["skipped_override"] += 1
            continue
 
        old_tier = source.schedule_tier
        result   = apply_schedule(row.id, db)
        new_tier = result.get("applied_tier") or 4
 
        summary["processed"] += 1
        summary["tier_summary"][new_tier] = summary["tier_summary"].get(new_tier, 0) + 1
 
        if old_tier != new_tier:
            summary["changes"].append({
                "source_id"  : row.id,
                "source_name": result.get("source_name"),
                "old_tier"   : old_tier,
                "new_tier"   : new_tier,
                "action"     : result.get("action"),
            })
        else:
            summary["no_change"] += 1
 
    return summary