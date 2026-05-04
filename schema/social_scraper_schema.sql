-- ============================================================
-- SOCIAL MEDIA SCRAPER - DATABASE SCHEMA (SQLite)
-- Hỗ trợ: Facebook, TikTok, Instagram, Threads, X
-- ============================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ============================================================
-- NHÓM 1: NGƯỜI DÙNG & XÁC THỰC
-- ============================================================

CREATE TABLE IF NOT EXISTS users (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    username         TEXT    NOT NULL UNIQUE,
    email            TEXT    NOT NULL UNIQUE,
    password_hash    TEXT    NOT NULL,
    is_active        INTEGER NOT NULL DEFAULT 1,   -- 1 = active, 0 = disabled
    is_admin         INTEGER NOT NULL DEFAULT 0,   -- 1 = admin
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    last_login       TEXT
);

-- Index tìm kiếm user theo email khi đăng nhập
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);


CREATE TABLE IF NOT EXISTS platform_accounts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    platform         TEXT    NOT NULL CHECK(platform IN ('facebook','tiktok','instagram','threads','x')),
    account_id       TEXT    NOT NULL,             -- UID gốc trên nền tảng
    auth_tokens      TEXT    NOT NULL,             -- JSON: access_token, cookie, fb_dtsg...
    token_expires_at TEXT,                         -- NULL = không hết hạn (cookie-based)
    is_active        INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, platform, account_id)          -- tránh thêm trùng cùng tài khoản
);

CREATE INDEX IF NOT EXISTS idx_platacct_user_platform
    ON platform_accounts(user_id, platform, is_active);


-- ============================================================
-- NHÓM 2: NGUỒN THEO DÕI & CẤU HÌNH
-- ============================================================

CREATE TABLE IF NOT EXISTS sources (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id              INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    platform             TEXT    NOT NULL CHECK(platform IN ('facebook','tiktok','instagram','threads','x')),
    source_type          TEXT    NOT NULL CHECK(source_type IN ('group','page','user','hashtag','channel')),
    platform_id          TEXT    NOT NULL,          -- ID gốc trên nền tảng
    platform_url         TEXT,
    source_name          TEXT,
    member_count         INTEGER DEFAULT 0,
    is_active            INTEGER NOT NULL DEFAULT 1,
    permission_status    TEXT    NOT NULL DEFAULT 'ok'
                             CHECK(permission_status IN ('ok','restricted','private','error')),
    last_scraped         TEXT,
    platform_credentials TEXT,                     -- JSON: thông tin xác thực riêng nếu cần
    created_at           TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, platform, platform_id)          -- tránh đăng ký trùng nguồn
);

CREATE INDEX IF NOT EXISTS idx_sources_user      ON sources(user_id, is_active);
CREATE INDEX IF NOT EXISTS idx_sources_platform  ON sources(platform, is_active);


CREATE TABLE IF NOT EXISTS monitor_configs (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id               INTEGER NOT NULL UNIQUE REFERENCES sources(id) ON DELETE CASCADE,
    scrape_interval_minutes INTEGER NOT NULL DEFAULT 30,  -- tần suất scrape
    monitor_window_hours    INTEGER NOT NULL DEFAULT 24,  -- theo dõi bài trong bao nhiêu giờ
    is_active               INTEGER NOT NULL DEFAULT 1,
    created_at              TEXT    NOT NULL DEFAULT (datetime('now'))
);


CREATE TABLE IF NOT EXISTS scrape_jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id     INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    platform      TEXT    NOT NULL,
    started_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    finished_at   TEXT,
    status        TEXT    NOT NULL DEFAULT 'pending'
                      CHECK(status IN ('pending','running','success','failed','partial')),
    posts_found   INTEGER DEFAULT 0,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_scrapejobs_source  ON scrape_jobs(source_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_scrapejobs_status  ON scrape_jobs(status, started_at DESC);


-- ============================================================
-- NHÓM 3: NỘI DUNG BÀI ĐĂNG
-- ============================================================

CREATE TABLE IF NOT EXISTS posts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id        INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    platform         TEXT    NOT NULL CHECK(platform IN ('facebook','tiktok','instagram','threads','x')),
    platform_post_id TEXT    NOT NULL,             -- ID bài gốc trên nền tảng
    platform_url     TEXT,
    author_name      TEXT,
    author_id        TEXT,
    author_url       TEXT,
    post_type        TEXT    NOT NULL DEFAULT 'text'
                         CHECK(post_type IN ('text','image','video','reel','story','carousel','thread','tweet')),
    content          TEXT,
    posted_at        TEXT    NOT NULL,             -- thời điểm đăng trên nền tảng
    scraped_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    is_active        INTEGER NOT NULL DEFAULT 1,
    trend_score      REAL    NOT NULL DEFAULT 0,   -- tổng tương tác / sqrt(giờ kể từ đăng)
    UNIQUE(platform, platform_post_id)             -- tránh lưu trùng bài
);

CREATE INDEX IF NOT EXISTS idx_posts_source       ON posts(source_id, posted_at DESC);
CREATE INDEX IF NOT EXISTS idx_posts_platform     ON posts(platform, posted_at DESC);
CREATE INDEX IF NOT EXISTS idx_posts_trend        ON posts(trend_score DESC, posted_at DESC);
CREATE INDEX IF NOT EXISTS idx_posts_posted_at    ON posts(posted_at DESC);


