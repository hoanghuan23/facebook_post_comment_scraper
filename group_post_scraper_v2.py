import requests
import json
import time
import os
import re
import uuid
from urllib.parse import urlparse
from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta

# Load environment variables from .env file
load_dotenv()

# Import common extractor functions
from utils.facebook_extractor import (
    extract_comment_count,
    extract_reaction_count,
    extract_share_count,
    extract_posted_at,
    extract_author,
    make_scraped_at,
)

GRAPHQL_URL = "https://www.facebook.com/api/graphql/"
WRITE_DEBUG_FILES = os.getenv("SCRAPER_WRITE_DEBUG_FILES", "false").lower() == "true"
WRITE_POST_FILES = os.getenv("SCRAPER_WRITE_POST_FILES", "false").lower() == "true"

# ========= CONFIG (FILL THESE) =========
GROUP_ID = "361726451351144"  # group id
GROUP_NAME = None  # Will be extracted automatically
GROUP_FEED_FRIENDLY_NAME = "GroupsCometFeedRegularStoriesPaginationQuery"
DOC_ID = "26849346238033346"  # GroupsCometFeedRegularStoriesPaginationQuery

HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "content-type": "application/x-www-form-urlencoded",
    "origin": "https://www.facebook.com",
    "priority": "u=1, i",
    "referer": f"https://www.facebook.com/groups/{GROUP_ID}",
    "sec-ch-prefers-color-scheme": "light",
    "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "x-asbd-id": "359341",
    "x-fb-friendly-name": GROUP_FEED_FRIENDLY_NAME,
}

# Get proxy configuration
PROXY = os.getenv('PROXY')
PROXIES = {'http': PROXY, 'https': PROXY} if PROXY else None

# Cookies (set by UI when provided)
COOKIES = {}

# FB_DTSG token (set by UI when provided)
FB_DTSG = os.getenv("FB_DTSG", "")
LSD = os.getenv("FB_LSD", "pSvNTcwAmHL0yEdKD5oy1Y")
JAZOEST = os.getenv("FB_JAZOEST", "25401")

if PROXY:
    print(f"Using proxy: {PROXY}")


def extract_group_name(node):
    """Extract group name from post node"""
    try:
        # Try from context_layout > story > comet_sections > title > story > to
        context_layout = node.get('comet_sections', {}).get('context_layout', {})
        story = context_layout.get('story', {})
        title_section = story.get('comet_sections', {}).get('title', {})
        title_story = title_section.get('story', {})
        to_obj = title_story.get('to', {})
        if to_obj.get('__typename') == 'Group':
            return to_obj.get('name')
        
        # Try from content > story > target_group (if available)
        content = node.get('comet_sections', {}).get('content', {})
        content_story = content.get('story', {})
        target_group = content_story.get('target_group', {})
        if target_group and 'name' in target_group:
            return target_group.get('name')
        
        # Try from feedback > associated_group
        feedback = node.get('feedback', {})
        associated_group = feedback.get('associated_group', {})
        if associated_group and 'name' in associated_group:
            return associated_group.get('name')
        
        return None
    except Exception:
        return None

# ========= RETRY HELPER =========
def retry_request(
    url,
    headers,
    data,
    proxies,
    cookies=None,
    max_retries=5,
    *,
    scraper="group_posts",
    endpoint_label="facebook_graphql",
    source_id=None,
    facebook_id=None,
    log_success=True,
):
    """Make a POST request with retry logic"""
    global PROXIES
    from proxy_utils import rotate_proxy_for_retry, is_proxy_infra_error, is_ip_blocked
    from backend.scraper import request_telemetry as telemetry

    request_cookies = COOKIES if cookies is None else cookies
    request_id = str(uuid.uuid4())
    run_id = telemetry.get_run_id()
    source_id = telemetry.get_source_id(source_id)
    facebook_id = telemetry.get_facebook_id(facebook_id or GROUP_ID)

    for attempt in range(1, max_retries + 1):
        start_monotonic, start_time, current_concurrency = telemetry.begin_attempt()
        try:
            r = requests.post(url, headers=headers, data=data, proxies=proxies, cookies=request_cookies, timeout=30)
            end_time, duration_ms = telemetry.finish_attempt(start_monotonic)
            if r.status_code == 200:
                if log_success:
                    telemetry.append_event(
                        request_id=request_id,
                        run_id=run_id,
                        start_time=start_time,
                        end_time=end_time,
                        duration_ms=duration_ms,
                        scraper=scraper,
                        endpoint_label=endpoint_label,
                        source_id=source_id,
                        facebook_id=facebook_id,
                        attempt=attempt,
                        max_retries=max_retries,
                        classification=telemetry.classify_response(r.status_code, r.text),
                        status_code=r.status_code,
                        response_size_bytes=telemetry.response_size_bytes(r),
                        current_concurrency=current_concurrency,
                        proxy_mode=telemetry.proxy_mode(proxies, bool(request_cookies)),
                        proxy_label=telemetry.proxy_label(proxies),
                    )
                else:
                    telemetry.attach_response_metadata(
                        r,
                        request_id=request_id,
                        run_id=run_id,
                        start_time=start_time,
                        end_time=end_time,
                        duration_ms=duration_ms,
                        scraper=scraper,
                        endpoint_label=endpoint_label,
                        source_id=source_id,
                        facebook_id=facebook_id,
                        attempt=attempt,
                        max_retries=max_retries,
                        current_concurrency=current_concurrency,
                        proxy_mode=telemetry.proxy_mode(proxies, bool(request_cookies)),
                        proxy_label=telemetry.proxy_label(proxies),
                    )
                return r
            telemetry.append_event(
                request_id=request_id,
                run_id=run_id,
                start_time=start_time,
                end_time=end_time,
                duration_ms=duration_ms,
                scraper=scraper,
                endpoint_label=endpoint_label,
                source_id=source_id,
                facebook_id=facebook_id,
                attempt=attempt,
                max_retries=max_retries,
                classification=telemetry.classify_response(r.status_code, r.text),
                status_code=r.status_code,
                response_size_bytes=telemetry.response_size_bytes(r),
                current_concurrency=current_concurrency,
                proxy_mode=telemetry.proxy_mode(proxies, bool(request_cookies)),
                proxy_label=telemetry.proxy_label(proxies),
            )
            if is_proxy_infra_error(status_code=r.status_code):
                print(f"Attempt {attempt}/{max_retries}: Proxy auth failed (HTTP {r.status_code}) - retrying proxy...")
                new_p = rotate_proxy_for_retry(proxies, has_cookies=bool(request_cookies))
                if new_p:
                    proxies = new_p
                    PROXIES = new_p
            elif is_ip_blocked(status_code=r.status_code, response_text=r.text):
                print(f"Attempt {attempt}/{max_retries}: Facebook blocked this IP (HTTP {r.status_code}) - retrying proxy...")
                new_p = rotate_proxy_for_retry(proxies, has_cookies=bool(request_cookies))
                if new_p:
                    proxies = new_p
                    PROXIES = new_p
            else:
                print(f"Attempt {attempt}/{max_retries}: Status {r.status_code}")
        except requests.exceptions.ProxyError as e:
            end_time, duration_ms = telemetry.finish_attempt(start_monotonic)
            telemetry.append_event(
                request_id=request_id,
                run_id=run_id,
                start_time=start_time,
                end_time=end_time,
                duration_ms=duration_ms,
                scraper=scraper,
                endpoint_label=endpoint_label,
                source_id=source_id,
                facebook_id=facebook_id,
                attempt=attempt,
                max_retries=max_retries,
                classification=telemetry.CLASS_PROXY_ERROR,
                status_code=None,
                response_size_bytes=None,
                current_concurrency=current_concurrency,
                proxy_mode=telemetry.proxy_mode(proxies, bool(request_cookies)),
                proxy_label=telemetry.proxy_label(proxies),
            )
            print(f"Attempt {attempt}/{max_retries}: Proxy unreachable - retrying proxy...")
            new_p = rotate_proxy_for_retry(proxies, has_cookies=bool(request_cookies))
            if new_p:
                proxies = new_p
                PROXIES = new_p
        except Exception as e:
            end_time, duration_ms = telemetry.finish_attempt(start_monotonic)
            telemetry.append_event(
                request_id=request_id,
                run_id=run_id,
                start_time=start_time,
                end_time=end_time,
                duration_ms=duration_ms,
                scraper=scraper,
                endpoint_label=endpoint_label,
                source_id=source_id,
                facebook_id=facebook_id,
                attempt=attempt,
                max_retries=max_retries,
                classification=telemetry.classify_exception(e),
                status_code=None,
                response_size_bytes=None,
                current_concurrency=current_concurrency,
                proxy_mode=telemetry.proxy_mode(proxies, bool(request_cookies)),
                proxy_label=telemetry.proxy_label(proxies),
            )
            if is_proxy_infra_error(exc=e):
                print(f"Attempt {attempt}/{max_retries}: Proxy connection error - retrying proxy...")
                new_p = rotate_proxy_for_retry(proxies, has_cookies=bool(request_cookies))
                if new_p:
                    proxies = new_p
                    PROXIES = new_p
            else:
                print(f"Attempt {attempt}/{max_retries}: {str(e)}")

        if attempt < max_retries:
            wait_time = attempt * 2
            print(f"Retrying in {wait_time} seconds...")
            time.sleep(wait_time)

    raise Exception(f"Failed after {max_retries} attempts")


