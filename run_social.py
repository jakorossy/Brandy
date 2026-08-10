#!/usr/bin/env python3
"""
Brandy — Social Arm Entry Point.

Pipeline: provider fetch → SQLite storage → comment classification
        → sentiment aggregation → optional LLM commentary → Excel export.

Usage:
    python run_social.py "Toyota,BMW" --provider local_file
    python run_social.py "Starbucks:starbucks" --provider scrapecreators
    python run_social.py "Starbucks:starbucks" --provider scrapecreators --since 2025-01-01
    python run_social.py "Starbucks:starbucks" --provider scrapecreators \\
        --start-date 2025-01-01 --end-date 2025-03-31 --post-limit 30

Comment fetching (ScrapeCreators only — costs 1 credit per post):
    python run_social.py "Starbucks:starbucks" --provider scrapecreators --fetch-comments
    python run_social.py "Starbucks:starbucks" --provider scrapecreators \\
        --fetch-comments --comment-post-limit 3 --top-comments 5
"""

import logging
import os
import sys
import uuid

import pandas as pd
import yaml

from brandy.db.engine import init_db
from brandy.social.ingest import get_provider, PLATFORMS
from brandy.social.comments import classify_comment, aggregate_post_sentiment
from brandy.social.sentiment import get_sentiment_score
from brandy.social.storage import (
    upsert_brand,
    upsert_social_account,
    insert_post,
    insert_comments_batch,
    insert_post_sentiment,
    get_brand_sentiment_summary,
)
from brandy.financial.storage import start_run, finish_run, store_raw_payload
from brandy.llm.commentary import generate_social_commentary
from brandy.export.excel import build_social_workbook


def load_config():
    try:
        with open("config/settings.yaml", "r") as f:
            settings = yaml.safe_load(f)
    except FileNotFoundError:
        settings = {}
    try:
        with open("config/social_config.yaml", "r") as f:
            social_config = yaml.safe_load(f)
    except FileNotFoundError:
        social_config = {}
    return settings, social_config


def _llm_is_available() -> bool:
    """
    Check whether a real OpenAI API key is present before attempting any LLM call.
    Placeholder strings (from .env.example) are treated as missing.
    """
    key = os.environ.get("OPENAI_API_KEY", "")
    placeholders = {"", "your_key_here", "your-key-here", "sk-...", "placeholder"}
    return key.strip().lower() not in placeholders


