"""
Social media data ingestion.

File discovery, loading, and field extraction for Instagram, LinkedIn, Facebook.

Reuses the robust helpers from scoring2_updated.py:
  _coerce_int, _coerce_float, _normalize_row_keys, _first_present,
  _find_existing_file, _load_posts, and the field extractors.
"""

import os
import csv
import json
import logging

logger = logging.getLogger(__name__)


# ── Coercion helpers (from scoring2_updated.py L8-48) ───────────────

def _coerce_int(v):
    """Robust numeric coercion for ints, strings with commas, floats, and dicts-with-count."""
    if v is None:
        return 0
    if isinstance(v, dict):
        v = v.get("count", 0)
    try:
        return int(v)
    except (ValueError, TypeError):
        try:
            return int(float(str(v).replace(",", "").strip()))
        except Exception:
            return 0


def _coerce_float(v):
    if v is None:
        return 0.0
    try:
        return float(v)
    except (ValueError, TypeError):
        try:
            return float(str(v).replace(",", "").strip())
        except Exception:
            return 0.0


def _normalize_row_keys(post):
    """Lowercase + strip keys so CSV headers match robustly."""
    if not isinstance(post, dict):
        return post
    return {str(k).strip().lower(): v for k, v in post.items()}


def _first_present(d, keys):
    """Return the first present value for any candidate key."""
    for k in keys:
        if k in d and d[k] is not None and d[k] != "":
            v = d[k]
            if isinstance(v, dict) and "count" in v:
                return v.get("count", 0)
            return v
    return 0


# ── File discovery + loading (from scoring2_updated.py L51-87) ──────

def find_data_file(directory: str, brand_name: str, platform: str) -> str | None:
    """
    Look for BrandPlatformPostData.json or .csv in the given directory.
    Returns the path or None.
    """
    base = os.path.join(directory, f"{brand_name}{platform}PostData")
    for ext in (".json", ".csv"):
        path = base + ext
        if os.path.isfile(path):
            logger.debug("Found data file: %s", path)
            return path
    return None


