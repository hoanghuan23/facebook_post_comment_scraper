from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import base64
import json
import logging

from sqlalchemy.orm import Session

from backend.database.crud import CommentCRUD, FacebookSessionCRUD, PostCRUD, PostMetricCRUD, SourceCRUD
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
    filtered_by_cutoff: int
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
    def _apply_source_auth_context(db: Session, source: Source) -> None:
        session = FacebookSessionCRUD.get_active_by_user_id(db, source.user_id)
        cookies = _load_json_dict(session.fb_cookies) if session else {}
        group_scraper.COOKIES = cookies
        group_scraper.FB_DTSG = session.fb_dtsg if session and session.fb_dtsg else ""

    @staticmethod
    def _apply_group_context(source: Source) -> None:
        group_scraper.GROUP_ID = source.facebook_id
        group_scraper.GROUP_NAME = source.source_name
        group_scraper.HEADERS["referer"] = f"https://www.facebook.com/groups/{source.facebook_id}/"
        group_scraper.WRITE_DEBUG_FILES = settings.SCRAPER_WRITE_DEBUG_FILES

    @staticmethod
    def _resolve_group_id(source: Source) -> str:
        """Resolve group slug URL to numeric group id when possible."""
        facebook_id = str(source.facebook_id or "").strip()
        if not facebook_id:
            return facebook_id
        if facebook_id.isdigit():
            return facebook_id

        try:
            from main import extract_group_id_from_url

            resolved_id = extract_group_id_from_url(
                source.facebook_url,
                cookies=group_scraper.COOKIES or None,
            )
            if resolved_id:
                return str(resolved_id).strip()
        except Exception as exc:
            logger.warning(
                "Failed to resolve group id from URL for source %s (%s): %s",
                source.id,
                source.facebook_url,
                exc,
            )
        return facebook_id

    @staticmethod
    def _fetch_group_posts_with_compat(
        *,
        limit: Optional[int],
        last_24_hours_only: bool,
        group_id: str,
        group_name: Optional[str],
        download_media: bool,
    ) -> List[Dict[str, Any]]:
        """Call group scraper with new args, fallback to legacy signature for compatibility."""
        try:
            return group_scraper.fetch_posts(
                limit=limit,
                last_24_hours_only=last_24_hours_only,
                group_id=group_id,
                group_name=group_name,
                cookies=group_scraper.COOKIES,
                fb_dtsg=group_scraper.FB_DTSG,
                download_media=download_media,
            )
        except TypeError:
            # Legacy tests/mocks may patch fetch_posts(limit=...) only.
            if last_24_hours_only:
                return group_scraper.fetch_posts(limit=None, last_24_hours_only=True)
            return group_scraper.fetch_posts(limit=limit)

    @staticmethod
    def _fetch_timeline_posts_with_compat(
        *,
        limit: Optional[int],
        base_folder: str,
        last_24_hours_only: bool,
        download_media: bool,
    ) -> List[Dict[str, Any]]:
        try:
            if last_24_hours_only:
                return timeline_scraper.fetch_posts(
                    limit=None,
                    base_folder=base_folder,
                    last_24_hours_only=True,
                    download_media=download_media,
                )
            return timeline_scraper.fetch_posts(
                limit=limit,
                base_folder=base_folder,
                download_media=download_media,
            )
        except TypeError:
            if last_24_hours_only:
                return timeline_scraper.fetch_posts(
                    limit=None,
                    base_folder=base_folder,
                    last_24_hours_only=True,
                )
            return timeline_scraper.fetch_posts(limit=limit, base_folder=base_folder)

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

        # Replies are intentionally disabled at runtime to reduce scrape scope.
        # Keep `include_replies` in source settings for backward compatibility only.
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
    def scrape_group_source(
        cls,
        db: Session,
        source: Source,
        limit: int = 20,
        last_24_hours_only: bool = False,
        min_posted_at: Optional[datetime] = None,
    ) -> FacebookScrapeResult:
        cls._apply_source_auth_context(db, source)
        resolved_group_id = cls._resolve_group_id(source)
        if resolved_group_id and resolved_group_id != source.facebook_id:
            SourceCRUD.update(db, source.id, facebook_id=resolved_group_id)
            source.facebook_id = resolved_group_id
        cls._apply_group_context(source)

        raw_posts = cls._fetch_group_posts_with_compat(
            limit=None if last_24_hours_only else limit,
            last_24_hours_only=last_24_hours_only,
            group_id=source.facebook_id,
            group_name=source.source_name,
            download_media=settings.SCRAPER_DOWNLOAD_MEDIA,
        )
        created_posts = 0
        updated_posts = 0
        skipped_posts = 0
        skipped_by_cutoff = 0
        post_ids: List[int] = []
        detected_source_name = source.source_name

        for raw_post in raw_posts:
            facebook_post_id = raw_post.get("post_id")
            if not facebook_post_id:
                skipped_posts += 1
                continue

            normalized_post = _normalize_group_post(raw_post)
            cls._ensure_valid_posted_at(raw_post, normalized_post)
            if min_posted_at is not None and normalized_post["posted_at"] <= min_posted_at:
                skipped_by_cutoff += 1
                continue
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
            "Hoàn tất scrape source group: source_id=%s fetched=%s created=%s updated=%s skipped=%s filtered_by_cutoff=%s",
            source.id,
            len(raw_posts),
            created_posts,
            updated_posts,
            skipped_posts,
            skipped_by_cutoff,
        )
        return FacebookScrapeResult(
            source_id=source.id,
            source_name=detected_source_name,
            total_fetched=len(raw_posts),
            created_posts=created_posts,
            updated_posts=updated_posts,
            skipped_posts=skipped_posts,
            filtered_by_cutoff=skipped_by_cutoff,
            post_ids=post_ids,
        )

    @classmethod
    def scrape_timeline_source(
        cls,
        db: Session,
        source: Source,
        limit: int = 20,
        last_24_hours_only: bool = False,
        min_posted_at: Optional[datetime] = None,
    ) -> FacebookScrapeResult:
        cls._apply_source_auth_context(db, source)
        cls._apply_timeline_context(source)

        base_folder = "page_post" if source.source_type == SourceType.PAGE else "user_post"
        raw_posts = cls._fetch_timeline_posts_with_compat(
            limit=limit,
            base_folder=base_folder,
            last_24_hours_only=last_24_hours_only,
            download_media=settings.SCRAPER_DOWNLOAD_MEDIA,
        )
        created_posts = 0
        updated_posts = 0
        skipped_posts = 0
        skipped_by_cutoff = 0
        post_ids: List[int] = []
        detected_source_name = source.source_name

        for raw_post in raw_posts:
            facebook_post_id = raw_post.get("post_id")
            if not facebook_post_id:
                skipped_posts += 1
                continue

            normalized_post = _normalize_timeline_post(raw_post)
            cls._ensure_valid_posted_at(raw_post, normalized_post)
            if min_posted_at is not None and normalized_post["posted_at"] <= min_posted_at:
                skipped_by_cutoff += 1
                continue
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
            "Hoàn tất scrape source timeline: source_id=%s fetched=%s created=%s updated=%s skipped=%s filtered_by_cutoff=%s",
            source.id,
            len(raw_posts),
            created_posts,
            updated_posts,
            skipped_posts,
            skipped_by_cutoff,
        )
        return FacebookScrapeResult(
            source_id=source.id,
            source_name=detected_source_name,
            total_fetched=len(raw_posts),
            created_posts=created_posts,
            updated_posts=updated_posts,
            skipped_posts=skipped_posts,
            filtered_by_cutoff=skipped_by_cutoff,
            post_ids=post_ids,
        )

    @classmethod
    def refresh_recent_post_metrics(cls, db: Session, source: Source, limit: int = 20) -> Dict[str, Any]:
        cls._apply_source_auth_context(db, source)
        fetched_posts: List[Dict[str, Any]]
        normalize_post: Any

        if source.source_type == SourceType.GROUP:
            cls._apply_group_context(source)
            fetched_posts = cls._fetch_group_posts_with_compat(
                limit=limit,
                last_24_hours_only=False,
                group_id=source.facebook_id,
                group_name=source.source_name,
                download_media=settings.SCRAPER_DOWNLOAD_MEDIA,
            )
            normalize_post = _normalize_group_post
        elif source.source_type in {SourceType.PAGE, SourceType.USER}:
            cls._apply_timeline_context(source)
            base_folder = "page_post" if source.source_type == SourceType.PAGE else "user_post"
            fetched_posts = cls._fetch_timeline_posts_with_compat(
                limit=limit,
                base_folder=base_folder,
                last_24_hours_only=False,
                download_media=settings.SCRAPER_DOWNLOAD_MEDIA,
            )
            normalize_post = _normalize_timeline_post
        else:
            raise NotImplementedError(f"Facebook source type '{source.source_type.value}' is not implemented yet")

        updated_posts = 0
        skipped_posts = 0
        updated_post_refs: List[str] = []
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
                updated_post_refs.append(str(db_post.facebook_post_id or db_post.id))
            if source.include_comments:
                cls._sync_post_comments(db, source, db_post)

        return {
            "fetched": len(fetched_posts),
            "updated": updated_posts,
            "skipped": skipped_posts,
            "updated_post_refs": updated_post_refs,
        }

    @classmethod
    def scrape_source(
        cls,
        db: Session,
        source_id: int,
        limit: int = 20,
        last_24_hours_only: bool = False,
        min_posted_at: Optional[datetime] = None,
    ) -> FacebookScrapeResult:
        source = SourceCRUD.get_by_id(db, source_id)
        if not source:
            raise ValueError(f"Source {source_id} not found")
        if source.source_type == SourceType.GROUP:
            return cls.scrape_group_source(
                db,
                source,
                limit=limit,
                last_24_hours_only=last_24_hours_only,
                min_posted_at=min_posted_at,
            )
        if source.source_type in {SourceType.PAGE, SourceType.USER}:
            return cls.scrape_timeline_source(
                db,
                source,
                limit=limit,
                last_24_hours_only=last_24_hours_only,
                min_posted_at=min_posted_at,
            )
        raise NotImplementedError(f"Facebook source type '{source.source_type.value}' is not implemented yet")
