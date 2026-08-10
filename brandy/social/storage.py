"""
Social data persistence layer.

Insert and query brands, accounts, posts, comments, and sentiment aggregations.
"""

import json
import logging
import sqlite3

logger = logging.getLogger(__name__)


# ── Brands ──────────────────────────────────────────────────────────

def upsert_brand(conn: sqlite3.Connection, brand_name: str, industry: str = None) -> int:
    """Insert or get a brand, return its id."""
    row = conn.execute("SELECT id FROM brands WHERE brand_name = ?", (brand_name,)).fetchone()
    if row:
        return row["id"]
    cursor = conn.execute(
        "INSERT INTO brands (brand_name, industry) VALUES (?, ?)",
        (brand_name, industry),
    )
    conn.commit()
    return cursor.lastrowid


# ── Social accounts ─────────────────────────────────────────────────

def upsert_social_account(
    conn: sqlite3.Connection,
    brand_id: int,
    platform: str,
    followers: int = None,
    account_handle: str = None,
    bio: str = None,
) -> int:
    """Insert or update an account for a brand/platform pair."""
    row = conn.execute(
        "SELECT id FROM social_accounts WHERE brand_id = ? AND platform = ?",
        (brand_id, platform),
    ).fetchone()

    if row:
        conn.execute(
            """UPDATE social_accounts
               SET followers = COALESCE(?, followers),
                   account_handle = COALESCE(?, account_handle),
                   bio = COALESCE(?, bio),
                   last_updated = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (followers, account_handle, bio, row["id"]),
        )
        conn.commit()
        return row["id"]

    cursor = conn.execute(
        """INSERT INTO social_accounts (brand_id, platform, followers, account_handle, bio)
           VALUES (?, ?, ?, ?, ?)""",
        (brand_id, platform, followers, account_handle, bio),
    )
    conn.commit()
    return cursor.lastrowid


# ── Posts ───────────────────────────────────────────────────────────

def insert_post(conn: sqlite3.Connection, brand_id: int, post_data: dict) -> int:
    """Insert a social media post. Returns the post id."""
    cursor = conn.execute(
        """INSERT INTO social_posts
           (brand_id, platform, post_id_external, post_text, likes, num_comments, post_date, source_file)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            brand_id,
            post_data.get("platform"),
            post_data.get("post_id_external"),
            post_data.get("post_text"),
            post_data.get("likes", 0),
            post_data.get("num_comments", 0),
            post_data.get("post_date"),
            post_data.get("source_file"),
        ),
    )
    conn.commit()
    return cursor.lastrowid


# ── Comments ────────────────────────────────────────────────────────

def insert_comment(conn: sqlite3.Connection, post_id: int, brand_id: int, platform: str, comment: dict) -> int:
    """Insert a classified comment. Returns comment id."""
    cursor = conn.execute(
        """INSERT INTO social_comments
           (post_id, brand_id, platform, comment_text, commenter,
            comment_like_count, is_spam, is_irrelevant, sentiment_score, sentiment_label)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            post_id,
            brand_id,
            platform,
            comment.get("text"),
            comment.get("commenter"),
            comment.get("comment_like_count", 0),
            int(comment.get("is_spam", False)),
            int(comment.get("is_irrelevant", False)),
            comment.get("sentiment_score"),
            comment.get("sentiment_label"),
        ),
    )
    conn.commit()
    return cursor.lastrowid


def insert_comments_batch(conn: sqlite3.Connection, post_id: int, brand_id: int, platform: str, comments: list[dict]):
    """Insert a batch of classified comments."""
    conn.executemany(
        """INSERT INTO social_comments
           (post_id, brand_id, platform, comment_text, commenter,
            comment_like_count, is_spam, is_irrelevant, sentiment_score, sentiment_label)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                post_id, brand_id, platform,
                c.get("text"), c.get("commenter"),
                c.get("comment_like_count", 0),
                int(c.get("is_spam", False)), int(c.get("is_irrelevant", False)),
                c.get("sentiment_score"), c.get("sentiment_label"),
            )
            for c in comments
        ],
    )
    conn.commit()


# ── Post-level sentiment ───────────────────────────────────────────

def insert_post_sentiment(conn: sqlite3.Connection, post_id: int, agg: dict, caption_sentiment: float = None, basis: str = "comments"):
    """Insert aggregated sentiment for a post."""
    conn.execute(
        """INSERT INTO social_post_sentiment
           (post_id, total_comments_analyzed, positive_count, neutral_count, negative_count,
            spam_count, irrelevant_count, avg_sentiment, caption_sentiment, sentiment_basis)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            post_id,
            agg.get("total_comments_analyzed", 0),
            agg.get("positive_count", 0),
            agg.get("neutral_count", 0),
            agg.get("negative_count", 0),
            agg.get("spam_count", 0),
            agg.get("irrelevant_count", 0),
            agg.get("avg_sentiment"),
            caption_sentiment,
            basis,
        ),
    )
    conn.commit()


# ── Queries ─────────────────────────────────────────────────────────

def get_brand_posts(conn: sqlite3.Connection, brand_name: str, platform: str = None) -> list[dict]:
    """Retrieve posts for a brand, optionally filtered by platform."""
    if platform:
        rows = conn.execute(
            """SELECT sp.*, b.brand_name FROM social_posts sp
               JOIN brands b ON sp.brand_id = b.id
               WHERE b.brand_name = ? AND sp.platform = ?
               ORDER BY sp.created_at DESC""",
            (brand_name, platform),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT sp.*, b.brand_name FROM social_posts sp
               JOIN brands b ON sp.brand_id = b.id
               WHERE b.brand_name = ?
               ORDER BY sp.platform, sp.created_at DESC""",
            (brand_name,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_brand_sentiment_summary(conn: sqlite3.Connection, brand_name: str) -> list[dict]:
    """Get aggregated sentiment data per platform for a brand."""
    rows = conn.execute(
        """SELECT sp.platform,
                  COUNT(DISTINCT sp.id) as total_posts,
                  SUM(sp.likes) as total_likes,
                  SUM(sp.num_comments) as total_comments,
                  AVG(sps.avg_sentiment) as avg_sentiment,
                  SUM(sps.positive_count) as total_positive,
                  SUM(sps.neutral_count) as total_neutral,
                  SUM(sps.negative_count) as total_negative,
                  SUM(sps.spam_count) as total_spam
           FROM social_posts sp
           JOIN brands b ON sp.brand_id = b.id
           LEFT JOIN social_post_sentiment sps ON sp.id = sps.post_id
           WHERE b.brand_name = ?
           GROUP BY sp.platform""",
        (brand_name,),
    ).fetchall()
    return [dict(r) for r in rows]
