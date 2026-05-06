from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import base64
import json
import logging

from sqlalchemy.orm import Session

from backend.database.crud import CommentCRUD, PostCRUD, PostMetricCRUD, SourceCRUD
from backend.config import settings
from backend.database.models import Source, SourceType
import comment_scraper
import group_post_scraper_v2 as group_scraper
import post_scraper as timeline_scraper

logger = logging.getLogger("facebook_scraper")


@dataclass
class FacebookScrapeResult:
    source_id: int
    source_name: Optional[str]
    total_fetched: int
    created_posts: int
    updated_posts: int
    skipped_posts: int
    post_ids: List[int]


def _load_json_dict(raw_value: Optional[str]) -> Dict[str, Any]:
    if not raw_value:
        return {}
    if isinstance(raw_value, dict):
        return raw_value
    return json.loads(raw_value)


def _coerce_datetime(value: Any) -> Optional[datetime]:
    """Convert raw scraper datetime-like values into UTC naive datetime."""
    if value is None:
        return None

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    if isinstance(value, (int, float)):
        try:
            return datetime.utcfromtimestamp(float(value))
        except (TypeError, ValueError, OSError):
            return None

    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        try:
            if candidate.isdigit():
                return datetime.utcfromtimestamp(float(candidate))
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return parsed
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        except ValueError:
            return None

    return None


def _normalize_group_post(post: Dict[str, Any]) -> Dict[str, Any]:
    photos = post.get("photos") or []
    videos = post.get("videos") or []
    posted_at = _coerce_datetime(post.get("posted_at")) or datetime.utcnow()
    return {
        "facebook_post_id": str(post["post_id"]),
        "facebook_url": post.get("permalink") or "",
        "content": post.get("message") or None,
        "posted_at": posted_at,
        "likes_count": int(post.get("reaction_count") or 0),
        "shares_count": int(post.get("share_count") or 0),
        "comments_count": int(post.get("comment_count") or 0),
        "media_count": len(photos) + len(videos),
        "has_images": bool(photos),
        "has_videos": bool(videos),
    }


def _normalize_timeline_post(post: Dict[str, Any]) -> Dict[str, Any]:
    media = post.get("media") or []
    posted_at = _coerce_datetime(post.get("posted_at")) or datetime.utcnow()
    return {
        "facebook_post_id": str(post["post_id"]),
        "facebook_url": post.get("permalink") or "",
        "content": post.get("text") or None,
        "posted_at": posted_at,
        "likes_count": int(post.get("reaction_count") or 0),
        "shares_count": int(post.get("share_count") or 0),
        "comments_count": int(post.get("comment_count") or 0),
        "media_count": len(media),
        "has_images": any(item.get("type") == "photo" for item in media),
        "has_videos": any(item.get("type") == "video" for item in media),
    }


