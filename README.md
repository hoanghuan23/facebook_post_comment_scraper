# Facebook Post & Comment Scraper (v2.0)

Tool thu thập **bài viết / bình luận / chỉ số tương tác** từ Facebook (Page / Group / User), hỗ trợ 2 cách sử dụng:

- **Chạy Desktop UI (PyQt6)**: phù hợp dùng cá nhân, xuất dữ liệu ra thư mục `page_post/`, `user_post/`, `group_post/`, `simple_post/` dưới dạng **JSON + ảnh**.
- **Chạy Backend API (FastAPI)**: phù hợp triển khai dịch vụ, dữ liệu được quản lý trong **DB (SQLite/PostgreSQL)** + scheduler chạy nền, có API để truy vấn/analytics.

---

## Tính năng chính

- **Scrape bài viết** từ Page/User timeline, hoặc Group feed (tùy module).
- **Scrape bình luận & reply** cho từng bài viết (tùy chọn có/không).
- **Tải ảnh** đi kèm bài viết (lưu theo từng `post_id`).
- **Proxy & retry**: có cơ chế đổi proxy (module `proxy_utils.py`) và retry khi bị lỗi/mạng chập chờn.
- **Backend API + DB**: quản lý Source/Post/Comment, lưu lịch sử metrics theo thời gian, hỗ trợ analytics cơ bản.
- **Scheduler (APScheduler)**: chạy tác vụ định kỳ (nếu bật).

---

## Kiến trúc & “DB lưu trữ”

### 1) Lưu trữ dạng file (khi chạy UI/script)

Các script/UI hiện tại lưu theo cấu trúc thư mục:

- `page_post/<Tên Page>/<post_id>/<post_id>.json` (+ ảnh: `<post_id>.jpg`, `<post_id>_2.jpg`, …)
- `user_post/<Tên User>/<post_id>/<post_id>.json` (+ ảnh)
- `group_post/<Tên Group>/<post_id>/<post_id>.json`
- `simple_post/<post_id>/<post_id>.json` (+ ảnh nếu lấy được)

 file JSON, thường có:
- **Thông tin post**: `post_id`, `feedback_id`, `permalink`, `text`, `reaction_count`, `comment_count`, `media`…
- **Danh sách comments**: nằm trong key `comments` (mỗi comment có thể có `replies`).

### 2) Lưu trữ DB (khi chạy Backend API)

Backend dùng **SQLAlchemy** và hỗ trợ:
- **SQLite** (mặc định): file DB đặt tại `./data/facebook_scraper.db`
- **PostgreSQL** (production): cấu hình qua `DATABASE_URL`

Các bảng chính (xem `backend/database/models.py`):
- **users**: tài khoản, lưu `fb_cookies`, `fb_dtsg` (dạng text, dự kiến mã hóa ở tầng nghiệp vụ)
- **sources**: nguồn theo dõi (PAGE/GROUP/USER) + trạng thái quyền truy cập
- **posts**: bài viết + metrics hiện tại
- **post_metrics**: lịch sử metrics theo thời gian
- **comments**: bình luận (hỗ trợ reply qua `parent_comment_id`, `depth_level`)
- **analytics_cache**: cache thống kê
- **scraper_logs**, **task_logs**: log hệ thống

---

## Cách triển khai (Deploy/Run)

### Yêu cầu

- Python **3.9+**
- (Tùy chọn) Docker + Docker Compose nếu chạy bằng container

### Cài đặt nhanh (local)

```bash
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # macOS/Linux

pip install -r requirements.txt
```

### A) Chạy Desktop UI (khuyến nghị nếu bạn muốn “xuất file”)

```bash
python facebook_ui.py
```

Trong UI có phần cấu hình:
- **Cookies + `fb_dtsg`**: có thể lấy bằng SeleniumBase (mở Chrome login) hoặc dán “Copy as cURL” từ DevTools.
- **Proxy**: tự chọn proxy phù hợp theo việc có/không có cookie session.

### B) Chạy CLI menu (xuất file JSON/JPG)

```bash
python main.py
```

Chọn:
- `User Post`: lấy N Post từ trang cá nhân user và comment
- `Page Posts`: lấy N post từ page/user và comment
- `Group Posts`: lấy N post từ group và comment

### C) Chạy Backend API + DB (FastAPI)

#### Tạo `.env` (tối thiểu)

Repo hiện chưa có `.env.example`, bạn có thể tạo `.env` với các biến phổ biến sau:

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

#### Chạy trên Windows

```bash
run.bat
```

#### Chạy trên macOS/Linux

```bash
bash run.sh
```

Hoặc chạy trực tiếp:

```bash
python -c "from backend.database.db import init_db; init_db()"
uvicorn backend.api.main:app --reload --host 0.0.0.0 --port 8000
```

- API root: `http://localhost:8000`
- Swagger: `http://localhost:8000/api/docs`

---

## Triển khai bằng Docker Compose (production mẫu)

```bash
docker-compose up -d --build
docker-compose logs -f api
```

Mặc định `docker-compose.yml` sẽ chạy:
- `postgres` (DB)
- `api` (FastAPI)
- `redis` (tùy chọn)
- `nginx` (tùy chọn)

---

## API (tóm tắt)

Xem đầy đủ tại Swagger: `http://localhost:8000/api/docs`

Các nhóm endpoint chính:
- `/api/auth`: đăng ký/đăng nhập
- `/api/sources`: quản lý nguồn theo dõi
- `/api/posts`: truy vấn bài viết / metrics / comments
- `/api/analytics`: thống kê/trending
- `/api/admin`: thông tin hệ thống

---

## Development & Testing

```bash
pytest
black .
flake8 .
isort .
```

---


