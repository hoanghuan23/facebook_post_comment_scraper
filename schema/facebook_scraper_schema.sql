-- ============================================================
--  Facebook Scraper — SQLite Schema (v2)
--  Tạo mới hoàn toàn: chạy file này trên DB trống
--  Nếu migrate từ DB cũ: xem phần MIGRATION ở cuối file
-- ============================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;


-- ------------------------------------------------------------
--  1. users
--     Tài khoản app — KHÔNG chứa Facebook credentials
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      VARCHAR(50)  NOT NULL UNIQUE,
    email         VARCHAR(100) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    is_active     BOOLEAN      NOT NULL DEFAULT 1,
    is_admin      BOOLEAN      NOT NULL DEFAULT 0,
    created_at    DATETIME     NOT NULL DEFAULT (datetime('now')),
    last_login    DATETIME
);


-- ------------------------------------------------------------
--  2. facebook_sessions
--     Facebook credentials tách riêng — rotate / multi-account
--     Nên encrypt fb_cookies ở tầng application trước khi lưu
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS facebook_sessions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    fb_user_id     VARCHAR(50),
    fb_cookies     TEXT,                        -- encrypt ở app layer
    fb_dtsg        VARCHAR(255),
    fb_user_agent  VARCHAR(500),
    is_active      BOOLEAN  NOT NULL DEFAULT 1,
    is_valid       BOOLEAN  NOT NULL DEFAULT 1, -- false khi cookie hết hạn
    last_verified  DATETIME,
    expires_at     DATETIME,
    created_at     DATETIME NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_fb_sessions_user
    ON facebook_sessions(user_id, is_active);


-- ------------------------------------------------------------
--  3. sources
--     Group / Page / User Facebook mà user đăng ký theo dõi
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sources (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id               INTEGER      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_type           VARCHAR(10)  NOT NULL CHECK (source_type IN ('group', 'page', 'user')),
    facebook_id           VARCHAR(50)  NOT NULL,
    facebook_url          VARCHAR(255) NOT NULL,   -- URL group/page, KHÔNG phải URL post
    source_name           VARCHAR(255),
    description           TEXT,
    cover_image_url       VARCHAR(500),
    member_count          INTEGER,
    follower_count        INTEGER,

    -- Cấu hình scrape
    is_active             BOOLEAN  NOT NULL DEFAULT 1,
    include_comments      BOOLEAN  NOT NULL DEFAULT 1,
    include_replies       BOOLEAN  NOT NULL DEFAULT 1,
    max_days_old          INTEGER  NOT NULL DEFAULT 30,

    -- Trạng thái quyền truy cập
    permission_status     VARCHAR(20) CHECK (permission_status IN ('granted', 'denied', 'pending', 'unknown')),
    permission_message    TEXT,
    access_restrictions   TEXT,
    permission_checked_at DATETIME,
    is_accessible         BOOLEAN  NOT NULL DEFAULT 1,

    -- Lịch scrape
    created_at            DATETIME NOT NULL DEFAULT (datetime('now')),
    last_scraped          DATETIME,
    next_scrape           DATETIME,

    UNIQUE (user_id, facebook_id)
);

CREATE INDEX IF NOT EXISTS idx_sources_user_active
    ON sources(user_id, is_active);

CREATE INDEX IF NOT EXISTS idx_sources_next_scrape
    ON sources(next_scrape)
    WHERE is_active = 1;


-- ------------------------------------------------------------
--  4. posts
--     Bài đăng thu thập được từ source
--     KHÔNG lưu initial_*/current_* — dùng post_metrics thay thế
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS posts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id         INTEGER      NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    facebook_post_id  VARCHAR(100) NOT NULL UNIQUE,
    facebook_url      VARCHAR(500) NOT NULL,
    content           TEXT,
    media_count       INTEGER  NOT NULL DEFAULT 0,
    has_images        BOOLEAN  NOT NULL DEFAULT 0,
    has_videos        BOOLEAN  NOT NULL DEFAULT 0,
    posted_at         DATETIME NOT NULL,
    created_at        DATETIME NOT NULL DEFAULT (datetime('now')),

    -- Trạng thái theo dõi 24h
    is_tracked        BOOLEAN  NOT NULL DEFAULT 1,
    tracking_until    DATETIME,                 -- posted_at + 24h; NULL = theo dõi vĩnh viễn
    is_deleted        BOOLEAN  NOT NULL DEFAULT 0,

    -- Thời điểm cập nhật metrics gần nhất (tham chiếu nhanh, không lưu số liệu)
    last_metric_update DATETIME
);

