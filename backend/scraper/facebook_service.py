from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import base64
import json
import logging
import re

from sqlalchemy.orm import Session

from backend.database.crud import CommentCRUD, FacebookSessionCRUD, PostCRUD, PostMetricCRUD, SourceCRUD
from backend.config import settings
from backend.database.models import Source, SourceType
import comment_scraper
import group_post_scraper_v2 as group_scraper
import post_scraper as timeline_scraper
from proxy_utils import select_proxy

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
    if isinstance(raw_value, str):
        stripped = raw_value.strip()
        if stripped and not (stripped.startswith("{") or stripped.startswith("[")):
            cookies: Dict[str, Any] = {}
            for part in stripped.split(";"):
                item = part.strip()
                if not item or "=" not in item:
                    continue
                key, value = item.split("=", 1)
                key = key.strip()
                value = value.strip()
                if key:
                    cookies[key] = value
            return cookies
    try:
        parsed = json.loads(raw_value)
    except (TypeError, json.JSONDecodeError):
        return {}
    if isinstance(parsed, dict):
        return parsed
    return {}


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


def _coerce_count(value: Any) -> int:
    """Convert Facebook compact count values like 2K or 1.2M to integers."""
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)

    if isinstance(value, dict):
        for key in ("count", "value", "text", "total_count"):
            if key in value:
                return _coerce_count(value.get(key))
        return 0

    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return 0

        match = re.search(r"(\d[\d.,]*)\s*([KMB])?", candidate, re.IGNORECASE)
        if not match:
            return 0

        number_text = match.group(1)
        suffix = (match.group(2) or "").upper()
        if suffix and "," in number_text and "." not in number_text:
            number_text = number_text.replace(",", ".")
        else:
            number_text = number_text.replace(",", "")

        try:
            number = float(number_text)
        except ValueError:
            return 0

        multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(suffix, 1)
        return int(number * multiplier)

    return 0


def _normalize_group_post(post: Dict[str, Any]) -> Dict[str, Any]:
    photos = post.get("photos") or []
    videos = post.get("videos") or []
    posted_at = _coerce_datetime(post.get("posted_at")) or datetime.utcnow()
    return {
        "facebook_post_id": str(post["post_id"]),
        "facebook_url": post.get("permalink") or "",
        "content": post.get("message") or None,
        "posted_at": posted_at,
        "likes_count": _coerce_count(post.get("reaction_count")),
        "shares_count": _coerce_count(post.get("share_count")),
        "comments_count": _coerce_count(post.get("comment_count")),
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
        "likes_count": _coerce_count(post.get("reaction_count")),
        "shares_count": _coerce_count(post.get("share_count")),
        "comments_count": _coerce_count(post.get("comment_count")),
        "media_count": len(media),
        "has_images": any(item.get("type") == "photo" for item in media),
        "has_videos": any(item.get("type") == "video" for item in media),
    }


def _post_cutoff_skip_reason(
    raw_post: Dict[str, Any],
    normalized_post: Dict[str, Any],
    *,
    last_24_hours_only: bool,
    min_posted_at: Optional[datetime],
) -> Optional[str]:
    raw_posted_at = _coerce_datetime(raw_post.get("posted_at"))
    posted_at = raw_posted_at or normalized_post.get("posted_at")

    if min_posted_at is not None and posted_at <= min_posted_at:
        return "latest_cutoff"

    if last_24_hours_only:
        if raw_posted_at is None:
            return "missing_posted_at_24h"
        recent_cutoff = datetime.utcnow() - timedelta(hours=24)
        if raw_posted_at < recent_cutoff:
            return "24h_cutoff"

    return None


