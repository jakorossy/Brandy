"""
Base class for social media data providers.

Every provider must implement fetch_brand() and return data in the
normalized format that run_social.py expects. This keeps the rest of
the pipeline completely independent of where data comes from.

Normalized post dict:
    {
        "brand_name":        str,
        "platform":          str,      # Instagram | LinkedIn | FaceBook
        "post_id_external":  str | None,
        "post_text":         str,
        "likes":             int,
        "num_comments":      int,
        "post_date":         str | None,  # YYYY-MM-DD
        "source_file":       str,         # filename or API identifier
        "comments":          list[{"text": str, "commenter": str}],
        "_raw":              dict,        # original payload, for raw_payloads storage
        "_comments_fetched": bool,        # True = provider attempted comment extraction
                                          # False = endpoint does not return comment text
    }

Account metadata dict (mixed into the same list):
    {
        "_account_meta":  True,
        "brand_name":     str,
        "platform":       str,
        "followers":      int,
        "following":      int,
        "account_handle": str,
        "bio":            str,
        "is_verified":    bool,
        "category":       str,
    }
"""

from abc import ABC, abstractmethod


class SocialProvider(ABC):

    @property
    def name(self) -> str:
        """Short identifier used in logs and config. Override in subclass."""
        return self.__class__.__name__.lower().replace("provider", "")

    @abstractmethod
    def fetch_brand(
        self,
        brand_name: str,
        platforms: list[str],
        since: str | None = None,
        until: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """
        Fetch posts for a brand across the given platforms.

        Args:
            brand_name: Brand name (or "Brand:handle" to specify a different handle).
            platforms:  List of platform names to fetch for.
            since:      ISO date string (YYYY-MM-DD). Exclude posts before this date.
            until:      ISO date string (YYYY-MM-DD). Exclude posts after this date.
            limit:      Max posts to return per brand (applied after date filtering,
                        newest-first).

        Returns a flat list containing:
        - normalized post dicts  (one per post, newest-first)
        - account metadata dicts (one per platform, keyed by _account_meta=True)

        Providers should log warnings rather than raising exceptions for
        missing data, so the pipeline can continue with partial results.
        """
        raise NotImplementedError
