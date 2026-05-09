import requests
import json
import time
import os
import uuid
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta

# Load environment variables from .env file
load_dotenv()

# Import common extractor functions
from utils.facebook_extractor import (
    extract_comment_count,
    extract_reaction_count,
    extract_share_count,
    is_reel_or_video_post,
    extract_posted_at,
    extract_author,
    make_scraped_at,
)

GRAPHQL_URL = "https://www.facebook.com/api/graphql/"
WRITE_DEBUG_FILES = os.getenv("SCRAPER_WRITE_DEBUG_FILES", "true").lower() == "true"

# ========= CONFIG (FILL THESE) =========
USER_ID = "100019577483175"   # profile / page id
# USER_ID = "100015055006523" # id profile cÃ´ng anh
PAGE_NAME = None  # Will be extracted automatically
DOC_ID = "25430544756617998" # ProfileCometTimelineFeedRefetchQuery


def sanitize_page_folder_name(page_name):
    """Convert page/user name to a safe folder name."""
    if page_name:
        name_folder = "".join(c for c in page_name if c.isalnum() or c in (' ', '-', '_')).strip()
        if name_folder:
            return name_folder
    return "Unknown"

# ========= RETRY HELPER =========
def retry_request(url, headers, data, proxies, max_retries=5):
    """Make a POST request with retry logic"""
    global PROXIES
    from proxy_utils import rotate_static_proxy, is_proxy_infra_error, is_ip_blocked

    for attempt in range(1, max_retries + 1):
        try:
            r = requests.post(url, headers=headers, data=data, proxies=proxies, cookies=COOKIES, timeout=30)
            if r.status_code == 200:
                return r
            if is_proxy_infra_error(status_code=r.status_code):
                print(f"  ðŸš« Attempt {attempt}/{max_retries}: Proxy auth failed (HTTP {r.status_code}) â€” rotating static proxy...")
                new_p = rotate_static_proxy()
                if new_p:
                    proxies = new_p
                    PROXIES = new_p
            elif is_ip_blocked(status_code=r.status_code, response_text=r.text):
                print(f"  ðŸ›½ Attempt {attempt}/{max_retries}: Facebook blocked this IP (HTTP {r.status_code}) â€” rotating static proxy...")
                new_p = rotate_static_proxy()
                if new_p:
                    proxies = new_p
                    PROXIES = new_p
            else:
                print(f"  âš ï¸ Attempt {attempt}/{max_retries}: Status {r.status_code}")
        except requests.exceptions.ProxyError as e:
            print(f"  ðŸš« Attempt {attempt}/{max_retries}: Proxy unreachable â€” rotating static proxy...")
            new_p = rotate_static_proxy()
            if new_p:
                proxies = new_p
                PROXIES = new_p
        except Exception as e:
            if is_proxy_infra_error(exc=e):
                print(f"  ðŸš« Attempt {attempt}/{max_retries}: Proxy connection error â€” rotating static proxy...")
                new_p = rotate_static_proxy()
                if new_p:
                    proxies = new_p
                    PROXIES = new_p
            else:
                print(f"  âš ï¸ Attempt {attempt}/{max_retries}: {str(e)}")

        if attempt < max_retries:
            wait_time = attempt * 2
            print(f"  â³ Retrying in {wait_time} seconds...")
            time.sleep(wait_time)

    raise Exception(f"Failed after {max_retries} attempts")