CREATE INDEX IF NOT EXISTS idx_posts_source_posted
    ON posts(source_id, posted_at DESC);

CREATE INDEX IF NOT EXISTS idx_posts_posted_at
    ON posts(posted_at DESC);

CREATE INDEX IF NOT EXISTS idx_posts_tracking
    ON posts(is_tracked, tracking_until)
    WHERE is_tracked = 1;


-- ------------------------------------------------------------
--  5. post_metrics
--     Snapshot tương tác theo thời gian — INSERT mỗi lần scrape,
--     KHÔNG update row cũ. Đây là nguồn duy nhất cho số liệu.
--
--     engagement_rate và comment_ratio KHÔNG lưu vào DB,
--     tính runtime trong query:
--       engagement_rate = (likes + shares + comments) / NULLIF(follower_count, 0)
--       comment_ratio   = comments / NULLIF(likes + shares + comments, 0)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS post_metrics (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id        INTEGER  NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    likes_count    INTEGER  NOT NULL DEFAULT 0,
    shares_count   INTEGER  NOT NULL DEFAULT 0,
    comments_count INTEGER  NOT NULL DEFAULT 0,
    views_count    INTEGER,                     -- NULL nếu không lấy được
    recorded_at    DATETIME NOT NULL DEFAULT (datetime('now'))
);

-- Index quan trọng nhất: query lịch sử 1 post theo thời gian
CREATE INDEX IF NOT EXISTS idx_metrics_post_time
    ON post_metrics(post_id, recorded_at DESC);

-- Index cho analytics tổng hợp theo thời gian
CREATE INDEX IF NOT EXISTS idx_metrics_recorded_at
    ON post_metrics(recorded_at DESC);


-- ------------------------------------------------------------
--  6. comments
--     Comments và replies của bài đăng (nested 2 cấp)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS comments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id             INTEGER      NOT NULL REFERENCES posts(id) ON DELETE CASCADE,

    -- Self-referencing FK cho nested: NULL = top-level comment
    parent_id           INTEGER REFERENCES comments(id) ON DELETE CASCADE,

    facebook_comment_id VARCHAR(100) NOT NULL UNIQUE,
    commenter_id        VARCHAR(50),
    commenter_name      VARCHAR(255),
    commenter_url       VARCHAR(500),
    comment_text        TEXT,
    likes_count         INTEGER  NOT NULL DEFAULT 0,
    reply_count         INTEGER  NOT NULL DEFAULT 0,
    depth_level         INTEGER  NOT NULL DEFAULT 0,  -- 0 = top-level, 1 = reply
    created_at          DATETIME NOT NULL DEFAULT (datetime('now')),
    last_updated        DATETIME NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_comments_post_depth
    ON comments(post_id, depth_level);

CREATE INDEX IF NOT EXISTS idx_comments_parent
    ON comments(parent_id)
    WHERE parent_id IS NOT NULL;


