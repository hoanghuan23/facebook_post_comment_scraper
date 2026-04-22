import requests
import json
import time
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

GRAPHQL = "https://www.facebook.com/api/graphql/"

# Base headers for all requests
BASE_HEADERS = {
    "user-agent": "Mozilla/5.0",
    "content-type": "application/x-www-form-urlencoded"
}

# Get proxy configuration
PROXY = os.getenv('PROXY')
PROXIES = {'http': PROXY, 'https': PROXY} if PROXY else None

# FB_DTSG token (set by UI when provided)
FB_DTSG = ""

if PROXY:
    print(f"Using proxy: {PROXY}")

# ========= RETRY HELPER =========
def retry_request(url, headers, data, proxies, cookies=None, max_retries=5):
    """Make a POST request with retry logic"""
    global PROXIES
    from proxy_utils import rotate_static_proxy, is_proxy_infra_error, is_ip_blocked

    for attempt in range(1, max_retries + 1):
        try:
            r = requests.post(url, headers=headers, data=data, proxies=proxies, cookies=cookies, timeout=30)
            if r.status_code == 200:
                return r
            if is_proxy_infra_error(status_code=r.status_code):
                print(f"  🚫 Attempt {attempt}/{max_retries}: Proxy auth failed (HTTP {r.status_code}) — rotating static proxy...")
                new_p = rotate_static_proxy()
                if new_p:
                    proxies = new_p
                    PROXIES = new_p
            elif is_ip_blocked(status_code=r.status_code, response_text=r.text):
                print(f"  🛑 Attempt {attempt}/{max_retries}: Facebook blocked this IP (HTTP {r.status_code}) — rotating static proxy...")
                new_p = rotate_static_proxy()
                if new_p:
                    proxies = new_p
                    PROXIES = new_p
            else:
                print(f"  ⚠️ Attempt {attempt}/{max_retries}: Status {r.status_code}")
        except requests.exceptions.ProxyError as e:
            print(f"  🚫 Attempt {attempt}/{max_retries}: Proxy unreachable — rotating static proxy...")
            new_p = rotate_static_proxy()
            if new_p:
                proxies = new_p
                PROXIES = new_p
        except Exception as e:
            if is_proxy_infra_error(exc=e):
                print(f"  🚫 Attempt {attempt}/{max_retries}: Proxy connection error — rotating static proxy...")
                new_p = rotate_static_proxy()
                if new_p:
                    proxies = new_p
                    PROXIES = new_p
            else:
                print(f"  ⚠️ Attempt {attempt}/{max_retries}: {str(e)}")

        if attempt < max_retries:
            wait_time = attempt * 2
            print(f"  ⏳ Retrying in {wait_time} seconds...")
            time.sleep(wait_time)

    raise Exception(f"Failed after {max_retries} attempts")

# ===== PAYLOADS =====

def comments_payload(feedback_id, cursor=None, cookies=None):
    # Extract user ID from cookies if available
    user_id = "0"
    if cookies and "c_user" in cookies:
        user_id = cookies["c_user"]
    
    return {
        "av": user_id,
        "__user": user_id,
        "__a": "1",
        "fb_dtsg": FB_DTSG if FB_DTSG else "",
        "doc_id": "25550760954572974",
        "variables": json.dumps({
            "commentsAfterCount": -1,
            "commentsAfterCursor": cursor,
            "commentsIntentToken": "REVERSE_CHRONOLOGICAL_UNFILTERED_INTENT_V1",
            "feedLocation": "DEDICATED_COMMENTING_SURFACE",
            "focusCommentID": None,
            "scale": 2,
            "useDefaultActor": False,
            "id": feedback_id
        })
    }


def replies_payload(comment_feedback_id, expansion_token, cookies=None):
    # Extract user ID from cookies if available
    user_id = "0"
    if cookies and "c_user" in cookies:
        user_id = cookies["c_user"]
    
    return {
        "av": user_id,
        "__user": user_id,
        "__a": "1",
        "fb_dtsg": FB_DTSG if FB_DTSG else "",
        "doc_id": "26570577339199586",
        "variables": json.dumps({
            "clientKey": None,
            "expansionToken": expansion_token,
            "feedLocation": "POST_PERMALINK_DIALOG",
            "focusCommentID": None,
            "scale": 2,
            "useDefaultActor": False,
            "id": comment_feedback_id
        })
    }

# ===== FETCH COMMENTS =====
import json

def fb_json(response_text):
    """
    Facebook GraphQL sometimes returns:
    for (;;);
    {json}
    {json}

    This extracts the first valid JSON object safely.
    """
    text = response_text.strip()

    # Remove for (;;);
    if text.startswith("for (;;);"):
        text = text[len("for (;;);"):]

    # Keep only first JSON object
    first = text.split("\n")[0].strip()

    return json.loads(first)