def download_image(url, post_id, image_index=1, save_dir="page_post"):
    """Download image from URL and save as {post_id}.jpg or {post_id}_2.jpg etc"""
    if not url or not post_id:
        return None
    
    try:
        # Create post-specific directory
        post_dir = os.path.join(save_dir, str(post_id))
        os.makedirs(post_dir, exist_ok=True)
        
        # Get file extension from URL or default to .jpg
        ext = ".jpg"
        if ".png" in url.lower():
            ext = ".png"
        elif ".jpeg" in url.lower():
            ext = ".jpeg"
        
        # Name as {post_id}.jpg or {post_id}_2.jpg etc
        filename = f"{post_id}{ext}" if image_index == 1 else f"{post_id}_{image_index}{ext}"
        filepath = os.path.join(post_dir, filename)
        
        # Download the image
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        # Save the image
        with open(filepath, 'wb') as f:
            f.write(response.content)
        
        print(f"Downloaded image: {filename}")
        return filename
    
    except Exception as e:
        print(f"Failed to download image: {str(e)}")
        return None


def fetch_remaining_images(last_media_id, post_id, current_image_count, save_dir="page_post", seen_media_ids=None, seen_urls=None):
    """Fetch remaining images using media ID iteration (for posts with 5+ images)"""
    if not last_media_id or not post_id:
        return []

    seen_media_ids = set(seen_media_ids or [])
    seen_urls = set(seen_urls or [])
    
    print(f"Fetching remaining images after image #{current_image_count}...")
    
    DOC_ID_PHOTO = "26168653472729001"  # CometPhotoRootContentQuery
    HEADERS_PHOTO = {
        "user-agent": "Mozilla/5.0",
        "content-type": "application/x-www-form-urlencoded",
        "origin": "https://www.facebook.com",
        "x-fb-friendly-name": "CometPhotoRootContentQuery"
    }
    
    remaining_photos = []
    current_node = last_media_id
    visited = set()
    image_index = current_image_count + 1
    
    while current_node and current_node not in visited and image_index <= 50:  # Max 50 images safety limit
        visited.add(current_node)
        
        variables = {
            "isMediaset": True,
            "renderLocation": "comet_media_viewer",
            "nodeID": current_node,
            "mediasetToken": f"pcb.{post_id}",
            "scale": 2,
            "feedLocation": "COMET_MEDIA_VIEWER",
            "feedbackSource": 65,
            "focusCommentID": None,
            "privacySelectorRenderLocation": "COMET_MEDIA_VIEWER",
            "useDefaultActor": False,
            "shouldShowComments": True
        }
        
        payload = {
            "av": COOKIES.get("c_user", "0"),
            "__user": COOKIES.get("c_user", "0"),
            "__a": "1",
            "fb_dtsg": FB_DTSG if FB_DTSG else "",
            "doc_id": DOC_ID_PHOTO,
            "variables": json.dumps(variables)
        }
        
        try:
            r = requests.post(GRAPHQL_URL, headers=HEADERS_PHOTO, data=payload, proxies=PROXIES, cookies=COOKIES, timeout=30)
            if r.status_code != 200:
                break
            
            # Parse response
            cleaned_blocks = parse_fb_response(r.text)
            if not cleaned_blocks:
                break
            
            # Extract current image URL
            image_url = None
            current_media_id = None
            for block in cleaned_blocks:
                if "currMedia" in block:
                    curr_media = block["currMedia"] or {}
                    current_media_id = curr_media.get("id")
                    image_url = curr_media.get("image", {}).get("uri")
                    break
            
            media_is_new = (not current_media_id) or (current_media_id not in seen_media_ids)
            if image_url and media_is_new and image_url not in seen_urls:
                saved_filename = download_image(image_url, post_id, image_index, save_dir)
                if saved_filename:
                    if current_media_id:
                        seen_media_ids.add(current_media_id)
                    seen_urls.add(image_url)
                    remaining_photos.append({
                        'id': current_media_id or current_node,
                        'type': 'photo',
                        'url': image_url,
                        'saved_as': saved_filename
                    })
                    image_index += 1
            
            # Extract next node
            next_node = None
            for block in cleaned_blocks:
                if "nextMediaAfterNodeId" in block and block["nextMediaAfterNodeId"]:
                    node_id = block["nextMediaAfterNodeId"].get("id")
                    if node_id:
                        next_node = node_id
                        break
            
            if next_node:
                current_node = next_node
                time.sleep(0.5)  # Small delay between requests
            else:
                break  # No more images
                
        except Exception as e:
            print(f"  âš ï¸ Error fetching next image: {e}")
            break
    
    if remaining_photos:
        print(f"  âœ… Fetched {len(remaining_photos)} additional images")
    
    return remaining_photos


