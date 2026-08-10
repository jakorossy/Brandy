"""
LocalFileProvider — reads social post data from local JSON/CSV files.

This is the original ingestion logic from brandy/social/ingest.py,
wrapped in the SocialProvider interface. No pipeline logic has changed;
the field extractors and coercion helpers live in ingest.py and are
imported here.
"""

import logging
import os

from brandy.social.providers.base import SocialProvider
from brandy.social.ingest import (
    find_data_file,
    load_posts,
    extract_text,
    extract_likes,
    extract_comment_count,
    extract_followers,
    extract_post_id,
    extract_post_date,
    extract_comments,
)

logger = logging.getLogger(__name__)


def _apply_date_limit_filter(
    posts: list[dict],
    since: str | None,
    until: str | None,
    limit: int | None,
) -> list[dict]:
    """
    Sort posts newest-first, filter by date window, apply limit.
    Posts with no date are kept but sorted to the end.
    """
    # Sort: posts with a date newest-first, undated posts at the end
    def sort_key(p):
        d = p.get("post_date")
        return d if d else ""

    posts = sorted(posts, key=sort_key, reverse=True)

    # Date window filter
    if since or until:
        filtered = []
        for p in posts:
            d = p.get("post_date")
            if d is None:
                filtered.append(p)   # no date — keep, can't filter
                continue
            date_str = str(d)[:10]   # take YYYY-MM-DD portion
            if since and date_str < since:
                continue
            if until and date_str > until:
                continue
            filtered.append(p)
        posts = filtered

    if limit is not None:
        posts = posts[:limit]

    return posts


class LocalFileProvider(SocialProvider):
    """
    Loads post data from local files in data/raw/.

    Expected filename convention: {Brand}{Platform}PostData.json/.csv
    Example: StarbucksInstagramPostData.csv
    """

    def __init__(self, data_dir: str = "data/raw"):
        self.data_dir = data_dir

    @property
    def name(self) -> str:
        return "local_file"

    def fetch_brand(
        self,
        brand_name: str,
        platforms: list[str],
        since: str | None = None,
        until: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """
        Load posts for a brand from local files.
        Applies date filtering and post limit after loading.
        """
        all_items = []

        for platform in platforms:
            file_path = find_data_file(self.data_dir, brand_name, platform)
            if not file_path:
                logger.info(
                    "[local_file] No data file for %s/%s in %s",
                    brand_name, platform, self.data_dir,
                )
                continue

            try:
                raw_posts = load_posts(file_path)
            except Exception:
                logger.exception("[local_file] Error loading %s", file_path)
                continue

            if not raw_posts:
                logger.info("[local_file] No posts in %s", file_path)
                continue

            max_followers = 0
            platform_posts = []

            for post in raw_posts:
                if not isinstance(post, dict):
                    continue

                followers = extract_followers(post)
                max_followers = max(max_followers, followers)
                comments = extract_comments(post)

                platform_posts.append({
                    "brand_name":        brand_name,
                    "platform":          platform,
                    "post_id_external":  extract_post_id(post),
                    "post_text":         extract_text(post),
                    "likes":             extract_likes(post),
                    "num_comments":      extract_comment_count(post),
                    "post_date":         extract_post_date(post),
                    "source_file":       os.path.basename(file_path),
                    "comments":          comments,
                    "_comments_fetched": True,   # local files attempt comment extraction
                    "_raw":              post,
                })

            # Sort newest-first, apply date filter and limit
            platform_posts = _apply_date_limit_filter(platform_posts, since, until, limit)

            logger.info(
                "[local_file] %s/%s: %d posts after filtering (raw: %d, since=%s, until=%s, limit=%s)",
                brand_name, platform, len(platform_posts), len(raw_posts),
                since or "—", until or "—", limit or "—",
            )

            all_items.extend(platform_posts)

            if max_followers > 0:
                all_items.append({
                    "_account_meta":  True,
                    "brand_name":     brand_name,
                    "platform":       platform,
                    "followers":      max_followers,
                    "following":      0,
                    "account_handle": "",
                    "bio":            "",
                    "is_verified":    False,
                    "category":       "",
                })

        return all_items
