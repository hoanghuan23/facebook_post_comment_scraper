import argparse
from datetime import datetime, timedelta

from backend.database.db import SessionLocal
from backend.database.models import Post
from backend.services.post_metric_schedule_service import (
    EXPIRED,
    TRACKING_HOURS,
    recover_recent_expired_metric_misses,
)


def count_recoverable_posts(db, now: datetime) -> int:
    cutoff_time = now - timedelta(hours=TRACKING_HOURS)
    return db.query(Post).filter(
        Post.posted_at >= cutoff_time,
        Post.metric_tier == EXPIRED,
        Post.metric_scan_miss_count >= 3,
        Post.is_deleted.is_(False),
        Post.is_tracked.is_(False),
    ).count()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recover recent posts that were expired by repeated metric scan misses."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the recovery. Without this flag, only prints the recoverable count.",
    )
    args = parser.parse_args()

    now = datetime.utcnow()
    db = SessionLocal()
    try:
        recoverable = count_recoverable_posts(db, now)
        if not args.apply:
            print(f"recoverable_posts={recoverable} dry_run=true")
            return

        recovered = recover_recent_expired_metric_misses(db, now=now)
        print(f"recoverable_posts={recoverable} recovered_posts={recovered} dry_run=false")
    finally:
        db.close()


if __name__ == "__main__":
    main()
