# Kế Hoạch Cải Tiến Facebook Scraper Tool

## 📋 Tóm Tắt Yêu Cầu
1. ✅ Người dùng đăng ký: link group, page, user
2. ✅ Tool tự động quét lấy thông tin: bài đăng + metric (like, share, comment)
3. ✅ Theo dõi bài đăng mới trong 24h và cập nhật metric
4. ✅ Tạo API trả về dữ liệu thống kê
5. ✅ Lên kế hoạch cải tiến

---

## 📊 Phân Tích Tình Trạng Hiện Tại

### Điểm Mạnh ✅
- ✓ Đã có modules scrape: groups, pages, users
- ✓ Có UI (PyQt6) cho người dùng
- ✓ Hỗ trợ proxy rotation
- ✓ Xử lý cookies & fb_dtsg

### Hạn Chế ❌
- ✗ Không có database để lưu trữ dữ liệu
- ✗ Không theo dõi metric theo thời gian
- ✗ Không có scheduling/background task
- ✗ Không có API
- ✗ Không có system user registration
- ✗ Không update lại metric của bài cũ

---

## 🏗️ Kiến Trúc Đề Xuất

```
facebook_post_comment_scraper/
├── 📁 backend/
│   ├── 📁 api/
│   │   ├── __init__.py
│   │   ├── main.py (FastAPI app)
│   │   ├── auth.py
│   │   ├── routes/
│   │   │   ├── users.py
│   │   │   ├── sources.py (groups/pages/users)
│   │   │   ├── posts.py
│   │   │   ├── analytics.py
│   │   │   └── __init__.py
│   │   └── middleware.py
│   │
│   ├── 📁 database/
│   │   ├── __init__.py
│   │   ├── models.py (SQLAlchemy models)
│   │   ├── schemas.py (Pydantic schemas)
│   │   └── crud.py (Database operations)
│   │
│   ├── 📁 scraper/
│   │   ├── __init__.py
│   │   ├── scraper_engine.py (Main orchestrator)
│   │   ├── post_scraper.py
│   │   ├── group_scraper.py
│   │   ├── page_scraper.py
│   │   ├── user_scraper.py
│   │   └── metrics_updater.py
│   │
│   ├── 📁 scheduler/
│   │   ├── __init__.py
│   │   ├── task_scheduler.py (APScheduler)
│   │   ├── periodic_tasks.py
│   │   └── monitor.py
│   │
│   ├── 📁 utils/
│   │   ├── __init__.py
│   │   ├── proxy_manager.py
│   │   ├── logger.py
│   │   └── helpers.py
│   │
│   └── config.py
│
├── 📁 frontend/
│   ├── facebook_ui_v2.py (Improved PyQt6 UI)
│   └── components/
│       ├── dashboard.py
│       ├── source_manager.py
│       ├── analytics_viewer.py
│       └── settings.py
│
├── 📁 data/
│   ├── database.db (SQLite hoặc PostgreSQL)
│   └── config.json
│
├── requirements.txt
├── .env.example
├── README.md
└── run_app.py
```

---

## 🗄️ Database Schema

### 1. Users (Người dùng)
```sql
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    username VARCHAR UNIQUE NOT NULL,
    email VARCHAR UNIQUE NOT NULL,
    password_hash VARCHAR NOT NULL,
    created_at TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);
```

### 2. Sources (Nguồn theo dõi)
```sql
CREATE TABLE sources (
    id INTEGER PRIMARY KEY,
    user_id INTEGER FOREIGN KEY,
    source_type VARCHAR (group/page/user),
    facebook_id VARCHAR,
    facebook_url VARCHAR,
    source_name VARCHAR,
    created_at TIMESTAMP,
    last_scraped TIMESTAMP,
    is_active BOOLEAN DEFAULT TRUE
);
```

### 3. Posts (Bài đăng)
```sql
CREATE TABLE posts (
    id INTEGER PRIMARY KEY,
    source_id INTEGER FOREIGN KEY,
    facebook_post_id VARCHAR UNIQUE,
    post_url VARCHAR,
    content TEXT,
    posted_at TIMESTAMP,
    created_at TIMESTAMP,
    is_tracked BOOLEAN DEFAULT TRUE
);
```

### 4. Post Metrics (Metric của bài đăng)
```sql
CREATE TABLE post_metrics (
    id INTEGER PRIMARY KEY,
    post_id INTEGER FOREIGN KEY,
    likes_count INTEGER,
    shares_count INTEGER,
    comments_count INTEGER,
    views_count INTEGER,
    recorded_at TIMESTAMP,
    FOREIGN KEY (post_id) REFERENCES posts(id)
);
```

### 5. Comments (Bình luận)
```sql
CREATE TABLE comments (
    id INTEGER PRIMARY KEY,
    post_id INTEGER FOREIGN KEY,
    facebook_comment_id VARCHAR,
    commenter_name VARCHAR,
    comment_text TEXT,
    likes_count INTEGER,
    created_at TIMESTAMP,
    FOREIGN KEY (post_id) REFERENCES posts(id)
);
```

