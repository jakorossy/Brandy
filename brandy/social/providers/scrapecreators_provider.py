"""
ScrapeCreatorsProvider — live social media scraping via ScrapeCreators API.

All external API logic is isolated in this file. To remove this provider,
delete this file and remove "scrapecreators" from the factory in ingest.py.
Nothing else in the pipeline will be affected.

API key is read ONLY from the environment variable SCRAPECREATORS_API_KEY.
Load your .env file before running, or export the variable in your shell.

--- Confirmed API response shape (Instagram profile endpoint) ---

GET /v1/instagram/profile?handle=<username>

Top level:
    {
        "success": bool,
        "credits_remaining": int,
        "status": str,
        "data": {
            "user": { ... }
        }
    }

data.user fields used here:
    username, full_name, biography
    is_verified, is_business_account, is_professional_account
    category_name
    edge_followed_by.count      → followers
    edge_follow.count           → following
    edge_owner_to_timeline_media.edges[].node  → up to 12 posts (mixed media)
    edge_felix_video_timeline.edges[].node     → up to 2 video posts

Post node fields:
    id, shortcode
    taken_at_timestamp          → Unix timestamp
    is_video
    edge_media_to_caption.edges[0].node.text   → caption
    edge_media_preview_like.count              → like count (full, shown on post)
    edge_liked_by.count                        → confirmed likes (lower for videos)
    edge_media_to_comment.count                → comment count (no text in profile)
    comments_disabled

Note: comment TEXT is not included in the profile response — only counts.
      Separate comment fetching can be added later via a comments endpoint.
Note: page_info.has_next_page / end_cursor indicate more posts exist (not fetched yet).
"""

import json
import logging
import os
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()

from brandy.social.providers.base import SocialProvider

logger = logging.getLogger(__name__)

API_BASE_URL = "https://api.scrapecreators.com"
API_KEY_ENV  = "SCRAPECREATORS_API_KEY"
REQUEST_DELAY = 0.5  # seconds between calls


