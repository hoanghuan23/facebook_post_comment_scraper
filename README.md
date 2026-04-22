# Facebook Post & Comment Scraper Tool - v2.0

## 📋 Project Description

A comprehensive Facebook scraping tool that automatically collects posts, comments, and engagement metrics from Facebook groups, pages, and users. The tool includes:

- ✅ **Automatic data collection** - Posts and comments
- ✅ **Metrics tracking** - Likes, shares, comments (with history)
- ✅ **24-hour monitoring** - Automatic updates for new posts
- ✅ **REST API** - Complete API for data access
- ✅ **Analytics** - Trending posts, growth analysis, engagement rates
- ✅ **Multi-user support** - User accounts and data isolation
- ✅ **Scheduling system** - Background tasks for automatic updates
- ✅ **Web UI** - PyQt6 interface for easy management

---

## 🎯 Key Features

### 1. User Management
- User registration and authentication
- JWT-based authorization
- Account settings and preferences

### 2. Source Management
- Add Facebook groups, pages, or user profiles
- Automatic metadata extraction (name, members, followers)
- Enable/disable tracking for individual sources
- Customize scraping preferences

### 3. Post Tracking
- Automatic scraping of all posts from sources
- Store post content and metadata
- Track engagement metrics (likes, shares, comments)
- Maintain metric history for trend analysis

### 4. Comment Collection
- Scrape comments from posts
- Store commenter information
- Track comment engagement (likes, replies)
- Support for nested reply threads

### 5. Metrics & Analytics
- Time-series metric snapshots
- Calculate growth rates and trends
- Identify trending posts
- Generate statistical reports
- Export data (CSV, JSON, PDF)

### 6. API & Integration
- RESTful API endpoints
- Complete API documentation (Swagger/OpenAPI)
- Programmatic data access
- Third-party integration support

---

## 🚀 Quick Start

### Prerequisites
- Python 3.9+
- Git
- Virtual environment (optional but recommended)

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/yourusername/facebook_post_comment_scraper.git
cd facebook_post_comment_scraper

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Setup environment
cp .env.example .env
# Edit .env with your configuration

# 5. Run the application
# On Windows:
run.bat
# On macOS/Linux:
bash run.sh

# Or using uvicorn directly:
uvicorn backend.api.main:app --reload
```

Server will start at: `http://localhost:8000`
API Documentation: `http://localhost:8000/api/docs`

---

## 📊 Architecture Overview

### Technology Stack
```
Frontend:  PyQt6 (Desktop UI)
Backend:   FastAPI (Python web framework)
Database:  PostgreSQL / SQLite
Scheduler: APScheduler (background tasks)
ORM:       SQLAlchemy
Auth:      JWT + Passlib (bcrypt)
```

### Project Structure
```
facebook_post_comment_scraper/
├── backend/
│   ├── api/              # FastAPI routes
│   ├── database/         # Database models & schemas
│   ├── scraper/          # Scraping logic
│   ├── scheduler/        # Background tasks
│   └── utils/            # Utilities (logging, etc.)
├── data/                 # SQLite database
├── logs/                 # Application logs
├── facebook_ui.py        # PyQt6 interface
└── requirements.txt      # Python dependencies
```

---

## 🔌 API Endpoints

### Authentication
```
POST   /api/auth/register      Register new user
POST   /api/auth/login         Login and get token
GET    /api/auth/me            Get current user
POST   /api/auth/logout        Logout
```

### Sources (Groups/Pages/Users)
```
POST   /api/sources              Add new source
GET    /api/sources              List all sources
GET    /api/sources/{id}         Get source details
PUT    /api/sources/{id}         Update source
DELETE /api/sources/{id}         Delete source
POST   /api/sources/{id}/refresh Trigger manual scrape
GET    /api/sources/{id}/posts   Get source posts
```

### Posts
```
GET    /api/posts                 List all posts
GET    /api/posts/{id}            Get post details
GET    /api/posts/{id}/metrics    Get metrics history
GET    /api/posts/{id}/comments   Get comments
```

### Analytics
```
GET    /api/analytics/summary      Overall statistics
GET    /api/analytics/source/{id}  Source analytics
GET    /api/analytics/posts/{id}   Post growth analysis
GET    /api/analytics/trending     Trending posts
GET    /api/analytics/growth       Growth trends
POST   /api/analytics/export       Export data
```

### Admin
```
GET    /api/admin/scraper-status    Scraper status
POST   /api/admin/scraper-action    Control scraper
GET    /api/admin/logs              View logs
GET    /api/admin/users             List users
GET    /api/admin/stats             System statistics
```

---

## 🗄️ Database Schema