def load_posts(file_path: str) -> list[dict]:
    """
    Load posts from JSON or CSV.
    JSON: list of dicts OR dict with 'posts' key.
    CSV: DictReader rows.
    """
    if file_path.endswith(".json"):
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "posts" in data:
            data = data["posts"]
        if not isinstance(data, list):
            raise ValueError(f"Unexpected JSON format in {file_path}")
        return [p for p in data if isinstance(p, dict)]

    if file_path.endswith(".csv"):
        with open(file_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return [row for row in reader]

    return []


# ── Field extractors (from scoring2_updated.py L91-163) ─────────────

def extract_text(post: dict) -> str:
    """Extract the main text/caption from a post."""
    post = _normalize_row_keys(post)

    txt = _first_present(post, [
        "description", "alt_text", "post_content",
        "caption", "text", "message", "body", "content", "title",
        "post_text", "edge_media_to_caption",
    ])

    # Normalize list/dict caption variants
    if isinstance(txt, list):
        parts = []
        for item in txt:
            parts.append(item.get("text", "") if isinstance(item, dict) else str(item))
        txt = " ".join(parts)
    elif isinstance(txt, dict):
        if "text" in txt:
            txt = txt["text"]
        elif "edges" in txt and isinstance(txt["edges"], list):
            parts = [str(e.get("node", {}).get("text", "")) for e in txt["edges"] if isinstance(e, dict)]
            txt = " ".join(parts)
        else:
            txt = ""

    return str(txt) if txt is not None else ""


def extract_likes(post: dict) -> int:
    post = _normalize_row_keys(post)
    v = _first_present(post, ["likes", "like_count", "num_likes", "reaction_count", "reactions", "likecount"])
    return _coerce_int(v)


def extract_comment_count(post: dict) -> int:
    post = _normalize_row_keys(post)
    v = _first_present(post, ["num_comments", "comment_count", "comments", "edge_media_to_comment"])
    if v not in (0, "0", "", None):
        return _coerce_int(v)

    # Fallback: count latest_comments if present
    lc = _normalize_row_keys(post).get("latest_comments", None)
    if isinstance(lc, list):
        return len(lc)
    if isinstance(lc, str) and lc.strip():
        return 1
    return 0


def extract_followers(post: dict) -> int:
    """Extract follower count. Caller should use max() across posts, NOT sum."""
    post = _normalize_row_keys(post)
    v = _first_present(post, ["followers", "num_followers", "follower_count"])
    return _coerce_int(v)


def extract_shares(post: dict) -> int:
    post = _normalize_row_keys(post)
    v = _first_present(post, ["shares", "share_count", "num_shares", "reposts"])
    return _coerce_int(v)


def extract_post_id(post: dict) -> str | None:
    """Try to extract a platform-specific post ID."""
    post = _normalize_row_keys(post)
    v = _first_present(post, ["post_id", "id", "pk", "shortcode", "post_url", "url"])
    return str(v) if v else None


def extract_post_date(post: dict) -> str | None:
    """Try to extract a post date."""
    post = _normalize_row_keys(post)
    v = _first_present(post, [
        "post_date", "date", "timestamp", "created_at", "posted_at",
        "taken_at", "created_time",
    ])
    return str(v) if v else None


# ── Comment extraction ──────────────────────────────────────────────

def extract_comments(post: dict) -> list[dict]:
    """
    Extract individual comments from a post if available.
    Returns list of {"text": ..., "commenter": ...} dicts.

    Supports common formats:
    - latest_comments: list of dicts or strings
    - comments: list of dicts
    - edge_media_to_comment.edges: Instagram graph format
    """
    post = _normalize_row_keys(post)
    comments = []

    # Try latest_comments (common in scraped data)
    lc = post.get("latest_comments") or post.get("latestcomments")
    if isinstance(lc, list):
        for item in lc:
            if isinstance(item, dict):
                text = item.get("text") or item.get("comment") or item.get("body") or ""
                commenter = item.get("user") or item.get("username") or item.get("author") or ""
                if isinstance(commenter, dict):
                    commenter = commenter.get("username", "")
                if text:
                    comments.append({"text": str(text), "commenter": str(commenter)})
            elif isinstance(item, str) and item.strip():
                comments.append({"text": item.strip(), "commenter": ""})
        if comments:
            return comments

    # Try comments field
    c = post.get("comments")
    if isinstance(c, list):
        for item in c:
            if isinstance(item, dict):
                text = item.get("text") or item.get("comment") or item.get("body") or ""
                commenter = item.get("user") or item.get("username") or item.get("author") or ""
                if isinstance(commenter, dict):
                    commenter = commenter.get("username", "")
                if text:
                    comments.append({"text": str(text), "commenter": str(commenter)})
        if comments:
            return comments

    # Try Instagram graph format
    edge = post.get("edge_media_to_comment") or post.get("edge_media_to_parent_comment")
    if isinstance(edge, dict):
        edges = edge.get("edges", [])
        for e in edges:
            if isinstance(e, dict):
                node = e.get("node", {})
                text = node.get("text", "")
                owner = node.get("owner", {})
                commenter = owner.get("username", "") if isinstance(owner, dict) else ""
                if text:
                    comments.append({"text": str(text), "commenter": str(commenter)})

    return comments


# ── Default platform list ────────────────────────────────────────────

PLATFORMS = ["Instagram", "LinkedIn", "FaceBook"]


# ── Provider factory ─────────────────────────────────────────────────

def get_provider(name: str = "local_file", **kwargs):
    """
    Return a SocialProvider instance by name.

    Available providers:
      "local_file"      — reads from local JSON/CSV files (default)
      "scrapecreators"  — live scraping via ScrapeCreators API

    Extra kwargs are forwarded to the provider constructor.
    Example:
        get_provider("local_file", data_dir="data/raw")
        get_provider("scrapecreators", post_limit=30)
    """
    if name == "local_file":
        from brandy.social.providers.local_file_provider import LocalFileProvider
        return LocalFileProvider(**kwargs)

    if name == "scrapecreators":
        from brandy.social.providers.scrapecreators_provider import ScrapeCreatorsProvider
        return ScrapeCreatorsProvider(**kwargs)

    raise ValueError(
        f"Unknown provider '{name}'. "
        f"Available: 'local_file', 'scrapecreators'"
    )
