import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, delete, insert, select, text

from backend.database.models import Base


DEFAULT_SQLITE_URL = "sqlite:///./data/facebook_scraper.db"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def normalize_database_url(database_url: str) -> str:
    if not database_url.startswith("sqlite:///"):
        return database_url

    raw_path = database_url.replace("sqlite:///", "", 1)
    if raw_path == ":memory:" or raw_path.startswith("file:"):
        return database_url

    sqlite_path = Path(raw_path)
    if not sqlite_path.is_absolute():
        sqlite_path = (PROJECT_ROOT / sqlite_path).resolve()

    return f"sqlite:///{sqlite_path.as_posix()}"


def _engine(url: str):
    return create_engine(normalize_database_url(url))


def _default_target_url() -> str | None:
    explicit_target = os.getenv("TARGET_DATABASE_URL")
    if explicit_target:
        return explicit_target

    database_url = os.getenv("DATABASE_URL")
    if database_url and database_url.startswith(("postgresql://", "postgresql+")):
        return database_url

    postgres_user = os.getenv("POSTGRES_USER")
    postgres_password = os.getenv("POSTGRES_PASSWORD")
    postgres_db = os.getenv("POSTGRES_DB")
    if postgres_user and postgres_password and postgres_db:
        postgres_port = os.getenv("POSTGRES_PORT", "5432")
        return f"postgresql://{postgres_user}:{postgres_password}@localhost:{postgres_port}/{postgres_db}"

    return database_url


def _reset_postgres_sequence(conn, table_name: str, id_column: str = "id") -> None:
    sequence_name = conn.execute(
        text("SELECT pg_get_serial_sequence(:table_name, :id_column)"),
        {"table_name": table_name, "id_column": id_column},
    ).scalar()
    if not sequence_name:
        return

    max_id = conn.execute(text(f"SELECT COALESCE(MAX({id_column}), 0) FROM {table_name}")).scalar() or 0
    if max_id == 0:
        conn.execute(text("SELECT setval(:sequence_name, 1, false)"), {"sequence_name": sequence_name})
    else:
        conn.execute(text("SELECT setval(:sequence_name, :max_id, true)"), {
            "sequence_name": sequence_name,
            "max_id": max_id,
        })


def migrate(source_url: str, target_url: str, truncate: bool = False) -> None:
    source_engine = _engine(source_url)
    target_engine = _engine(target_url)

    if source_engine.dialect.name != "sqlite":
        raise ValueError("Source database must be SQLite.")
    if target_engine.dialect.name != "postgresql":
        raise ValueError("Target database must be PostgreSQL.")

    Base.metadata.create_all(bind=target_engine)

    with source_engine.connect() as source_conn, target_engine.begin() as target_conn:
        if truncate:
            for table in reversed(Base.metadata.sorted_tables):
                target_conn.execute(delete(table))

        for table in Base.metadata.sorted_tables:
            rows = [dict(row) for row in source_conn.execute(select(table)).mappings()]
            if not rows:
                print(f"{table.name}: 0 rows")
                continue

            target_conn.execute(insert(table), rows)
            print(f"{table.name}: copied {len(rows)} rows")

        for table in Base.metadata.sorted_tables:
            if "id" in table.columns:
                _reset_postgres_sequence(target_conn, table.name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy project data from SQLite to PostgreSQL.")
    parser.add_argument(
        "--source",
        default=os.getenv("SOURCE_DATABASE_URL", DEFAULT_SQLITE_URL),
        help="SQLite source URL. Default: sqlite:///./data/facebook_scraper.db",
    )
    parser.add_argument(
        "--target",
        default=_default_target_url(),
        help=(
            "PostgreSQL target URL. Can also be set with TARGET_DATABASE_URL, "
            "DATABASE_URL, or POSTGRES_* env vars."
        ),
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="Delete target rows before copying. Use only on a fresh or disposable PostgreSQL DB.",
    )
    args = parser.parse_args()

    if not args.target:
        parser.error("--target is required unless TARGET_DATABASE_URL or DATABASE_URL is set")

    migrate(args.source, args.target, truncate=args.truncate)
    print("SQLite to PostgreSQL migration completed.")


if __name__ == "__main__":
    main()
