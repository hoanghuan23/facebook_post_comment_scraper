from __future__ import annotations

import base64
import json
import logging
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("facebook_scraper")


HEADERS_DESKTOP = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

HEADERS_MOBILE = {
    **HEADERS_DESKTOP,
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 12; Pixel 6) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Mobile Safari/537.36"
    ),
}

RATE_LIMIT_SIGNALS = [
    "temporarily blocked",
    "you're blocked",
    "checkpoint",
    "please try again later",
    "security check",
    "we limit how often",
]

LOGIN_SIGNALS = [
    "login_form",
    'id="email"',
    'name="email"',
    "Log into Facebook",
]

NOT_FOUND_SIGNALS = [
    "content isn't available",
    "this page isn't available",
    "sorry, something went wrong",
]


@dataclass
class DirectPostMetric:
    post_id: str
    source_url: str
    likes: int = 0
    comments: int = 0
    shares: int = 0
    is_rate_limited: bool = False
    is_login_required: bool = False
    is_not_found: bool = False
    has_metric_signal: bool = False
    fetch_method: str = ""
    raw_snippet: str = ""
    error_message: Optional[str] = None


def parse_cookies(raw: Optional[str]) -> dict[str, str]:
    if not raw:
        return {}

    raw = str(raw).strip()
    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return {
                str(item["name"]): str(item.get("value", ""))
                for item in parsed
                if isinstance(item, dict) and item.get("name")
            }

    if raw.startswith("{"):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return {str(key): str(value) for key, value in parsed.items()}

    cookies = {}
    for part in raw.split(";"):
        item = part.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        if key:
            cookies[key] = value.strip()
    return cookies


def build_session(cookies_raw: Optional[str], mobile: bool = False) -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS_MOBILE if mobile else HEADERS_DESKTOP)

    cookie_dict = parse_cookies(cookies_raw)
    if not cookie_dict:
        return session

    for domain in [".facebook.com", "www.facebook.com", "m.facebook.com", "mbasic.facebook.com"]:
        for key, value in cookie_dict.items():
            session.cookies.set(key, value, domain=domain)
    return session


def parse_number(text: str) -> int:
    if not text:
        return 0
    text = str(text).strip()

    match = re.search(r"(\d+[.,]\d+)\s*([KkMmTtBb])", text)
    if match:
        number = float(match.group(1).replace(",", "."))
        suffix = match.group(2).upper()
        return int(number * {"K": 1e3, "T": 1e3, "M": 1e6, "B": 1e9}.get(suffix, 1))

    match = re.search(r"(\d[\d,]*)\s*([KkMmTtBb])\b", text)
    if match:
        number = int(match.group(1).replace(",", ""))
        suffix = match.group(2).upper()
        return int(number * {"K": 1e3, "T": 1e3, "M": 1e6, "B": 1e9}.get(suffix, 1))

    match = re.search(r"(\d[\d,]*)", text)
    return int(match.group(1).replace(",", "")) if match else 0


def first_count(pattern: str, text: str) -> tuple[bool, int]:
    match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    if not match:
        return False, 0
    return True, parse_number(match.group(1))


def max_count(pattern: str, text: str) -> tuple[bool, int]:
    found = False
    values = []
    for match in re.finditer(pattern, text, re.IGNORECASE | re.DOTALL):
        found = True
        values.append(parse_number(match.group(1)))
    return found, max(values, default=0)


def feedback_id_for_post(post_id: str) -> str:
    return base64.b64encode(f"feedback:{post_id}".encode("utf-8")).decode("ascii")


def detect_html_state(html_text: str) -> str:
    lowered = (html_text or "").lower()
    if any(signal.lower() in lowered for signal in RATE_LIMIT_SIGNALS):
        return "rate_limited"
    if any(signal.lower() in lowered for signal in LOGIN_SIGNALS):
        return "login"
    if any(signal.lower() in lowered for signal in NOT_FOUND_SIGNALS):
        return "not_found"
    return "ok"


def response_text(response: requests.Response) -> str:
    text = response.text or ""
    sample = text[:2000]
    replacement_count = sample.count("\ufffd")
    has_nul = "\x00" in sample
    has_html_signal = any(marker in sample.lower() for marker in ("<html", "<!doctype", "__bbox", "facebook"))
    if (has_nul or replacement_count > 20) and not has_html_signal:
        logger.warning(
            "Direct metric response decode looks invalid: content_encoding=%s",
            response.headers.get("Content-Encoding", "none"),
        )
    return text