def download_image(url, post_id, image_index=1, save_dir="group_post"):
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


def fetch_remaining_images(
    last_media_id,
    post_id,
    current_image_count,
    save_dir="group_post",
    download_media=True,
    seen_media_ids=None,
    seen_urls=None,
    cookies=None,
    fb_dtsg=None,
    proxies=None,
):
    """Fetch remaining images using media ID iteration (for posts with 5+ images)"""
    if not last_media_id or not post_id:
        return []

    seen_media_ids = set(seen_media_ids or [])
    seen_urls = set(seen_urls or [])
    request_cookies = COOKIES if cookies is None else cookies
    request_fb_dtsg = FB_DTSG if fb_dtsg is None else fb_dtsg
    request_proxies = PROXIES if proxies is None else proxies
    
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
            "av": request_cookies.get("c_user", "0"),
            "__user": request_cookies.get("c_user", "0"),
            "__a": "1",
            "fb_dtsg": request_fb_dtsg if request_fb_dtsg else "",
            "doc_id": DOC_ID_PHOTO,
            "variables": json.dumps(variables)
        }
        
        try:
            r = requests.post(
                GRAPHQL_URL,
                headers=HEADERS_PHOTO,
                data=payload,
                proxies=request_proxies,
                cookies=request_cookies,
                timeout=30,
            )
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
                saved_filename = None
                if download_media:
                    saved_filename = download_image(image_url, post_id, image_index, save_dir)
                if current_media_id:
                    seen_media_ids.add(current_media_id)
                seen_urls.add(image_url)
                remaining_photos.append({
                    'id': current_media_id or current_node,
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
            print(f"Error fetching next image: {e}")
            break
    
    if remaining_photos:
        print(f"Fetched {len(remaining_photos)} additional images")
    
    return remaining_photos


def extract_data_blocks(raw_text):
    """Extract all 'data' blocks from raw text"""
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


def clean_data_blocks(blocks):
    """Clean unwanted keys from data blocks"""
    cleaned = []

    for block in blocks:
        if not isinstance(block, dict):
            continue

        block.pop("errors", None)
        block.pop("extensions", None)

        cleaned.append(block)

    return cleaned


def parse_fb_response(text):
    """Parse Facebook response using the same logic as post_scraper"""
    text = text.replace("for (;;);", "").replace("for(;;);", "").strip()

    # Prefer line-by-line JSON parsing first because Facebook often streams
    # multiple JSON lines, including useful blocks without a top-level "data".
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
        # Fallback for single-line / embedded JSON payloads.
        parsed_blocks = extract_data_blocks(text)

    cleaned = clean_data_blocks(parsed_blocks)
    return cleaned


def _summarize_group_response(data):
    """Build a compact diagnostic summary for unexpected empty group responses."""
    summary = {
        "total_blocks": 0,
        "top_level_keys": {},
        "node_typenames": {},
        "group_nodes": 0,
        "group_feed_edges": 0,
        "timeline_edges": 0,
        "story_nodes": 0,
        "page_info_blocks": 0,
        "errors": [],
    }

    def _bump(bucket, key):
        if not key:
            key = "<empty>"
        bucket[key] = bucket.get(key, 0) + 1

    for item in data:
        if not isinstance(item, dict):
            continue
        summary["total_blocks"] += 1
        for key in item.keys():
            _bump(summary["top_level_keys"], str(key))

        if item.get("error") is not None or item.get("errorSummary") or item.get("errorDescription"):
            summary["errors"].append(
                {
                    "error": item.get("error"),
                    "errorSummary": item.get("errorSummary"),
                    "errorDescription": item.get("errorDescription"),
                    "isNotCritical": item.get("isNotCritical"),
                }
            )

        node = item.get("node")
        if not isinstance(node, dict):
            if isinstance(item.get("page_info"), dict):
                summary["page_info_blocks"] += 1
            continue

        node_typename = node.get("__typename") or "<missing>"
        _bump(summary["node_typenames"], node_typename)

        if node_typename == "Story":
            summary["story_nodes"] += 1

        if node_typename == "Group":
            summary["group_nodes"] += 1
            group_feed = node.get("group_feed") or {}
            edges = group_feed.get("edges") or []
            if isinstance(edges, list):
                summary["group_feed_edges"] += len(edges)
            if isinstance(group_feed.get("page_info"), dict):
                summary["page_info_blocks"] += 1

        timeline = node.get("timeline_list_feed_units") or {}
        tl_edges = timeline.get("edges") or []
        if isinstance(tl_edges, list):
            summary["timeline_edges"] += len(tl_edges)
        if isinstance(timeline.get("page_info"), dict):
            summary["page_info_blocks"] += 1

    return summary


def sanitize_group_folder_name(group_name):
    """Convert group name to a safe folder name."""
    if group_name:
        name_folder = "".join(c for c in group_name if c.isalnum() or c in (' ', '-', '_')).strip()
        if name_folder:
            return name_folder
    return "Unknown"


def _extract_thumbnail_uri(media_data):
    """Return a thumbnail URI when Facebook supplies the optional nested shape."""
    if not isinstance(media_data, dict):
        return None
    preferred_thumbnail = media_data.get('preferred_thumbnail')
    if not isinstance(preferred_thumbnail, dict):
        return None
    image = preferred_thumbnail.get('image')
    return image.get('uri') if isinstance(image, dict) else None


def extract_media(node, post_id, save_dir="group_post", cookies=None, fb_dtsg=None, proxies=None, download_media=True):
    """Extract photo and video URLs from a post"""
    media = {
        'photos': [],
        'videos': []
    }

    seen_photo_ids = set()
    seen_photo_urls = set()
    
    # Track image index for this post
    image_index = 0
    last_media_id = None
    
    attachments = node.get('attachments', [])
    
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue

        styles = attachment.get('styles', {}) or {}
        styled_attachment = styles.get('attachment', {}) or {}
        attachment_media = attachment.get('media') or {}
        if not isinstance(attachment_media, dict):
            attachment_media = {}

        # Some group posts expose the image under styles.attachment.media,
        # others only under attachment.media. Try both.
        single_media = styled_attachment.get('media') or attachment_media or {}
        if isinstance(single_media, dict):
            media_id = single_media.get('id') or attachment_media.get('id')

            photo_image = single_media.get('photo_image') or single_media.get('image')
            image_url = photo_image.get('uri') if photo_image else None
            media_is_new = (not media_id) or (media_id not in seen_photo_ids)
            if image_url and media_is_new and image_url not in seen_photo_urls:
                image_index += 1
                last_media_id = media_id
                saved_filename = None
                if download_media:
                    saved_filename = download_image(image_url, post_id, image_index, save_dir)
                if media_id:
                    seen_photo_ids.add(media_id)
                seen_photo_urls.add(image_url)
                media['photos'].append({
                    'id': media_id,
                    'url': image_url,
                    'width': photo_image.get('width'),
                    'height': photo_image.get('height'),
                    'saved_as': saved_filename
                })

            if single_media.get('__typename') == 'Video':
                media['videos'].append({
                    'id': single_media.get('id'),
                    'url': single_media.get('playable_url'),
                    'thumbnail': _extract_thumbnail_uri(single_media)
                })

        # Handle albums (multiple photos)
        subattachments = (
            attachment.get('all_subattachments', {}).get('nodes', [])
            or styled_attachment.get('all_subattachments', {}).get('nodes', [])
        )
        for subattachment in subattachments:
            if not isinstance(subattachment, dict):
                continue
            photo_data = subattachment.get('media', {}) or {}
            if not isinstance(photo_data, dict):
                continue
            media_id = photo_data.get('id')
            photo_image = photo_data.get('photo_image') or photo_data.get('image')

            image_url = photo_image.get('uri') if photo_image else None
            media_is_new = (not media_id) or (media_id not in seen_photo_ids)
            if image_url and media_is_new and image_url not in seen_photo_urls:
                image_index += 1
                last_media_id = media_id
                saved_filename = None
                if download_media:
                    saved_filename = download_image(image_url, post_id, image_index, save_dir)
                if media_id:
                    seen_photo_ids.add(media_id)
                seen_photo_urls.add(image_url)
                media['photos'].append({
                    'id': media_id,
                    'url': image_url,
                    'width': photo_image.get('width'),
                    'height': photo_image.get('height'),
                    'saved_as': saved_filename
                })

            if photo_data.get('__typename') == 'Video':
                media['videos'].append({
                    'id': photo_data.get('id'),
                    'url': photo_data.get('playable_url'),
                    'thumbnail': _extract_thumbnail_uri(photo_data)
                })

        # Handle video attachments from the direct attachment media shape
        if isinstance(attachment_media, dict) and attachment_media.get('__typename') == 'Video':
            video_data = attachment_media
            media['videos'].append({
                'id': video_data.get('id'),
                'url': video_data.get('playable_url'),
                'thumbnail': _extract_thumbnail_uri(video_data)
            })
    
    # Fetch remaining images if we have exactly 5 photos (indicating there may be more)
    if image_index == 5 and last_media_id:
        remaining_photos = fetch_remaining_images(
            last_media_id,
            post_id,
            image_index,
            save_dir,
            download_media=download_media,
            seen_media_ids=seen_photo_ids,
            seen_urls=seen_photo_urls,
            cookies=cookies,
            fb_dtsg=fb_dtsg,
            proxies=proxies,
        )
        media['photos'].extend(remaining_photos)
    
    return media


def post_already_exists(post_id, base_folder, name_folder):
    """Check if a post has already been scraped by checking if its JSON file exists"""
    if not post_id or not name_folder:
        return False
    
    post_file = os.path.join(base_folder, name_folder, str(post_id), f"{post_id}.json")
    return os.path.exists(post_file)


def build_group_link(group_id=None, permalink=None):
    """Build a canonical group URL from explicit group_id or a post permalink."""
    resolved_group_id = str(group_id).strip() if group_id else ""
    if not resolved_group_id and permalink:
        try:
            parsed = urlparse(permalink)
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) >= 2 and parts[0] == "groups":
                resolved_group_id = parts[1]
        except Exception:
            resolved_group_id = ""
    if not resolved_group_id:
        return ""
    return f"https://www.facebook.com/groups/{resolved_group_id}/"


def extract_post_data(
    node,
    group_name=None,
    group_id=None,
    cookies=None,
    fb_dtsg=None,
    proxies=None,
    download_media=True,
    reaction_count_override=None,
    share_count_override=None,
):
    """Extract relevant data from a post node"""
    if not node or node.get('__typename') != 'Story':
        return None
    
    # Get the post content from the nested structure
    content_story = node.get('comet_sections', {}).get('content', {}).get('story', {})
    
    # Extract message/text
    message = ''
    message_obj = content_story.get('message', {})
    if message_obj:
        message = message_obj.get('text', '')
    
    post_id = node.get('post_id')
    if not post_id:
        return None
    
    author = extract_author(node)
    posted_at = extract_posted_at(node)
    
    # Extract comment count
    comment_count = extract_comment_count(node)
    
    # Extract reaction count
    reaction_count = extract_reaction_count(node)
    if reaction_count_override is not None:
        reaction_count = reaction_count_override
    
    # Extract share count
    share_count = extract_share_count(node)
    if share_count_override is not None:
        share_count = share_count_override
    
    # Extract group name if not provided
    if not group_name:
        group_name = extract_group_name(node)
    
    name_folder = sanitize_group_folder_name(group_name)
    
    # Prepare save directory for media
    media_save_dir = os.path.join("group_post", name_folder)
    
    extracted_media = extract_media(
        node,
        post_id,
        media_save_dir,
        cookies=cookies,
        fb_dtsg=fb_dtsg,
        proxies=proxies,
        download_media=download_media,
    )

    permalink = node.get('permalink_url', '')
    post_data = {
        'id': node.get('id'),
        'post_id': post_id,
        'message': message,
        'comment_count': comment_count,
        'reaction_count': reaction_count,
        'share_count': share_count,
        'group_name': group_name,
        'group_link': build_group_link(group_id=group_id or GROUP_ID, permalink=permalink),
        'permalink': permalink,
        # Added metadata fields (best-effort)
        'posted_at': posted_at,
        'scraped_at': make_scraped_at(),
        'author_name': author.get("author_name"),
        'author_url': author.get("author_url"),
        # Group scraper is always group source
        'source_type': "group",
        'is_active': True,
        'photos': extracted_media['photos'],
        'videos': extracted_media['videos']
    }
    
    if WRITE_POST_FILES:
        # Save individual post to folder structure: group_post/{group_name}/{post_id}/{post_id}.json
        post_dir = os.path.join("group_post", name_folder, str(post_id))
        os.makedirs(post_dir, exist_ok=True)
        post_file = os.path.join(post_dir, f"{post_id}.json")
        with open(post_file, "w", encoding="utf-8") as f:
            json.dump(post_data, f, ensure_ascii=False, indent=2)
        print(f"Saved to {post_file}")
    
    return post_data


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


def _unwrap_count_value(value):
    while isinstance(value, dict):
        value = value.get("count")
    return value


def _coerce_int(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None

        compact = raw.replace(" ", "")
        suffix_match = re.fullmatch(r"([+-]?(?:\d+(?:[\.,]\d+)?|[\.,]\d+))([kKmMbB])", compact)
        if suffix_match:
            number_text, suffix = suffix_match.groups()
            try:
                number = float(number_text.replace(",", "."))
            except Exception:
                return None
            multiplier = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[suffix.lower()]
            return int(number * multiplier)

        cleaned = "".join(ch for ch in raw if ch.isdigit() or ch == "-")
        if not cleaned or cleaned == "-":
            return None
        try:
            return int(cleaned)
        except Exception:
            return None
    return None


def _extract_feedback_reaction_count_from_node(node):
    if not isinstance(node, dict):
        return None

    renderer_feedback = (
        node.get("comet_ufi_summary_and_actions_renderer", {})
        .get("feedback")
    )
    parsed = _extract_feedback_reaction_count_from_node(renderer_feedback)
    if parsed is not None:
        return parsed

    reaction_count = node.get("reaction_count")
    if isinstance(reaction_count, dict):
        parsed = _coerce_int(_unwrap_count_value(reaction_count))
        if parsed is not None:
            return parsed
    else:
        parsed = _coerce_int(reaction_count)
        if parsed is not None:
            return parsed

    reactors = node.get("reactors")
    if isinstance(reactors, dict):
        parsed = _coerce_int(_unwrap_count_value(reactors.get("count_reduced")))
        if parsed is not None:
            return parsed
        parsed = _coerce_int(_unwrap_count_value(reactors.get("count")))
        if parsed is not None:
            return parsed

    return None


def _iter_dicts(obj, seen=None):
    if seen is None:
        seen = set()

    if isinstance(obj, dict):
        obj_id = id(obj)
        if obj_id in seen:
            return
        seen.add(obj_id)
        yield obj
        for value in obj.values():
            yield from _iter_dicts(value, seen)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_dicts(item, seen)


def _is_feedback_id(value):
    return isinstance(value, str) and value.startswith("ZmVlZGJhY2s6")


def _build_feedback_metric_lookups(data):
    reaction_by_id = {}
    share_by_id = {}

    for node in _iter_dicts(data):
        feedback_ids = [
            value for value in (node.get("id"), node.get("__id"))
            if _is_feedback_id(value)
        ]
        if not feedback_ids:
            continue

        count = _extract_feedback_reaction_count_from_node(node)
        if count is not None:
            for feedback_id in feedback_ids:
                reaction_by_id[feedback_id] = count

        count = _extract_feedback_share_count_from_node(node)
        if count is not None:
            for feedback_id in feedback_ids:
                share_by_id[feedback_id] = count

    return reaction_by_id, share_by_id


def _extract_story_feedback_ids(story_node):
    if not isinstance(story_node, dict):
        return []

    ids = []
    seen = set()

    def add(value):
        if _is_feedback_id(value) and value not in seen:
            seen.add(value)
            ids.append(value)

    add((story_node.get("feedback") or {}).get("id"))

    for node in _iter_dicts(story_node):
        add(node.get("__id"))
        add(node.get("id"))

        feedback = node.get("feedback")
        if isinstance(feedback, dict):
            add(feedback.get("__id"))
            add(feedback.get("id"))

        target = node.get("feedback_target_with_context")
        if isinstance(target, dict):
            add(target.get("__id"))
            add(target.get("id"))

        renderer_feedback = (
            node.get("comet_ufi_summary_and_actions_renderer", {})
            .get("feedback")
        )
        if isinstance(renderer_feedback, dict):
            add(renderer_feedback.get("__id"))
            add(renderer_feedback.get("id"))

    return ids


def _extract_reaction_count_from_story_feedback_tree(story_node):
    for node in _iter_dicts(story_node):
        parsed = _extract_feedback_reaction_count_from_node(node)
        if parsed is not None:
            return parsed
    return None


def _extract_share_count_from_story_feedback_tree(story_node):
    for node in _iter_dicts(story_node):
        parsed = _extract_feedback_share_count_from_node(node)
        if parsed is not None:
            return parsed
    return None


def _resolve_story_reaction_count(story_node, feedback_reaction_count_by_id):
    reaction_count = _coerce_int(extract_reaction_count(story_node))
    feedback_ids = _extract_story_feedback_ids(story_node)
    used_detached_lookup = False

    if reaction_count in (None, 0):
        for feedback_id in feedback_ids:
            if feedback_id in feedback_reaction_count_by_id:
                reaction_count = feedback_reaction_count_by_id[feedback_id]
                used_detached_lookup = True
                break

    if reaction_count in (None, 0):
        fallback_count = _extract_reaction_count_from_story_feedback_tree(story_node)
        if fallback_count is not None:
            reaction_count = fallback_count

    if reaction_count is None:
        reaction_count = 0

    if reaction_count == 0 and WRITE_DEBUG_FILES:
        print(
            "  Debug reaction_count=0:"
            f" post_id={story_node.get('post_id')},"
            f" feedback_ids={len(feedback_ids)},"
            f" detached_lookup={'yes' if used_detached_lookup else 'no'}"
        )

    return reaction_count


def _resolve_story_share_count(story_node, feedback_share_count_by_id):
    share_count = _coerce_int(extract_share_count(story_node))

    if share_count in (None, 0):
        for feedback_id in _extract_story_feedback_ids(story_node):
            if feedback_id in feedback_share_count_by_id:
                share_count = feedback_share_count_by_id[feedback_id]
                break

    if share_count in (None, 0):
        fallback_count = _extract_share_count_from_story_feedback_tree(story_node)
        if fallback_count is not None:
            share_count = fallback_count

    return 0 if share_count is None else share_count


def _extract_feedback_share_count_from_node(node):
    if not isinstance(node, dict):
        return None

    renderer_feedback = (
        node.get("comet_ufi_summary_and_actions_renderer", {})
        .get("feedback")
    )
    parsed = _extract_feedback_share_count_from_node(renderer_feedback)
    if parsed is not None:
        return parsed

    for key in ("share_count", "share_count_reduced"):
        parsed = _coerce_int(_unwrap_count_value(node.get(key)))
        if parsed is not None:
            return parsed

    return None


def fetch_posts(
    limit=10,
    min_comments=0,
    batch_size=10,
    on_batch_complete=None,
    last_24_hours_only=False,
    min_posted_at=None,
    consecutive_old_limit=None,
    group_id=None,
    group_name=None,
    cookies=None,
    fb_dtsg=None,
    headers=None,
    proxies=None,
    download_media=True,
    skip_existing_posts=True,
    target_post_ids=None,
    stop_when_targets_found=False,
    on_page_diagnostic=None,
):
    """Fetch posts from Facebook group
    
    Args:
        limit: Maximum number of posts to fetch. Use None with last_24_hours_only=True to fetch all recent posts.
        min_comments: Minimum number of comments required for a post to be included (0 = no filter)
        batch_size: Number of posts to fetch before calling on_batch_complete callback
        on_batch_complete: Optional callback function(batch_posts, total_so_far, limit) called after each batch
        last_24_hours_only: Only include posts whose posted_at is within the last 24 hours.
        min_posted_at: When set, skip posts at or before this timestamp.
        consecutive_old_limit: Stop pagination after this many consecutive posts are at or before min_posted_at.
        group_id/group_name/cookies/fb_dtsg/headers/proxies: Optional per-run context.
            When omitted, module globals are used for backwards compatibility.
        download_media: Whether to download media files locally. Metadata is still extracted when False.
        skip_existing_posts: When True, skip posts already persisted on disk. Disable for metric refresh.
        target_post_ids: Optional post IDs to look for during metric refresh.
        stop_when_targets_found: Stop pagination once all target_post_ids have been seen.
        on_page_diagnostic: Optional callback receiving details for pages with no extracted posts.
    """
    request_group_id = group_id or GROUP_ID
    request_group_name = GROUP_NAME if group_name is None else group_name
    request_cookies = COOKIES if cookies is None else cookies
    request_fb_dtsg = FB_DTSG if fb_dtsg is None else fb_dtsg
    request_lsd = (headers or {}).get("x-fb-lsd") or HEADERS.get("x-fb-lsd") or LSD
    request_jazoest = JAZOEST
    request_proxies = PROXIES if proxies is None else proxies
    request_headers = dict(HEADERS)
    if headers:
        request_headers.update(headers)
    request_headers["referer"] = f"https://www.facebook.com/groups/{request_group_id}"
    request_headers["x-fb-friendly-name"] = GROUP_FEED_FRIENDLY_NAME
    if request_lsd:
        request_headers["x-fb-lsd"] = request_lsd

    all_posts = []
    batch_posts = []
    cursor = None
    page_num = 1
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=24) if last_24_hours_only else None
    min_posted_at_cutoff = _parse_iso_datetime(min_posted_at)
    consecutive_old_limit = int(consecutive_old_limit or 0)
    consecutive_old_count = 0
    stop_due_to_time = False
    stop_due_to_consecutive_old = False
    target_post_id_set = {str(post_id) for post_id in (target_post_ids or []) if post_id}
    matched_target_post_ids = set()
    stop_due_to_targets = False
    max_pages = int(os.getenv("SCRAPER_MAX_24H_PAGES", "100")) if last_24_hours_only else None
    page_size = int(os.getenv("SCRAPER_GROUP_PAGE_SIZE", "3"))
    if page_size <= 0:
        page_size = 3
    
    if min_comments > 0:
        print(f"Filtering posts with at least {min_comments} comments")
    
    if cutoff_time:
        print(f"Chỉ lấy bài đăng trong 24h gần nhất (từ {cutoff_time.isoformat()})")

    if limit is not None and batch_size > 0 and batch_size < limit:
        print(f"Processing in batches of {batch_size} posts")
    
    while limit is None or len(all_posts) < limit:
        if max_pages is not None and page_num > max_pages:
            print(f"Dat gioi han an toan so trang ({max_pages}) cho che do 24h. Dung lai. reason=max_pages")
            break
        print(f"\nĐang lấy trang {page_num}...")
        
        variables = {
            "count": page_size,
            "cursor": cursor,
            "feedLocation": "GROUP",
            "feedType": "DISCUSSION",
            "feedbackSource": 0,
            "filterTopicId": None,
            "focusCommentID": None,
            "privacySelectorRenderLocation": "COMET_STREAM",
            "referringStoryRenderLocation": None,
            "renderLocation": "group",
            "scale": 1,
            "sortingSetting": "CHRONOLOGICAL",
            "stream_initial_count": 1,
            "useDefaultActor": False,
            "groupID": request_group_id,
            "id": request_group_id,
            "__relay_internal__pv__GHLShouldChangeAdIdFieldNamerelayprovider": True,
            "__relay_internal__pv__GHLShouldChangeSponsoredDataFieldNamerelayprovider": True,
            "__relay_internal__pv__CometFeedStory_enable_reactor_facepilerelayprovider": False,
            "__relay_internal__pv__CometFeedStory_enable_post_permalink_white_space_clickrelayprovider": False,
            "__relay_internal__pv__CometUFICommentActionLinksRewriteEnabledrelayprovider": False,
            "__relay_internal__pv__CometUFICommentAvatarStickerAnimatedImagerelayprovider": False,
            "__relay_internal__pv__IsWorkUserrelayprovider": False,
            "__relay_internal__pv__TestPilotShouldIncludeDemoAdUseCaserelayprovider": False,
            "__relay_internal__pv__FBReels_deprecate_short_form_video_context_gkrelayprovider": True,
            "__relay_internal__pv__FBReels_enable_view_dubbed_audio_type_gkrelayprovider": True,
            "__relay_internal__pv__CometFeedShareMedia_shouldPrefetchShareImagerelayprovider": False,
            "__relay_internal__pv__CometImmersivePhotoCanUserDisable3DMotionrelayprovider": False,
            "__relay_internal__pv__WorkCometIsEmployeeGKProviderrelayprovider": False,
            "__relay_internal__pv__IsMergQAPollsrelayprovider": False,
            "__relay_internal__pv__FBReelsMediaFooter_comet_enable_reels_ads_gkrelayprovider": True,
            "__relay_internal__pv__CometUFIReactionsEnableShortNamerelayprovider": False,
            "__relay_internal__pv__CometUFICommentAutoTranslationTyperelayprovider": "AUTO_TRANSLATE",
            "__relay_internal__pv__CometUFIShareActionMigrationrelayprovider": True,
            "__relay_internal__pv__CometUFISingleLineUFIrelayprovider": True,
            "__relay_internal__pv__relay_provider_comet_ufi_ssr_seo_deferrelayprovider": True,
            "__relay_internal__pv__CometUFI_dedicated_comment_routable_dialog_gkrelayprovider": True,
            "__relay_internal__pv__ReelsIFUCard_reelsIFULikeCountrelayprovider": False,
            "__relay_internal__pv__FBReelsIFUTileContent_reelsIFUPlayOnHoverrelayprovider": True,
            "__relay_internal__pv__GroupsCometGYSJFeedItemHeightrelayprovider": 206,
            "__relay_internal__pv__ShouldEnableBakedInTextStoriesrelayprovider": False,
            "__relay_internal__pv__StoriesShouldIncludeFbNotesrelayprovider": True,
        }
        
        payload = {
            "av": request_cookies.get("c_user", "0"),
            "__aaid": "0",
            "__user": request_cookies.get("c_user", "0"),
            "__a": "1",
            "__req": "1i",
            "__comet_req": "15",
            "dpr": "1",
            "__ccg": "EXCELLENT",
            "fb_dtsg": request_fb_dtsg if request_fb_dtsg else "",
            "jazoest": request_jazoest,
            "lsd": request_lsd,
            "__crn": "comet.fbweb.CometGroupDiscussionRoute",
            "fb_api_caller_class": "RelayModern",
            "fb_api_req_friendly_name": GROUP_FEED_FRIENDLY_NAME,
            "server_timestamps": "true",
            "doc_id": DOC_ID,
            "variables": json.dumps(variables),
        }
        
        # Retry loop for empty response handling
        max_empty_retries = 3
        empty_retry_count = 0
        data = []
        
        while empty_retry_count < max_empty_retries:
            from backend.scraper import request_telemetry as telemetry

            try:
                r = retry_request(
                    GRAPHQL_URL,
                    request_headers,
                    payload,
                    request_proxies,
                    cookies=request_cookies,
                    scraper="group_posts",
                    endpoint_label="facebook_graphql",
                    facebook_id=request_group_id,
                    log_success=False,
                )
                r.raise_for_status()
            except requests.RequestException as e:
                print(f"Yeu cau that bai: {e}")
                break
            
            # Parse the response
            try:
                data = parse_fb_response(r.text)
            except Exception:
                telemetry.record_response(r, success=False, classification=telemetry.CLASS_PARSE_ERROR)
                raise
            
            if data and len(data) > 0:
                classification = telemetry.classify_response(getattr(r, "status_code", 200), r.text)
                telemetry.record_response(
                    r,
                    success=classification == telemetry.CLASS_SUCCESS,
                    classification=classification,
                )
                # Got valid data, break retry loop
                break
            else:
                telemetry.record_response(r, success=False, classification=telemetry.CLASS_EMPTY_RESPONSE)
                empty_retry_count += 1
                if empty_retry_count < max_empty_retries:
                    print(f"Phản hồi rỗng, đang thử lại ({empty_retry_count}/{max_empty_retries})...")
                    time.sleep(2)  # Wait before retry
                else:
                    print(f"Phản hồi rỗng sau {max_empty_retries} lần thử, bỏ qua trang")
        
        if not data or len(data) == 0:
            print("Không nhận được dữ liệu sau khi thử lại, dừng phân trang")
            break
        
        # Save raw response for debugging
        # with open(f"group_raw_page_{page_num}.json", "w", encoding="utf-8") as f:
        #     json.dump(data, f, ensure_ascii=False, indent=2)
        # print(f"Saved group_raw_page_{page_num}.json")
        
        # Build metric lookups by feedback id. Authenticated group responses
        # often return UFI counts in detached feedback blocks instead of Story nodes.
        feedback_reaction_count_by_id, feedback_share_count_by_id = _build_feedback_metric_lookups(data)

        # Extract posts from the response array
        posts_found = 0
        received_posts = 0
        filtered_by_latest_cutoff = 0
        next_cursor = None
        has_next_page = False
        response_summary = _summarize_group_response(data)
        
        for item in data:
            if not isinstance(item, dict):
                continue
            
            node = item.get('node')
            if not isinstance(node, dict):
                continue
            node_typename = node.get('__typename')
            
            # Collect Story nodes from multiple sources
            story_nodes = []
            
            # Direct Story node
            if node_typename == 'Story':
                story_nodes.append(node)
            
            # Story nodes inside Group edges
            elif node_typename == 'Group':
                edges = node.get('group_feed', {}).get('edges', [])
                for edge in edges:
                    if not isinstance(edge, dict):
                        continue
                    edge_node = edge.get('node')
                    if isinstance(edge_node, dict) and edge_node.get('__typename') == 'Story':
                        story_nodes.append(edge_node)

            # Some responses use timeline_list_feed_units shape (similar to page timeline)
            if "timeline_list_feed_units" in node:
                tl_edges = node.get("timeline_list_feed_units", {}).get("edges", [])
                for edge in tl_edges:
                    if not isinstance(edge, dict):
                        continue
                    edge_node = edge.get("node")
                    if isinstance(edge_node, dict) and edge_node.get("__typename") == "Story":
                        story_nodes.append(edge_node)
            
            # Process all found Story nodes
            for story_node in story_nodes:
                received_posts += 1
                temp_post_id = story_node.get('post_id')
                posted_at = extract_posted_at(story_node)
                posted_dt = _parse_iso_datetime(posted_at)

                if min_posted_at_cutoff and posted_dt:
                    if posted_dt <= min_posted_at_cutoff:
                        filtered_by_latest_cutoff += 1
                        consecutive_old_count += 1
                        print(
                            f"  Gặp post cũ hơn/equal latest cutoff:"
                            f" {temp_post_id} ({posted_at})"
                            f" consecutive_old={consecutive_old_count}/{consecutive_old_limit or '-'}"
                        )
                        if consecutive_old_limit > 0 and consecutive_old_count >= consecutive_old_limit:
                            stop_due_to_consecutive_old = True
                        continue
                    consecutive_old_count = 0

                if cutoff_time:
                    if not posted_dt:
                        print(f"  Skipping post {temp_post_id} vì không xác định được posted_at")
                        continue
                    if posted_dt < cutoff_time:
                        print(f"  Đã gặp post cũ hơn 24h: {temp_post_id} ({posted_at})")
                        stop_due_to_time = True
                        continue
                
                # Check comment count threshold
                comment_count = extract_comment_count(story_node)
                if min_comments > 0 and comment_count < min_comments:
                    print(f"Bỏ qua post chỉ có {comment_count} bình luận (can {min_comments}+)")
                    continue
                
                # Extract group name from first post if not set
                if not request_group_name:
                    request_group_name = extract_group_name(story_node)
                    if request_group_name:
                        print(f"Tên group: {request_group_name}")
                
                # Check if post already exists
                temp_group_name = request_group_name or extract_group_name(story_node)
                if skip_existing_posts and temp_group_name:
                    temp_name_folder = sanitize_group_folder_name(temp_group_name)
                    if post_already_exists(temp_post_id, "group_post", temp_name_folder):
                        print(f"Bỏ qua post đã scrape trước đó: {temp_post_id}")
                        continue
                
                # Prefer Story metrics; fallback to detached/nested feedback blocks.
                reaction_count = _resolve_story_reaction_count(story_node, feedback_reaction_count_by_id)
                share_count = _resolve_story_share_count(story_node, feedback_share_count_by_id)

                post_data = extract_post_data(
                    story_node,
                    request_group_name,
                    group_id=request_group_id,
                    cookies=request_cookies,
                    fb_dtsg=request_fb_dtsg,
                    proxies=request_proxies,
                    download_media=download_media,
                    reaction_count_override=reaction_count,
                    share_count_override=share_count,
                )
                if post_data:
                    post_data["group_id"] = request_group_id
                    batch_posts.append(post_data)
                    all_posts.append(post_data)
                    posts_found += 1
                    print(f"  - Tìm thấy post: {post_data['post_id']}")

                    post_id_text = str(post_data.get("post_id"))
                    if post_id_text in target_post_id_set:
                        matched_target_post_ids.add(post_id_text)
                        if (
                            stop_when_targets_found
                            and target_post_id_set
                            and len(matched_target_post_ids) >= len(target_post_id_set)
                        ):
                            stop_due_to_targets = True
                    
                    # Check if we should process this batch
                    if batch_size > 0 and len(batch_posts) >= batch_size and on_batch_complete:
                        total_label = limit if limit is not None else "24h"
                        print(f"Hoàn tất: {len(batch_posts)} posts. Total: {len(all_posts)}/{total_label}")
                        on_batch_complete(batch_posts, len(all_posts), limit)
                        batch_posts = []  # Reset batch
                    
                    if limit is not None and len(all_posts) >= limit:
                        break

                    if stop_due_to_targets:
                        break
            
            # Break outer loop if limit reached
            if limit is not None and len(all_posts) >= limit:
                break

            if stop_due_to_consecutive_old:
                break

            if stop_due_to_targets:
                break

            # Keep behavior aligned with page scraper: once an older post is seen,
            # stop pagination because feed is chronological.
            if stop_due_to_time:
                break
            
            # Look for pagination info (prefer Group -> group_feed.page_info)
            page_info = None
            if node_typename == 'Group':
                page_info = (node.get('group_feed') or {}).get('page_info')
            # Fallback: page-like timeline structure
            if not isinstance(page_info, dict):
                page_info = (node.get("timeline_list_feed_units") or {}).get("page_info")
            if not isinstance(page_info, dict):
                page_info = item.get('page_info')
            if isinstance(page_info, dict):
                candidate_has_next = bool(page_info.get('has_next_page'))
                candidate_cursor = page_info.get('end_cursor')
                # Keep the best available pagination signal seen in this page.
                has_next_page = has_next_page or candidate_has_next
                if candidate_cursor:
                    next_cursor = candidate_cursor

        # Last fallback: search page_info at top-level blocks if not found above.
        if not next_cursor:
            for item in data:
                if not isinstance(item, dict):
                    continue
                page_info = item.get("page_info")
                if isinstance(page_info, dict):
                    candidate_cursor = page_info.get("end_cursor")
                    candidate_has_next = bool(page_info.get("has_next_page"))
                    if candidate_cursor:
                        next_cursor = candidate_cursor
                    has_next_page = has_next_page or candidate_has_next
        
        if received_posts > 0 and posts_found == 0:
            print(
                f"Response chứa {received_posts} post nhưng 0 post được giữ lại sau bộ lọc"
                f"(filtered_by_latest_cutoff={filtered_by_latest_cutoff})"
            )
        else:
            print(
                f"Tìm thấy {posts_found} post mới trong trang này"
                f"(has_next_page={has_next_page}, next_cursor={'yes' if next_cursor else 'no'})"
            )

        if received_posts == 0:
            print(
                "Chuẩn đoán response:"
                f" blocks={response_summary['total_blocks']},"
                f" group_nodes={response_summary['group_nodes']},"
                f" group_feed_edges={response_summary['group_feed_edges']},"
                f" timeline_edges={response_summary['timeline_edges']},"
                f" story_nodes={response_summary['story_nodes']},"
                f" page_info_blocks={response_summary['page_info_blocks']}"
            )
            if response_summary["node_typenames"]:
                print(f"  node typenames: {response_summary['node_typenames']}")
            # if response_summary["top_level_keys"]:
            #     print(f"  top-level keys: {response_summary['top_level_keys']}")
            if response_summary["errors"]:
                for idx, err in enumerate(response_summary["errors"], start=1):
                    print(
                        f"  GraphQL error #{idx}: code={err.get('error')},"
                        f" summary={err.get('errorSummary')},"
                        f" description={err.get('errorDescription')},"
                        f" isNotCritical={err.get('isNotCritical')}"
                    )
            # if response_summary["group_nodes"] > 0 and response_summary["group_feed_edges"] == 0 and response_summary["story_nodes"] == 0:
            #     print(
            #         "  Nghi ngờ: response có Group node nhưng không có feed edges."
            #         " Thường do session không xem được nội dung group,"
            #         " user không còn là member, hoặc Facebook đã đổi schema query."
            #     )
            # elif response_summary["group_nodes"] == 0 and response_summary["story_nodes"] == 0:
            #     # print(
            #     #     "  Nghi ngờ: response không chứa Group/Story node."
            #     #     " Khả năng cao là payload/doc_id/schema không còn khớp."
            #     # )

            if WRITE_DEBUG_FILES:
                debug_dir = "logs"
                os.makedirs(debug_dir, exist_ok=True)
                raw_path = os.path.join(debug_dir, f"group_debug_page_{page_num}.txt")
                parsed_path = os.path.join(debug_dir, f"group_debug_page_{page_num}.json")
                with open(raw_path, "w", encoding="utf-8") as f:
                    f.write(r.text)
                with open(parsed_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print(f"  Đã lưu debug: {raw_path}, {parsed_path}")

        page_stop_reason = "next_page"
        if stop_due_to_time:
            page_stop_reason = "old_post"
        elif stop_due_to_consecutive_old:
            page_stop_reason = "consecutive_old"
        elif stop_due_to_targets:
            page_stop_reason = "targets_found"
        elif limit is not None and len(all_posts) >= limit:
            page_stop_reason = "limit"
        elif not has_next_page:
            page_stop_reason = "no_next_page"
        elif not next_cursor:
            page_stop_reason = "no_cursor"

        if received_posts == 0 and on_page_diagnostic:
            on_page_diagnostic(
                {
                    "page_num": page_num,
                    "posts_found": posts_found,
                    "received_posts": received_posts,
                    "filtered_by_latest_cutoff": filtered_by_latest_cutoff,
                    "consecutive_old_count": consecutive_old_count,
                    "consecutive_old_limit": consecutive_old_limit,
                    "has_next_page": has_next_page,
                    "next_cursor": bool(next_cursor),
                    "response_summary": response_summary,
                    "stop_reason": page_stop_reason,
                }
            )

        if stop_due_to_time:
            print("Đã gặp post cũ hơn 24h. Dừng phân trang.")
            break

        if stop_due_to_consecutive_old:
            print(
                "Đã gặp đủ số post cũ liên tiếp theo latest cutoff."
                f" Dừng phân trang. consecutive_old={consecutive_old_count}/{consecutive_old_limit}"
            )
            break

        if stop_due_to_targets:
            print(
                "Đã gặp đủ target posts. Dừng phân trang."
                f" matched_targets={len(matched_target_post_ids)}/{len(target_post_id_set)}"
            )
            break
        
        if limit is not None and len(all_posts) >= limit:
            print("Không còn trang hoặc đã đạt giới hạn. Dừng lại. reason=limit")
            break

        if not has_next_page or not next_cursor:
            if not has_next_page:
                print("Không còn trang hoặc đã đạt giới hạn. Dừng lại. reason=no_next_page")
            else:
                print("Không còn trang hoặc đã đạt giới hạn. Dừng lại. reason=no_cursor")
            break
        
        cursor = next_cursor
        page_num += 1
        time.sleep(2)  # Be nice to the server
    
    # Process any remaining posts in the final batch
    if batch_posts and on_batch_complete:
        total_label = limit if limit is not None else "24h"
        print(f"Lô cuối: {len(batch_posts)} posts. Total: {len(all_posts)}/{total_label}")
        on_batch_complete(batch_posts, len(all_posts), limit)
    
    return all_posts

# # log raw node cá»§a 1 post dÃ¹ng biá»‡t hiá»‡u Ä‘Äƒng bÃ i
# def log_raw_node_for_post(post_id_to_find):
#     posts = fetch_posts(limit=100)
#     for post in posts:
#         if post['post_id'] == post_id_to_find:
#             with open(f"raw_node_{post_id_to_find}.json", "w", encoding="utf-8") as f:
#                 json.dump(post, f, ensure_ascii=False, indent=2)
#             print(f"Raw node for post {post_id_to_find} saved to raw_node_{post_id_to_find}.json")
#             break
#     else:
#         print(f"Post with ID {post_id_to_find} not found.")

if __name__ == "__main__":
    # post_id = "2244771673046603"
    # log_raw_node_for_post(post_id)
    count = int(input("How many posts to fetch? "))
    
    print(f"\nFetching {count} posts from group {GROUP_ID}...")
    posts = fetch_posts(count)
    
    # Save posts to file
    with open("group_posts.json", "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False, indent=2)
    
    print(f"\n“ Saved {len(posts)} posts to group_posts.json")
    
    # Print summary
    print("\nSummary:")
    for i, post in enumerate(posts, 1):
        photos = len(post['photos'])
        videos = len(post['videos'])
        print(f"{i}. Post ID: {post['post_id']}")
        if photos:
            print(f"{photos} photo(s)")
        if videos:
            print(f"{videos} video(s)")
        if post['message']:
            preview = post['message'][:100] + '...' if len(post['message']) > 100 else post['message']
            print(f"   {preview}")
