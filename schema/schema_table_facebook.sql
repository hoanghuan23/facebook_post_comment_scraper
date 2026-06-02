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

-- bảng task_logs lưu trữ lịch sử thực thi của các tác vụ định kỳ (scrape_posts, update_metrics, generate_analytics) để theo dõi hiệu suất và phát hiện lỗi
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

-- bảng facebook_sessions lưu trữ thông tin về các phiên đăng nhập Facebook của người dùng, bao gồm cookie, token và trạng thái phiên để quản lý việc truy cập dữ liệu từ Facebook một cách hiệu quả và an toàn
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

-- bảng sources lưu trữ thông tin về các nguồn dữ liệu Facebook mà người dùng theo dõi, bao gồm ID Facebook, URL, tên nguồn, mô tả, số lượng thành viên và người theo dõi, trạng thái quyền truy cập và các cài đặt liên quan đến việc thu thập dữ liệu để quản lý hiệu quả việc theo dõi và phân tích các trang Facebook được quan tâm
CREATE TABLE sources (
        id INTEGER NOT NULL, 
        user_id INTEGER NOT NULL, 
        source_type VARCHAR(5) NOT NULL, 
        facebook_id VARCHAR(50) NOT NULL, 
        facebook_url VARCHAR(255) NOT NULL, 
        source_name VARCHAR(255), 
        description TEXT, 
        member_count INTEGER, 
        is_active BOOLEAN, 
        include_comments BOOLEAN, 
        max_days_old INTEGER, 
        permission_status VARCHAR(11), 
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

-- thêm cột schedule_tier là chiều hệ thống => DB. Thông qua tính toán dựa tần suất , số lượng đăng của từng source để phân loại tier (1 - 4). null = chưa tính
ALTER TABLE sources ADD COLUMN schedule_tier INTEGER DEFAULT NULL;

-- thêm cột schedule_override_min. User tự set tần suất (phút). Null = auto . Khi có giá trị sẽ bỏ qua tier
ALTER TABLE sources ADD COLUMN schedule_override_minutes INTEGER DEFAULT NULL;

-- bảng posts lưu trữ thông tin về các bài đăng trên Facebook của từng nguồn, bao gồm ID bài đăng, nội dung, số lượng media, trạng thái có hình ảnh/video, thời gian đăng, trạng thái theo dõi và các chỉ số tương tác để quản lý và phân tích hiệu suất của các bài đăng trên Facebook được theo dõi
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
        metric_tier VARCHAR(20) NOT NULL DEFAULT 'bootstrap',
        next_metric_update DATETIME,
        last_engagement_velocity FLOAT,
        cold_check_count INTEGER NOT NULL DEFAULT 0,
        metric_scan_miss_count INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (id), 
        FOREIGN KEY(source_id) REFERENCES sources (id),
        CONSTRAINT ck_posts_metric_tier CHECK (metric_tier IN ('bootstrap', 'hot', 'warm', 'cold', 'expired'))
);
CREATE INDEX idx_post_last_update ON posts (last_metric_update);
CREATE INDEX idx_post_source ON posts (source_id);
CREATE INDEX idx_post_facebook_id ON posts (facebook_post_id);
CREATE UNIQUE INDEX ix_posts_facebook_post_id ON posts (facebook_post_id);
CREATE INDEX idx_post_posted_at ON posts (posted_at);
CREATE INDEX idx_post_metric_due ON posts (is_tracked, next_metric_update);