def _snippet(chunk: str, *needles: str) -> str:
    positions = [chunk.find(needle) for needle in needles if needle and chunk.find(needle) >= 0]
    start = min(positions) if positions else 0
    return re.sub(r"\s+", " ", chunk[start : start + 500])


def parse_unified_video_metrics(html: str, video_id: str) -> tuple[bool, int, int, int, str]:
    best: tuple[bool, int, int, int, str] = (False, 0, 0, 0, "")
    best_score = -1

    for label in ["FBUnifiedVideoFeedbackBar_feedback", "FBReelsFeedbackBar_feedback"]:
        for match in re.finditer(label, html, re.IGNORECASE):
            start = max(0, match.start() - 1200)
            end = min(len(html), match.start() + 15000)
            chunk = html[start:end]

            exact_video = bool(
                re.search(
                    rf'"video"\s*:\s*\{{\s*"id"\s*:\s*"{re.escape(video_id)}"\s*\}}',
                    chunk,
                    re.IGNORECASE,
                )
            )
            exact_media = bool(
                re.search(
                    rf'"media"\s*:\s*\{{[^{{}}]{{0,900}}"id"\s*:\s*"{re.escape(video_id)}"',
                    chunk,
                    re.IGNORECASE | re.DOTALL,
                )
            )
            exact_url = bool(re.search(rf"reel\\?/{re.escape(video_id)}", chunk, re.IGNORECASE))
            exact_tracking = bool(
                re.search(
                    rf'\\"(?:post_id|top_level_post_id)\\"\\s*:\\s*\\"{re.escape(video_id)}\\"',
                    chunk,
                    re.IGNORECASE,
                )
            )
            if not (exact_video or exact_media or exact_url or exact_tracking):
                continue

            found_likers, likers = first_count(r'"likers"\s*:\s*\{"count"\s*:\s*(\d+)', chunk)
            found_reactors, reactors = first_count(r'"unified_reactors"\s*:\s*\{"count"\s*:\s*(\d+)', chunk)
            found_comments, comments = first_count(r'"total_comment_count"\s*:\s*(\d+)', chunk)
            found_shares, shares = first_count(r'"share_count_reduced"\s*:\s*"([^"]+)"', chunk)
            if not found_shares:
                found_shares, shares = first_count(r'"share_count"\s*:\s*\{"count"\s*:\s*(\d+)', chunk)

            has_signal = found_likers or found_reactors or found_comments or found_shares
            if not has_signal:
                continue

            likes = max(likers, reactors)
            score = int(likes > 0) + int(comments > 0) + int(shares > 0)
            score *= 100
            if exact_video:
                score += 80
            if exact_media:
                score += 50
            if exact_url:
                score += 30
            if exact_tracking:
                score += 20

            if score > best_score:
                best = (
                    True,
                    likes,
                    comments,
                    shares,
                    _snippet(chunk, label, '"unified_reactors"', '"total_comment_count"'),
                )
                best_score = score

    return best