### Core Tables
- **users** - User accounts and authentication
- **sources** - Facebook sources to track (groups/pages/users)
- **posts** - Facebook posts with current metrics
- **post_metrics** - Time-series metrics history
- **comments** - Comments on posts
- **analytics_cache** - Pre-calculated analytics
- **logs** - Application and task logs

---

## ⚙️ Configuration

Environment variables in `.env`:

```
# Server
API_HOST=0.0.0.0
API_PORT=8000
DEBUG=False

# Database
DATABASE_URL=postgresql://user:password@localhost:5432/facebook_scraper

# Security
SECRET_KEY=your-secret-key-here
ENCRYPTION_KEY=your-encryption-key

# Scheduling
SCHEDULER_ENABLED=True
TASK_SCRAPE_NEW_POSTS_INTERVAL=1800      # 30 minutes
TASK_UPDATE_RECENT_METRICS_INTERVAL=21600 # 6 hours

# Proxy (optional)
PROXY_ENABLED=False
PROXY_URL=http://proxy.example.com:8080
```

---

## 🧪 Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=backend

# Run specific test file
pytest tests/test_auth.py -v
```

---

## 🐳 Docker Deployment

```bash
# Build and run with Docker Compose
docker-compose up -d

# View logs
docker-compose logs -f api

# Stop services
docker-compose down
```

---

## 📚 Documentation

- [IMPROVEMENT_PLAN.md](IMPROVEMENT_PLAN.md) - Detailed improvement roadmap
- [IMPLEMENTATION_GUIDE.md](IMPLEMENTATION_GUIDE.md) - Implementation steps
- [API Documentation](http://localhost:8000/api/docs) - Interactive API docs

---

## 🔐 Security Features

- **Password Security**: Bcrypt hashing (passlib)
- **JWT Authentication**: Secure token-based auth
- **Data Encryption**: Sensitive data encrypted at rest
- **User Isolation**: Users only see their own data
- **Access Control**: Role-based permissions (user/admin)
- **Rate Limiting**: API rate limiting
- **CORS Protection**: Cross-origin request validation

---

## 📈 Usage Examples

### Register and Login
```python
import requests

# Register
response = requests.post("http://localhost:8000/api/auth/register", json={
    "username": "john_doe",
    "email": "john@example.com",
    "password": "secure_password"
})
token = response.json()["access_token"]

# Login
response = requests.post("http://localhost:8000/api/auth/login", json={
    "username": "john_doe",
    "password": "secure_password"
})
token = response.json()["access_token"]
```

### Add a Source
```python
headers = {"Authorization": f"Bearer {token}"}

response = requests.post(
    "http://localhost:8000/api/sources",
    headers=headers,
    json={
        "source_type": "group",
        "facebook_url": "https://facebook.com/groups/12345",
        "include_comments": True,
        "max_days_old": 30
    }
)
source_id = response.json()["id"]
```

### Get Analytics
```python
response = requests.get(
    f"http://localhost:8000/api/analytics/source/{source_id}",
    headers=headers,
    params={"days": 30}
)
analytics = response.json()
```

---

## 🛠️ Development

### Setup Development Environment
```bash
# Install dev dependencies
pip install -r requirements.txt

# Format code
black .

# Check code style
flake8 .

# Sort imports
isort .
```

### Running Tests
```bash
pytest --cov=backend --cov-report=html
```

---

## 🐛 Troubleshooting

### Database Connection Issues
- Ensure PostgreSQL/SQLite is running
- Check DATABASE_URL in .env
- Verify database credentials

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

### Scheduler Not Starting
- Check SCHEDULER_ENABLED=True in .env
- Check application logs
- Verify APScheduler is installed

---

## 📝 License

[Specify your license here - MIT, Apache 2.0, etc.]

---

## 🤝 Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Code Guidelines
- Follow PEP 8 style guide
- Write tests for new features
- Update documentation
- Use descriptive commit messages

---

## 📞 Support & Contact

For issues, questions, or suggestions:

1. Check [Issues](https://github.com/yourusername/facebook_post_comment_scraper/issues)
2. Review [Documentation](IMPLEMENTATION_GUIDE.md)
3. Create new issue with details
4. Contact: your.email@example.com

---

## 📦 Changelog

### Version 2.0.0 (Current)
- ✨ New FastAPI backend
- 🗄️ SQLAlchemy ORM with PostgreSQL
- 🔐 JWT authentication system
- 📊 Time-series metrics tracking
- ⏰ APScheduler background tasks
- 📈 Analytics and reporting features
- 🐳 Docker support

### Version 1.0.0
- Initial release with basic scraping

---

## 🙏 Acknowledgments

- FastAPI documentation and community
- SQLAlchemy ORM documentation
- APScheduler for scheduling library
- All contributors and supporters

---

**Last Updated**: 2024
**Maintainer**: Your Name
**Status**: Active Development ✅
