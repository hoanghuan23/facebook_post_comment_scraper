"""One-off migration helpers for legacy scraper tables."""

from sqlalchemy import inspect, text

from backend.database.db import engine


def migrate_post_metric_scheduling_columns() -> None:
    """Add per-post metric scheduling state and backfill existing tracked posts."""
    inspector = inspect(engine)
    if not inspector.has_table("posts"):
        return

    existing_columns = {column["name"] for column in inspector.get_columns("posts")}
    column_definitions = {
        "metric_tier": "VARCHAR(20) NOT NULL DEFAULT 'bootstrap'",
        "next_metric_update": "DATETIME",
        "last_engagement_velocity": "FLOAT",
        "cold_check_count": "INTEGER NOT NULL DEFAULT 0",
        "metric_scan_miss_count": "INTEGER NOT NULL DEFAULT 0",
    }
    with engine.begin() as conn:
        for column_name, definition in column_definitions.items():
            if column_name not in existing_columns:
                conn.execute(text(f"ALTER TABLE posts ADD COLUMN {column_name} {definition}"))

        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_post_metric_due ON posts (is_tracked, next_metric_update)"))
        if engine.dialect.name == "postgresql":
            conn.execute(
                text(
                    """
                    UPDATE posts
                    SET tracking_until = COALESCE(tracking_until, posted_at + INTERVAL '24 hours'),
                        metric_tier = COALESCE(metric_tier, 'bootstrap'),
                        next_metric_update = COALESCE(next_metric_update, CURRENT_TIMESTAMP)
                    WHERE is_tracked = TRUE
                      AND is_deleted = FALSE
                      AND posted_at >= CURRENT_TIMESTAMP - INTERVAL '24 hours'
                    """
                )
            )
            conn.execute(
                text(
                    """
                    UPDATE posts
                    SET is_tracked = FALSE, metric_tier = 'expired', next_metric_update = NULL
                    WHERE is_tracked = TRUE
                      AND is_deleted = FALSE
                      AND posted_at < CURRENT_TIMESTAMP - INTERVAL '24 hours'
                    """
                )
            )
        else:
            conn.execute(
                text(
                    """
                    UPDATE posts
                    SET tracking_until = COALESCE(tracking_until, datetime(posted_at, '+24 hours')),
                        metric_tier = COALESCE(metric_tier, 'bootstrap'),
                        next_metric_update = COALESCE(next_metric_update, CURRENT_TIMESTAMP)
                    WHERE is_tracked = 1
                      AND is_deleted = 0
                      AND posted_at >= datetime('now', '-24 hours')
                    """
                )
            )
            conn.execute(
                text(
                    """
                    UPDATE posts
                    SET is_tracked = 0, metric_tier = 'expired', next_metric_update = NULL
                    WHERE is_tracked = 1
                      AND is_deleted = 0
                      AND posted_at < datetime('now', '-24 hours')
                    """
                )
            )


def _reset_sqlite_sequence(conn, table_name: str) -> None:
    try:
        conn.execute(text("DELETE FROM sqlite_sequence WHERE name = :table_name"), {"table_name": table_name})
    except Exception:
        pass


def _reset_postgres_sequence(conn, table_name: str) -> None:
    conn.execute(
        text(
            f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table_name}), 1), true)"
        )
    )


def migrate_legacy_scraper_tables(drop_legacy_tables: bool = False) -> None:
    """Copy data from legacy scraper tables into the new pipeline tables.

    Run this once on an existing database before the old tables are dropped.
    """

    inspector = inspect(engine)
    has_old_jobs = inspector.has_table("scrape_jobs")
    has_old_logs = inspector.has_table("scraper_logs")
    has_new_jobs = inspector.has_table("pipeline_jobs")
    has_new_logs = inspector.has_table("pipeline_logs")

    with engine.begin() as conn:
        if has_old_jobs and has_new_jobs:
            new_job_count = conn.execute(text("SELECT COUNT(*) FROM pipeline_jobs")).scalar() or 0
            if new_job_count == 0:
                conn.execute(
                    text(
                        """
                        INSERT INTO pipeline_jobs (
                            id, job_type, source_id, session_id, status,
                            posts_found, posts_new, items_total, items_updated, items_failed,
                            error_message, started_at, finished_at
                        )
                        SELECT
                            id, 'scraper_job', source_id, session_id, status,
                            COALESCE(posts_found, 0), COALESCE(posts_new, 0),
                            COALESCE(posts_found, 0), COALESCE(posts_new, 0), 0,
                            error_message, started_at, finished_at
                        FROM scrape_jobs
                        """
                    )
                )
                if engine.dialect.name == "sqlite":
                    _reset_sqlite_sequence(conn, "pipeline_jobs")
                elif engine.dialect.name == "postgresql":
                    _reset_postgres_sequence(conn, "pipeline_jobs")

        if has_old_logs and has_new_logs:
            new_log_count = conn.execute(text("SELECT COUNT(*) FROM pipeline_logs")).scalar() or 0
            if new_log_count == 0:
                conn.execute(
                    text(
                        """
                        INSERT INTO pipeline_logs (
                            id, job_id, source_id, log_level, message,
                            error_type, error_details, created_at
                        )
                        SELECT
                            id, NULL, source_id, log_level, message,
                            error_type, error_details, created_at
                        FROM scraper_logs
                        """
                    )
                )
                if engine.dialect.name == "sqlite":
                    _reset_sqlite_sequence(conn, "pipeline_logs")
                elif engine.dialect.name == "postgresql":
                    _reset_postgres_sequence(conn, "pipeline_logs")

        if drop_legacy_tables:
            if has_old_logs:
                conn.execute(text("DROP TABLE IF EXISTS scraper_logs"))
            if has_old_jobs:
                conn.execute(text("DROP TABLE IF EXISTS scrape_jobs"))


if __name__ == "__main__":
    migrate_post_metric_scheduling_columns()
    migrate_legacy_scraper_tables(drop_legacy_tables=False)
    print("Legacy scraper table migration completed.")
