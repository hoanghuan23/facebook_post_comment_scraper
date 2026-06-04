from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

from backend.scraper.direct_post_metrics import (
    DirectPostMetric,
    extract_group_id,
    extract_post_id,
    smart_fetch,
)


def print_result(result: DirectPostMetric) -> None:
    width = 58
    print("\n" + "=" * width)
    print("FACEBOOK POST METRICS")
    print("=" * width)
    print(f"Post ID : {result.post_id}")
    print(f"URL     : {result.source_url[:80]}")
    print(f"Method  : {result.fetch_method}")
    print("-" * width)

    if result.is_rate_limited:
        print("RATE LIMITED - wait before retrying.")
    elif result.is_login_required:
        print("LOGIN REQUIRED - cookie may be expired or missing access.")
    elif result.is_not_found:
        print("NOT FOUND - post is unavailable or not accessible.")
    elif not result.has_metric_signal:
        print("PARSE MISS - no targeted metric block was found.")
    else:
        print(f"Likes    : {result.likes:,}")
        print(f"Comments : {result.comments:,}")
        print(f"Shares   : {result.shares:,}")

    if result.error_message:
        print("-" * width)
        print(f"Error   : {result.error_message}")
    if result.raw_snippet:
        print("-" * width)
        print(f"Snippet : {result.raw_snippet[:180].replace(chr(10), ' ')}")
    print("=" * width + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Facebook direct post metric fetcher")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--url", help="Facebook post URL")
    target.add_argument("--id", help="Facebook post id")

    auth = parser.add_mutually_exclusive_group(required=False)
    auth.add_argument("--cookie-file", metavar="FILE", help="Cookie JSON/header string file")
    auth.add_argument("--cookies", metavar="STR", help="Cookie JSON/header string")

    parser.add_argument("--group-id", help="Group id or slug for group posts")
    parser.add_argument("--json", nargs="?", const=True, default=False, help="Print JSON output")
    parser.add_argument("--dump-html", action="store_true", help="Write debug HTML files")
    parser.add_argument("--dump-dir", default=".", help="Directory for --dump-html output")
    parser.add_argument("--timeout", type=int, default=20, help="Request timeout in seconds")
    parser.add_argument("--debug", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    cookies_raw = ""
    if args.cookie_file:
        try:
            cookies_raw = Path(args.cookie_file).read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            print(f"Cookie file not found: {args.cookie_file}", file=sys.stderr)
            sys.exit(1)
    elif args.cookies:
        cookies_raw = args.cookies.strip()

    original_url = args.url
    group_id = args.group_id
    if args.url:
        post_id = extract_post_id(args.url)
        if not post_id:
            print(f"Cannot extract post id from URL: {args.url}", file=sys.stderr)
            sys.exit(1)
        group_id = group_id or extract_group_id(args.url)
    else:
        post_id = args.id.strip()

    result = smart_fetch(
        cookies_raw,
        post_id,
        original_url=original_url,
        group_id=group_id,
        timeout=args.timeout,
        jitter_min_seconds=1.5,
        jitter_max_seconds=3.5,
        dump_html=args.dump_html,
        dump_dir=Path(args.dump_dir),
    )

    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print_result(result)


if __name__ == "__main__":
    main()