-- bảng analytics_cache lưu trữ kết quả tổng hợp các chỉ số tương tác (likes, shares, comments, views) của từng source theo ngày để phục vụ cho việc phân tích hiệu suất và xu hướng của các trang Facebook được theo dõi
CREATE TABLE analytics_cache (
        id INTEGER NOT NULL, 
        source_id INTEGER NOT NULL, 
        date DATETIME NOT NULL, 
        total_posts INTEGER, 
        total_likes INTEGER, 
        total_shares INTEGER, 
        total_comments INTEGER, 
        avg_likes_per_post FLOAT, 
        top_post_id VARCHAR(100), 
        growth_rate FLOAT, 
        cached_at DATETIME, 
        PRIMARY KEY (id), 
        CONSTRAINT uq_analytics_cache UNIQUE (source_id, date), 
        FOREIGN KEY(source_id) REFERENCES sources (id)
);
CREATE INDEX idx_analytics_source_date ON analytics_cache (source_id, date);

-- tạo bảng post_metrics lưu lịch sử thay đổi của các chỉ số tương tác (likes, shares, comments, views) theo thời gian để có thể phân tích xu hướng và hiệu suất của bài đăng
CREATE TABLE post_metrics (
        id INTEGER NOT NULL, 
        post_id INTEGER NOT NULL, 
        job_id INTEGER,
        likes_count INTEGER, 
        shares_count INTEGER, 
        comments_count INTEGER, 
        recorded_at DATETIME, 
        PRIMARY KEY (id), 
        FOREIGN KEY(post_id) REFERENCES posts (id),
        FOREIGN KEY(job_id) REFERENCES pipeline_jobs (id) ON DELETE SET NULL
);
CREATE INDEX ix_post_metrics_recorded_at ON post_metrics (recorded_at);
CREATE INDEX idx_metric_post_date ON post_metrics (post_id, recorded_at);
CREATE INDEX idx_post_metrics_job_time ON post_metrics (job_id, recorded_at);

-- bảng comments lưu trữ tất cả bình luận và phản hồi trên các bài đăng của Facebook, bao gồm cả thông tin về người bình luận, nội dung bình luận, số lượt thích và số lượng phản hồi để phục vụ cho việc phân tích tương tác chi tiết
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

-- bảng pipeline_jobs theo dõi toàn bộ pipeline (scrape_24h, scraper_job, update_metric, analytics)

CREATE TABLE pipeline_jobs (
    id              INTEGER PRIMARY KEY,
    job_type        VARCHAR(20) NOT NULL DEFAULT 'scraper_job'
                    CHECK (job_type IN ('scrape_24h', 'scraper_job', 'update_metric', 'analytics')),

    source_id       INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    session_id      INTEGER REFERENCES facebook_sessions(id) ON DELETE SET NULL,

    status          VARCHAR(10) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'running', 'done', 'failed')),

    posts_found     INTEGER NOT NULL DEFAULT 0,
    posts_new       INTEGER NOT NULL DEFAULT 0,
    items_total     INTEGER NOT NULL DEFAULT 0,
    items_updated   INTEGER NOT NULL DEFAULT 0,
    items_failed    INTEGER NOT NULL DEFAULT 0,

    error_message   TEXT,
    started_at      DATETIME,
    finished_at     DATETIME
);

CREATE INDEX idx_pipeline_jobs_source_time ON pipeline_jobs (source_id, started_at DESC);
CREATE INDEX idx_pipeline_jobs_type_status ON pipeline_jobs (job_type, status, started_at DESC);

-- bảng pipeline_logs lưu log chi tiết cho từng pipeline job để debug
CREATE TABLE pipeline_logs (
    id              INTEGER PRIMARY KEY,

    -- Liên kết job (thêm mới, quan trọng)
    job_id          INTEGER REFERENCES pipeline_jobs(id) ON DELETE SET NULL,

    source_id       INTEGER REFERENCES sources(id),
    log_level       VARCHAR(20),  -- 'INFO', 'WARNING', 'ERROR'
    message         TEXT NOT NULL,
    error_type      VARCHAR(100),
    error_details   TEXT,

    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_pipeline_logs_job     ON pipeline_logs (job_id, created_at);
CREATE INDEX idx_pipeline_logs_source  ON pipeline_logs (source_id, created_at);
CREATE INDEX idx_pipeline_logs_level   ON pipeline_logs (log_level, created_at);
