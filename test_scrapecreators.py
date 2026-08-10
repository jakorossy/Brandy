#!/usr/bin/env python3
"""
Standalone ScrapeCreators API test script.

Does NOT run the full pipeline or touch the database.
Makes API calls, prints raw responses, and saves them to disk.

Usage:
    python test_scrapecreators.py                       # defaults: Starbucks, Instagram
    python test_scrapecreators.py Nike Instagram
    python test_scrapecreators.py "Nike:nike" Instagram  # explicit handle

Steps:
  1. Profile   — GET /v1/instagram/profile (account + embedded posts)
  2. Comments  — GET /v1/instagram/comments for the first non-disabled post
                 (shortcode taken from profile response — no extra API call)

The raw JSON responses are saved to:
    data/intermediate/sc_test_<brand>_<platform>_<label>_<timestamp>.json

Read those files to verify field names before running the full pipeline
or adjusting extractors in scrapecreators_provider.py.
"""

import json
import os
import sys
from datetime import datetime

import requests
from dotenv import load_dotenv

# ── Load .env from project root ──────────────────────────────────────
load_dotenv()

API_KEY = os.environ.get("SCRAPECREATORS_API_KEY")
API_BASE_URL = "https://api.scrapecreators.com"


def check_key():
    if not API_KEY:
        print("\n  ERROR: SCRAPECREATORS_API_KEY is not set.")
        print("  1. Copy .env.example to .env")
        print("  2. Add your key: SCRAPECREATORS_API_KEY=your_key_here")
        print("  3. Rerun this script.\n")
        sys.exit(1)
    print(f"  API key loaded: ...{API_KEY[-6:]}")


def call_api(endpoint: str, params: dict) -> tuple[dict | None, int | None]:
    """Make a single API call. Returns (response_json, status_code)."""
    url = f"{API_BASE_URL}{endpoint}"
    headers = {
        "x-api-key": API_KEY,
        "Accept": "application/json",
    }
    print(f"\n  GET {url}")
    print(f"  Params: {params}")

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        print(f"  Status: {resp.status_code}")
        return resp.json(), resp.status_code
    except requests.HTTPError as e:
        print(f"  HTTP error: {e}")
        return None, getattr(e.response, "status_code", None)
    except Exception as e:
        print(f"  Request failed: {e}")
        return None, None


def save_response(data: dict, brand: str, platform: str, label: str):
    """Save raw response to data/intermediate/ for inspection."""
    os.makedirs("data/intermediate", exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"data/intermediate/sc_test_{brand}_{platform}_{label}_{ts}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  Saved → {filename}")
    return filename


def summarise_response(data, label: str):
    """Print a quick summary of what came back."""
    if data is None:
        print(f"  [{label}] No data returned.")
        return

    if isinstance(data, list):
        print(f"  [{label}] Response is a list with {len(data)} items.")
        if data:
            print(f"  [{label}] First item keys: {list(data[0].keys()) if isinstance(data[0], dict) else type(data[0])}")
    elif isinstance(data, dict):
        print(f"  [{label}] Response is a dict with keys: {list(data.keys())}")
        # Try to find the posts list inside
        for key in ("data", "posts", "items", "results", "edges"):
            val = data.get(key)
            if isinstance(val, list):
                print(f"  [{label}] Found list under '{key}': {len(val)} items.")
                if val and isinstance(val[0], dict):
                    print(f"  [{label}] First item keys: {list(val[0].keys())}")
                break


def main():
    # ── Parse args ──────────────────────────────────────────────────
    brand_arg   = sys.argv[1] if len(sys.argv) > 1 else "Starbucks"
    platform    = sys.argv[2] if len(sys.argv) > 2 else "Instagram"

    # Support "Brand:handle" format
    if ":" in brand_arg:
        brand_name, handle = brand_arg.split(":", 1)
    else:
        brand_name = brand_arg
        handle = brand_arg.lower().replace(" ", "").replace("-", "")

    print("=" * 60)
    print("  ScrapeCreators API — standalone test")
    print(f"  Brand:    {brand_name}")
    print(f"  Handle:   {handle}")
    print(f"  Platform: {platform}")
    print("=" * 60)

    # ── Check key ────────────────────────────────────────────────────
    check_key()

    if platform == "Instagram":
        # ── Step 1: Profile ─────────────────────────────────────────
        print("\n── Step 1: Fetch profile ──")
        profile_data, status = call_api(
            "/v1/instagram/profile",
            {"handle": handle},
        )
        if profile_data:
            summarise_response(profile_data, "profile")
            save_response(profile_data, brand_name, platform, "profile")

        # ── Step 2: Comments — use shortcode from profile response ───
        # No separate posts endpoint call needed; profile embeds up to ~14 posts.
        post_url = None

        if isinstance(profile_data, dict):
            user = profile_data.get("data", {}).get("user", {})
            # Try timeline posts first, then felix video posts
            for container_key in ("edge_owner_to_timeline_media", "edge_felix_video_timeline"):
                edges = user.get(container_key, {}).get("edges", [])
                for edge in edges:
                    node = edge.get("node", {})
                    if node.get("comments_disabled"):
                        continue
                    shortcode = node.get("shortcode") or node.get("id")
                    if shortcode:
                        post_url = f"https://www.instagram.com/p/{shortcode}/"
                        print(f"\n  Using first non-disabled post: shortcode={shortcode}")
                        print(f"  URL: {post_url}")
                        break
                if post_url:
                    break

        if post_url:
            print("\n── Step 2: Fetch comments ──")
            comments_data, status = call_api(
                "/v2/instagram/post/comments",
                {"url": post_url, "limit": 20},
            )
            if comments_data:
                summarise_response(comments_data, "comments")
                save_response(comments_data, brand_name, platform, "comments")

                # Print first comment to confirm shape
                comments_list = None
                if isinstance(comments_data, list):
                    comments_list = comments_data
                elif isinstance(comments_data, dict):
                    for key in ("data", "comments", "items", "results", "edges"):
                        val = comments_data.get(key)
                        if isinstance(val, list) and val:
                            comments_list = val
                            break
                if comments_list and isinstance(comments_list[0], dict):
                    print(f"\n  First comment keys: {list(comments_list[0].keys())}")
                    print(f"  First comment: {json.dumps(comments_list[0], indent=2, ensure_ascii=False)[:500]}")
        else:
            print("\n── Step 2: Skipped (no non-disabled post found in profile) ──")
            print("  Check the profile JSON in data/intermediate/ to confirm post structure.")

    else:
        print(f"\n  Platform '{platform}' test not implemented yet.")
        print("  Currently only Instagram is configured in this test script.")

    print("\n" + "=" * 60)
    print("  Test complete. Check data/intermediate/ for raw JSON files.")
    print("  Use those to verify field names before running the full pipeline.")
    print("=" * 60)


if __name__ == "__main__":
    main()