# -----------------------------
# Extract all "data" blocks from raw text
# -----------------------------
def extract_data_blocks(raw_text):
    blocks = []
    i = 0
    n = len(raw_text)

    while True:
        idx = raw_text.find('"data"', i)
        if idx == -1:
            break

        brace_start = raw_text.find('{', idx)
        if brace_start == -1:
            break

        depth = 0
        for j in range(brace_start, n):
            if raw_text[j] == '{':
                depth += 1
            elif raw_text[j] == '}':
                depth -= 1
                if depth == 0:
                    block_text = raw_text[brace_start:j+1]
                    try:
                        block = json.loads(block_text)
                        blocks.append(block)
                    except Exception:
                        pass
                    i = j + 1
                    break
        else:
            break

    return blocks


# -----------------------------
# Clean unwanted keys
# -----------------------------
def clean_data_blocks(blocks):
    cleaned = []

    for block in blocks:
        if not isinstance(block, dict):
            continue

        block.pop("errors", None)
        block.pop("extensions", None)

        cleaned.append(block)

    return cleaned


# -----------------------------
# Parse Facebook response using cleaning logic
# -----------------------------
def parse_fb_response(text):
    text = text.replace("for (;;);", "").strip()

    # Prefer parsing line-by-line JSON blocks (Facebook often streams JSON lines,
    # and some useful blocks don't include a top-level "data" key).
    parsed_blocks = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except Exception:
            continue

        if isinstance(obj, dict) and isinstance(obj.get("data"), dict):
            parsed_blocks.append(obj["data"])
        elif isinstance(obj, dict):
            parsed_blocks.append(obj)

    if not parsed_blocks:
        # Fallback to legacy extraction for single-line / embedded JSON
        parsed_blocks = extract_data_blocks(text)

    cleaned = clean_data_blocks(parsed_blocks)
    return cleaned


BASE_HEADERS = {
    "user-agent": "Mozilla/5.0",
    "content-type": "application/x-www-form-urlencoded",
    "origin": "https://www.facebook.com",
    "referer": f"https://www.facebook.com/profile.php?id={USER_ID}",
}

# Get proxy configuration
PROXY = os.getenv('PROXY')
PROXIES = {'http': PROXY, 'https': PROXY} if PROXY else None

# Cookies (set by UI when provided)
COOKIES = {}

# FB_DTSG token (set by UI when provided)
FB_DTSG = ""

if PROXY:
    print(f"Using proxy: {PROXY}")


def extract_page_name(node):
    """Extract page/user name from post node"""
    try:
        # Try from actors
        actors = node.get('comet_sections', {}).get('content', {}).get('story', {}).get('actors', [])
        if actors and len(actors) > 0:
            return actors[0].get('name')
        
        # Try from feedback > owning_profile
        feedback = node.get('feedback', {})
        owning_profile = feedback.get('owning_profile', {})
        if owning_profile:
            return owning_profile.get('name') or owning_profile.get('short_name')
        
        return None
    except Exception:
        return None


