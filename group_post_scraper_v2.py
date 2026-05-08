import requests
import json
import time
import os
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
    is_reel_or_video_post,
    extract_posted_at,
    extract_author,
    make_scraped_at,
)

GRAPHQL_URL = "https://www.facebook.com/api/graphql/"
WRITE_DEBUG_FILES = os.getenv("SCRAPER_WRITE_DEBUG_FILES", "true").lower() == "true"

# ========= CONFIG (FILL THESE) =========
GROUP_ID = "361726451351144"  # group id
GROUP_NAME = None  # Will be extracted automatically
DOC_ID = "25716860671307636"  # GroupsCometFeedRegularStoriesPaginationQuery

HEADERS = {
    "user-agent": "Mozilla/5.0",
    "content-type": "application/x-www-form-urlencoded",
    "origin": "https://www.facebook.com",
    "referer": f"https://www.facebook.com/groups/{GROUP_ID}/",
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
def retry_request(url, headers, data, proxies, cookies=None, max_retries=5):
    """Make a POST request with retry logic"""
    global PROXIES
    from proxy_utils import rotate_static_proxy, is_proxy_infra_error, is_ip_blocked

    request_cookies = COOKIES if cookies is None else cookies

    for attempt in range(1, max_retries + 1):
        try:
            r = requests.post(url, headers=headers, data=data, proxies=proxies, cookies=request_cookies, timeout=30)
            # dá»¯ liá»‡u group tráº£ vá»
            # with open(f"group_response_attempt_{attempt}.json", "w", encoding="utf-8") as f:
            #     f.write(r.text)
            #     print(f"lÆ°u thÃ nh cÃ´ng dá»¯ liá»‡u group")
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
        
        print(f"  ðŸ“¥ Downloaded image: {filename}")
        return filename
    
    except Exception as e:
        print(f"  âŒ Failed to download image: {str(e)}")
        return None


def fetch_remaining_images(
    last_media_id,
    post_id,
    current_image_count,
    save_dir="group_post",
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
    
    print(f"  ðŸ”„ Fetching remaining images after image #{current_image_count}...")
    
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
                saved_filename = download_image(image_url, post_id, image_index, save_dir)
                if saved_filename:
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
            print(f"  âš ï¸ Error fetching next image: {e}")
            break
    
    if remaining_photos:
        print(f"  âœ… Fetched {len(remaining_photos)} additional images")
    
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
    text = text.replace("for (;;);", "").strip()

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


def sanitize_group_folder_name(group_name):
    """Convert group name to a safe folder name."""
    if group_name:
        name_folder = "".join(c for c in group_name if c.isalnum() or c in (' ', '-', '_')).strip()
        if name_folder:
            return name_folder
    return "Unknown"


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
                    'thumbnail': single_media.get('preferred_thumbnail', {}).get('image', {}).get('uri')
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
                    'thumbnail': photo_data.get('preferred_thumbnail', {}).get('image', {}).get('uri')
                })

        # Handle video attachments from the direct attachment media shape
        if isinstance(attachment_media, dict) and attachment_media.get('__typename') == 'Video':
            video_data = attachment_media
            media['videos'].append({
                'id': video_data.get('id'),
                'url': video_data.get('playable_url'),
                'thumbnail': video_data.get('preferred_thumbnail', {}).get('image', {}).get('uri')
            })
    
    # Fetch remaining images if we have exactly 5 photos (indicating there may be more)
    if download_media and image_index == 5 and last_media_id:
        remaining_photos = fetch_remaining_images(
            last_media_id,
            post_id,
            image_index,
            save_dir,
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


def extract_post_data(node, group_name=None, group_id=None, cookies=None, fb_dtsg=None, proxies=None, download_media=True):
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
    
    # Extract share count
    share_count = extract_share_count(node)
    
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
    
    if WRITE_DEBUG_FILES:
        # Save individual post to folder structure: group_post/{group_name}/{post_id}/{post_id}.json
        post_dir = os.path.join("group_post", name_folder, str(post_id))
        os.makedirs(post_dir, exist_ok=True)
        post_file = os.path.join(post_dir, f"{post_id}.json")
        with open(post_file, "w", encoding="utf-8") as f:
            json.dump(post_data, f, ensure_ascii=False, indent=2)
        print(f"âœ“ Saved to {post_file}")
    
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


def fetch_posts(
    limit=10,
    min_comments=0,
    batch_size=10,
    on_batch_complete=None,
    last_24_hours_only=False,
    group_id=None,
    group_name=None,
    cookies=None,
    fb_dtsg=None,
    headers=None,
    proxies=None,
    download_media=True,
):
    """Fetch posts from Facebook group
    
    Args:
        limit: Maximum number of posts to fetch. Use None with last_24_hours_only=True to fetch all recent posts.
        min_comments: Minimum number of comments required for a post to be included (0 = no filter)
        batch_size: Number of posts to fetch before calling on_batch_complete callback
        on_batch_complete: Optional callback function(batch_posts, total_so_far, limit) called after each batch
        last_24_hours_only: Only include posts whose posted_at is within the last 24 hours.
        group_id/group_name/cookies/fb_dtsg/headers/proxies: Optional per-run context.
            When omitted, module globals are used for backwards compatibility.
        download_media: Whether to download media files locally. Metadata is still extracted when False.
    """
    global GROUP_NAME
    use_global_group_name = group_id is None and group_name is None
    request_group_id = group_id or GROUP_ID
    request_group_name = GROUP_NAME if group_name is None else group_name
    request_cookies = COOKIES if cookies is None else cookies
    request_fb_dtsg = FB_DTSG if fb_dtsg is None else fb_dtsg
    request_proxies = PROXIES if proxies is None else proxies
    request_headers = dict(HEADERS if headers is None else headers)
    request_headers["referer"] = f"https://www.facebook.com/groups/{request_group_id}/"

    all_posts = []
    batch_posts = []
    cursor = None
    page_num = 1
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=24) if last_24_hours_only else None
    stop_due_to_time = False
    max_pages = int(os.getenv("SCRAPER_MAX_24H_PAGES", "100")) if last_24_hours_only else None
    page_size = int(os.getenv("SCRAPER_GROUP_PAGE_SIZE", "10"))
    if page_size <= 0:
        page_size = 10
    
    if min_comments > 0:
        print(f"ðŸ“Š Filtering posts with at least {min_comments} comments")
    
    if cutoff_time:
        print(f"Chỉ lấy bài đăng trong 24h gần nhất (từ {cutoff_time.isoformat()})")

    if limit is not None and batch_size > 0 and batch_size < limit:
        print(f"ðŸ“¦ Processing in batches of {batch_size} posts")
    
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
            "sortingSetting": "CHRONOLOGICAL",
            "feedbackSource": 0,
            "filterTopicId": None,
            "focusCommentID": None,
            "privacySelectorRenderLocation": "COMET_STREAM",
            "renderLocation": "group",
            "scale": 2,
            #"sortingSetting": "TOP_POSTS",
            "stream_initial_count": 1,
            "useDefaultActor": False,
            "id": request_group_id,
        }
        
        payload = {
            "av": request_cookies.get("c_user", "0"),
            "__user": request_cookies.get("c_user", "0"),
            "__a": "1",
            "fb_dtsg": request_fb_dtsg if request_fb_dtsg else "",
            "doc_id": DOC_ID,
            "variables": json.dumps(variables),
        }
        
        # Retry loop for empty response handling
        max_empty_retries = 3
        empty_retry_count = 0
        data = []
        
        while empty_retry_count < max_empty_retries:
            try:
                r = retry_request(
                    GRAPHQL_URL,
                    request_headers,
                    payload,
                    request_proxies,
                    cookies=request_cookies,
                )
                r.raise_for_status()
            except requests.RequestException as e:
                print(f"Yeu cau that bai: {e}")
                break
            
            # Parse the response
            data = parse_fb_response(r.text)
            
            if data and len(data) > 0:
                # Got valid data, break retry loop
                break
            else:
                empty_retry_count += 1
                if empty_retry_count < max_empty_retries:
                    print(f"  âš ï¸ Phan hoi rong, dang thu lai ({empty_retry_count}/{max_empty_retries})...")
                    time.sleep(2)  # Wait before retry
                else:
                    print(f"  âŒ Phan hoi rong sau {max_empty_retries} lan thu, bo qua trang")
        
        if not data or len(data) == 0:
            print("âŒ Khong nhan duoc du lieu sau khi thu lai, dung phan trang")
            break
        
        # Save raw response for debugging
        # with open(f"group_raw_page_{page_num}.json", "w", encoding="utf-8") as f:
        #     json.dump(data, f, ensure_ascii=False, indent=2)
        # print(f"Saved group_raw_page_{page_num}.json")
        
        # Extract posts from the response array
        posts_found = 0
        next_cursor = None
        has_next_page = False
        
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
                # Skip reels and video posts
                if is_reel_or_video_post(story_node):
                    print(f"Bỏ qua bài reel/video")
                    continue

                temp_post_id = story_node.get('post_id')
                posted_at = extract_posted_at(story_node)
                posted_dt = _parse_iso_datetime(posted_at)

                if cutoff_time:
                    if not posted_dt:
                        print(f"  Skipping post {temp_post_id} vi khong xac dinh duoc posted_at")
                        continue
                    if posted_dt < cutoff_time:
                        print(f"  Đã gặp post cũ hơn 24h: {temp_post_id} ({posted_at})")
                        stop_due_to_time = True
                        continue
                
                # Check comment count threshold
                comment_count = extract_comment_count(story_node)
                if min_comments > 0 and comment_count < min_comments:
                    print(f"  â­ï¸  Bo qua post chi co {comment_count} binh luan (can {min_comments}+)")
                    continue
                
                # Extract group name from first post if not set
                if not request_group_name:
                    request_group_name = extract_group_name(story_node)
                    if use_global_group_name:
                        GROUP_NAME = request_group_name
                    if request_group_name:
                        print(f"Tên group: {request_group_name}")
                
                # Check if post already exists
                temp_group_name = request_group_name or extract_group_name(story_node)
                if temp_group_name:
                    temp_name_folder = sanitize_group_folder_name(temp_group_name)
                    if post_already_exists(temp_post_id, "group_post", temp_name_folder):
                        print(f"  â­ï¸  Bo qua post da scrape truoc do: {temp_post_id}")
                        continue
                
                post_data = extract_post_data(
                    story_node,
                    request_group_name,
                    group_id=request_group_id,
                    cookies=request_cookies,
                    fb_dtsg=request_fb_dtsg,
                    proxies=request_proxies,
                    download_media=download_media,
                )
                if post_data:
                    post_data["group_id"] = request_group_id
                    batch_posts.append(post_data)
                    all_posts.append(post_data)
                    posts_found += 1
                    print(f"  - Tìm thấy post: {post_data['post_id']}")
                    
                    # Check if we should process this batch
                    if batch_size > 0 and len(batch_posts) >= batch_size and on_batch_complete:
                        total_label = limit if limit is not None else "24h"
                        print(f"\nðŸ“¦ Hoan tat lo: {len(batch_posts)} posts. Total: {len(all_posts)}/{total_label}")
                        on_batch_complete(batch_posts, len(all_posts), limit)
                        batch_posts = []  # Reset batch
                    
                    if limit is not None and len(all_posts) >= limit:
                        break
            
            # Break outer loop if limit reached
            if limit is not None and len(all_posts) >= limit:
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
        
        print(
            f"Tìm thấy {posts_found} post trong trang này"
            f"(has_next_page={has_next_page}, next_cursor={'yes' if next_cursor else 'no'})"
        )

        if stop_due_to_time:
            print("Đã gặp post cũ hơn 24h. Dừng phân trang.")
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
        print(f"\nðŸ“¦ Lo cuoi: {len(batch_posts)} posts. Total: {len(all_posts)}/{total_label}")
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
    
    print(f"\nâœ“ Saved {len(posts)} posts to group_posts.json")
    
    # Print summary
    print("\nSummary:")
    for i, post in enumerate(posts, 1):
        photos = len(post['photos'])
        videos = len(post['videos'])
        print(f"{i}. Post ID: {post['post_id']}")
        if photos:
            print(f"   ðŸ“· {photos} photo(s)")
        if videos:
            print(f"   ðŸŽ¥ {videos} video(s)")
        if post['message']:
            preview = post['message'][:100] + '...' if len(post['message']) > 100 else post['message']
            print(f"   {preview}")

