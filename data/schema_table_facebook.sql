CREATE TABLE users (
        id INTEGER NOT NULL, 
        username VARCHAR(50) NOT NULL, 
        email VARCHAR(100) NOT NULL, 
        password_hash VARCHAR(255) NOT NULL, 
        is_active BOOLEAN, 
        is_admin BOOLEAN, 
        created_at DATETIME, 
        last_login DATETIME, 
        PRIMARY KEY (id)
);
CREATE UNIQUE INDEX ix_users_username ON users (username);
CREATE UNIQUE INDEX ix_users_email ON users (email);
CREATE INDEX idx_user_username ON users (username);
CREATE INDEX idx_user_email ON users (email);
CREATE TABLE task_logs (
        id INTEGER NOT NULL, 
        task_name VARCHAR(100) NOT NULL, 
        status VARCHAR(20), 
        started_at DATETIME, 
        completed_at DATETIME, 
        duration_seconds FLOAT, 
        items_processed INTEGER, 
        errors_count INTEGER, 
        error_message TEXT, 
        created_at DATETIME, 
        PRIMARY KEY (id)
);
CREATE INDEX idx_task_name_date ON task_logs (task_name, created_at);
CREATE TABLE facebook_sessions (
        id INTEGER NOT NULL, 
        user_id INTEGER NOT NULL, 
        fb_user_id VARCHAR(50), 
        fb_cookies TEXT, 
        fb_dtsg VARCHAR(255), 
        fb_user_agent VARCHAR(500), 
        is_active BOOLEAN NOT NULL, 
        is_valid BOOLEAN NOT NULL, 
        last_verified DATETIME, 
        expires_at DATETIME, 
        created_at DATETIME, 
        PRIMARY KEY (id), 
        FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE INDEX idx_fb_sessions_user ON facebook_sessions (user_id, is_active);
CREATE TABLE sources (
        id INTEGER NOT NULL, 
        user_id INTEGER NOT NULL, 
        source_type VARCHAR(5) NOT NULL, 
        facebook_id VARCHAR(50) NOT NULL, 
        facebook_url VARCHAR(255) NOT NULL, 
        source_name VARCHAR(255), 
        description TEXT, 
        cover_image_url VARCHAR(500), 
        member_count INTEGER, 
        follower_count INTEGER, 
        is_active BOOLEAN, 
        include_comments BOOLEAN, 
        include_replies BOOLEAN, 
        max_days_old INTEGER, 
        permission_status VARCHAR(11), 
        permission_message TEXT, 
        access_restrictions TEXT, 
        permission_checked_at DATETIME, 
        is_accessible BOOLEAN, 
        created_at DATETIME, 
        last_scraped DATETIME, 
        next_scrape DATETIME, 
        PRIMARY KEY (id), 
        CONSTRAINT uq_user_source UNIQUE (user_id, facebook_id), 
        CONSTRAINT ck_sources_permission_status CHECK (permission_status IN ('granted', 'denied', 'restricted', 'not_checked', 'error')), 
        FOREIGN KEY(user_id) REFERENCES users (id)
);
CREATE INDEX idx_source_user_active ON sources (user_id, is_active);
CREATE INDEX idx_source_accessible ON sources (is_accessible);
CREATE INDEX idx_source_next_scrape ON sources (next_scrape);
CREATE INDEX idx_source_permission ON sources (permission_status);
CREATE TABLE posts (
        id INTEGER NOT NULL, 
        source_id INTEGER NOT NULL, 
        facebook_post_id VARCHAR(100) NOT NULL, 
        facebook_url VARCHAR(500) NOT NULL, 
        content TEXT, 
        media_count INTEGER, 
        has_images BOOLEAN, 
        has_videos BOOLEAN, 
        posted_at DATETIME NOT NULL, 
        created_at DATETIME, 
        is_tracked BOOLEAN, 
        tracking_until DATETIME, 
        is_deleted BOOLEAN, 
        last_metric_update DATETIME, 
        PRIMARY KEY (id), 
        FOREIGN KEY(source_id) REFERENCES sources (id)
);
CREATE INDEX idx_post_last_update ON posts (last_metric_update);
CREATE INDEX idx_post_source ON posts (source_id);
CREATE INDEX idx_post_facebook_id ON posts (facebook_post_id);
CREATE UNIQUE INDEX ix_posts_facebook_post_id ON posts (facebook_post_id);
CREATE INDEX idx_post_posted_at ON posts (posted_at);
CREATE TABLE analytics_cache (
        id INTEGER NOT NULL, 
        source_id INTEGER NOT NULL, 
        date DATETIME NOT NULL, 
        total_posts INTEGER, 
        total_likes INTEGER, 
        total_shares INTEGER, 
        total_comments INTEGER, 
        total_views INTEGER, 
        avg_engagement_rate FLOAT, 
        avg_likes_per_post FLOAT, 
        top_post_id VARCHAR(100), 
        growth_rate FLOAT, 
        cached_at DATETIME, 
        PRIMARY KEY (id), 
        CONSTRAINT uq_analytics_cache UNIQUE (source_id, date), 
        FOREIGN KEY(source_id) REFERENCES sources (id)
);
CREATE INDEX idx_analytics_source_date ON analytics_cache (source_id, date);
CREATE TABLE scraper_logs (
        id INTEGER NOT NULL, 
        source_id INTEGER, 
        log_level VARCHAR(20), 
        message TEXT NOT NULL, 
        error_type VARCHAR(100), 
        error_details TEXT, 
        created_at DATETIME, 
        PRIMARY KEY (id), 
        FOREIGN KEY(source_id) REFERENCES sources (id)
);
CREATE INDEX ix_scraper_logs_created_at ON scraper_logs (created_at);
CREATE INDEX idx_log_level_date ON scraper_logs (log_level, created_at);
CREATE TABLE post_metrics (
        id INTEGER NOT NULL, 
        post_id INTEGER NOT NULL, 
        likes_count INTEGER, 
        shares_count INTEGER, 
        comments_count INTEGER, 
        views_count INTEGER, 
        recorded_at DATETIME, 
        PRIMARY KEY (id), 
        FOREIGN KEY(post_id) REFERENCES posts (id)
);
CREATE INDEX ix_post_metrics_recorded_at ON post_metrics (recorded_at);
CREATE INDEX idx_metric_post_date ON post_metrics (post_id, recorded_at);
CREATE TABLE comments (
        id INTEGER NOT NULL, 
        post_id INTEGER NOT NULL, 
        parent_id INTEGER, 
        facebook_comment_id VARCHAR(100) NOT NULL, 
        commenter_id VARCHAR(50), 
        commenter_name VARCHAR(255), 
        commenter_url VARCHAR(500), 
        comment_text TEXT, 
        likes_count INTEGER, 
        reply_count INTEGER, 
        depth_level INTEGER, 
        created_at DATETIME, 
        last_updated DATETIME NOT NULL, 
        PRIMARY KEY (id), 
        FOREIGN KEY(post_id) REFERENCES posts (id), 
        FOREIGN KEY(parent_id) REFERENCES comments (id), 
        UNIQUE (facebook_comment_id)
);
CREATE INDEX idx_comment_facebook_id ON comments (facebook_comment_id);
CREATE INDEX idx_comment_post ON comments (post_id);
CREATE TABLE scrape_jobs (
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
CREATE TABLE sqlite_sequence(name,seq);
CREATE INDEX idx_jobs_source_time
    ON scrape_jobs(source_id, created_at DESC);
CREATE INDEX idx_jobs_status
    ON scrape_jobs(status, created_at DESC);