def fetch_comments(feedback_id, cookies=None):
    results = []
    cursor = None
    response_count = 0

    def extract_post_reaction_count(feedback):
        """Extract post reaction count from feedback object across schemas."""
        if not isinstance(feedback, dict):
            return "0"

        candidates = [
            feedback,
            feedback.get("comet_ufi_summary_and_actions_renderer", {}).get("feedback", {}),
            feedback.get("if_viewer_cannot_see_seen_by_member_list", {}),
        ]

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue

            reaction_count = candidate.get("reaction_count", {})
            if isinstance(reaction_count, dict):
                value = reaction_count.get("count")
                while isinstance(value, dict):
                    value = value.get("count")
                if value is not None:
                    return str(value)

            reactors = candidate.get("reactors", {})
            if isinstance(reactors, dict):
                value = reactors.get("count_reduced")
                if value is not None:
                    return str(value)

        return "0"

    def extract_post_comment_count(feedback):
        """Extract post comment count from feedback object across schemas."""
        if not isinstance(feedback, dict):
            return 0

        candidates = [
            feedback,
            feedback.get("comet_ufi_summary_and_actions_renderer", {}).get("feedback", {}),
            feedback.get("comments_count_summary_renderer", {}).get("feedback", {}),
        ]

        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue

            count = (
                candidate.get("comment_rendering_instance", {})
                .get("comments", {})
                .get("total_count")
            )
            if count is not None:
                return count

            count = (
                candidate.get("comments_count_summary_renderer", {})
                .get("feedback", {})
                .get("comment_rendering_instance", {})
                .get("comments", {})
                .get("total_count")
            )
            if count is not None:
                return count

        return 0
    post_info = None  # Store parent post info from first response

    while True:
        headers = {**BASE_HEADERS, "x-fb-friendly-name": "CommentsListComponentsPaginationQuery"}
        r = retry_request(
            GRAPHQL,
            headers,
            comments_payload(feedback_id, cursor, cookies),
            PROXIES,
            cookies=cookies
        )
        j = fb_json(r.text)
        
        # Save each JSON response for inspection
        response_count += 1
        # with open(f"response_{response_count}.json", "w", encoding="utf-8") as f:
        #     json.dump(j, f, ensure_ascii=False, indent=2)
        # print(f"💾 Saved response_{response_count}.json")
        
        comments_block = (
            j.get("data", {})
             .get("node", {})
             .get("comment_rendering_instance_for_feed_location", {})
             .get("comments", {})
        )

        edges = comments_block.get("edges", [])
        if not edges:
            break

        for e in edges:
            n = e["node"]
            fb = n["feedback"]

            # Extract parent_post_story info from first response
            if response_count == 1 and post_info is None:
                parent_post_story = n.get("parent_post_story", {})
                
                if parent_post_story:
                    # Extract post reaction count
                    post_feedback = parent_post_story.get("feedback", {})
                    post_reaction_count = extract_post_reaction_count(post_feedback)
                    post_comment_count = extract_post_comment_count(post_feedback)
                    
                    post_info = {
                        "post_story_id": parent_post_story.get("id"),
                        "media_id": None,
                        "comment_count": post_comment_count,
                        "reaction_count": post_reaction_count
                    }
                    
                    # Extract first media ID
                    attachments = parent_post_story.get("attachments", [])
                    for attachment in attachments:
                        media = attachment.get("media", {})
                        if media and media.get("id"):
                            post_info["media_id"] = media.get("id")
                            break  # Only get first one
                    
                    print(f"📎 Extracted post info: {post_info}")

            # Extract reaction count for comment
            reactors = fb.get("reactors", {})
            total_reactions = reactors.get("count_reduced", "0")
            
            results.append({
                # "comment_id": n["legacy_fbid"],
                # "author": n["author"]["name"],
                "text": (n.get("body") or {}).get("text", ""),
                "reaction_count": total_reactions,
                "_feedback_id": fb["id"],  # Internal use only (for fetching replies)
                "_expansion_token": fb["expansion_info"]["expansion_token"]  # Internal use only
            })

        cursor = comments_block.get("page_info", {}).get("end_cursor")
        #break
        if not cursor:
            break

        #time.sleep(0.4)

    return results, post_info

# ===== FETCH REPLIES =====

def fetch_replies(comment, cookies=None):
    headers = {**BASE_HEADERS, "x-fb-friendly-name": "Depth1CommentsListPaginationQuery"}
    r = retry_request(
        GRAPHQL,
        headers,
        replies_payload(comment["_feedback_id"], comment["_expansion_token"], cookies),
        PROXIES,
        cookies=cookies
    )

    j = fb_json(r.text)
    replies = []

    edges = (
        j.get("data", {})
         .get("node", {})
         .get("replies_connection", {})
         .get("edges", [])
    )

    for e in edges:
        n = e["node"]
        fb = n.get("feedback", {})
        
        # Extract reaction count
        reactors = fb.get("reactors", {})
        total_reactions = reactors.get("count_reduced", "0")
        
        replies.append({
            # "reply_id": n["legacy_fbid"],
            # "author": n["author"]["name"],
            "text": (n.get("body") or {}).get("text", ""),
            "reaction_count": total_reactions
        })

    return replies

# ===== RUN =====

if __name__ == "__main__":
    POST_FEEDBACK_ID = "ZmVlZGJhY2s6MTg3NDE2NTYxMzI0NjAwMw=="
    POST_ID = "1420269302790428"  # The actual post ID

    all_data = []

    comments, post_info = fetch_comments(POST_FEEDBACK_ID)
    
    # Add post info to the output
    output = {
        "post_info": post_info,
        "comments": []
    }

    for c in comments:
        # print(f"\n🗨️ {c['author']}: {c['text']}")
        c["replies"] = fetch_replies(c)

        # for r in c["replies"]:
        #     print(f"   ↳ {r['author']}: {r['text']}")

        output["comments"].append(c)

    # Create directory for this post
    os.makedirs(f"simple_post/{POST_ID}", exist_ok=True)
    
    # Save as {post_id}.json
    output_file = f"simple_post/{POST_ID}/{POST_ID}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"💬 Saved to {output_file}")