def extract_permalink(node):
    """Extract permalink URL from a page post node"""
    try:
        # Direct and most common permalink fields
        direct = node.get("permalink_url") or node.get("url") or node.get("wwwURL")
        if direct:
            return direct

        # Common nested story permalink paths
        comet_sections = node.get("comet_sections", {})
        content_story = comet_sections.get("content", {}).get("story", {})
        nested = content_story.get("wwwURL") or content_story.get("url") or content_story.get("permalink_url")
        if nested:
            return nested

        # Fallback from title/context story links
        context_story = comet_sections.get("context_layout", {}).get("story", {})
        title_story = context_story.get("comet_sections", {}).get("title", {}).get("story", {})
        title_url = title_story.get("url") or title_story.get("wwwURL")
        if title_url:
            return title_url

        # Attachment URL fallback
        attachments = node.get("attachments", [])
        if attachments:
            url = attachments[0].get("styles", {}).get("attachment", {}).get("url")
            if url:
                return url

        return None
    except Exception:
        return None


# Global counter for tracking image indices per post
_image_counters = {}

def extract_media(node, post_id, save_dir="page_post", download_media=True):
    global _image_counters
    
    # Initialize counter for this post if not exists
    if post_id not in _image_counters:
        _image_counters[post_id] = 0
    
    media = []
    last_media_id = None
    seen_photo_ids = set()
    seen_photo_urls = set()

    attachments = node.get("attachments") or []
    for att in attachments:
        styles = att.get("styles") or {}
        attachment = styles.get("attachment") or {}

        # Check for single photo (direct media attachment)
        single_media = attachment.get("media") or att.get("media") or {}
        if isinstance(single_media, dict):
            photo_image = single_media.get("photo_image") or single_media.get("image")
            media_id = single_media.get("id")
            image_url = photo_image.get("uri") if photo_image else None
            media_is_new = (not media_id) or (media_id not in seen_photo_ids)
            if image_url and media_is_new and image_url not in seen_photo_urls:
                _image_counters[post_id] += 1
                last_media_id = media_id  # Track the last media ID
                saved_filename = None
                if download_media:
                    saved_filename = download_image(image_url, post_id, _image_counters[post_id], save_dir)
                if media_id:
                    seen_photo_ids.add(media_id)
                seen_photo_urls.add(image_url)
                media.append({
                    "id": media_id,
                    "type": "photo",
                    "url": image_url,
                    "saved_as": saved_filename
                })

            # Single video case
            if single_media.get("__typename") == "Video":
                media.append({
                    "type": "video",
                    "url": single_media.get("playable_url")
                })

        # Check for album (multiple photos/videos)
        all_media = (
            attachment.get("all_subattachments", {}).get("nodes", [])
            or att.get("all_subattachments", {}).get("nodes", [])
        )
        for m in all_media:
            media_node = m.get("media") or {}

            photo_image = media_node.get("photo_image") or media_node.get("image")
            media_id = media_node.get("id")
            image_url = photo_image.get("uri") if photo_image else None
            media_is_new = (not media_id) or (media_id not in seen_photo_ids)
            if image_url and media_is_new and image_url not in seen_photo_urls:
                _image_counters[post_id] += 1
                last_media_id = media_id  # Track the last media ID
                saved_filename = None
                if download_media:
                    saved_filename = download_image(image_url, post_id, _image_counters[post_id], save_dir)
                if media_id:
                    seen_photo_ids.add(media_id)
                seen_photo_urls.add(image_url)
                media.append({
                    "id": media_id,
                    "type": "photo",
                    "url": image_url,
                    "saved_as": saved_filename
                })

            if media_node.get("__typename") == "Video":
                media.append({
                    "type": "video",
                    "url": media_node.get("playable_url")
                })
    
    # Fetch remaining images if we have exactly 5 photos (indicating there may be more)
    photo_count = sum(1 for m in media if m.get("type") == "photo")
    if download_media and photo_count == 5 and last_media_id:
        remaining_photos = fetch_remaining_images(
            last_media_id,
            post_id,
            _image_counters[post_id],
            save_dir,
            seen_media_ids=seen_photo_ids,
            seen_urls=seen_photo_urls,
        )
        media.extend(remaining_photos)

    return media