def run(
    brands: list[str],
    data_dir: str = None,
    provider_name: str = None,
    since: str | None = None,
    until: str | None = None,
    post_limit: int | None = None,
    fetch_comments: bool = False,
    comment_post_limit: int = 3,
    top_comments: int = 5,
):
    settings, social_config = load_config()

    # Logging
    log_level = settings.get("logging", {}).get("level", "INFO")
    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    logger = logging.getLogger("brandy.social")

    # DB
    db_path = settings.get("database", {}).get("path", "brandy.db")
    conn = init_db(db_path)

    # Provider selection: CLI arg > config > default
    chosen_provider = provider_name or social_config.get("provider", "local_file")
    data_dir = data_dir or social_config.get("data_directory", "data/raw")

    # Post limit: CLI arg > config > None (no limit)
    if post_limit is None:
        post_limit = social_config.get("scrapecreators", {}).get("post_limit") if chosen_provider == "scrapecreators" else None

    if chosen_provider == "local_file":
        provider = get_provider("local_file", data_dir=data_dir)
    elif chosen_provider == "scrapecreators":
        provider = get_provider(
            "scrapecreators",
            fetch_comments=fetch_comments,
            comment_post_limit=comment_post_limit,
            top_comments=top_comments,
        )
    else:
        raise ValueError(f"Unknown provider: '{chosen_provider}'")

    logger.info("Using provider: %s", provider.name)
    if since or until or post_limit:
        logger.info("Filters — since: %s, until: %s, limit: %s", since or "—", until or "—", post_limit or "—")
    if fetch_comments:
        logger.info(
            "Comment fetching enabled — top %d posts per brand, keep top %d comments each",
            comment_post_limit, top_comments,
        )

    # LLM availability check — warn once upfront, skip silently per brand
    use_llm = _llm_is_available()
    if not use_llm:
        logger.warning(
            "OPENAI_API_KEY is missing or is a placeholder — "
            "LLM commentary will be skipped for all brands."
        )
    llm_model = settings.get("llm", {}).get("model", "gpt-4.1-mini")

    # Sentiment config
    spam_keywords = social_config.get("spam_keywords")
    sentiment_cfg = social_config.get("sentiment", {})
    pos_thresh = sentiment_cfg.get("positive_threshold", 0.05)
    neg_thresh = sentiment_cfg.get("negative_threshold", -0.05)
    min_comment_len = sentiment_cfg.get("min_comment_length", 5)

    # Pipeline run
    run_id = str(uuid.uuid4())
    start_run(conn, run_id, "social", brands, {
        "provider": chosen_provider,
        "since": since,
        "until": until,
        "post_limit": post_limit,
    })
    logger.info("=== Social pipeline run %s ===", run_id)
    logger.info("Brands: %s", brands)

    all_post_rows = []
    all_sentiment_rows = []
    all_account_rows = []
    all_comment_rows = []
    commentaries = []

    for brand_spec in brands:
        # Split "Brand:handle" before anything touches the DB.
        # The clean name is stored; the full spec (with handle) is
        # passed to the provider, which splits it again internally.
        brand_name = brand_spec.split(":", 1)[0] if ":" in brand_spec else brand_spec
        logger.info("── Processing %s ──", brand_name)
        brand_id = upsert_brand(conn, brand_name)

        # 1. Fetch posts via the selected provider
        ingested = provider.fetch_brand(
            brand_spec, PLATFORMS,
            since=since, until=until, limit=post_limit,
        )

        if not ingested:
            logger.warning("No data found for %s", brand_name)
            continue

        account_metas = [p for p in ingested if p.get("_account_meta")]
        posts = [p for p in ingested if not p.get("_account_meta")]

        # 2. Store account-level metadata
        for meta in account_metas:
            upsert_social_account(
                conn, brand_id, meta["platform"],
                followers=meta.get("followers"),
                account_handle=meta.get("account_handle"),
                bio=meta.get("bio"),
            )
            all_account_rows.append({
                "brand":       brand_name,
                "platform":    meta["platform"],
                "handle":      meta.get("account_handle", ""),
                "followers":   meta.get("followers", 0),
                "following":   meta.get("following", 0),
                "is_verified": meta.get("is_verified", ""),
                "category":    meta.get("category", ""),
            })

        # 3. Process each post
        for post_data in posts:
            platform = post_data["platform"]
            post_id = insert_post(conn, brand_id, post_data)

            raw_comments = post_data.get("comments", [])
            comments_fetched = post_data.get("_comments_fetched", True)

            classified_comments = []
            for c in raw_comments:
                classification = classify_comment(
                    c.get("text", ""),
                    spam_keywords=spam_keywords,
                    min_length=min_comment_len,
                    pos_threshold=pos_thresh,
                    neg_threshold=neg_thresh,
                )
                classified_comments.append({
                    "text":      c.get("text", ""),
                    "commenter": c.get("commenter", ""),
                    **classification,
                })

            if classified_comments:
                # ── Path A: actual comment text available ──────────────
                insert_comments_batch(conn, post_id, brand_id, platform, classified_comments)
                agg = aggregate_post_sentiment(classified_comments)
                caption_score = get_sentiment_score(post_data.get("post_text", ""))
                insert_post_sentiment(conn, post_id, agg, caption_score, basis="comments")
                sentiment_basis = "comments"

                # Collect comment rows for Excel export
                for c in classified_comments:
                    all_comment_rows.append({
                        "brand":               brand_name,
                        "platform":            platform,
                        "post_date":           post_data.get("post_date", ""),
                        "post_id_external":    post_data.get("post_id_external", ""),
                        "commenter":           c.get("commenter", ""),
                        "comment_text":        c.get("text", ""),
                        "comment_like_count":  c.get("comment_like_count", 0),
                        "sentiment_label":     c.get("sentiment_label", ""),
                        "sentiment_score":     c.get("sentiment_score"),
                        "is_spam":             c.get("is_spam", False),
                        "is_irrelevant":       c.get("is_irrelevant", False),
                    })

            elif not comments_fetched and post_data.get("num_comments", 0) > 0:
                # ── Path B: comments exist on the post but were NOT fetched ──
                # e.g. ScrapeCreators profile endpoint — counts only, no text.
                # Do NOT run VADER on the caption and pretend it's comment sentiment.
                logger.info(
                    "Post %s has %d comments but text was not fetched — "
                    "sentiment recorded as unavailable",
                    post_data.get("post_id_external", "?"),
                    post_data.get("num_comments", 0),
                )
                agg = {
                    "total_comments_analyzed": 0,
                    "positive_count": 0, "neutral_count": 0, "negative_count": 0,
                    "spam_count": 0, "irrelevant_count": 0,
                    "avg_sentiment": None,
                }
                insert_post_sentiment(conn, post_id, agg, caption_sentiment=None, basis="not_fetched")
                sentiment_basis = "not_fetched"

            else:
                # ── Path C: no comments on the post — caption fallback ──
                caption_text = post_data.get("post_text", "")
                caption_score = get_sentiment_score(caption_text) if caption_text else None
                agg = {
                    "total_comments_analyzed": 0,
                    "positive_count": 0, "neutral_count": 0, "negative_count": 0,
                    "spam_count": 0, "irrelevant_count": 0,
                    "avg_sentiment": caption_score,
                }
                insert_post_sentiment(conn, post_id, agg, caption_score, basis="caption_fallback")
                sentiment_basis = "caption_fallback"

            all_post_rows.append({
                "brand":           brand_name,
                "platform":        platform,
                "post_date":       post_data.get("post_date", ""),
                "post_text":       (post_data.get("post_text", "") or "")[:200],
                "likes":           post_data.get("likes", 0),
                "comment_count":   post_data.get("num_comments", 0),
                "source_file":     post_data.get("source_file", ""),
            })

            all_sentiment_rows.append({
                "brand":              brand_name,
                "platform":           platform,
                "post_date":          post_data.get("post_date", ""),
                "sentiment_basis":    sentiment_basis,
                "comments_analyzed":  agg.get("total_comments_analyzed", 0),
                "positive":           agg.get("positive_count", 0),
                "neutral":            agg.get("neutral_count", 0),
                "negative":           agg.get("negative_count", 0),
                "spam":               agg.get("spam_count", 0),
                "avg_sentiment":      agg.get("avg_sentiment"),
            })

        # 4. LLM commentary — only if key is valid
        if use_llm:
            platform_summaries = get_brand_sentiment_summary(conn, brand_name)
            if platform_summaries:
                commentary = generate_social_commentary(brand_name, platform_summaries, model=llm_model)
                if commentary:
                    conn.execute(
                        "INSERT INTO social_commentary (brand_id, run_id, commentary, model_used) VALUES (?, ?, ?, ?)",
                        (brand_id, run_id, commentary, llm_model),
                    )
                    conn.commit()
                    commentaries.append({"brand": brand_name, "commentary": commentary})

        logger.info("✓ %s — %d posts processed", brand_name, len(posts))

    # 5. Excel export
    output_path = None
    if all_post_rows:
        posts_df = pd.DataFrame(all_post_rows)
        sentiment_df = pd.DataFrame(all_sentiment_rows) if all_sentiment_rows else None
        accounts_df = pd.DataFrame(all_account_rows) if all_account_rows else None
        commentary_df = pd.DataFrame(commentaries) if commentaries else None
        comments_df = pd.DataFrame(all_comment_rows) if all_comment_rows else None

        output_dir = settings.get("output", {}).get("directory", "output")
        output_path = build_social_workbook(
            posts_df, sentiment_df, accounts_df, commentary_df,
            comments_df=comments_df,
            output_dir=output_dir,
        )
        logger.info("Excel report: %s", output_path)

    # 6. Finalize
    finish_run(conn, run_id, "completed", output_path)
    conn.close()

    print(f"\n{'='*60}")
    print(f"  Social pipeline complete")
    print(f"  Brands:          {', '.join(brands)}")
    print(f"  Posts processed: {len(all_post_rows)}")
    if since or until:
        print(f"  Date filter:     {since or 'start'} → {until or 'now'}")
    if post_limit:
        print(f"  Post limit:      {post_limit}")
    if output_path:
        print(f"  Output:          {output_path}")
    print(f"{'='*60}")

    return output_path


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Brandy social media pipeline")
    parser.add_argument("brands", nargs="?", help="Comma-separated brand names (use Brand:handle for custom handles)")
    parser.add_argument("--provider", default=None, help="Provider: local_file | scrapecreators")
    parser.add_argument("--data-dir", default=None, dest="data_dir", help="Data directory (local_file provider only)")

    # Date filters — two equivalent styles
    parser.add_argument("--since", default=None, help="Include posts on or after this date (YYYY-MM-DD)")
    parser.add_argument("--start-date", default=None, dest="start_date", help="Alias for --since")
    parser.add_argument("--end-date", default=None, dest="end_date", help="Include posts on or before this date (YYYY-MM-DD)")

    parser.add_argument("--post-limit", default=None, type=int, dest="post_limit",
                        help="Max posts per brand (applied after date filtering, newest-first)")

    # Comment fetching (ScrapeCreators only — costs 1 credit per post)
    parser.add_argument("--fetch-comments", action="store_true", dest="fetch_comments",
                        help="Fetch comment text for top posts (ScrapeCreators only, off by default)")
    parser.add_argument("--comment-post-limit", default=3, type=int, dest="comment_post_limit",
                        help="Max posts to fetch comments for, selected by highest likes (default 3)")
    parser.add_argument("--top-comments", default=5, type=int, dest="top_comments",
                        help="Keep only the top-N comments by like count per post (default 5)")

    args = parser.parse_args()

    if args.brands:
        brands = [b.strip() for b in args.brands.split(",") if b.strip()]
    else:
        raw = input("Enter brand names (comma-separated, e.g. Toyota,BMW): ").strip()
        if not raw:
            print("No brands entered.")
            sys.exit(1)
        brands = [b.strip() for b in raw.split(",") if b.strip()]

    # --since and --start-date are equivalent; --since takes priority
    since = args.since or args.start_date

    run(
        brands,
        data_dir=args.data_dir,
        provider_name=args.provider,
        since=since,
        until=args.end_date,
        post_limit=args.post_limit,
        fetch_comments=args.fetch_comments,
        comment_post_limit=args.comment_post_limit,
        top_comments=args.top_comments,
    )


if __name__ == "__main__":
    main()