-- ------------------------------------------------------------
--  7. scrape_jobs
--     Log từng lần scrape — dùng cho /api/admin/logs và debug
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scrape_jobs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id      INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    session_id     INTEGER REFERENCES facebook_sessions(id) ON DELETE SET NULL,
    status         VARCHAR(10) NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'running', 'done', 'failed')),
    posts_found    INTEGER NOT NULL DEFAULT 0,
    posts_new      INTEGER NOT NULL DEFAULT 0,
    posts_updated  INTEGER NOT NULL DEFAULT 0,
    error_message  TEXT,
    started_at     DATETIME,
    finished_at    DATETIME,
    created_at     DATETIME NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_jobs_source_time
    ON scrape_jobs(source_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_jobs_status
    ON scrape_jobs(status, created_at DESC);


-- ============================================================
--  VIEWS tiện ích (không bắt buộc, nhưng giúp query đơn giản)
-- ============================================================

-- View: số liệu mới nhất của mỗi post (thay thế current_likes/shares/comments cũ)
CREATE VIEW IF NOT EXISTS v_post_latest_metrics AS
SELECT
    p.id              AS post_id,
    p.source_id,
    p.facebook_post_id,
    p.posted_at,
    p.is_tracked,
    p.tracking_until,
    pm.likes_count,
    pm.shares_count,
    pm.comments_count,
    pm.views_count,
    pm.recorded_at    AS metrics_at,
    -- delta so với snapshot đầu tiên
    pm.likes_count    - COALESCE(pm_first.likes_count, 0)    AS delta_likes,
    pm.shares_count   - COALESCE(pm_first.shares_count, 0)   AS delta_shares,
    pm.comments_count - COALESCE(pm_first.comments_count, 0) AS delta_comments
FROM posts p
LEFT JOIN post_metrics pm ON pm.id = (
    SELECT id FROM post_metrics
    WHERE post_id = p.id
    ORDER BY recorded_at DESC LIMIT 1
)
LEFT JOIN post_metrics pm_first ON pm_first.id = (
    SELECT id FROM post_metrics
    WHERE post_id = p.id
    ORDER BY recorded_at ASC LIMIT 1
);


-- View: bài đang trong chu kỳ theo dõi 24h
CREATE VIEW IF NOT EXISTS v_tracking_posts AS
SELECT
    p.*,
    pm.likes_count,
    pm.shares_count,
    pm.comments_count,
    pm.recorded_at AS last_snapshot_at
FROM posts p
LEFT JOIN post_metrics pm ON pm.id = (
    SELECT id FROM post_metrics
    WHERE post_id = p.id
    ORDER BY recorded_at DESC LIMIT 1
)
WHERE p.is_tracked = 1
  AND (p.tracking_until IS NULL OR p.tracking_until > datetime('now'));


-- View: tổng hợp analytics theo source
CREATE VIEW IF NOT EXISTS v_source_analytics AS
SELECT
    s.id              AS source_id,
    s.source_name,
    s.source_type,
    s.follower_count,
    COUNT(DISTINCT p.id)          AS total_posts,
    SUM(pm_last.likes_count)      AS total_likes,
    SUM(pm_last.shares_count)     AS total_shares,
    SUM(pm_last.comments_count)   AS total_comments,
    MAX(p.posted_at)              AS last_post_at,
    s.last_scraped
FROM sources s
LEFT JOIN posts p ON p.source_id = s.id AND p.is_deleted = 0
LEFT JOIN post_metrics pm_last ON pm_last.id = (
    SELECT id FROM post_metrics
    WHERE post_id = p.id
    ORDER BY recorded_at DESC LIMIT 1
)
GROUP BY s.id;


-- ============================================================
--  MIGRATION từ DB cũ sang DB mới
--  Chạy từng block, kiểm tra kết quả trước khi sang block tiếp
-- ============================================================

/*

-- BƯỚC 1: Tạo bảng mới (chạy toàn bộ phần CREATE TABLE ở trên)

-- BƯỚC 2: Copy users (bỏ fb_cookies, fb_dtsg, fb_user_agent)
INSERT INTO users (id, username, email, password_hash, is_active, is_admin, created_at, last_login)
SELECT id, username, email, password_hash, is_active, is_admin, created_at, last_login
FROM users_old;

-- BƯỚC 3: Tạo facebook_sessions từ data cũ trong users
INSERT INTO facebook_sessions (user_id, fb_cookies, fb_dtsg, fb_user_agent, is_active, is_valid)
SELECT id, fb_cookies, fb_dtsg, fb_user_agent, is_active,
       CASE WHEN fb_cookies IS NOT NULL AND fb_cookies != '' THEN 1 ELSE 0 END
FROM users_old
WHERE fb_cookies IS NOT NULL;

-- BƯỚC 4: Copy sources — sửa facebook_url về URL group/page
INSERT INTO sources (id, user_id, source_type, facebook_id, facebook_url,
                     source_name, description, cover_image_url,
                     member_count, follower_count,
                     is_active, include_comments, include_replies, max_days_old,
                     permission_status, permission_message, access_restrictions,
                     permission_checked_at, is_accessible, created_at, last_scraped, next_scrape)
SELECT
    id, user_id, source_type, facebook_id,
    -- Sửa URL: dùng facebook_id thay vì URL post sai
    CASE source_type
        WHEN 'group' THEN 'https://www.facebook.com/groups/' || facebook_id || '/'
        WHEN 'page'  THEN 'https://www.facebook.com/' || facebook_id || '/'
        ELSE facebook_url
    END,
    source_name, description, cover_image_url,
    member_count, follower_count,
    is_active, include_comments, include_replies, max_days_old,
    permission_status, permission_message, access_restrictions,
    permission_checked_at, is_accessible, created_at, last_scraped, next_scrape
FROM sources_old;

-- BƯỚC 5: Copy posts — bỏ initial_ current_*, thêm tracking_until
INSERT INTO posts (id, source_id, facebook_post_id, facebook_url,
                   content, media_count, has_images, has_videos,
                   posted_at, created_at, is_tracked, tracking_until,
                   is_deleted, last_metric_update)
SELECT
    id, source_id, facebook_post_id, facebook_url,
    content, media_count, has_images, has_videos,
    posted_at, created_at, is_tracked,
    -- tracking_until = posted_at + 24h
    datetime(posted_at, '+24 hours'),
    is_deleted, last_metric_update
FROM posts_old;

-- BƯỚC 6: Copy post_metrics (giữ nguyên, bỏ engagement_rate và comment_ratio)
INSERT INTO post_metrics (id, post_id, likes_count, shares_count, comments_count, views_count, recorded_at)
SELECT id, post_id, likes_count, shares_count, comments_count, views_count, recorded_at
FROM post_metrics_old;

-- Nếu post_metrics_old chưa có data đủ, seed từ current_* của posts_old:
-- (Chạy block này THAY VÌ BƯỚC 6 nếu post_metrics_old chỉ có 1 row/post)
INSERT INTO post_metrics (post_id, likes_count, shares_count, comments_count, views_count, recorded_at)
SELECT id, current_likes, current_shares, current_comments, current_views, last_metric_update
FROM posts_old
WHERE current_likes != initial_likes
   OR current_shares != initial_shares
   OR current_comments != initial_comments;

-- BƯỚC 7: Copy comments — thêm parent_id (int) từ parent_comment_id (string)
INSERT INTO comments (id, post_id, parent_id,
                      facebook_comment_id, commenter_id, commenter_name,
                      commenter_url, comment_text,
                      likes_count, reply_count, depth_level,
                      created_at, last_updated)
SELECT
    c.id, c.post_id,
    -- Resolve parent_comment_id string → parent_id int
    c2.id,
    c.facebook_comment_id, c.commenter_id, c.commenter_name,
    c.commenter_url, c.comment_text,
    c.likes_count, c.reply_count, c.depth_level,
    c.created_at, c.last_updated
FROM comments_old c
LEFT JOIN comments_old c2 ON c2.facebook_comment_id = c.parent_comment_id;

-- BƯỚC 8: Verify
SELECT 'users'          , COUNT(*) FROM users
UNION ALL
SELECT 'facebook_sessions', COUNT(*) FROM facebook_sessions
UNION ALL
SELECT 'sources'        , COUNT(*) FROM sources
UNION ALL
SELECT 'posts'          , COUNT(*) FROM posts
UNION ALL
SELECT 'post_metrics'   , COUNT(*) FROM post_metrics
UNION ALL
SELECT 'comments'       , COUNT(*) FROM comments;

*/
