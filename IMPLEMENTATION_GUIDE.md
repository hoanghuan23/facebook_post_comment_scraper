# 🚀 Facebook Post & Comment Scraper - Implementation Guide

## 📖 Overview

This is a complete upgrade plan for the Facebook scraper tool. The new architecture includes:
- **FastAPI backend** with REST API
- **SQLAlchemy ORM** for database management
- **APScheduler** for background tasks
- **JWT authentication** for user management
- **Analytics engine** with time-series metrics
- **Multi-source support** (groups, pages, users)

---

## 🛠️ Quick Start

### 1. Setup Environment

```bash
# Clone or setup project
cd facebook_post_comment_scraper

# Create virtual environment
python -m venv venv

# Activate virtual environment
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment Variables

```bash
# Copy example config
cp .env.example .env

# Edit .env with your settings
# - Set SECRET_KEY (generate with: openssl rand -hex 32)
# - Set DATABASE_URL (use SQLite for development)
# - Configure proxy settings if needed
```

### 3. Initialize Database

```bash
# Database will auto-initialize on first run
# Or manually with Python:
python -c "from backend.database.db import init_db; init_db()"
```

### 4. Run API Server

```bash
# Development mode
python backend/api/main.py

# Or with uvicorn directly
uvicorn backend.api.main:app --reload --host 0.0.0.0 --port 8000
```

Server will be available at: `http://localhost:8000`
API docs: `http://localhost:8000/api/docs`

---

## 📚 API Endpoints Overview

### Authentication
```
POST   /api/auth/register       - Register new user
POST   /api/auth/login          - Login and get token
GET    /api/auth/me             - Get current user profile
POST   /api/auth/logout         - Logout
```

### Sources (Groups/Pages/Users)
```
POST   /api/sources              - Add new source
GET    /api/sources              - List all sources
GET    /api/sources/{id}         - Get source details
PUT    /api/sources/{id}         - Update source
DELETE /api/sources/{id}         - Delete source
POST   /api/sources/{id}/refresh - Trigger manual scrape
```

### Posts
```
GET    /api/posts                - List all posts
GET    /api/posts/{id}           - Get post details with metrics
GET    /api/posts/{id}/metrics   - Get metrics history
GET    /api/posts/{id}/comments  - Get comments
```

### Analytics
```
GET    /api/analytics/summary         - Overall statistics
GET    /api/analytics/source/{id}     - Source analytics
GET    /api/analytics/posts/{id}      - Post growth
GET    /api/analytics/trending        - Trending posts
GET    /api/analytics/growth          - Growth trends
POST   /api/analytics/export          - Export data
```

### Admin
```
GET    /api/admin/scraper-status    - Scraper status
POST   /api/admin/scraper-action    - Control scraper
GET    /api/admin/logs              - View logs
GET    /api/admin/users             - List all users
GET    /api/admin/stats             - System statistics
```

---

## 🗂️ Project Structure

```
facebook_post_comment_scraper/
├── backend/
│   ├── api/
│   │   ├── main.py                 # FastAPI application
│   │   ├── auth.py                 # JWT authentication
│   │   ├── middleware.py           # Custom middleware
│   │   └── routes/
│   │       ├── auth.py             # Auth endpoints
│   │       ├── sources.py          # Source management
│   │       ├── posts.py            # Post management
│   │       ├── analytics.py        # Analytics endpoints
│   │       └── admin.py            # Admin endpoints
│   │
│   ├── database/
│   │   ├── models.py               # SQLAlchemy models
│   │   ├── schemas.py              # Pydantic schemas
│   │   ├── crud.py                 # Database operations
│   │   └── db.py                   # Database connection
│   │
│   ├── scraper/
│   │   ├── scraper_engine.py       # Main scraper
│   │   ├── post_scraper.py         # Post scraping
│   │   ├── comment_scraper.py      # Comment scraping
│   │   └── metrics_updater.py      # Metrics update
│   │
│   ├── scheduler/
│   │   ├── task_scheduler.py       # APScheduler setup
│   │   └── periodic_tasks.py       # Background tasks
│   │
│   ├── utils/
│   │   ├── logger.py               # Logging setup
│   │   ├── proxy_manager.py        # Proxy rotation
│   │   └── helpers.py              # Helper functions
│   │
│   └── config.py                   # Configuration
│
├── data/
│   └── facebook_scraper.db         # SQLite database
│
├── logs/
│   └── app.log                     # Application logs
│
├── facebook_ui.py                  # PyQt6 UI (legacy)
├── main.py                         # Utility functions
├── requirements.txt                # Dependencies
├── .env.example                    # Environment template
├── .env                            # Environment (create from .env.example)
├── IMPROVEMENT_PLAN.md             # This plan
└── README.md                       # Project README
```