def parse_targeted_comet_metrics(html: str, post_id: str) -> tuple[bool, int, int, int, str]:
    feedback_id = feedback_id_for_post(post_id)
    anchors: list[tuple[int, int]] = []
    anchor_patterns = [
        (rf'"subscription_target_id"\s*:\s*"{re.escape(post_id)}"', 100),
        (rf'"post_id"\s*:\s*"{re.escape(post_id)}"', 80),
        (rf'\\"post_id\\"\s*:\s*\\"{re.escape(post_id)}\\"', 80),
        (rf'"share_fbid"\s*:\s*"{re.escape(post_id)}"', 60),
        (rf'"video_id"\s*:\s*"{re.escape(post_id)}"', 90),
        (rf'\\"video_id\\"\s*:\s*\\"{re.escape(post_id)}\\"', 90),
        (rf'\\"top_level_post_id\\"\s*:\s*\\"{re.escape(post_id)}\\"', 120),
        (rf'"root_video_id"\s*:\s*"{re.escape(post_id)}"', 100),
        (rf'"initial_node_id"\s*:\s*"{re.escape(post_id)}"', 100),
        (rf'"shareable_url"\s*:\s*"https:\\/\\/www\.facebook\.com\\/reel\\/{re.escape(post_id)}', 100),
        (re.escape(feedback_id), 50),
        (rf'groups\\?/[^"\\]+\\?/posts\\?/{re.escape(post_id)}', 40),
        (rf'groups\\?/[^"\\]+\\?/permalink\\?/{re.escape(post_id)}', 40),
    ]

    for pattern, weight in anchor_patterns:
        for match in re.finditer(pattern, html, re.IGNORECASE):
            anchors.append((match.start(), weight))

    if not anchors:
        title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        page_title = title_match.group(1).strip() if title_match else ""
        if page_title and page_title.lower() != "facebook":
            for match in re.finditer(r'"comet_ufi_summary_and_actions_renderer"\s*:', html, re.IGNORECASE):
                anchors.append((match.start(), 10))

    best: tuple[bool, int, int, int, str] = (False, 0, 0, 0, "")
    best_score = -1
    for position, weight in anchors:
        start = max(0, position - 9000)
        end = min(len(html), position + 30000)
        chunk = html[start:end]

        like_matches = [
            first_count(r'"reaction_count"\s*:\s*\{"count"\s*:\s*(\d+)', chunk),
            first_count(r'"unified_reactors"\s*:\s*\{"count"\s*:\s*(\d+)', chunk),
            first_count(r'"reactors"\s*:\s*\{"count"\s*:\s*(\d+)', chunk),
            first_count(r'"likers"\s*:\s*\{"count"\s*:\s*(\d+)', chunk),
        ]
        found_likes = any(found for found, _ in like_matches)
        likes = max(value for _, value in like_matches)
        if likes == 0:
            top_reaction_totals = []
            for match in re.finditer(
                r'"top_reactions"\s*:\s*\{"edges"\s*:\s*\[(.*?)\]\}',
                chunk,
                re.IGNORECASE | re.DOTALL,
            ):
                total = sum(parse_number(value) for value in re.findall(r'"reaction_count"\s*:\s*(\d+)', match.group(1)))
                if total:
                    top_reaction_totals.append(total)
            if top_reaction_totals:
                found_likes = True
                likes = max(top_reaction_totals)

        comment_matches = [
            first_count(
                r'"comment_rendering_instance"\s*:\s*\{"comments"\s*:\s*\{"total_count"\s*:\s*(\d+)',
                chunk,
            ),
            first_count(r'"comments"\s*:\s*\{[^{}]{0,500}"total_count"\s*:\s*(\d+)', chunk),
            first_count(r'"total_comment_count"\s*:\s*(\d+)', chunk),
            first_count(r'\\"total_comment_count\\"\s*:\s*(\d+)', chunk),
            first_count(r'"comment_count"\s*:\s*(\d+)', chunk),
            first_count(r'\\"comment_count\\"\s*:\s*(\d+)', chunk),
            first_count(r'"aggregated_comment_count"\s*:\s*(\d+)', chunk),
            first_count(r'\\"aggregated_comment_count\\"\s*:\s*(\d+)', chunk),
        ]
        found_comments = any(found for found, _ in comment_matches)
        comments = max(value for _, value in comment_matches)

        share_matches = [
            first_count(r'"share_count"\s*:\s*\{"count"\s*:\s*(\d+)', chunk),
            first_count(r'"share_count_reduced"\s*:\s*"([^"]+)"', chunk),
            first_count(r'\\"share_count_reduced\\"\s*:\s*\\"([^"\\]+)', chunk),
            first_count(r'"i18n_share_count"\s*:\s*"([^"]+)"', chunk),
        ]
        found_shares = any(found for found, _ in share_matches)
        shares = max(value for _, value in share_matches)

        has_signal = found_likes or found_comments or found_shares
        if not has_signal:
            continue

        score = weight + (int(likes > 0) + int(comments > 0) + int(shares > 0)) * 100
        if feedback_id in chunk:
            score += 40
        if f'"subscription_target_id":"{post_id}"' in chunk:
            score += 40
        if f'\\"top_level_post_id\\":\\"{post_id}\\"' in chunk:
            score += 80
        if f'\\"video_id\\":\\"{post_id}\\"' in chunk or f'"video_id":"{post_id}"' in chunk:
            score += 40
        if "fb_reel_react_button" in chunk:
            score += 30
        if "comment_id=" in chunk or '"comment_id"' in chunk:
            score -= 20

        if score > best_score:
            snippet_start = -1
            for needle in (
                '"comet_ufi_summary_and_actions_renderer"',
                f'"post_id":"{post_id}"',
                '\\"top_level_post_id\\":\\"' + post_id + '\\"',
                "fb_reel_react_button",
            ):
                snippet_start = chunk.find(needle)
                if snippet_start >= 0:
                    break
            if snippet_start < 0:
                snippet_start = 0
            snippet = re.sub(r"\s+", " ", chunk[snippet_start : snippet_start + 500])
            best = (
                True,
                likes,
                comments,
                shares,
                snippet,
            )
            best_score = score

    return best