---

## 🔌 API Endpoints Design

### Authentication
```
POST   /api/auth/register              - Đăng ký user mới
POST   /api/auth/login                 - Đăng nhập
POST   /api/auth/logout                - Đăng xuất
GET    /api/auth/me                    - Lấy thông tin user hiện tại
```

### Source Management
```
POST   /api/sources                    - Thêm nguồn mới (group/page/user)
GET    /api/sources                    - Lấy danh sách nguồn của user
PUT    /api/sources/{id}               - Cập nhật nguồn
DELETE /api/sources/{id}               - Xóa nguồn
POST   /api/sources/{id}/refresh       - Quét ngay lập tức
GET    /api/sources/{id}/posts         - Lấy bài đăng của nguồn
```

### Posts & Metrics
```
GET    /api/posts                      - Lấy danh sách bài đăng
GET    /api/posts/{id}                 - Chi tiết bài đăng
GET    /api/posts/{id}/metrics         - Lịch sử metric của bài
GET    /api/posts/{id}/comments        - Bình luận của bài
GET    /api/posts/source/{source_id}   - Bài đăng theo nguồn
```

### Analytics
```
GET    /api/analytics/summary          - Tổng hợp thống kê
GET    /api/analytics/source/{id}      - Thống kê theo nguồn
GET    /api/analytics/posts/{id}       - Xu hướng metric của bài
GET    /api/analytics/trending         - Bài đăng trending
GET    /api/analytics/growth           - Biểu đồ tăng trưởng
GET    /api/analytics/export           - Export dữ liệu (CSV/JSON)
```

### Admin
```
GET    /api/admin/scraper-status       - Trạng thái scraper
POST   /api/admin/scraper-action       - Điều khiển scraper (start/stop/pause)
GET    /api/admin/logs                 - Xem logs
```

---

## ⏱️ Scheduling Strategy

### Tần suất Quét
```python
# Bài đăng mới (< 24h)
- Quét lại: Mỗi 30 phút
- Lý do: Để theo dõi xu hướng metric ban đầu

# Bài đăng cũ (24h - 7 ngày)
- Quét lại: Mỗi 6 tiếng
- Lý do: Metric tăng chậm hơn

# Bài đăng rất cũ (> 7 ngày)
- Quét lại: Mỗi 24 giờ (nếu user yêu cầu)
- Lý do: Tiết kiệm proxy/resources
```

### APScheduler Tasks
```python
1. periodic_scrape_new_posts()     # Mỗi 30 phút
2. update_recent_post_metrics()    # Mỗi 6 tiếng
3. cleanup_old_data()              # Mỗi 24h
4. generate_analytics_cache()      # Mỗi 1 giờ
5. health_check()                  # Mỗi 5 phút
```

---

## 📊 Features Chi Tiết

### 1. User Registration & Authentication
- ✓ Đăng ký tài khoản
- ✓ JWT token-based auth
- ✓ Lưu cookies/fb_dtsg (encrypted) theo user
- ✓ Reset password

### 2. Source Management
- ✓ Thêm group Facebook bằng URL
- ✓ Thêm page Facebook bằng URL
- ✓ Thêm user profile bằng URL
- ✓ Validate URL & extract ID
- ✓ Test connection trước khi lưu
- ✓ Dashboard hiển thị tất cả sources

### 3. Scraping Engine
- ✓ Scrape bài đăng từ group/page/user
- ✓ Lấy comments (1 level hoặc nested replies)
- ✓ Lấy images/videos
- ✓ Handle rate limiting & blocking
- ✓ Retry mechanism
- ✓ Proxy rotation tự động

### 4. Metrics Tracking
- ✓ Lưu metric snapshot theo thời gian
- ✓ Tính delta (tăng/giảm)
- ✓ Lưu engagement rate
- ✓ Phát hiện anomaly (spike likes)

### 5. Analytics & Dashboard
- ✓ Tổng hợp thống kê theo source
- ✓ Biểu đồ xu hướng (chart)
- ✓ Top posts by engagement
- ✓ Growth rate analysis
- ✓ Keyword/hashtag analysis (tuỳ chọn)

### 6. Export & Reports
- ✓ Export CSV/JSON
- ✓ Schedule reports (daily/weekly)
- ✓ Email reports
- ✓ PDF reports

---

## 🛠️ Tech Stack Đề Xuất

### Backend
```
Framework:     FastAPI (async, modern)
Database:      PostgreSQL + SQLAlchemy ORM
                (SQLite for dev/testing)
Scheduling:    APScheduler
Authentication: JWT + python-jose
Validation:    Pydantic
Logging:       Python logging + structured logging
```