class ScrapeCreatorsProvider(SocialProvider):
    """
    Fetches live social media data from the ScrapeCreators API.

    Current implementation:
      - One API call per brand: GET /v1/instagram/profile
      - Parses account metadata and embedded posts from the profile response
      - Returns up to ~14 posts per brand (12 timeline + 2 felix video)
      - Optional comment fetching via GET /v1/instagram/comments (off by default)

    Requires environment variable: SCRAPECREATORS_API_KEY

    Comment fetching params (credit-efficient — off by default):
      fetch_comments    — enable comment API calls (default False)
      comment_post_limit — max posts to fetch comments for, selected by highest likes (default 3)
      top_comments      — keep only the top-N comments by like count per post (default 5)
    """

    def __init__(
        self,
        post_limit: int = 50,
        fetch_comments: bool = False,
        comment_post_limit: int = 3,
        top_comments: int = 5,
    ):
        self.post_limit = post_limit
        self.fetch_comments = fetch_comments
        self.comment_post_limit = comment_post_limit
        self.top_comments = top_comments
        self._api_key = self._load_api_key()

    @property
    def name(self) -> str:
        return "scrapecreators"

    # ── Key management ───────────────────────────────────────────────

    def _load_api_key(self) -> str | None:
        key = os.environ.get(API_KEY_ENV)
        if not key:
            logger.warning(
                "ScrapeCreators API key not found. "
                "Set %s in your .env file or shell environment.", API_KEY_ENV
            )
        return key

    def _headers(self) -> dict:
        return {
            "x-api-key": self._api_key or "",
            "Accept": "application/json",
        }

    # ── HTTP ─────────────────────────────────────────────────────────

    def _get(self, endpoint: str, params: dict) -> dict | None:
        """GET request to the API. Returns parsed JSON or None."""
        if not self._api_key:
            logger.error("Cannot call API — SCRAPECREATORS_API_KEY not set.")
            return None

        url = f"{API_BASE_URL}{endpoint}"
        logger.debug("GET %s  params=%s", url, params)

        try:
            resp = requests.get(url, headers=self._headers(), params=params, timeout=30)
            if not resp.ok:
                logger.error(
                    "HTTP %s from ScrapeCreators — endpoint: %s, params: %s, body: %s",
                    resp.status_code, endpoint, params, resp.text[:400],
                )
                return None
            return resp.json()
        except requests.RequestException:
            logger.exception("Request failed: %s", endpoint)
            return None

    # ── Instagram profile parsing ─────────────────────────────────────

    def _parse_profile_response(self, raw: dict) -> dict | None:
        """
        Extract data.user from the profile API response.
        Returns the user dict, or None if the response shape is unexpected.
        """
        if not isinstance(raw, dict):
            logger.error("Profile response is not a dict")
            return None

        if not raw.get("success"):
            logger.error("Profile response success=False: %s", raw.get("status"))
            return None

        user = raw.get("data", {}).get("user")
        if not user:
            logger.error("Profile response missing data.user")
            return None

        return user

    def _extract_account_meta(self, brand_name: str, user: dict) -> dict:
        """Build the account metadata dict from data.user."""
        return {
            "_account_meta":    True,
            "brand_name":       brand_name,
            "platform":         "Instagram",
            "followers":        user.get("edge_followed_by", {}).get("count", 0),
            "following":        user.get("edge_follow", {}).get("count", 0),
            "account_handle":   user.get("username", ""),
            "full_name":        user.get("full_name", ""),
            "bio":              user.get("biography", ""),
            "is_verified":      user.get("is_verified", False),
            "is_business":      user.get("is_business_account", False),
            "category":         user.get("category_name", ""),
        }

    def _extract_caption(self, node: dict) -> str:
        """
        Extract caption text from edge_media_to_caption.edges[0].node.text.
        Falls back to accessibility_caption if no caption edges exist.
        """
        cap_edges = node.get("edge_media_to_caption", {}).get("edges", [])
        if cap_edges and isinstance(cap_edges[0], dict):
            text = cap_edges[0].get("node", {}).get("text", "")
            if text:
                return str(text)

        # Fallback: accessibility caption (usually describes image for screen readers)
        return str(node.get("accessibility_caption") or "")

    def _extract_likes(self, node: dict) -> int:
        """
        Use edge_media_preview_like.count as the primary like count.
        For non-video posts both fields are equal.
        For video posts, preview_like is the higher visible count.
        Falls back to edge_liked_by.count.
        """
        preview = node.get("edge_media_preview_like", {}).get("count")
        if preview is not None:
            return int(preview)
        liked_by = node.get("edge_liked_by", {}).get("count")
        if liked_by is not None:
            return int(liked_by)
        return 0

    def _extract_comment_count(self, node: dict) -> int:
        return int(node.get("edge_media_to_comment", {}).get("count", 0))

    def _extract_timestamp(self, node: dict) -> str | None:
        """Convert Unix taken_at_timestamp to ISO date string."""
        ts = node.get("taken_at_timestamp")
        if ts:
            try:
                return datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d")
            except (ValueError, OSError):
                return str(ts)
        return None

    def _node_to_post(self, node: dict, brand_name: str, handle: str) -> dict:
        """Convert a post node dict into a normalized post dict."""
        shortcode = node.get("shortcode", "")
        post_url = f"https://www.instagram.com/p/{shortcode}/" if shortcode else None

        return {
            "brand_name":        brand_name,
            "platform":          "Instagram",
            "post_id_external":  shortcode or node.get("id"),
            "post_text":         self._extract_caption(node),
            "likes":             self._extract_likes(node),
            "num_comments":      self._extract_comment_count(node),
            "post_date":         self._extract_timestamp(node),
            "source_file":       f"scrapecreators_instagram_profile_{handle}",
            "comments":          [],    # Profile endpoint returns counts only, not text
            "_comments_fetched": False, # Signal to pipeline: comment text was NOT fetched
            "_raw":              node,
            "_post_url":         post_url,
            "_is_video":         node.get("is_video", False),
            "_comments_disabled": node.get("comments_disabled", False),
        }

    def _extract_posts_from_user(
        self,
        user: dict,
        brand_name: str,
        handle: str,
        since: str | None = None,
        until: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """
        Extract embedded post nodes from a user profile dict.

        Two edge containers hold posts:
          edge_owner_to_timeline_media — up to 12 mixed posts (newest-first)
          edge_felix_video_timeline    — up to 2 video posts (may overlap)

        Deduplicates by shortcode, then applies date filtering and limit.
        """
        posts = []
        seen_shortcodes = set()

        containers = [
            ("edge_owner_to_timeline_media", "timeline"),
            ("edge_felix_video_timeline",    "felix"),
        ]

        for container_key, _ in containers:
            container = user.get(container_key, {})
            edges = container.get("edges", [])
            total_available = container.get("count", 0)

            if total_available > len(edges):
                logger.info(
                    "[scrapecreators] %s has %d total posts; profile embedded %d "
                    "(pagination not yet implemented — use post_limit to work within this)",
                    container_key, total_available, len(edges),
                )

            for edge in edges:
                node = edge.get("node")
                if not isinstance(node, dict):
                    continue
                shortcode = node.get("shortcode") or node.get("id")
                if shortcode in seen_shortcodes:
                    continue
                seen_shortcodes.add(shortcode)
                posts.append(self._node_to_post(node, brand_name, handle))

        # Profile edges are already newest-first; apply date filter then limit
        if since or until:
            filtered = []
            for p in posts:
                d = p.get("post_date")
                if d is None:
                    filtered.append(p)
                    continue
                if since and d < since:
                    continue
                if until and d > until:
                    continue
                filtered.append(p)
            posts = filtered

        if limit is not None:
            posts = posts[:limit]

        logger.info(
            "[scrapecreators] %s/Instagram: %d posts after filtering (since=%s, until=%s, limit=%s)",
            brand_name, len(posts), since or "—", until or "—", limit or "—",
        )
        return posts

    # ── Instagram comment fetching ────────────────────────────────────

    def _parse_comments_response(self, raw: dict | list) -> list[dict]:
        """
        Extract comment records from the /v1/instagram/comments response.

        The API may return:
          - A list of comment objects directly
          - A dict with a "data" key containing the list
          - A dict with a "comments" key containing the list

        Each comment object is expected to have at minimum a text field.
        Known fields: text, owner (username), like_count, timestamp.
        """
        if isinstance(raw, list):
            comments = raw
        elif isinstance(raw, dict):
            # Try common wrapper keys
            for key in ("data", "comments", "items", "results", "edges"):
                val = raw.get(key)
                if isinstance(val, list):
                    comments = val
                    break
            else:
                logger.warning("Comments response has unexpected shape: %s", list(raw.keys()))
                return []
        else:
            logger.warning("Comments response is not a list or dict: %s", type(raw))
            return []

        result = []
        for item in comments:
            if not isinstance(item, dict):
                continue

            # Text — try multiple candidate keys
            text = (
                item.get("text")
                or item.get("comment_text")
                or item.get("message")
                or ""
            )
            if not text:
                continue

            # Commenter username
            owner = item.get("owner") or item.get("user") or {}
            if isinstance(owner, dict):
                commenter = owner.get("username") or owner.get("name") or ""
            else:
                commenter = str(owner)

            # Like count
            like_count = item.get("like_count") or item.get("likes") or 0
            try:
                like_count = int(like_count)
            except (TypeError, ValueError):
                like_count = 0

            result.append({
                "text":               str(text),
                "commenter":          commenter,
                "comment_like_count": like_count,
            })

        return result

    def fetch_instagram_comments(
        self,
        post_url: str,
        limit: int = 20,
    ) -> list[dict]:
        """
        Fetch comments for a single Instagram post.

        Returns a list of raw comment dicts: {text, commenter, comment_like_count}.
        Sorted by comment_like_count descending.
        Returns [] if the API call fails or returns no comments.
        """
        raw = self._get("/v2/instagram/post/comments", {"url": post_url, "limit": limit})
        if raw is None:
            return []

        comments = self._parse_comments_response(raw)
        comments.sort(key=lambda c: c.get("comment_like_count", 0), reverse=True)

        logger.debug(
            "[scrapecreators] %s → %d comments parsed", post_url, len(comments)
        )
        return comments

    # ── Public interface ─────────────────────────────────────────────

    def fetch_instagram_profile(
        self,
        brand_name: str,
        handle: str,
        since: str | None = None,
        until: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """
        Fetch and parse a single Instagram profile API call.
        Returns account meta dict + normalized post dicts (filtered + limited).

        If self.fetch_comments is True, additionally calls the comments endpoint
        for the top self.comment_post_limit posts (by likes), populates the
        'comments' list on each post, and sets '_comments_fetched' = True for
        those posts.
        """
        raw = self._get("/v1/instagram/profile", {"handle": handle})
        if not raw:
            return []

        user = self._parse_profile_response(raw)
        if not user:
            return []

        results = []

        account_meta = self._extract_account_meta(brand_name, user)
        results.append(account_meta)

        logger.info(
            "[scrapecreators] @%s: %d followers, %d following, verified=%s",
            handle,
            account_meta["followers"],
            account_meta["following"],
            account_meta["is_verified"],
        )

        posts = self._extract_posts_from_user(user, brand_name, handle, since, until, limit)

        # Optional comment fetching — select top posts by likes, skip disabled
        if self.fetch_comments and posts:
            eligible = [
                p for p in posts
                if not p.get("_comments_disabled") and p.get("_post_url")
            ]
            # Sort by likes descending; take up to comment_post_limit
            eligible.sort(key=lambda p: p.get("likes", 0), reverse=True)
            fetch_targets = eligible[: self.comment_post_limit]

            logger.info(
                "[scrapecreators] Fetching comments for %d/%d posts (top by likes)",
                len(fetch_targets), len(posts),
            )

            for post in fetch_targets:
                post_url = post["_post_url"]
                # Request more than top_comments so we can sort and trim
                raw_comments = self.fetch_instagram_comments(post_url, limit=max(self.top_comments * 4, 20))
                # Already sorted by like_count desc; keep top K
                top = raw_comments[: self.top_comments]

                post["comments"] = top
                post["_comments_fetched"] = True

                logger.info(
                    "[scrapecreators] %s → %d comments fetched, keeping top %d",
                    post_url, len(raw_comments), len(top),
                )
                time.sleep(REQUEST_DELAY)

        results.extend(posts)
        return results

    def fetch_brand(
        self,
        brand_name: str,
        platforms: list[str],
        since: str | None = None,
        until: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """
        Fetch live data for a brand across supported platforms.

        Brand name is used as the Instagram handle by default (lowercased, no spaces).
        To use a different handle: pass brand_name as "Brand:handle"
          e.g. "Burger King:burgerking"
        """
        if ":" in brand_name:
            brand_name, handle = brand_name.split(":", 1)
        else:
            handle = brand_name.lower().replace(" ", "").replace("-", "")

        all_items = []

        for platform in platforms:
            if platform != "Instagram":
                logger.info(
                    "[scrapecreators] Platform '%s' not yet implemented — skipping", platform
                )
                continue

            logger.info(
                "[scrapecreators] Fetching %s/Instagram (handle: @%s, since=%s, until=%s, limit=%s)",
                brand_name, handle, since or "—", until or "—", limit or "—",
            )
            items = self.fetch_instagram_profile(brand_name, handle, since, until, limit)
            all_items.extend(items)
            time.sleep(REQUEST_DELAY)

        return all_items

    # ── Parse from saved file (for development / credit-free testing) ─

    def fetch_brand_from_file(
        self,
        brand_name: str,
        file_path: str,
        since: str | None = None,
        until: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """
        Parse a previously saved profile JSON response instead of calling the API.
        Useful for development and testing without spending API credits.

        Usage:
            provider = ScrapeCreatorsProvider()
            items = provider.fetch_brand_from_file(
                "Starbucks",
                "data/intermediate/sc_test_Starbucks_Instagram_profile_*.json"
            )
        """
        logger.info("[scrapecreators] Parsing from saved file: %s", file_path)
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            logger.exception("Could not load saved response from %s", file_path)
            return []

        handle = brand_name.lower().replace(" ", "").replace("-", "")
        user = self._parse_profile_response(raw)
        if not user:
            return []

        results = [self._extract_account_meta(brand_name, user)]
        results.extend(self._extract_posts_from_user(user, brand_name, handle, since, until, limit))
        return results