def parse_html_metrics(html: str, post_id: str, url: str, method: str) -> DirectPostMetric:
    metric = DirectPostMetric(post_id=post_id, source_url=url, fetch_method=method)
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    metric.raw_snippet = text[:400]
    if len(metric.raw_snippet) < 40:
        feedback_index = html.find('"feedback"')
        if feedback_index >= 0:
            metric.raw_snippet = re.sub(r"\s+", " ", html[feedback_index : feedback_index + 400])

    state = detect_html_state(text)
    if state == "rate_limited":
        metric.is_rate_limited = True
        return metric
    if state == "login":
        metric.is_login_required = True
        return metric
    if state == "not_found":
        metric.is_not_found = True
        return metric

    for parser in (parse_unified_video_metrics, parse_targeted_comet_metrics):
        has_signal, likes, comments, shares, snippet = parser(html, post_id)
        if has_signal:
            metric.likes = likes
            metric.comments = comments
            metric.shares = shares
            metric.has_metric_signal = True
            if snippet:
                metric.raw_snippet = snippet
            return metric

    if post_id.startswith("pfbid"):
        metric.raw_snippet = text[:400] or "No targeted Comet metric block found for this pfbid URL"
        return metric

    found_likes, likes = max_count(r'"unified_reactors"\s*:\s*\{"count"\s*:\s*(\d+)', html)
    found_reactors, reactors = max_count(r'"reactors"\s*:\s*\{"count"\s*:\s*(\d+)', html)
    found_comments_a, comments_a = max_count(
        r'"comment_rendering_instance"\s*:\s*\{"comments"\s*:\s*\{"total_count"\s*:\s*(\d+)',
        html,
    )
    found_comments_b, comments_b = max_count(r'"comments"\s*:\s*\{[^{}]{0,500}"total_count"\s*:\s*(\d+)', html)
    found_shares_a, shares_a = max_count(r'"share_count"\s*:\s*\{"count"\s*:\s*(\d+)', html)
    found_shares_b, shares_b = max_count(r'"i18n_share_count"\s*:\s*"([^"]+)"', html)

    if found_likes or found_reactors or found_comments_a or found_comments_b or found_shares_a or found_shares_b:
        metric.likes = max(likes, reactors)
        metric.comments = max(comments_a, comments_b)
        metric.shares = max(shares_a, shares_b)
        metric.has_metric_signal = True

    return metric


def _dump_html(html: str, method: str, dump_dir: Path) -> None:
    dump_dir.mkdir(parents=True, exist_ok=True)
    filename = f"debug_{method.replace('/', '_')}.html"
    (dump_dir / filename).write_text(html, encoding="utf-8")


def do_get(
    session: requests.Session,
    url: str,
    post_id: str,
    method: str,
    *,
    timeout: int = 20,
    dump_html: bool = False,
    dump_dir: Optional[Path] = None,
) -> Optional[DirectPostMetric]:
    try:
        response = session.get(url, timeout=timeout, allow_redirects=True)
    except requests.RequestException as exc:
        logger.warning("Direct metric request failed: method=%s url=%s error=%s", method, url, exc)
        return DirectPostMetric(
            post_id=post_id,
            source_url=url,
            fetch_method=method,
            error_message=str(exc),
        )

    if response.status_code in (404, 410):
        return DirectPostMetric(post_id=post_id, source_url=url, fetch_method=method, is_not_found=True)

    html = response_text(response)
    if dump_html:
        _dump_html(html, method, dump_dir or Path("."))

    return parse_html_metrics(html, post_id, response.url, method)


