# PostgreSQL setup

Use this when the project needs a remote database that another party can access.

## 1. Start PostgreSQL

With Docker Compose:

```bash
docker compose -f docker/docker-compose.yml up -d postgres
```

The default local connection URL is:

```env
DATABASE_URL=postgresql://scraper:change_this_password@localhost:5432/facebook_scraper
```

Change `POSTGRES_PASSWORD` in `.env` before exposing this database outside your machine.

## 2. Configure the app

Copy `.env.example` to `.env`, then set:

```env
DATABASE_URL=postgresql://scraper:change_this_password@localhost:5432/facebook_scraper
API_DATABASE_URL=postgresql://scraper:change_this_password@postgres:5432/facebook_scraper
```

Create or update tables:

```bash
python -c "from backend.database.db import init_db; init_db()"
```

Run the API:

```bash
uvicorn backend.api.main:app --reload --host 0.0.0.0 --port 8000
```

## 3. Copy SQLite data to PostgreSQL

If the current SQLite file is `data/facebook_scraper.db`, run:

```bash
python -m backend.tools.migrate_sqlite_to_postgres --truncate
```

The tool uses:

- source: `sqlite:///./data/facebook_scraper.db`
- target: `DATABASE_URL` from `.env`

You can also pass both URLs explicitly:

```bash
python -m backend.tools.migrate_sqlite_to_postgres ^
  --source sqlite:///./data/facebook_scraper.db ^
  --target postgresql://scraper:change_this_password@localhost:5432/facebook_scraper ^
  --truncate
```

Use `--truncate` only when the target PostgreSQL database is fresh or disposable.

## 4. Create read-only third-party access

Create a separate user for the third party:

```sql
CREATE USER third_party_user WITH PASSWORD 'strong_password_here';
GRANT CONNECT ON DATABASE facebook_scraper TO third_party_user;
GRANT USAGE ON SCHEMA public TO third_party_user;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO third_party_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
GRANT SELECT ON TABLES TO third_party_user;
```

Share only their connection URL:

```env
DATABASE_URL=postgresql://third_party_user:strong_password_here@your-server-ip-or-domain:5432/facebook_scraper
```

Do not share the app user or an admin user. Only expose port `5432` when the server firewall or security group limits access to trusted client IPs.