### Frontend
```
UI Framework:  PyQt6 (giữ lại) hoặc migrate sang web
Web:           React/Vue.js (optional)
HTTP Client:   requests/httpx
Visualization: Matplotlib/Plotly
```

### DevOps
```
Container:     Docker + Docker Compose
Task Queue:    Celery (optional, nếu cần scale)
Redis:         Cache/message broker (optional)
Monitoring:    Prometheus + Grafana (optional)
```

---

## 📋 Implementation Roadmap

### Phase 1: Core Infrastructure (Week 1-2)
- [ ] Setup FastAPI project structure
- [ ] Implement database models (SQLAlchemy)
- [ ] Create authentication system (JWT)
- [ ] Setup base API endpoints
- [ ] Create .env configuration

### Phase 2: Source Management (Week 2-3)
- [ ] Build user registration system
- [ ] Implement source CRUD endpoints
- [ ] URL validation & ID extraction
- [ ] Connection testing
- [ ] Database integration

### Phase 3: Scraping Integration (Week 3-4)
- [ ] Refactor existing scrapers into engine
- [ ] Integrate post scraper
- [ ] Integrate comment scraper
- [ ] Implement error handling & retries
- [ ] Add proxy rotation

### Phase 4: Scheduling & Monitoring (Week 4-5)
- [ ] Setup APScheduler
- [ ] Implement periodic tasks
- [ ] Create task monitoring
- [ ] Add health checks
- [ ] Implement logging

### Phase 5: Metrics & Analytics (Week 5-6)
- [ ] Metrics collection system
- [ ] Time-series data tracking
- [ ] Analytics calculation
- [ ] Dashboard endpoints
- [ ] Export functionality

### Phase 6: Testing & Optimization (Week 6-7)
- [ ] Unit tests
- [ ] Integration tests
- [ ] Performance optimization
- [ ] Error handling improvements
- [ ] Documentation

### Phase 7: Deployment & UI (Week 7-8)
- [ ] Docker setup
- [ ] Update PyQt UI
- [ ] API documentation (Swagger)
- [ ] Deployment guide
- [ ] User guide

---

## 🔒 Security Considerations

1. **Authentication**
   - Mã hóa password (bcrypt)
   - JWT tokens (expire trong 24h)
   - Refresh token mechanism

2. **Data Protection**
   - Encrypt cookies/sensitive data
   - HTTPS only (production)
   - Rate limiting on API

3. **Access Control**
   - User chỉ thấy dữ liệu của mình
   - Role-based access (admin)
   - API key for programmatic access

4. **Proxy & IP**
   - Rotate proxy thường xuyên
   - Hide real IP
   - Respect Facebook's robots.txt

---

## 📦 Dependencies Cần Thêm

```
# Framework
fastapi>=0.104.0
uvicorn>=0.24.0
sqlalchemy>=2.0.0
alembic>=1.12.0

# Database
psycopg2-binary>=2.9.0  (PostgreSQL)
sqlite3 (built-in)

# Authentication
pydantic>=2.0.0
python-jose[cryptography]>=3.3.0
passlib[bcrypt]>=1.7.4
python-dotenv>=1.0.0

# Scheduling
APScheduler>=3.10.4

# Data Processing
pandas>=2.0.0
numpy>=1.24.0

# Visualization
matplotlib>=3.7.0
plotly>=5.14.0

# Utilities
requests>=2.31.0
httpx>=0.25.0
pydantic-settings>=2.0.0

# Development
pytest>=7.4.0
pytest-asyncio>=0.21.0
black>=23.0.0
flake8>=6.0.0
```

---

## 🎯 Success Metrics

- ✓ API response time < 200ms
- ✓ Database queries optimized (< 100ms)
- ✓ Scraper uptime > 99%
- ✓ Handle 1000+ posts simultaneously
- ✓ Update metrics untuk 100 sources daily
- ✓ Zero data loss (backup system)
- ✓ User-friendly dashboard

---

## 📝 Next Steps

1. **Immediate**: Thiết kế chi tiết database schema
2. **Week 1**: Setup FastAPI & database infrastructure
3. **Week 2**: Implement authentication & user management
4. **Week 3**: Build source management API
5. **Week 4**: Refactor & integrate scrapers
6. **Week 5**: Setup scheduling system
7. **Week 6-8**: Analytics, testing, deployment

---

## 📞 Questions & Considerations

1. Dùng PostgreSQL hay SQLite?
   - **Recommend**: PostgreSQL (production), SQLite (dev)

2. Lưu image/video URLs hay download to disk?
   - **Recommend**: Lưu URLs, download on-demand

3. Lưu full comments hay summary only?
   - **Recommend**: Full comments (analytics advantage)

4. Mối quan hệ nested replies?
   - **Recommend**: Lưu comment hierarchy

5. Export format nào?
   - **Recommend**: CSV, JSON, PDF

---

**Last Updated**: 2024
**Status**: Ready for Implementation