class FacebookScraperService:
    """Bridge existing Facebook scraper scripts into backend storage flows."""

    @staticmethod
    def _apply_source_auth_context(db: Session, source: Source) -> None:
        session = FacebookSessionCRUD.get_active_by_user_id(db, source.user_id)
        cookies = _load_json_dict(session.fb_cookies) if session else {}
        user_agent = session.fb_user_agent if session and session.fb_user_agent else None
        proxies = select_proxy(bool(cookies))

        group_scraper.COOKIES = cookies
        group_scraper.FB_DTSG = session.fb_dtsg if session and session.fb_dtsg else ""
        group_scraper.PROXIES = proxies
        timeline_scraper.COOKIES = cookies
        timeline_scraper.FB_DTSG = group_scraper.FB_DTSG
        timeline_scraper.PROXIES = proxies
        comment_scraper.FB_DTSG = group_scraper.FB_DTSG
        comment_scraper.PROXIES = proxies

        if user_agent:
            group_scraper.HEADERS["user-agent"] = user_agent
            timeline_scraper.BASE_HEADERS["user-agent"] = user_agent
            comment_scraper.BASE_HEADERS["user-agent"] = user_agent

        actual_proxy_mode = "none"
        if proxies:
            actual_proxy_mode = "static" if cookies else "rotating"

        logger.info(
            "Applied Facebook auth context for source_id=%s user_id=%s has_cookies=%s c_user=%s has_fb_dtsg=%s has_user_agent=%s proxy_mode=%s",
            source.id,
            source.user_id,
            bool(cookies),
            bool(cookies.get("c_user")),
            bool(group_scraper.FB_DTSG),
            bool(user_agent),
            actual_proxy_mode,
        )

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
        min_posted_at: Optional[datetime] = None,
        consecutive_old_limit: Optional[int] = None,
        group_id: Optional[str] = None,
        group_name: Optional[str] = None,
        download_media: bool = True,
        skip_existing_posts: bool = True,
        target_post_ids: Optional[List[str]] = None,
        stop_when_targets_found: bool = False,
    ) -> List[Dict[str, Any]]:
        """Call group scraper with new args, fallback to legacy signature for compatibility."""
        try:
            return group_scraper.fetch_posts(
                limit=limit,
                last_24_hours_only=last_24_hours_only,
                min_posted_at=min_posted_at,
                consecutive_old_limit=consecutive_old_limit,
                group_id=group_id,
                group_name=group_name,
                cookies=group_scraper.COOKIES,
                fb_dtsg=group_scraper.FB_DTSG,
                download_media=download_media,
                skip_existing_posts=skip_existing_posts,
                target_post_ids=target_post_ids,
                stop_when_targets_found=stop_when_targets_found,
            )
        except TypeError:
            # Legacy tests/mocks may patch fetch_posts(limit=...) only.
            if last_24_hours_only:
                try:
                    return group_scraper.fetch_posts(limit=None, last_24_hours_only=True)
                except TypeError:
                    return group_scraper.fetch_posts(limit=limit)
            return group_scraper.fetch_posts(limit=limit)

    @staticmethod
    def _fetch_timeline_posts_with_compat(
        *,
        limit: Optional[int],
        base_folder: str,
        last_24_hours_only: bool,
        min_posted_at: Optional[datetime] = None,
        consecutive_old_limit: Optional[int] = None,
        download_media: bool = True,
        skip_existing_posts: bool = True,
    ) -> List[Dict[str, Any]]:
        try:
            if last_24_hours_only:
                return timeline_scraper.fetch_posts(
                    limit=None,
                    base_folder=base_folder,
                    last_24_hours_only=True,
                    min_posted_at=min_posted_at,
                    consecutive_old_limit=consecutive_old_limit,
                    download_media=download_media,
                    skip_existing_posts=skip_existing_posts,
                )
            return timeline_scraper.fetch_posts(
                limit=limit,
                base_folder=base_folder,
                download_media=download_media,
                skip_existing_posts=skip_existing_posts,
                min_posted_at=min_posted_at,
                consecutive_old_limit=consecutive_old_limit,
            )
        except TypeError:
            if last_24_hours_only:
                try:
                    return timeline_scraper.fetch_posts(
                        limit=None,
                        base_folder=base_folder,
                        last_24_hours_only=True,
                    )
                except TypeError:
                    return timeline_scraper.fetch_posts(limit=limit, base_folder=base_folder)
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
                likes_count=_coerce_count(comment.get("reaction_count")),
                reply_count=_coerce_count(comment.get("reply_count")),
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
        consecutive_old_limit: Optional[int] = None,
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
            min_posted_at=min_posted_at,
            consecutive_old_limit=consecutive_old_limit,
            group_id=source.facebook_id,
            group_name=source.source_name,
            download_media=settings.SCRAPER_DOWNLOAD_MEDIA,
            # Persistence is DB-backed; a JSON file may have been written by metric refresh.
            skip_existing_posts=False,
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
            skip_reason = _post_cutoff_skip_reason(
                raw_post,
                normalized_post,
                last_24_hours_only=last_24_hours_only,
                min_posted_at=min_posted_at,
            )
            if skip_reason:
                skipped_by_cutoff += 1
                logger.info(
                    "Skip group post by cutoff: source_id=%s post_id=%s reason=%s posted_at=%s min_posted_at=%s last_24_hours_only=%s",
                    source.id,
                    facebook_post_id,
                    skip_reason,
                    normalized_post["posted_at"],
                    min_posted_at,
                    last_24_hours_only,
                )
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
        consecutive_old_limit: Optional[int] = None,
    ) -> FacebookScrapeResult:
        cls._apply_source_auth_context(db, source)
        cls._apply_timeline_context(source)

        base_folder = "page_post" if source.source_type == SourceType.PAGE else "user_post"
        raw_posts = cls._fetch_timeline_posts_with_compat(
            limit=limit,
            base_folder=base_folder,
            last_24_hours_only=last_24_hours_only,
            min_posted_at=min_posted_at,
            consecutive_old_limit=consecutive_old_limit,
            download_media=settings.SCRAPER_DOWNLOAD_MEDIA,
            skip_existing_posts=False,
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
            skip_reason = _post_cutoff_skip_reason(
                raw_post,
                normalized_post,
                last_24_hours_only=last_24_hours_only,
                min_posted_at=min_posted_at,
            )
            if skip_reason:
                skipped_by_cutoff += 1
                logger.info(
                    "Skip timeline post by cutoff: source_id=%s post_id=%s reason=%s posted_at=%s min_posted_at=%s last_24_hours_only=%s",
                    source.id,
                    facebook_post_id,
                    skip_reason,
                    normalized_post["posted_at"],
                    min_posted_at,
                    last_24_hours_only,
                )
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
    def refresh_target_post_metrics(
        cls,
        db: Session,
        source: Source,
        target_post_ids: List[str],
        max_pages: int = 20,
        stop_when_all_found: bool = True,
        last_24_hours_only: bool = True,
        download_media: bool = False,
    ) -> Dict[str, Any]:
        cls._apply_source_auth_context(db, source)
        target_set = {str(post_id) for post_id in target_post_ids if post_id}
        if not target_set:
            return {
                "fetched": 0,
                "updated": 0,
                "skipped": 0,
                "deleted": 0,
                "matched_target_count": 0,
                "target_posts_count": 0,
                "pages_scanned": 0,
                "stop_reason": "empty_targets",
                "updated_post_refs": [],
                "deleted_post_refs": [],
            }

        post_per_page = 3
        max_scan_posts = max_pages * post_per_page if max_pages and max_pages > 0 else None

        if source.source_type == SourceType.GROUP:
            cls._apply_group_context(source)
            fetched_posts = cls._fetch_group_posts_with_compat(
                limit=max_scan_posts,
                last_24_hours_only=last_24_hours_only,
                group_id=source.facebook_id,
                group_name=source.source_name,
                download_media=download_media,
                skip_existing_posts=False,
                target_post_ids=list(target_set),
                stop_when_targets_found=stop_when_all_found,
            )
            normalize_post = _normalize_group_post
        elif source.source_type in {SourceType.PAGE, SourceType.USER}:
            cls._apply_timeline_context(source)
            base_folder = "page_post" if source.source_type == SourceType.PAGE else "user_post"
            fetched_posts = cls._fetch_timeline_posts_with_compat(
                limit=max_scan_posts,
                base_folder=base_folder,
                last_24_hours_only=last_24_hours_only,
                download_media=download_media,
                skip_existing_posts=False,
            )
            normalize_post = _normalize_timeline_post
        else:
            raise NotImplementedError(f"Facebook source type '{source.source_type.value}' is not implemented yet")

        updated_posts = 0
        skipped_posts = 0
        deleted_posts = 0
        updated_post_refs: List[str] = []
        deleted_post_refs: List[str] = []
        matched_targets = set()
        scanned_count = 0
        stop_reason = "completed"

        for raw_post in fetched_posts:
            if max_scan_posts is not None and scanned_count >= max_scan_posts:
                stop_reason = "max_pages_reached"
                break
            scanned_count += 1

            facebook_post_id = raw_post.get("post_id")
            if not facebook_post_id:
                skipped_posts += 1
                continue

            facebook_post_id = str(facebook_post_id)
            if facebook_post_id not in target_set:
                skipped_posts += 1
                continue

            db_post = PostCRUD.get_by_source_and_facebook_post_id(db, source.id, facebook_post_id)
            if not db_post:
                skipped_posts += 1
                continue

            matched_targets.add(facebook_post_id)
            normalized_post = normalize_post(raw_post)
            if cls._save_metric_snapshot_if_changed(db, db_post, normalized_post):
                updated_posts += 1
                updated_post_refs.append(str(db_post.facebook_post_id or db_post.id))
            if source.include_comments:
                cls._sync_post_comments(db, source, db_post)

            if stop_when_all_found and len(matched_targets) >= len(target_set):
                stop_reason = "all_targets_found"
                break
        else:
            if stop_reason == "completed":
                if (
                    max_scan_posts is not None
                    and scanned_count >= max_scan_posts
                    and len(matched_targets) < len(target_set)
                ):
                    stop_reason = "max_pages_reached"
                else:
                    stop_reason = "source_exhausted"

        can_mark_missing_deleted = (
            scanned_count > 0
            and stop_reason == "source_exhausted"
        )
        if can_mark_missing_deleted:
            recent_cutoff = datetime.utcnow() - timedelta(hours=24)
            for missing_post_id in sorted(target_set - matched_targets):
                db_post = PostCRUD.get_by_source_and_facebook_post_id(
                    db, source.id, missing_post_id
                )
                if not db_post:
                    continue
                if db_post.posted_at < recent_cutoff:
                    if db_post.is_tracked:
                        db_post.is_tracked = False
                        db.commit()
                    continue
                if PostCRUD.delete(db, db_post.id):
                    deleted_posts += 1
                    deleted_post_refs.append(str(db_post.facebook_post_id or db_post.id))

        pages_scanned = (scanned_count + post_per_page - 1) // post_per_page if scanned_count > 0 else 0
        return {
            "fetched": scanned_count,
            "updated": updated_posts,
            "skipped": skipped_posts,
            "deleted": deleted_posts,
            "matched_target_count": len(matched_targets),
            "target_posts_count": len(target_set),
            "pages_scanned": pages_scanned,
            "stop_reason": stop_reason,
            "updated_post_refs": updated_post_refs,
            "deleted_post_refs": deleted_post_refs,
        }

    @classmethod
    def scrape_source(
        cls,
        db: Session,
        source_id: int,
        limit: int = 20,
        last_24_hours_only: bool = False,
        min_posted_at: Optional[datetime] = None,
        consecutive_old_limit: Optional[int] = None,
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
                consecutive_old_limit=consecutive_old_limit,
            )
        if source.source_type in {SourceType.PAGE, SourceType.USER}:
            return cls.scrape_timeline_source(
                db,
                source,
                limit=limit,
                last_24_hours_only=last_24_hours_only,
                min_posted_at=min_posted_at,
                consecutive_old_limit=consecutive_old_limit,
            )
        raise NotImplementedError(f"Facebook source type '{source.source_type.value}' is not implemented yet")
