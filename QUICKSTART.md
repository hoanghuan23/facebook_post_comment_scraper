# 🚀 Quick Start Checklist

## Day 1: Setup & Verification (1-2 hours)

### Environment Setup
- [ ] Copy `.env.example` to `.env`
  ```bash
  cp .env.example .env
  ```
- [ ] Edit `.env` file:
  - [ ] Change `SECRET_KEY` (run: `python -c "import secrets; print(secrets.token_hex(32))"`)
  - [ ] Change `ENCRYPTION_KEY` (similar to SECRET_KEY)
  - [ ] Review database settings (SQLite is fine for now)
  - [ ] Check proxy settings (set to False if not using)

### Python Environment
- [ ] Create virtual environment
  ```bash
  python -m venv venv
  ```
- [ ] Activate virtual environment
  - Windows: `venv\Scripts\activate`
  - macOS/Linux: `source venv/bin/activate`
- [ ] Install dependencies
  ```bash
  pip install -r requirements.txt
  ```

### Database Initialization
- [ ] Initialize database
  ```bash
  python -c "from backend.database.db import init_db; init_db()"
  ```
- [ ] Verify database file created: `data/facebook_scraper.db`

### API Startup
- [ ] Start API server
  ```bash
  uvicorn backend.api.main:app --reload --host 0.0.0.0 --port 8000
  ```
- [ ] Verify startup messages (look for "Uvicorn running")

### API Testing
- [ ] Open browser: http://localhost:8000
- [ ] Should see welcome message
- [ ] Visit http://localhost:8000/api/docs
- [ ] Should see Swagger UI with all endpoints
- [ ] Test health check: GET /health

---

## Day 2-3: Authentication Testing (2-3 hours)

### Register User
- [ ] In Swagger UI, test `POST /api/auth/register`:
  ```json
  {
    "username": "testuser",
    "email": "test@example.com",
    "password": "Test@12345"
  }
  ```
- [ ] Should return token and user info

### Login User
- [ ] Test `POST /api/auth/login`:
  ```json
  {
    "username": "testuser",
    "password": "Test@12345"
  }
  ```
- [ ] Copy the `access_token` from response

### Get Profile
- [ ] Click "Authorize" button in Swagger
- [ ] Paste token: `Bearer {your_token_here}`
- [ ] Test `GET /api/auth/me`
- [ ] Should return logged-in user info

---

## Week 1: Implementation Priority

### Task 1: CRUD Operations (Days 1-2)
**File to create**: `backend/database/crud.py`

Tasks:
- [ ] User CRUD functions
- [ ] Source CRUD functions
- [ ] Post CRUD functions
- [ ] Comment CRUD functions
- [ ] Analytics CRUD functions

Start with:
```python
# backend/database/crud.py
from sqlalchemy.orm import Session
from backend.database import models

# User operations
def create_user(db: Session, username: str, email: str, password_hash: str):
    db_user = models.User(username=username, email=email, password_hash=password_hash)
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

# Continue with other models...
```

### Task 2: Scraper Integration (Days 3-4)
**File to create/update**: `backend/scraper/scraper_engine.py`

Tasks:
- [ ] Move existing scraper code to this file
- [ ] Integrate `post_scraper.py`
- [ ] Integrate `group_post_scraper_v2.py`
- [ ] Integrate `comment_scraper.py`
- [ ] Add error handling
- [ ] Add logging

### Task 3: Periodic Tasks (Days 5)
**File to update**: `backend/scheduler/periodic_tasks.py`

Tasks:
- [ ] Implement `periodic_scrape_new_posts()`
- [ ] Implement `update_recent_post_metrics()`
- [ ] Implement `cleanup_old_data()`
- [ ] Implement `generate_analytics_cache()`
- [ ] Test scheduling

---

## Week 2: Features & Testing

### Task 4: Wire CRUD to API Routes
- [ ] Update `backend/api/routes/sources.py` with real CRUD calls
- [ ] Update `backend/api/routes/posts.py` with real CRUD calls
- [ ] Update `backend/api/routes/analytics.py` with real logic
- [ ] Test all endpoints in Swagger

### Task 5: Create Test Suite
**Directory to create**: `tests/`

Files:
- [ ] `tests/conftest.py` - Test fixtures
- [ ] `tests/test_auth.py` - Authentication tests
- [ ] `tests/test_crud.py` - CRUD operation tests
- [ ] `tests/test_api.py` - API endpoint tests

Commands:
```bash
pytest                    # Run all tests
pytest -v               # Verbose output
pytest --cov=backend    # With coverage
```