CREATE TABLE IF NOT EXISTS post_media (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id          INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    media_type       TEXT    NOT NULL CHECK(media_type IN ('image','video','gif','audio')),
    media_url        TEXT,
    duration_seconds INTEGER,                      -- thời lượng video (giây)
    width            INTEGER,
    height           INTEGER,
    thumbnail_url    TEXT
);

CREATE INDEX IF NOT EXISTS idx_postmedia_post ON post_media(post_id);


CREATE TABLE IF NOT EXISTS post_tags (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id    INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    tag_type   TEXT    NOT NULL CHECK(tag_type IN ('hashtag','mention','keyword')),
    tag_value  TEXT    NOT NULL,                   -- chuẩn hóa chữ thường: "#viral", "@user"
    platform   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_posttags_post     ON post_tags(post_id);
CREATE INDEX IF NOT EXISTS idx_posttags_value    ON post_tags(tag_value, platform);
CREATE INDEX IF NOT EXISTS idx_posttags_type     ON post_tags(tag_type, tag_value);


CREATE TABLE IF NOT EXISTS comments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id             INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    platform_comment_id TEXT    NOT NULL,
    comment_text        TEXT,
    commenter_name      TEXT,
    commenter_id        TEXT,
    comment_likes       INTEGER DEFAULT 0,
    reply_count         INTEGER DEFAULT 0,
    parent_comment_id   TEXT,                      -- NULL = bình luận gốc, có giá trị = reply
    created_at          TEXT    NOT NULL,
    UNIQUE(post_id, platform_comment_id)
);

CREATE INDEX IF NOT EXISTS idx_comments_post    ON comments(post_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_comments_parent  ON comments(parent_comment_id);


-- ============================================================
-- NHÓM 4: ĐO LƯỜNG & XU HƯỚNG
-- ============================================================

CREATE TABLE IF NOT EXISTS metrics_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id        INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    snapshot_at    TEXT    NOT NULL DEFAULT (datetime('now')),

    -- Chỉ số tuyệt đối tại thời điểm đo
    like_count     INTEGER DEFAULT 0,
    share_count    INTEGER DEFAULT 0,
    comment_count  INTEGER DEFAULT 0,
    view_count     INTEGER DEFAULT 0,
    save_count     INTEGER DEFAULT 0,    -- Instagram, TikTok
    repost_count   INTEGER DEFAULT 0,   -- Threads, X
    quote_count    INTEGER DEFAULT 0,   -- X (quote tweet)
    bookmark_count INTEGER DEFAULT 0,   -- X

    -- Chênh lệch so với snapshot trước (đo tốc độ tăng)
    delta_likes    INTEGER DEFAULT 0,
    delta_shares   INTEGER DEFAULT 0,
    delta_comments INTEGER DEFAULT 0,
    delta_views    INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_metrics_post        ON metrics_history(post_id, snapshot_at DESC);
CREATE INDEX IF NOT EXISTS idx_metrics_snapshot    ON metrics_history(snapshot_at DESC);


CREATE TABLE IF NOT EXISTS trend_summaries (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    platform           TEXT    NOT NULL,           -- tên nền tảng hoặc 'all' nếu cross-platform
    tag_value          TEXT    NOT NULL,
    tag_type           TEXT    NOT NULL CHECK(tag_type IN ('hashtag','mention','keyword')),
    post_count         INTEGER NOT NULL DEFAULT 0,
    total_interactions INTEGER NOT NULL DEFAULT 0,
    velocity_score     REAL    NOT NULL DEFAULT 0, -- tốc độ tăng: delta_interactions / giờ
    window_start       TEXT    NOT NULL,
    window_end         TEXT    NOT NULL,
    computed_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_trend_velocity  ON trend_summaries(velocity_score DESC, computed_at DESC);
CREATE INDEX IF NOT EXISTS idx_trend_platform  ON trend_summaries(platform, computed_at DESC);
CREATE INDEX IF NOT EXISTS idx_trend_tag       ON trend_summaries(tag_value, platform);


-- ============================================================
-- VIEW TIỆN ÍCH
-- ============================================================

-- Top bài đăng hot trong 24h gần nhất (dùng cho dashboard)
CREATE VIEW IF NOT EXISTS v_hot_posts_24h AS
SELECT
    p.id,
    p.platform,
    p.platform_url,
    p.author_name,
    p.post_type,
    p.content,
    p.posted_at,
    p.trend_score,
    m.like_count,
    m.view_count,
    m.comment_count,
    m.share_count,
    m.delta_likes,
    m.delta_views
FROM posts p
LEFT JOIN metrics_history m
    ON m.id = (
        SELECT id FROM metrics_history
        WHERE post_id = p.id
        ORDER BY snapshot_at DESC LIMIT 1
    )
WHERE p.is_active = 1
  AND p.posted_at >= datetime('now', '-24 hours')
ORDER BY p.trend_score DESC;


-- Top trending hashtag cross-platform trong 24h (dùng cho dashboard)
CREATE VIEW IF NOT EXISTS v_trending_tags_24h AS
SELECT
    tag_value,
    tag_type,
    platform,
    post_count,
    total_interactions,
    velocity_score,
    computed_at
FROM trend_summaries
WHERE computed_at >= datetime('now', '-1 hours')   -- lấy batch tính gần nhất
ORDER BY velocity_score DESC;