def post_already_exists(post_id, base_folder, name_folder):
    """Check if a post has already been scraped by checking if its JSON file exists"""
    if not post_id or not name_folder:
        return False
    
    post_file = os.path.join(base_folder, name_folder, str(post_id), f"{post_id}.json")
    return os.path.exists(post_file)


def _parse_iso_datetime(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def fetch_posts(limit=10, min_comments=0, batch_size=10, on_batch_complete=None, base_folder="page_post", last_24_hours_only=False, download_media=True):
    """Fetch posts from a Facebook timeline (page or user profile).
    
    Args:
        limit: Maximum number of posts to fetch. Use None with last_24_hours_only=True to fetch all recent posts.
        min_comments: Minimum number of comments required for a post to be included (0 = no filter)
        batch_size: Number of posts to fetch before calling on_batch_complete callback
        on_batch_complete: Optional callback function(batch_posts, total_so_far, limit) called after each batch
        base_folder: Base output folder for saving posts/media (e.g. "page_post", "user_post")
        last_24_hours_only: Only include posts whose posted_at is within the last 24 hours.
        download_media: Whether to download media files locally. Metadata is still extracted when False.
    """
    global PAGE_NAME
    all_posts = []
    batch_posts = []
    cursor = None
    page_num = 1  # Track page number for saving cleaned data
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=24) if last_24_hours_only else None
    stop_due_to_time = False
    max_pages = int(os.getenv("SCRAPER_MAX_24H_PAGES", "100")) if last_24_hours_only else None
    
    if min_comments > 0:
        print(f"ðŸ“Š Filtering posts with at least {min_comments} comments")
    
    if cutoff_time:
        print(f"Chỉ lấy bài đăng trong 24h gần nhất (từ {cutoff_time.isoformat()})")

    if limit is not None and batch_size > 0 and batch_size < limit:
        print(f"ðŸ“¦ Xu ly theo lo {batch_size} posts")

    while limit is None or len(all_posts) < limit:
        if max_pages is not None and page_num > max_pages:
            print(f"Dat gioi han an toan so trang ({max_pages}) cho che do 24h. Dung lai.")
            break
        variables = {
            "count": 3,
            "cursor": cursor,
            "id": USER_ID,
            "feedLocation": "TIMELINE",
            "renderLocation": "timeline",
            "scale": 2,
            "useDefaultActor": False
        }

        payload = {
        "av": COOKIES.get("c_user", "0"),
        "__user": COOKIES.get("c_user", "0"),
        "__a": "1",
        "fb_dtsg": FB_DTSG if FB_DTSG else "",
            "doc_id": DOC_ID,
            "variables": json.dumps(variables),
        }

        # Retry loop for empty response handling
        max_empty_retries = 3
        empty_retry_count = 0
        cleaned_data = []
        
        while empty_retry_count < max_empty_retries:
            r = retry_request(GRAPHQL_URL, BASE_HEADERS, payload, PROXIES)
            # with open("response.txt", "w", encoding="utf-8") as f:
            #     f.write(r.text)
            print("Ma trang thai:", r.status_code)
            cleaned_data = parse_fb_response(r.text)
            
            if cleaned_data and len(cleaned_data) > 0:
                # Got valid data, break retry loop
                break
            else:
                empty_retry_count += 1
                if empty_retry_count < max_empty_retries:
                    print(f"  âš ï¸ Phan hoi rong, dang thu lai ({empty_retry_count}/{max_empty_retries})...")
                    time.sleep(2)  # Wait before retry
                else:
                    print(f"Phản hồi rỗng sau {max_empty_retries} lần thử, bỏ qua trang")
        
        # # Save cleaned data for verification
        # with open(f"cleaned_page_{page_num}.json", "w", encoding="utf-8") as f:
        #     json.dump(cleaned_data, f, ensure_ascii=False, indent=2)
        # print(f"Da luu cleaned_page_{page_num}.json")
        
        # If still empty after retries, stop pagination (can't get next cursor from empty response)
        if not cleaned_data or len(cleaned_data) == 0:
            print("  âŒ Khong nhan duoc du lieu sau khi thu lai, dung phan trang")
            break
        
        # Collect all Story nodes from the response
        # Stories can be in two places:
        # 1. Inside timeline_list_feed_units.edges[]
        # 2. As standalone nodes with __typename: "Story"
        
        story_nodes = []
        timeline_block = None

        # Some GraphQL responses return share_count (and other UFI counts) in
        # separate blocks keyed by feedback id, not inside the Story node.
        # Build a lookup so we can fill missing values reliably.
        feedback_share_count_by_id = {}
        def _unwrap_count(value):
            while isinstance(value, dict):
                value = value.get("count")
            return value
        for block in cleaned_data:
            if not isinstance(block, dict):
                continue
            node2 = block.get("node", block)
            if not isinstance(node2, dict):
                continue
            fb_id = node2.get("id")
            if not (isinstance(fb_id, str) and fb_id.startswith("ZmVlZGJhY2s6")):
                continue
            sc = node2.get("share_count")
            if sc is None:
                continue
            sc_val = _unwrap_count(sc) if isinstance(sc, dict) else sc
            if sc_val is None:
                continue
            try:
                feedback_share_count_by_id[fb_id] = int(sc_val)
            except Exception:
                pass
        
        for block in cleaned_data:
            if not isinstance(block, dict):
                continue
            
            node = block.get("node", {})
            node_typename = node.get("__typename")
            
            # Check if this block has timeline edges
            if "timeline_list_feed_units" in node:
                timeline_block = block
                edges = node["timeline_list_feed_units"].get("edges", [])
                for edge in edges:
                    edge_node = edge.get("node")
                    if edge_node and edge_node.get("__typename") == "Story":
                        story_nodes.append(edge_node)
            
            # Check if this block itself is a Story node
            elif node_typename == "Story":
                story_nodes.append(node)
            
            # Check for Story nodes inside Group edges (edge case)
            elif node_typename == "Group":
                edges = node.get('group_feed', {}).get('edges', [])
                for edge in edges:
                    edge_node = edge.get('node', {})
                    if edge_node.get('__typename') == 'Story':
                        story_nodes.append(edge_node)
        
        print(f"Tìm thấy {len(story_nodes)} post trong trang {page_num}")
        
        # Process all collected Story nodes
        for node in story_nodes:
            # Skip reels and video posts
            if is_reel_or_video_post(node):
                print(f"Bỏ qua bài reel/video")
                continue

            post_id = node.get("post_id")
            if not post_id:
                continue

            posted_at = extract_posted_at(node)
            posted_dt = _parse_iso_datetime(posted_at)

            if cutoff_time:
                if not posted_dt:
                    print(f"  Skipping post {post_id} vì không xác định được posted_at")
                    continue
                if posted_dt < cutoff_time:
                    print(f"  Đã gặp post cũ hơn 24h: {post_id} ({posted_at})")
                    stop_due_to_time = True
                    continue
            
            # Check comment count threshold
            comment_count = extract_comment_count(node)
            if min_comments > 0 and comment_count < min_comments:
                print(f"Bỏ qua post chỉ có {comment_count} bình luận (cần {min_comments}+)")
                continue
            
            # Extract share count
            share_count = extract_share_count(node)
            if share_count == 0:
                fb_id = node.get("feedback", {}).get("id")
                if fb_id and fb_id in feedback_share_count_by_id:
                    share_count = feedback_share_count_by_id[fb_id]

            # Extract reaction count
            reaction_count = extract_reaction_count(node)
            
            # Extract page name from first post if not set
            if not PAGE_NAME:
                PAGE_NAME = extract_page_name(node)
                if PAGE_NAME:
                    print(f"ðŸ“‚ Ten page: {PAGE_NAME}")
            
            # Check if post already exists
            temp_page_name = PAGE_NAME or extract_page_name(node)
            if temp_page_name:
                temp_name_folder = sanitize_page_folder_name(temp_page_name)
                if post_already_exists(post_id, base_folder, temp_name_folder):
                    print(f"Bỏ qua post đã scrape trước đó: {post_id}")
                    continue
                
            feedback_id = node.get("feedback", {}).get("id")

            message = (
                node.get("comet_sections", {})
                .get("content", {})
                .get("story", {})
                .get("message") or {}
            ).get("text")

            permalink = extract_permalink(node)
            author = extract_author(node)

            post = {
                "post_id": post_id,
                "feedback_id": feedback_id,
                "text": message,
                "permalink": permalink,
                "comment_count": comment_count,
                "reaction_count": reaction_count,
                "share_count": share_count,
                "page_name": PAGE_NAME,
                # Added metadata fields (best-effort)
                "posted_at": posted_at,
                "scraped_at": make_scraped_at(),
                "author_name": author.get("author_name"),
                "author_url": author.get("author_url"),
                # actor-based guess; if missing, default to "user" for timeline scrape
                "source_type": author.get("source_type") or "user",
                # We found it in feed => should be active at scrape time
                "is_active": True,
            }
            
            # Sanitize page name folder
            name_folder = sanitize_page_folder_name(PAGE_NAME)
            
            # Prepare save directory for media
            media_save_dir = os.path.join(base_folder, name_folder)
            
            # Extract media with correct save directory
            post["media"] = extract_media(node, post_id, media_save_dir, download_media=download_media)
            
            if WRITE_DEBUG_FILES:
                # Save individual post to folder structure: {base_folder}/{page_name}/{post_id}/{post_id}.json
                post_dir = os.path.join(base_folder, name_folder, str(post_id))
                os.makedirs(post_dir, exist_ok=True)
                post_file = os.path.join(post_dir, f"{post_id}.json")
                with open(post_file, "w", encoding="utf-8") as f:
                    json.dump(post, f, ensure_ascii=False, indent=2)
                print(f"âœ“ Da luu vao {post_file}")

            batch_posts.append(post)
            all_posts.append(post)
            
            # Check if we should process this batch
            if batch_size > 0 and len(batch_posts) >= batch_size and on_batch_complete:
                total_label = limit if limit is not None else "24h"
                print(f"\n“¦ Hoàn tất: {len(batch_posts)} posts. Total: {len(all_posts)}/{total_label}")
                on_batch_complete(batch_posts, len(all_posts), limit)
                batch_posts = []  # Reset batch
            
            if limit is not None and len(all_posts) >= limit:
                break

        if stop_due_to_time:
            print("Đã gặp post cũ hơn 24h. Dừng phân trang.")
            break

        # update cursor - get page_info from timeline_block or find it in cleaned_data
        page_info = timeline_block["node"]["timeline_list_feed_units"].get("page_info")
        
        # If not in timeline_block, search for it in cleaned_data array
        if not page_info:
            for block in cleaned_data:
                if isinstance(block, dict) and "page_info" in block:
                    page_info = block["page_info"]
                    break
        
        page_info = page_info or {}
        cursor = page_info.get("end_cursor")

        if not cursor:
            print("Không còn trang tiếp theo. Dừng phân trang.")
            break


        time.sleep(1)
        page_num += 1  # Increment page counter
    
    # Process any remaining posts in the final batch
    if batch_posts and on_batch_complete:
        total_label = limit if limit is not None else "24h"
        print(f"\n“¦ Lô cuối: {len(batch_posts)} posts. Total: {len(all_posts)}/{total_label}")
        on_batch_complete(batch_posts, len(all_posts), limit)

    return all_posts


if __name__ == "__main__":
    count = int(input("Nhap so post can lay? "))

    posts = fetch_posts(count)

    with open("posts.json", "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False, indent=2)

    print(f"Da luu {len(posts)} post vao posts.json")