class FacebookScraperService:
    """Bridge existing Facebook scraper scripts into backend storage flows."""

    @staticmethod
    def _apply_source_auth_context(source: Source) -> None:
        user = source.user
        cookies = _load_json_dict(user.fb_cookies) if user else {}
        group_scraper.COOKIES = cookies
        group_scraper.FB_DTSG = user.fb_dtsg if user and user.fb_dtsg else ""

    @staticmethod
    def _apply_group_context(source: Source) -> None:
        group_scraper.GROUP_ID = source.facebook_id
        group_scraper.GROUP_NAME = source.source_name
        group_scraper.HEADERS["referer"] = f"https://www.facebook.com/groups/{source.facebook_id}/"
        group_scraper.WRITE_DEBUG_FILES = settings.SCRAPER_WRITE_DEBUG_FILES

    @staticmethod
    def _apply_timeline_context(source: Source) -> None:
        timeline_scraper.USER_ID = source.facebook_id
        timeline_scraper.PAGE_NAME = source.source_name
        timeline_scraper.COOKIES = group_scraper.COOKIES
        timeline_scraper.FB_DTSG = group_scraper.FB_DTSG
        timeline_scraper.WRITE_DEBUG_FILES = settings.SCRAPER_WRITE_DEBUG_FILES

    @staticmethod
    def _apply_comment_context() -> None:
        comment_scraper.FB_DTSG = group_scraper.FB_DTSG

    @staticmethod
    def _build_feedback_id(post_id: str) -> str:
        return base64.b64encode(f"feedback:{post_id}".encode()).decode()

    @classmethod
    def _sync_post_comments(cls, db: Session, source: Source, db_post) -> int:
        if not source.include_comments:
            return 0

        cls._apply_comment_context()
        feedback_id = cls._build_feedback_id(db_post.facebook_post_id)
        try:
            comments, _post_info = comment_scraper.fetch_comments(
                feedback_id,
                cookies=group_scraper.COOKIES,
            )
        except Exception as exc:
            logger.warning(
                "Failed to sync comments for post %s from source %s: %s",
                db_post.facebook_post_id,
                source.id,
                exc,
            )
            return 0
        saved_count = 0

        for comment in comments:
            comment_id = comment.get("comment_id") or comment.get("_feedback_id")
            if not comment_id:
                continue

            CommentCRUD.upsert(
                db=db,
                post_id=db_post.id,
                facebook_comment_id=str(comment_id),
                comment_text=comment.get("text") or "",
                commenter_id=comment.get("author_id"),
                commenter_name=comment.get("author_name"),
                commenter_url=comment.get("author_url"),
                likes_count=int(comment.get("reaction_count") or 0),
                reply_count=int(comment.get("reply_count") or 0),
                depth_level=0,
            )
            saved_count += 1
            parent_comment = CommentCRUD.get_by_facebook_id(db, str(comment_id))

            if not source.include_replies:
                continue

            comment["replies"] = comment_scraper.fetch_replies(comment, cookies=group_scraper.COOKIES)

            for reply in comment.get("replies", []):
                reply_id = reply.get("comment_id")
                if not reply_id:
                    continue

                CommentCRUD.upsert(
                    db=db,
                    post_id=db_post.id,
                    facebook_comment_id=str(reply_id),
                    comment_text=reply.get("text") or "",
                    commenter_id=reply.get("author_id"),
                    commenter_name=reply.get("author_name"),
                    commenter_url=reply.get("author_url"),
                    likes_count=int(reply.get("reaction_count") or 0),
                    reply_count=0,
                    parent_id=parent_comment.id if parent_comment else None,
                    depth_level=1,
                )
                saved_count += 1

        return saved_count

    @staticmethod
    def _save_metric_snapshot_if_changed(db: Session, db_post, normalized_post: Dict[str, Any]) -> bool:
        PostCRUD.update_metrics(
            db=db,
            post_id=db_post.id,
            likes=normalized_post["likes_count"],
            shares=normalized_post["shares_count"],
            comments=normalized_post["comments_count"],
        )
        PostMetricCRUD.create(
            db=db,
            post_id=db_post.id,
            likes=normalized_post["likes_count"],
            shares=normalized_post["shares_count"],
            comments=normalized_post["comments_count"],
        )
        return True

    @staticmethod
    def _ensure_valid_posted_at(raw_post: Dict[str, Any], normalized_post: Dict[str, Any]) -> None:
        if isinstance(normalized_post.get("posted_at"), datetime):
            return
        fallback = datetime.utcnow()
        normalized_post["posted_at"] = fallback
        logger.warning(
            "Invalid posted_at for post %s; fallback to utcnow()",
            raw_post.get("post_id"),
        )

    @classmethod
    def scrape_group_source(cls, db: Session, source: Source, limit: int = 20) -> FacebookScrapeResult:
        cls._apply_source_auth_context(source)
        cls._apply_group_context(source)

        raw_posts = group_scraper.fetch_posts(limit=limit)
        created_posts = 0
        updated_posts = 0
        skipped_posts = 0
        post_ids: List[int] = []
        detected_source_name = source.source_name

        for raw_post in raw_posts:
            facebook_post_id = raw_post.get("post_id")
            if not facebook_post_id:
                skipped_posts += 1
                continue

            normalized_post = _normalize_group_post(raw_post)
            cls._ensure_valid_posted_at(raw_post, normalized_post)
            db_post, created = PostCRUD.upsert_for_source(
                db=db,
                source_id=source.id,
                **normalized_post,
            )
            post_ids.append(db_post.id)

            if created:
                created_posts += 1
                PostMetricCRUD.create(
                    db=db,
                    post_id=db_post.id,
                    likes=normalized_post["likes_count"],
                    shares=normalized_post["shares_count"],
                    comments=normalized_post["comments_count"],
                )
                cls._sync_post_comments(db, source, db_post)
            else:
                updated_posts += 1
                cls._save_metric_snapshot_if_changed(db, db_post, normalized_post)
                if source.include_comments:
                    cls._sync_post_comments(db, source, db_post)

            if raw_post.get("group_name"):
                detected_source_name = raw_post["group_name"]

        SourceCRUD.update_scrape_info(
            db,
            source.id,
            last_scraped=datetime.utcnow(),
        )
        if detected_source_name and detected_source_name != source.source_name:
            SourceCRUD.update(db, source.id, source_name=detected_source_name)

        logger.info(
            "Scraped group source %s: fetched=%s created=%s updated=%s skipped=%s",
            source.id,
            len(raw_posts),
            created_posts,
            updated_posts,
            skipped_posts,
        )
        return FacebookScrapeResult(
            source_id=source.id,
            source_name=detected_source_name,
            total_fetched=len(raw_posts),
            created_posts=created_posts,
            updated_posts=updated_posts,
            skipped_posts=skipped_posts,
            post_ids=post_ids,
        )

    @classmethod
    def scrape_timeline_source(cls, db: Session, source: Source, limit: int = 20) -> FacebookScrapeResult:
        cls._apply_source_auth_context(source)
        cls._apply_timeline_context(source)

        base_folder = "page_post" if source.source_type == SourceType.PAGE else "user_post"
        raw_posts = timeline_scraper.fetch_posts(limit=limit, base_folder=base_folder)
        created_posts = 0
        updated_posts = 0
        skipped_posts = 0
        post_ids: List[int] = []
        detected_source_name = source.source_name

        for raw_post in raw_posts:
            facebook_post_id = raw_post.get("post_id")
            if not facebook_post_id:
                skipped_posts += 1
                continue

            normalized_post = _normalize_timeline_post(raw_post)
            cls._ensure_valid_posted_at(raw_post, normalized_post)
            db_post, created = PostCRUD.upsert_for_source(
                db=db,
                source_id=source.id,
                **normalized_post,
            )
            post_ids.append(db_post.id)

            if created:
                created_posts += 1
                PostMetricCRUD.create(
                    db=db,
                    post_id=db_post.id,
                    likes=normalized_post["likes_count"],
                    shares=normalized_post["shares_count"],
                    comments=normalized_post["comments_count"],
                )
                cls._sync_post_comments(db, source, db_post)
            else:
                updated_posts += 1
                cls._save_metric_snapshot_if_changed(db, db_post, normalized_post)
                if source.include_comments:
                    cls._sync_post_comments(db, source, db_post)

            if raw_post.get("page_name"):
                detected_source_name = raw_post["page_name"]

        SourceCRUD.update_scrape_info(
            db,
            source.id,
            last_scraped=datetime.utcnow(),
        )
        if detected_source_name and detected_source_name != source.source_name:
            SourceCRUD.update(db, source.id, source_name=detected_source_name)

        logger.info(
            "Scraped timeline source %s: fetched=%s created=%s updated=%s skipped=%s",
            source.id,
            len(raw_posts),
            created_posts,
            updated_posts,
            skipped_posts,
        )
        return FacebookScrapeResult(
            source_id=source.id,
            source_name=detected_source_name,
            total_fetched=len(raw_posts),
            created_posts=created_posts,
            updated_posts=updated_posts,
            skipped_posts=skipped_posts,
            post_ids=post_ids,
        )

    @classmethod
    def refresh_recent_post_metrics(cls, db: Session, source: Source, limit: int = 20) -> Dict[str, int]:
        cls._apply_source_auth_context(source)
        fetched_posts: List[Dict[str, Any]]
        normalize_post: Any

        if source.source_type == SourceType.GROUP:
            cls._apply_group_context(source)
            fetched_posts = group_scraper.fetch_posts(limit=limit)
            normalize_post = _normalize_group_post
        elif source.source_type in {SourceType.PAGE, SourceType.USER}:
            cls._apply_timeline_context(source)
            base_folder = "page_post" if source.source_type == SourceType.PAGE else "user_post"
            fetched_posts = timeline_scraper.fetch_posts(limit=limit, base_folder=base_folder)
            normalize_post = _normalize_timeline_post
        else:
            raise NotImplementedError(f"Facebook source type '{source.source_type.value}' is not implemented yet")

        updated_posts = 0
        skipped_posts = 0
        for raw_post in fetched_posts:
            facebook_post_id = raw_post.get("post_id")
            if not facebook_post_id:
                skipped_posts += 1
                continue

            db_post = PostCRUD.get_by_source_and_facebook_post_id(db, source.id, str(facebook_post_id))
            if not db_post:
                skipped_posts += 1
                continue

            normalized_post = normalize_post(raw_post)
            if cls._save_metric_snapshot_if_changed(db, db_post, normalized_post):
                updated_posts += 1
            if source.include_comments:
                cls._sync_post_comments(db, source, db_post)

        return {
            "fetched": len(fetched_posts),
            "updated": updated_posts,
            "skipped": skipped_posts,
        }

    @classmethod
    def scrape_source(cls, db: Session, source_id: int, limit: int = 20) -> FacebookScrapeResult:
        source = SourceCRUD.get_by_id(db, source_id)
        if not source:
            raise ValueError(f"Source {source_id} not found")
        if source.source_type == SourceType.GROUP:
            return cls.scrape_group_source(db, source, limit=limit)
        if source.source_type in {SourceType.PAGE, SourceType.USER}:
            return cls.scrape_timeline_source(db, source, limit=limit)
        raise NotImplementedError(f"Facebook source type '{source.source_type.value}' is not implemented yet")