def build_urls(post_id: str, original_url: Optional[str]) -> list[tuple[str, str, bool]]:
    is_photo = bool(original_url and "fbid=" in original_url)
    urls: list[tuple[str, str, bool]] = []

    if is_photo:
        urls.extend(
            [
                (f"https://www.facebook.com/photo/?fbid={post_id}", "www/photo", False),
                (f"https://m.facebook.com/photo/?fbid={post_id}", "mobile/photo", True),
                (f"https://www.facebook.com/photo.php?fbid={post_id}", "www/photo.php", False),
            ]
        )
        return urls

    parts = post_id.split("_", 1)
    if original_url:
        urls.append((original_url, "www/original", False))
        parsed_original = urlparse(original_url)
        if parsed_original.netloc.endswith("facebook.com"):
            mobile_original = parsed_original._replace(netloc="m.facebook.com").geturl()
            if mobile_original != original_url:
                urls.append((mobile_original, "mobile/original", True))

    urls.append((f"https://www.facebook.com/{post_id}", "www/post", False))
    if len(parts) == 2:
        owner, story_id = parts
        urls.extend(
            [
                (
                    f"https://www.facebook.com/permalink.php?story_fbid={story_id}&id={owner}",
                    "www/permalink",
                    False,
                ),
                (
                    f"https://m.facebook.com/permalink.php?story_fbid={story_id}&id={owner}",
                    "mobile/perm",
                    True,
                ),
            ]
        )
    urls.append((f"https://m.facebook.com/{post_id}", "mobile/post", True))
    return urls


def _score(metric: DirectPostMetric) -> int:
    return metric.likes + metric.comments + metric.shares


def is_good(metric: DirectPostMetric) -> bool:
    return (
        not metric.is_rate_limited
        and not metric.is_login_required
        and not metric.is_not_found
        and metric.has_metric_signal
    )


def smart_fetch(
    cookies_raw: Optional[str],
    post_id: str,
    *,
    original_url: Optional[str] = None,
    group_id: Optional[str] = None,
    timeout: int = 20,
    jitter_min_seconds: float = 1.0,
    jitter_max_seconds: float = 2.5,
    dump_html: bool = False,
    dump_dir: Optional[Path] = None,
) -> DirectPostMetric:
    if jitter_max_seconds > 0:
        jitter_min_seconds = max(jitter_min_seconds, 0.0)
        jitter_max_seconds = max(jitter_max_seconds, jitter_min_seconds)
        time.sleep(random.uniform(jitter_min_seconds, jitter_max_seconds))

    desktop_session = build_session(cookies_raw, mobile=False)
    mobile_session = build_session(cookies_raw, mobile=True)
    best = DirectPostMetric(post_id=post_id, source_url="", is_not_found=True, fetch_method="none")

    urls: list[tuple[str, str, bool]] = []
    if group_id:
        urls.extend(
            [
                (f"https://www.facebook.com/groups/{group_id}/posts/{post_id}", "www/group", False),
                (f"https://m.facebook.com/groups/{group_id}/posts/{post_id}", "mobile/group", True),
            ]
        )
    urls.extend(build_urls(post_id, original_url))

    seen_urls = set()
    for url, label, use_mobile in urls:
        if url in seen_urls:
            continue
        seen_urls.add(url)
        session = mobile_session if use_mobile else desktop_session
        result = do_get(
            session,
            url,
            post_id,
            label,
            timeout=timeout,
            dump_html=dump_html,
            dump_dir=dump_dir,
        )
        if result is None:
            continue
        if result.is_rate_limited or result.is_login_required:
            return result
        if is_good(result):
            return result
        if result.has_metric_signal or _score(result) > _score(best):
            best = result
        time.sleep(random.uniform(0.5, 1.5))

    return best


def extract_post_id(url: str) -> Optional[str]:
    match = re.search(r"[?&](?:fbid|story_fbid)=(\d+)", url)
    if match:
        return match.group(1)

    match = re.search(r"/(?:posts|videos|photos|reel)/(\w+)", url)
    if match:
        return match.group(1)

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if "story_fbid" in query and "id" in query:
        return f"{query['id'][0]}_{query['story_fbid'][0]}"

    match = re.search(r"/groups/(\d+)/posts/(\d+)", url)
    if match:
        return match.group(2)

    return None


def extract_group_id(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    match = re.search(r"/groups/([^/?#]+)/", url)
    return match.group(1) if match else None