### Task 6: Analytics Implementation
**File to create**: `backend/utils/analytics.py`

Functions needed:
- [ ] Calculate engagement rate
- [ ] Calculate growth rate
- [ ] Find trending posts
- [ ] Generate daily summaries
- [ ] Aggregate metrics

---

## Testing Endpoints Manually

### Register & Get Token
```bash
curl -X POST "http://localhost:8000/api/auth/register" \
  -H "Content-Type: application/json" \
  -d '{
    "username":"user1",
    "email":"user1@test.com",
    "password":"Pass123456"
  }'
```

### Save Token
```bash
TOKEN="your_token_from_response"
```

### Add a Source
```bash
curl -X POST "http://localhost:8000/api/sources" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "source_type":"group",
    "facebook_url":"https://facebook.com/groups/123456",
    "include_comments":true,
    "max_days_old":30
  }'
```

### List Sources
```bash
curl -X GET "http://localhost:8000/api/sources" \
  -H "Authorization: Bearer $TOKEN"
```

---

## Common Issues & Solutions

### Issue: ModuleNotFoundError
**Solution**: Reinstall dependencies
```bash
pip install -r requirements.txt --force-reinstall
```

### Issue: Port 8000 already in use
**Solution**: Use different port
```bash
uvicorn backend.api.main:app --port 8001
```

### Issue: Database locked (SQLite)
**Solution**: Close other connections and restart
```bash
rm data/facebook_scraper.db
python -c "from backend.database.db import init_db; init_db()"
```

### Issue: JWT token invalid
**Solution**: Get new token after SECRET_KEY change
```
1. Change SECRET_KEY in .env
2. Restart API
3. Re-register or re-login
```

### Issue: Scheduler not starting
**Solution**: Check logs and configuration
```bash
# In .env, verify:
SCHEDULER_ENABLED=True
# Check logs/ directory for errors
```

---

## Git Workflow (If Using Git)

### Create Feature Branches
```bash
git checkout -b feature/crud-operations
git checkout -b feature/scraper-integration
git checkout -b feature/periodic-tasks
```

### Commit Changes
```bash
git add .
git commit -m "feat: implement CRUD operations for posts"
git push origin feature/crud-operations
```

### Code Style
```bash
black .          # Format code
flake8 .         # Check style
isort .          # Sort imports
```

---

## Debugging Tips

### Enable Debug Logging
Edit `.env`:
```
DEBUG=True
LOG_LEVEL=DEBUG
```

### Check Database
```bash
# Open SQLite database
sqlite3 data/facebook_scraper.db

# View tables
.tables

# Query users
SELECT * FROM users;

# Exit
.quit
```

### View Application Logs
```bash
# Linux/macOS
tail -f logs/app.log

# Windows PowerShell
Get-Content logs/app.log -Tail 50 -Wait
```

### Monitor API in Real-time
```bash
# Terminal 1: Start API
uvicorn backend.api.main:app --reload

# Terminal 2: Check logs
tail -f logs/app.log

# Terminal 3: Test endpoints
curl http://localhost:8000/health
```

---

## Success Indicators

✅ API starts without errors
✅ Swagger UI is accessible
✅ Can register and login
✅ Can add a source
✅ Can list sources
✅ Database has data
✅ Logs are being written
✅ Tests are passing

---

## Useful Commands

```bash
# Reinstall dependencies
pip install -r requirements.txt --upgrade

# Check installed packages
pip list

# Create fresh environment
python -m venv venv_fresh
source venv_fresh/bin/activate
pip install -r requirements.txt

# Run with different port
uvicorn backend.api.main:app --port 8001 --reload

# Run in production mode
uvicorn backend.api.main:app --host 0.0.0.0 --port 8000

# Run with workers
uvicorn backend.api.main:app --workers 4

# Format all Python files
black .

# Check code quality
flake8 .

# Sort imports
isort .

# Run tests with coverage
pytest --cov=backend --cov-report=html

# Generate API spec
curl http://localhost:8000/openapi.json > openapi.json
```

---

## Documentation References

- **API Guide**: `IMPLEMENTATION_GUIDE.md`
- **Architecture**: `IMPROVEMENT_PLAN.md`
- **Status**: `PROJECT_STATUS.md`
- **Full README**: `README.md`
- **Swagger UI**: http://localhost:8000/api/docs (when running)

---

## Contact & Support

If you encounter issues:
1. Check the `logs/app.log` file
2. Review relevant documentation file
3. Check GitHub issues (if applicable)
4. Contact development team

---

**Created**: 2024
**Version**: v1.0
**Last Updated**: Today
