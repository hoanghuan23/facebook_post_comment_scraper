# filepath: utils/facebook_extractor.py
"""
Common Facebook data extraction functions shared across scrapers.
Used by post_scraper.py (page/user) and group_post_scraper_v2.py (group).
"""

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