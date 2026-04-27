# filepath: utils/facebook_extractor.py
"""
Common Facebook data extraction functions shared across scrapers.
Used by post_scraper.py (page/user) and group_post_scraper_v2.py (group).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _to_iso_utc_from_unix(ts: Any) -> Optional[str]:
    """Convert unix seconds to ISO-8601 UTC string."""
    try:
        if ts is None:
            return None
        ts_int = int(ts)
        return datetime.fromtimestamp(ts_int, tz=timezone.utc).isoformat()
    except Exception:
        return None


def _looks_like_unix_seconds(value: Any) -> bool:
    try:
        if value is None:
            return False
        iv = int(value)
        # Rough sanity range: 2004-01-01 .. 2100-01-01 (unix seconds)
        return 1072915200 <= iv <= 4102444800
    except Exception:
        return False


def _find_first_unix_ts(obj: Any, keys: set[str], depth: int = 0, max_depth: int = 6) -> Optional[int]:
    """
    Best-effort recursive scan to find a unix-seconds timestamp.
    We only match on specific keys to avoid accidentally grabbing unrelated integers.
    """
    if depth > max_depth:
        return None

    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys and _looks_like_unix_seconds(v):
                return int(v)
        for v in obj.values():
            found = _find_first_unix_ts(v, keys=keys, depth=depth + 1, max_depth=max_depth)
            if found is not None:
                return found

    if isinstance(obj, list):
        for item in obj:
            found = _find_first_unix_ts(item, keys=keys, depth=depth + 1, max_depth=max_depth)
            if found is not None:
                return found

    return None


def extract_posted_at(node: Dict[str, Any]) -> Optional[str]:
    """
    Best-effort extract post publish time.

    Returns ISO-8601 string in UTC (e.g. "2026-04-27T10:20:30+00:00") or None.
    """
    if not isinstance(node, dict):
        return None

    # Common direct fields
    for key in ("creation_time", "created_time", "publish_time", "timestamp"):
        iso = _to_iso_utc_from_unix(node.get(key))
        if iso:
            return iso

    # Nested story containers sometimes carry creation_time
    try:
        story = (node.get("comet_sections", {}) or {}).get("content", {}).get("story", {}) or {}
        iso = _to_iso_utc_from_unix(story.get("creation_time") or story.get("created_time"))
        if iso:
            return iso
    except Exception:
        pass

    # Some responses keep metadata under context_layout
    try:
        story = (node.get("comet_sections", {}) or {}).get("context_layout", {}).get("story", {}) or {}
        comet_sections = story.get("comet_sections") or {}
        metadata = comet_sections.get("metadata")

        # metadata can be a dict or a list of dicts depending on query/schema
        if isinstance(metadata, dict):
            meta_story = (metadata.get("story") or {})
            iso = _to_iso_utc_from_unix(meta_story.get("creation_time") or meta_story.get("created_time"))
            if iso:
                return iso
        elif isinstance(metadata, list):
            for item in metadata:
                if not isinstance(item, dict):
                    continue
                meta_story = (item.get("story") or {})
                iso = _to_iso_utc_from_unix(meta_story.get("creation_time") or meta_story.get("created_time"))
                if iso:
                    return iso
    except Exception:
        pass

    # Last resort: recursive scan for a timestamp-like field
    ts = _find_first_unix_ts(node, keys={"creation_time", "created_time", "publish_time", "timestamp"})
    return _to_iso_utc_from_unix(ts)

    # return None  (handled above)


def extract_author(node: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """
    Best-effort extract author info from Story node.

    Returns: {author_name, author_url, source_type}
    - source_type is one of: "user" | "page" | "group" | None
    """
    author_name: Optional[str] = None
    author_url: Optional[str] = None
    source_type: Optional[str] = None

    if not isinstance(node, dict):
        return {"author_name": None, "author_url": None, "source_type": None}

    actor = None
    try:
        story = (node.get("comet_sections", {}) or {}).get("content", {}).get("story", {}) or {}
        actors = story.get("actors") or []
        if isinstance(actors, list) and actors:
            actor = actors[0]
    except Exception:
        actor = None

    if isinstance(actor, dict):
        author_name = actor.get("name") or actor.get("short_name")
        author_url = actor.get("url") or actor.get("profile_url") or actor.get("wwwURL")

        # Heuristic: if actor carries page fields, it's a Page even if typename is misleading.
        # Suggested rule: category/page_type => Page
        if any(k in actor and actor.get(k) for k in ("category", "page_type", "category_type")):
            source_type = "page"

        typename = actor.get("__typename")
        if isinstance(typename, str):
            t = typename.lower()
            if "page" in t:
                source_type = "page"
            elif "group" in t:
                source_type = "group"
            elif "user" in t or "profile" in t:
                source_type = "user"

        # Anonymous / alias posting: don't fabricate a profile URL; return sentinel.
        if isinstance(author_name, str):
            n = author_name.strip().lower()
            if n in {"anonymous", "ẩn danh", "facebook user"} or "ẩn danh" in n:
                return {"author_name": "Anonymous", "author_url": "Anonymous", "source_type": source_type or "user"}

        # Fallback: construct profile URL by id when possible
        if not author_url:
            actor_id = actor.get("id")
            if isinstance(actor_id, str) and actor_id.isdigit():
                author_url = f"https://www.facebook.com/profile.php?id={actor_id}"

    # Additional fallback from feedback owning_profile (often a Page)
    try:
        owning_profile = (node.get("feedback", {}) or {}).get("owning_profile", {}) or {}
        if not author_name:
            author_name = owning_profile.get("name") or owning_profile.get("short_name")
        if not author_url:
            author_url = owning_profile.get("url") or owning_profile.get("profile_url") or owning_profile.get("wwwURL")

        if not source_type and any(k in owning_profile and owning_profile.get(k) for k in ("category", "page_type", "category_type")):
            source_type = "page"

        if not source_type and owning_profile.get("__typename"):
            t = str(owning_profile.get("__typename")).lower()
            if "page" in t:
                source_type = "page"
            elif "group" in t:
                source_type = "group"
            elif "user" in t or "profile" in t:
                source_type = "user"
    except Exception:
        pass

    # Final anonymous normalization (covers cases where owning_profile filled the name)
    if isinstance(author_name, str):
        n = author_name.strip().lower()
        if n in {"anonymous", "ẩn danh", "facebook user"} or "ẩn danh" in n:
            author_name = "Anonymous"
            author_url = "Anonymous"

    return {"author_name": author_name, "author_url": author_url, "source_type": source_type}


def make_scraped_at() -> str:
    """Return current time as ISO-8601 UTC string."""
    return datetime.now(timezone.utc).isoformat()


def extract_comment_count(node):
    """Extract comment count from post node"""
    try:
        # Path 1: feedback.comment_rendering_instance.comments.total_count
        comment_count = node.get("feedback", {}).get("comment_rendering_instance", {}).get("comments", {}).get("total_count")
        if comment_count is not None:
            return comment_count
        
        # Path 2: comet_sections.feedback.story.story_ufi_container.story.feedback_context.feedback_target_with_context.comment_rendering_instance.comments.total_count
        comet_sections = node.get("comet_sections", {})
        feedback_section = comet_sections.get("feedback", {})
        story = feedback_section.get("story", {})
        story_ufi_container = story.get("story_ufi_container", {})
        ufi_story = story_ufi_container.get("story", {})
        feedback_context = ufi_story.get("feedback_context", {})
        feedback_target = feedback_context.get("feedback_target_with_context", {})
        comment_count = feedback_target.get("comment_rendering_instance", {}).get("comments", {}).get("total_count")
        if comment_count is not None:
            return comment_count
        
        # Path 3: comet_sections.feedback.story.story_ufi_container.story.feedback_context.feedback_target_with_context.comet_ufi_summary_and_actions_renderer.feedback.comment_rendering_instance.comments.total_count
        comet_ufi = feedback_target.get("comet_ufi_summary_and_actions_renderer", {}).get("feedback", {})
        comment_count = comet_ufi.get("comment_rendering_instance", {}).get("comments", {}).get("total_count")
        if comment_count is not None:
            return comment_count
        
        # Path 4: comet_sections.feedback.story.feedback_context.feedback_target_with_context.comment_rendering_instance.comments.total_count (old structure)
        comet_sections = node.get("comet_sections", {})
        feedback_section = comet_sections.get("feedback", {})
        story = feedback_section.get("story", {})
        feedback_context = story.get("feedback_context", {})
        feedback_target = feedback_context.get("feedback_target_with_context", {})
        comment_count = feedback_target.get("comment_rendering_instance", {}).get("comments", {}).get("total_count")
        if comment_count is not None:
            return comment_count
        
        # Path 5: feedback.comments_count_summary_renderer.feedback.comment_rendering_instance.comments.total_count
        comments_renderer = node.get("feedback", {}).get("comments_count_summary_renderer", {}).get("feedback", {})
        comment_count = comments_renderer.get("comment_rendering_instance", {}).get("comments", {}).get("total_count")
        if comment_count is not None:
            return comment_count
            
        return 0
    except Exception:
        return 0


def extract_reaction_count(node):
    """Extract reaction count from post node"""
    try:
        # Direct path seen in live debug output
        comet_sections = node.get("comet_sections", {})
        feedback_section = comet_sections.get("feedback", {})
        story = feedback_section.get("story", {})
        story_ufi_container = story.get("story_ufi_container", {})
        ufi_story = story_ufi_container.get("story", {})
        feedback_context = ufi_story.get("feedback_context", {})
        feedback_target = feedback_context.get("feedback_target_with_context", {})
        comet_ufi = feedback_target.get("comet_ufi_summary_and_actions_renderer", {})
        
        def get_reaction_count_from_feedback(feedback):
            if not isinstance(feedback, dict):
                return None

            # New structure: reaction_count.count.count
            reaction_count = feedback.get("reaction_count", {})
            if isinstance(reaction_count, dict):
                value = reaction_count.get("count")
                while isinstance(value, dict):
                    value = value.get("count")
                if value is not None:
                    return value

            # Fallback old structure: reactors.count_reduced
            reactors = feedback.get("reactors", {})
            if isinstance(reactors, dict):
                value = reactors.get("count_reduced")
                if value is not None:
                    return value

            return None

        if comet_ufi:
            ufi_feedback = comet_ufi.get("feedback", {})
            value = get_reaction_count_from_feedback(ufi_feedback)
            if value is not None:
                return value

        # Alternate structure on some page responses
        comet_ufi = story.get("comet_ufi_summary_and_actions_renderer", {})
        if comet_ufi:
            ufi_feedback = comet_ufi.get("feedback", {})
            value = get_reaction_count_from_feedback(ufi_feedback)
            if value is not None:
                return value

        # Target-level and node-level fallback
        value = get_reaction_count_from_feedback(feedback_target)
        if value is not None:
            return value

        value = get_reaction_count_from_feedback(node.get("feedback", {}))
        if value is not None:
            return value

        # Path 1: comet_sections.feedback.story.story_ufi_container.story.feedback_context.feedback_target_with_context.comet_ufi_summary_and_actions_renderer.feedback.reactors.count_reduced
        # Try comet_ufi_summary_and_actions_renderer first (main path)
        if comet_ufi:
            ufi_feedback = comet_ufi.get("feedback", {})
            reactors = ufi_feedback.get("reactors", {})
            if reactors:
                count = reactors.get("count_reduced")
                if count is not None:
                    return count
        
        # Path 2: feedback_target_with_context.reactors.count_reduced (direct)
        reactors = feedback_target.get("reactors", {})
        if reactors:
            count = reactors.get("count_reduced")
            if count is not None:
                return count
        
        # Path 3: comet_sections.feedback.story.feedback_context.feedback_target_with_context.reactors.count_reduced (old structure)
        comet_sections = node.get("comet_sections", {})
        feedback_section = comet_sections.get("feedback", {})
        story = feedback_section.get("story", {})
        feedback_context = story.get("feedback_context", {})
        feedback_target = feedback_context.get("feedback_target_with_context", {})
        reactors = feedback_target.get("reactors", {})
        if reactors:
            count = reactors.get("count_reduced")
            if count is not None:
                return count
        
        # Path 4: comet_sections.feedback.story.comet_ufi_summary_and_actions_renderer.feedback.reactors.count_reduced
        comet_sections = node.get("comet_sections", {})
        feedback_section = comet_sections.get("feedback", {})
        story = feedback_section.get("story", {})
        comet_ufi = story.get("comet_ufi_summary_and_actions_renderer", {})
        if comet_ufi:
            ufi_feedback = comet_ufi.get("feedback", {})
            reactors = ufi_feedback.get("reactors", {})
            if reactors:
                count = reactors.get("count_reduced")
                if count is not None:
                    return count
        
        # Path 5: feedback.reactors.count_reduced (fallback)
        reactors = node.get("feedback", {}).get("reactors", {})
        if reactors:
            count = reactors.get("count_reduced")
            if count is not None:
                return count
        
        # Path 6: Search for any reactors in feedback tree
        feedback = node.get("feedback", {})
        if isinstance(feedback, dict):
            reactors = feedback.get("reactors", {})
            if isinstance(reactors, dict) and "count_reduced" in reactors:
                return reactors.get("count_reduced", 0)
        
        return 0
    except Exception as e:
        return 0


def extract_share_count(node):
    """Extract share count from post node"""
    try:
        def get_share_count_from_feedback(feedback):
            if not isinstance(feedback, dict):
                return None

            # Newer structures can return nested objects: share_count.count.count...
            share_count = feedback.get("share_count")
            if isinstance(share_count, dict):
                value = share_count.get("count")
                while isinstance(value, dict):
                    value = value.get("count")
                if value is not None:
                    return value
            elif share_count is not None:
                return share_count

            # Sometimes share count lives under share_count_reduced
            share_count_reduced = feedback.get("share_count_reduced")
            if isinstance(share_count_reduced, dict):
                value = share_count_reduced.get("count")
                while isinstance(value, dict):
                    value = value.get("count")
                if value is not None:
                    return value
            elif share_count_reduced is not None:
                return share_count_reduced

            return None

        # Path 1: feedback.share_count (including nested count variants)
        value = get_share_count_from_feedback(node.get("feedback", {}))
        if value is not None:
            return value

        # Path 2: comet_sections.feedback.story.story_ufi_container.story.feedback_context.feedback_target_with_context.share_count
        comet_sections = node.get("comet_sections", {})
        feedback_section = comet_sections.get("feedback", {})
        story = feedback_section.get("story", {})
        story_ufi_container = story.get("story_ufi_container", {})
        ufi_story = story_ufi_container.get("story", {})
        feedback_context = ufi_story.get("feedback_context", {})
        feedback_target = feedback_context.get("feedback_target_with_context", {})
        value = get_share_count_from_feedback(feedback_target)
        if value is not None:
            return value

        # Path 2b: feedback_target_with_context.comet_ufi_summary_and_actions_renderer.feedback.share_count
        comet_ufi_feedback = (
            feedback_target.get("comet_ufi_summary_and_actions_renderer", {})
            .get("feedback", {})
        )
        value = get_share_count_from_feedback(comet_ufi_feedback)
        if value is not None:
            return value

        # Path 3: comet_sections.feedback.story.feedback_context.feedback_target_with_context.share_count (old structure)
        comet_sections = node.get("comet_sections", {})
        feedback_section = comet_sections.get("feedback", {})
        story = feedback_section.get("story", {})
        feedback_context = story.get("feedback_context", {})
        feedback_target = feedback_context.get("feedback_target_with_context", {})
        value = get_share_count_from_feedback(feedback_target)
        if value is not None:
            return value

        return 0
    except Exception as e:
        return 0


def is_reel_or_video_post(node):
    """Check if the post is a reel or video post"""
    # Check for reel in story type
    story_type = node.get("__typename", "")
    if "reel" in story_type.lower():
        return True
    
    # Check if comet_sections has content that indicates reel
    comet_sections = node.get("comet_sections", {})
    content = comet_sections.get("content", {})
    
    # Check for reel in content typename
    content_typename = content.get("__typename", "")
    if "reel" in content_typename.lower():
        return True
    
    # Check attachments for video/reel content
    attachments = node.get("attachments") or []
    for att in attachments:
        styles = att.get("styles") or {}
        attachment = styles.get("attachment") or {}
        
        # Check if it's a video attachment
        single_media = attachment.get("media")
        if single_media:
            media_typename = single_media.get("__typename", "")
            if media_typename == "Video":
                return True
            # Check for reel in typename or anywhere in media object
            if "reel" in str(single_media).lower():
                return True
        
        # Check in all_subattachments for videos
        all_media = attachment.get("all_subattachments", {}).get("nodes", [])
        for m in all_media:
            media_node = m.get("media") or {}
            if media_node.get("__typename") == "Video":
                return True
            # Check for reel substring
            if "reel" in str(media_node).lower():
                return True
    
    return False