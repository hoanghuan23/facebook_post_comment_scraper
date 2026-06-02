import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse


DB_PATH = os.getenv("UI_DATABASE_PATH", os.path.join("data", "facebook_scraper.db"))
_DB_LOCK = threading.Lock()


def _utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")


def _parse_datetime(value, fallback=None):
    if not value:
        return fallback or _utc_now()

    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed.isoformat(sep=" ")
    except Exception:
        return fallback or _utc_now()


def _to_int(value, default=0):
    try:
        return int(value or default)
    except Exception:
        return default


def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _ensure_minimal_schema(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username VARCHAR(50) NOT NULL UNIQUE,
            email VARCHAR(100) NOT NULL UNIQUE,
            password_hash VARCHAR(255) NOT NULL,
            is_active BOOLEAN DEFAULT 1,
            is_admin BOOLEAN DEFAULT 0,
            created_at DATETIME,
            last_login DATETIME
        );

        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            source_type VARCHAR(5) NOT NULL,
            facebook_id VARCHAR(50) NOT NULL,
            facebook_url VARCHAR(255) NOT NULL,
            source_name VARCHAR(255),
            description TEXT,
            member_count INTEGER,
            is_active BOOLEAN DEFAULT 1,
            include_comments BOOLEAN DEFAULT 1,
            max_days_old INTEGER DEFAULT 30,
            permission_status VARCHAR(11),
            permission_checked_at DATETIME,
            is_accessible BOOLEAN DEFAULT 1,
            created_at DATETIME,
            last_scraped DATETIME,
            next_scrape DATETIME,
            UNIQUE(user_id, facebook_id)
        );

        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL REFERENCES sources(id),
            facebook_post_id VARCHAR(100) NOT NULL UNIQUE,
            facebook_url VARCHAR(500) NOT NULL,
            content TEXT,
            media_count INTEGER DEFAULT 0,
            has_images BOOLEAN DEFAULT 0,
            has_videos BOOLEAN DEFAULT 0,
            posted_at DATETIME NOT NULL,
            created_at DATETIME,
            is_tracked BOOLEAN DEFAULT 1,
            tracking_until DATETIME,
            is_deleted BOOLEAN DEFAULT 0,
            last_metric_update DATETIME,
            metric_tier VARCHAR(20) NOT NULL DEFAULT 'bootstrap',
            next_metric_update DATETIME,
            last_engagement_velocity FLOAT,
            cold_check_count INTEGER NOT NULL DEFAULT 0,
            metric_scan_miss_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS post_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL REFERENCES posts(id),
            job_id INTEGER,
            likes_count INTEGER DEFAULT 0,
            shares_count INTEGER DEFAULT 0,
            comments_count INTEGER DEFAULT 0,
            recorded_at DATETIME
        );

        CREATE INDEX IF NOT EXISTS idx_post_metrics_job_time
            ON post_metrics (job_id, recorded_at);

        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL REFERENCES posts(id),
            parent_id INTEGER REFERENCES comments(id),
            facebook_comment_id VARCHAR(100) NOT NULL UNIQUE,
            commenter_id VARCHAR(50),
            commenter_name VARCHAR(255),
            commenter_url VARCHAR(500),
            comment_text TEXT,
            likes_count INTEGER DEFAULT 0,
            reply_count INTEGER DEFAULT 0,
            created_at DATETIME,
            last_updated DATETIME,
            depth_level INTEGER DEFAULT 0
        );
        """
    )
    post_columns = {row[1] for row in conn.execute("PRAGMA table_info(posts)").fetchall()}
    additions = {
        "metric_tier": "VARCHAR(20) NOT NULL DEFAULT 'bootstrap'",
        "next_metric_update": "DATETIME",
        "last_engagement_velocity": "FLOAT",
        "cold_check_count": "INTEGER NOT NULL DEFAULT 0",
        "metric_scan_miss_count": "INTEGER NOT NULL DEFAULT 0",
    }
    for column_name, column_sql in additions.items():
        if column_name not in post_columns:
            conn.execute(f"ALTER TABLE posts ADD COLUMN {column_name} {column_sql}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_post_metric_due ON posts (is_tracked, next_metric_update)")
    conn.execute(
        """
        UPDATE posts
        SET tracking_until = COALESCE(tracking_until, datetime(posted_at, '+24 hours')),
            metric_tier = COALESCE(metric_tier, 'bootstrap'),
            next_metric_update = COALESCE(next_metric_update, datetime('now'))
        WHERE is_tracked = 1 AND is_deleted = 0 AND posted_at >= datetime('now', '-24 hours')
        """
    )
    conn.execute(
        """
        UPDATE posts
        SET is_tracked = 0, metric_tier = 'expired', next_metric_update = NULL
        WHERE is_tracked = 1 AND is_deleted = 0 AND posted_at < datetime('now', '-24 hours')
        """
    )


def _default_user_id(conn):
    row = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
    if row:
        return row[0]

    now = _utc_now()
    cur = conn.execute(
        """
        INSERT INTO users (username, email, password_hash, is_active, is_admin, created_at)
        VALUES (?, ?, ?, 1, 1, ?)
        """,
        ("desktop_ui", "desktop_ui@example.local", "desktop-ui-placeholder", now),
    )
    return cur.lastrowid


def _source_identity(post_type, post_data):
    source_type = "group" if post_type == "group_post" else "page" if post_type == "page_post" else "user"
    name = post_data.get("group_name") or post_data.get("page_name") or post_data.get("author_name") or "Unknown"
    url = (
        post_data.get("group_link") if source_type == "group" else ""
    ) or post_data.get("source_url") or post_data.get("permalink") or ""
    explicit_id = post_data.get("group_id") or post_data.get("source_id") or post_data.get("facebook_id")

    if explicit_id:
        facebook_id = str(explicit_id)
    elif source_type == "group" and post_data.get("permalink"):
        parsed = urlparse(post_data["permalink"])
        parts = [part for part in parsed.path.split("/") if part]
        facebook_id = parts[1] if len(parts) >= 2 and parts[0] == "groups" else name
    else:
        facebook_id = name

    if not url and facebook_id:
        if source_type == "group":
            url = f"https://www.facebook.com/groups/{facebook_id}/"
        else:
            url = f"https://www.facebook.com/{facebook_id}"

    return source_type, facebook_id[:50], url[:255], name


def _upsert_source(conn, post_type, post_data, include_comments=None):
    user_id = _default_user_id(conn)
    source_type, facebook_id, facebook_url, source_name = _source_identity(post_type, post_data)
    now = _utc_now()
    include_comments_value = 1 if include_comments else 0

    row = conn.execute(
        "SELECT id FROM sources WHERE user_id = ? AND facebook_id = ?",
        (user_id, facebook_id),
    ).fetchone()

    if row:
        source_id = row[0]
        conn.execute(
            """
            UPDATE sources
            SET source_type = ?, facebook_url = ?, source_name = ?, is_active = 1,
                include_comments = ?, is_accessible = 1, last_scraped = ?
            WHERE id = ?
            """,
            (source_type, facebook_url, source_name, include_comments_value, now, source_id),
        )
        return source_id

    cur = conn.execute(
        """
        INSERT INTO sources (
            user_id, source_type, facebook_id, facebook_url, source_name,
            is_active, include_comments, permission_status,
            is_accessible, created_at, last_scraped
        )
        VALUES (?, ?, ?, ?, ?, 1, ?, 'granted', 1, ?, ?)
        """,
        (
            user_id,
            source_type,
            facebook_id,
            facebook_url,
            source_name,
            include_comments_value,
            now,
            now,
        ),
    )
    return cur.lastrowid


def _upsert_post(conn, source_id, post_data):
    post_id = str(post_data["post_id"])
    permalink = post_data.get("permalink") or ""
    content = post_data.get("message") or post_data.get("text") or ""
    photos = post_data.get("photos") or []
    videos = post_data.get("videos") or []
    media_count = len(photos) + len(videos)
    posted_at = _parse_datetime(post_data.get("posted_at"))
    now = _utc_now()
    tracking_until = (
        datetime.fromisoformat(posted_at) + timedelta(hours=24)
    ).isoformat(sep=" ")
    next_metric_update = (
        datetime.fromisoformat(now) + timedelta(minutes=15)
    ).isoformat(sep=" ")

    row = conn.execute(
        "SELECT id FROM posts WHERE facebook_post_id = ?",
        (post_id,),
    ).fetchone()

    if row:
        db_post_id = row[0]
        conn.execute(
            """
            UPDATE posts
            SET source_id = ?, facebook_url = ?, content = ?, media_count = ?,
                has_images = ?, has_videos = ?, posted_at = ?, is_tracked = 1,
                is_deleted = 0, last_metric_update = ?,
                tracking_until = COALESCE(tracking_until, ?),
                metric_tier = COALESCE(metric_tier, 'bootstrap'),
                next_metric_update = COALESCE(next_metric_update, ?)
            WHERE id = ?
            """,
            (
                source_id,
                permalink,
                content,
                media_count,
                bool(photos),
                bool(videos),
                posted_at,
                now,
                tracking_until,
                next_metric_update,
                db_post_id,
            ),
        )
        return db_post_id, True

    cur = conn.execute(
        """
        INSERT INTO posts (
            source_id, facebook_post_id, facebook_url, content, media_count,
            has_images, has_videos, posted_at, created_at, is_tracked, is_deleted,
            last_metric_update, tracking_until, metric_tier, next_metric_update,
            cold_check_count, metric_scan_miss_count
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?, 'bootstrap', ?, 0, 0)
        """,
        (
            source_id,
            post_id,
            permalink,
            content,
            media_count,
            bool(photos),
            bool(videos),
            posted_at,
            now,
            now,
            tracking_until,
            next_metric_update,
        ),
    )
    return cur.lastrowid, True


def _insert_metric_if_needed(conn, db_post_id, post_data, should_insert):
    likes = _to_int(post_data.get("reaction_count"))
    shares = _to_int(post_data.get("share_count"))
    comments = _to_int(post_data.get("comment_count"))
    conn.execute(
        """
        INSERT INTO post_metrics (post_id, likes_count, shares_count, comments_count, recorded_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (db_post_id, likes, shares, comments, _utc_now()),
    )