---

## 🔧 Implementation Phases

### Phase 1: Core Infrastructure ✅
- [x] Database models (SQLAlchemy)
- [x] Pydantic schemas
- [x] Configuration system
- [x] FastAPI app setup
- [x] Database initialization
- [ ] CRUD operations (to implement)

### Phase 2: Authentication ✅
- [x] User registration/login endpoints
- [x] JWT token generation
- [x] Password hashing
- [x] Protected route decorators
- [ ] Email verification (to add)
- [ ] Password reset (to add)

### Phase 3: Source Management (In Progress)
- [ ] Add source endpoint
- [ ] URL validation & ID extraction
- [ ] Connection testing
- [ ] Source listing
- [ ] Source updates

### Phase 4: Scraping Integration (Pending)
- [ ] Refactor existing scrapers
- [ ] Integrate into API
- [ ] Error handling
- [ ] Retry mechanism

### Phase 5: Scheduling (In Progress)
- [x] APScheduler setup
- [x] Task structure
- [ ] Implement periodic tasks
- [ ] Task monitoring

### Phase 6: Analytics (Pending)
- [ ] Metrics aggregation
- [ ] Time-series analysis
- [ ] Dashboard data
- [ ] Export functionality

### Phase 7: Testing & Deployment (Pending)
- [ ] Unit tests
- [ ] Integration tests
- [ ] Docker setup
- [ ] Deployment guide

---

## 📊 Database Models

### Users
- Stores user accounts with authentication
- Manages encrypted Facebook credentials

### Sources
- Represents Facebook groups, pages, or users to track
- Stores metadata and tracking preferences

### Posts
- Individual Facebook posts
- Stores engagement metrics (initial & current)

### Post Metrics
- Time-series data for each post
- Snapshots at each update for historical analysis

### Comments
- Comments on posts
- Stores commenter info and comment text

### Analytics Cache
- Pre-calculated analytics for fast queries
- Daily aggregations

### Logs
- Scraper execution logs
- Error tracking

---

## 🔐 Security Features

1. **Password Security**
   - Bcrypt hashing (via passlib)
   - Never store plain passwords

2. **JWT Tokens**
   - 24-hour access tokens
   - 7-day refresh tokens
   - Cryptographic signing

3. **Data Protection**
   - Encrypt sensitive data (cookies, tokens)
   - HTTPS recommended (production)
   - CORS configuration

4. **Access Control**
   - User isolation (see only own data)
   - Admin-only endpoints
   - Rate limiting

---

## 🚀 Testing

```bash
# Run tests
pytest

# With coverage
pytest --cov=backend

# Specific test file
pytest tests/test_auth.py -v
```

---

## 📦 Docker Deployment

```bash
# Build Docker image
docker build -t facebook-scraper .

# Run with docker-compose
docker-compose up -d

# Check logs
docker-compose logs -f api
```

---

## 📝 Development Workflow

1. Create branch for feature: `git checkout -b feature/add-export`
2. Implement feature
3. Write tests
4. Run linting: `black .` and `flake8`
5. Commit changes
6. Create pull request

---

## 🐛 Troubleshooting

### Database Connection Error
```
# Ensure .env DATABASE_URL is correct
# For SQLite: sqlite:///./data/facebook_scraper.db
# For PostgreSQL: postgresql://user:pass@localhost/dbname
```

### Port Already in Use
```bash
# Use different port
uvicorn backend.api.main:app --port 8001
```

### Import Errors
```bash
# Reinstall dependencies
pip install -r requirements.txt --force-reinstall
```

### Scheduler Not Running
```
# Check SCHEDULER_ENABLED in .env is True
# Check logs for scheduler startup messages
```

---

## 📚 API Usage Examples

### Register User
```bash
curl -X POST "http://localhost:8000/api/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "john_doe",
    "email": "john@example.com",
    "password": "secure_password"
  }'
```

### Login
```bash
curl -X POST "http://localhost:8000/api/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "john_doe",
    "password": "secure_password"
  }'
```

### Add Source
```bash
curl -X POST "http://localhost:8000/api/sources" \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{
    "source_type": "group",
    "facebook_url": "https://facebook.com/groups/xyz",
    "include_comments": true
  }'
```

### Get Analytics
```bash
curl -X GET "http://localhost:8000/api/analytics/source/1" \
  -H "Authorization: Bearer {token}"
```

---

## 🤝 Contributing

Contributions welcome! Please:
1. Follow PEP 8 style guide
2. Add tests for new features
3. Update documentation
4. Create descriptive commit messages

---

## 📄 License

[Specify your license here]

---

## 📞 Support

For issues and questions:
1. Check existing issues
2. Review documentation
3. Create new issue with details

---

**Last Updated**: 2024
**Version**: 2.0.0
