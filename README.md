# Facebook Post Comment Scraper

Du an Python dung de thu thap bai viet, binh luan va chi so tuong tac Facebook. Repo hien co 2 cach chay chinh:

- Script/Desktop UI: scrape va xuat du lieu ra file.
- Backend API: FastAPI + SQLAlchemy, luu source/post/comment/metric vao database va co scheduler cap nhat dinh ky.

## Cau truc chinh

```text
backend/
  api/          FastAPI routes: auth, sources, posts, analytics, admin
  database/     SQLAlchemy models, schemas, crud, init DB
  scraper/      Facebook scraping service
  scheduler/    APScheduler jobs
  services/     Logic schedule, metric, trending
  tests/        Unit tests
docker/         Docker Compose cho PostgreSQL
schema/         SQL schema tham khao
data/           SQLite DB, telemetry, du lieu local
```

Mot so script o root:

- `facebook_ui.py`: giao dien PyQt de scrape.
- `main.py`: menu CLI scrape page/user/group.
- `comment_scraper.py`, `post_scraper.py`, `group_post_scraper_v2.py`: cac module scrape cu.
- `fb_direct_fetch.py`: lay metric truc tiep.
- `db_persistence.py`: ghi du lieu scrape vao DB.

## Yeu cau

- Python 3.9+
- SQLite mac dinh, hoac PostgreSQL neu cau hinh `DATABASE_URL`
- Cookie/session Facebook hop le neu scrape noi dung can dang nhap

## Cai dat local

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Windows:

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Cau hinh

Tao file `.env` o root project. Cau hinh toi thieu:

```env
ENV=development
DEBUG=True
API_HOST=0.0.0.0
API_PORT=8000
DATABASE_URL=sqlite:///./data/facebook_scraper.db
SECRET_KEY=change-me
ENCRYPTION_KEY=change-me
SCHEDULER_ENABLED=True
```

Neu dung PostgreSQL:

```env
DATABASE_URL=postgresql://scraper:scraper_password@localhost:5432/facebook_scraper
```

Luu y: `run.sh` va `run.bat` hien co buoc copy tu `.env.example`, nhung repo chua co file `.env.example`. Neu chua tao `.env`, hay tao thu cong theo mau tren.

## Chay Backend API

Khoi tao DB:

```bash
python -c "from backend.database.db import init_db; init_db()"
```

Chay API:

```bash
uvicorn backend.api.main:app --reload --host 0.0.0.0 --port 8000
```

Dia chi:

- API root: `http://localhost:8000`
- Swagger: `http://localhost:8000/api/docs`
- Health: `http://localhost:8000/health`

Nhom endpoint chinh:

- `/api/auth`
- `/api/sources`
- `/api/posts`
- `/api/analytics`
- `/api/admin`

## Chay script scrape

Desktop UI:

```bash
python facebook_ui.py
```

CLI menu:

```bash
python main.py
```

Du lieu file local co the duoc luu theo cac thu muc nhu `page_post/`, `user_post/`, `group_post/`, `simple_post/` tuy script dang chay.

## PostgreSQL bang Docker

File compose hien tai chi chay PostgreSQL:

```bash
docker compose -f docker/docker-compose.yml up -d
```

Sau do cau hinh `.env` de API ket noi vao PostgreSQL.

## Database

Backend dung SQLAlchemy. Cac bang quan trong nam trong `backend/database/models.py`:

- `users`, `facebook_sessions`
- `sources`
- `posts`
- `post_metrics`
- `comments`
- `analytics_cache`
- `pipeline_jobs`, `pipeline_logs`, `task_logs`

SQLite mac dinh nam tai:

```text
data/facebook_scraper.db
```

## Test

```bash
pytest backend/tests
```

Co the chay tung file test khi can:

```bash
pytest backend/tests/test_facebook_service.py
```

## Ghi chu

- Thu muc `data/telemetry/` dung de ghi log request/debug local.
- Scheduler co the tat bang `SCHEDULER_ENABLED=False`.
- Scrape Facebook phu thuoc cookie, proxy, quyen truy cap source va thay doi giao dien/API cua Facebook.