def _comment_key(post_id, depth, index_path, text):
    raw = f"{post_id}|{depth}|{index_path}|{text or ''}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _upsert_comments(conn, db_post_id, facebook_post_id, comments_data):
    saved = 0
    now = _utc_now()
    comment_row_ids = {}

    for idx, comment in enumerate(comments_data or [], 1):
        if not isinstance(comment, dict):
            continue

        text = comment.get("text") or ""
        comment_id = (
            comment.get("comment_id")
            or comment.get("facebook_comment_id")
            or _comment_key(facebook_post_id, 0, str(idx), text)
        )
        replies = comment.get("replies") or []

        cur = conn.execute(
            """
            INSERT INTO comments (
                post_id, parent_id, facebook_comment_id, commenter_id, commenter_name,
                commenter_url, comment_text, likes_count, reply_count,
                created_at, last_updated, depth_level
            )
            VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(facebook_comment_id) DO UPDATE SET
                post_id = excluded.post_id,
                comment_text = excluded.comment_text,
                likes_count = excluded.likes_count,
                reply_count = excluded.reply_count,
                last_updated = excluded.last_updated,
                parent_id = NULL,
                depth_level = 0
            RETURNING id
            """,
            (
                db_post_id,
                str(comment_id),
                comment.get("author_id"),
                comment.get("author_name"),
                comment.get("author_url"),
                text,
                _to_int(comment.get("reaction_count")),
                len(replies),
                now,
                now,
            ),
        )
        comment_row_ids[str(comment_id)] = cur.fetchone()[0]
        saved += 1

        for reply_idx, reply in enumerate(replies, 1):
            if not isinstance(reply, dict):
                continue

            reply_text = reply.get("text") or ""
            reply_id = (
                reply.get("comment_id")
                or reply.get("facebook_comment_id")
                or _comment_key(facebook_post_id, 1, f"{idx}.{reply_idx}", reply_text)
            )

            conn.execute(
                """
                INSERT INTO comments (
                    post_id, parent_id, facebook_comment_id, commenter_id, commenter_name,
                    commenter_url, comment_text, likes_count, reply_count,
                    created_at, last_updated, depth_level
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 1)
                ON CONFLICT(facebook_comment_id) DO UPDATE SET
                    post_id = excluded.post_id,
                    comment_text = excluded.comment_text,
                    likes_count = excluded.likes_count,
                    last_updated = excluded.last_updated,
                    parent_id = excluded.parent_id,
                    depth_level = 1
                """,
                (
                    db_post_id,
                    comment_row_ids.get(str(comment_id)),
                    str(reply_id),
                    reply.get("author_id"),
                    reply.get("author_name"),
                    reply.get("author_url"),
                    reply_text,
                    _to_int(reply.get("reaction_count")),
                    now,
                    now,
                ),
            )
            saved += 1

    return saved


def save_scraped_post_to_db(post_type, post_data, comments_data, include_comments=None):
    """Persist one scraped post JSON payload to the backend SQLite database."""
    if post_type not in {"group_post", "page_post", "user_post"}:
        return None
    if not isinstance(post_data, dict) or not post_data.get("post_id"):
        return None

    with _DB_LOCK:
        conn = _connect()
        try:
            _ensure_minimal_schema(conn)
            if include_comments is None:
                include_comments = bool(comments_data)
            source_id = _upsert_source(conn, post_type, post_data, include_comments)
            db_post_id, insert_metric = _upsert_post(conn, source_id, post_data)
            _insert_metric_if_needed(conn, db_post_id, post_data, insert_metric)
            comments_saved = _upsert_comments(conn, db_post_id, str(post_data["post_id"]), comments_data)
            conn.commit()
            return {
                "db_path": DB_PATH,
                "source_id": source_id,
                "post_id": db_post_id,
                "comments_saved": comments_saved,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
