"""
Brand snapshot builder — merged analysis layer.

Reads from existing financial and social source tables (read-only).
Produces a single summary row per brand per analysis date, stored in
brand_snapshots and exported as JSON for later LLM consumption.

No source tables are modified. Deleting brand_snapshots loses nothing —
it can always be rebuilt by re-querying both arms.
"""

import json
import logging
import os
import sqlite3
from datetime import date

logger = logging.getLogger(__name__)


# ── Financial queries ──────────────────────────────────────────────

def get_financial_summary(conn: sqlite3.Connection, ticker: str) -> dict | None:
    """
    Fetch the most recent fiscal year's financial metrics for a ticker.

    Join path: companies → sec_filings → financial_metrics
    Left-joins wacc_inputs for the same company and fiscal year.
    Returns a flat dict or None if no data exists.
    """
    row = conn.execute(
        """SELECT
               fm.revenue, fm.gross_margin, fm.operating_margin, fm.net_margin,
               fm.ebitda, fm.ebitda_source, fm.free_cash_flow,
               fm.roe, fm.debt_to_equity, fm.eps_diluted,
               sf.fiscal_year, sf.filing_date,
               wi.wacc
           FROM companies c
           JOIN sec_filings sf ON sf.company_id = c.id
           JOIN financial_metrics fm ON fm.filing_id = sf.id
           LEFT JOIN wacc_inputs wi ON wi.company_id = c.id
                                    AND wi.fiscal_year = sf.fiscal_year
           WHERE c.ticker = ?
           ORDER BY sf.fiscal_year DESC
           LIMIT 1""",
        (ticker,),
    ).fetchone()

    if not row:
        logger.info("No financial data found for ticker: %s", ticker)
        return None

    return dict(row)


# ── Social queries ─────────────────────────────────────────────────

def get_social_summary(conn: sqlite3.Connection, brand_name: str) -> dict | None:
    """
    Aggregate social metrics for a brand across all platforms.

    total_followers is MAX(followers) across platforms — this is the
    single highest platform follower count, NOT a de-duplicated total
    audience across all platforms. This is a v1 simplification; cross-
    platform audience de-duplication is not feasible from available data.

    Returns a flat dict or None if the brand has no social data.
    """
    brand_row = conn.execute(
        "SELECT id FROM brands WHERE brand_name = ?", (brand_name,)
    ).fetchone()
    if not brand_row:
        logger.info("Brand not found: %s", brand_name)
        return None

    brand_id = brand_row["id"]

    # Aggregate post metrics
    post_agg = conn.execute(
        """SELECT
               COUNT(*) as total_posts,
               COALESCE(SUM(likes), 0) as total_likes,
               COALESCE(SUM(num_comments), 0) as total_comments
           FROM social_posts
           WHERE brand_id = ?""",
        (brand_id,),
    ).fetchone()

    if not post_agg or post_agg["total_posts"] == 0:
        logger.info("No social posts found for brand: %s", brand_name)
        return None

    total_posts = post_agg["total_posts"]
    total_likes = post_agg["total_likes"]
    total_comments = post_agg["total_comments"]

    # Primary platform = the one with the most posts
    platform_row = conn.execute(
        """SELECT platform
           FROM social_posts
           WHERE brand_id = ?
           GROUP BY platform
           ORDER BY COUNT(*) DESC
           LIMIT 1""",
        (brand_id,),
    ).fetchone()
    primary_platform = platform_row["platform"] if platform_row else None

    # total_followers = MAX(followers) across all platforms for this brand.
    # This is the highest single-platform follower count, NOT a sum or
    # de-duplicated cross-platform total.
    followers_row = conn.execute(
        """SELECT MAX(followers) as max_followers
           FROM social_accounts
           WHERE brand_id = ?""",
        (brand_id,),
    ).fetchone()
    total_followers = (followers_row["max_followers"] or 0) if followers_row else 0

    return {
        "primary_platform":     primary_platform,
        "total_followers":      total_followers,
        "total_posts_analyzed": total_posts,
        "total_likes":          total_likes,
        "total_comments":       total_comments,
        "avg_likes_per_post":   round(total_likes / total_posts, 2) if total_posts else None,
        "avg_comments_per_post": round(total_comments / total_posts, 2) if total_posts else None,
    }


# ── Sentiment query ────────────────────────────────────────────────

def get_sentiment_summary(conn: sqlite3.Connection, brand_name: str) -> dict | None:
    """
    Aggregate sentiment from social_post_sentiment for a brand.

    Only includes rows where sentiment_basis = 'comments' — excludes
    'not_fetched' and 'caption_fallback' so the score reflects only
    posts with real analysed comment text.

    Returns raw counts and avg sentiment, or None if no comment-basis
    sentiment data exists.
    """
    row = conn.execute(
        """SELECT
               COALESCE(SUM(sps.positive_count), 0) as positive,
               COALESCE(SUM(sps.neutral_count), 0) as neutral,
               COALESCE(SUM(sps.negative_count), 0) as negative,
               COALESCE(SUM(sps.total_comments_analyzed), 0) as total_analyzed,
               AVG(sps.avg_sentiment) as avg_sentiment
           FROM social_post_sentiment sps
           JOIN social_posts sp ON sps.post_id = sp.id
           JOIN brands b ON sp.brand_id = b.id
           WHERE b.brand_name = ?
             AND sps.sentiment_basis = 'comments'""",
        (brand_name,),
    ).fetchone()

    if not row or row["total_analyzed"] == 0:
        logger.info("No comment-basis sentiment data for brand: %s", brand_name)
        return None

    return {
        "positive":       row["positive"],
        "neutral":        row["neutral"],
        "negative":       row["negative"],
        "total_analyzed": row["total_analyzed"],
        "avg_sentiment":  row["avg_sentiment"],
    }


# ── Lineage ────────────────────────────────────────────────────────

def get_run_id(conn: sqlite3.Connection, arm: str, target_name: str) -> dict | None:
    """
    Find the most recent pipeline_runs entry for a given arm and target.

    Uses LIKE matching on input_targets (stored as JSON text).
    This is a v1 approach — slightly imperfect for substring collisions
    but correct for all current brand/ticker names.

    Returns {"run_id": str, "started_at": str} or None.
    """
    row = conn.execute(
        """SELECT run_id, started_at
           FROM pipeline_runs
           WHERE arm = ? AND input_targets LIKE ?
           ORDER BY started_at DESC
           LIMIT 1""",
        (arm, f"%{target_name}%"),
    ).fetchone()

    if not row:
        return None
    return {"run_id": row["run_id"], "started_at": row["started_at"]}


# ── Commentary ─────────────────────────────────────────────────────

def get_arm_commentaries(conn: sqlite3.Connection, brand_name: str, ticker: str | None) -> dict:
    """
    Fetch the most recent financial and social commentary strings.
    Returns {"financial_commentary": str|None, "social_commentary": str|None}.
    """
    fin_commentary = None
    if ticker:
        row = conn.execute(
            """SELECT fc.commentary
               FROM financial_commentary fc
               JOIN companies c ON fc.company_id = c.id
               WHERE c.ticker = ?
               ORDER BY fc.created_at DESC
               LIMIT 1""",
            (ticker,),
        ).fetchone()
        if row:
            fin_commentary = row["commentary"]

    soc_commentary = None
    row = conn.execute(
        """SELECT sc.commentary
           FROM social_commentary sc
           JOIN brands b ON sc.brand_id = b.id
           WHERE b.brand_name = ?
           ORDER BY sc.created_at DESC
           LIMIT 1""",
        (brand_name,),
    ).fetchone()
    if row:
        soc_commentary = row["commentary"]

    return {
        "financial_commentary": fin_commentary,
        "social_commentary":    soc_commentary,
    }


# ── Methodology string ────────────────────────────────────────────

def build_methodology_string(sentiment: dict | None, social: dict | None) -> str:
    """
    Build a plain-English description of how sentiment was derived.
    Deterministic string formatting — no LLM involvement.
    """
    if not sentiment or not social:
        return "No comment-basis sentiment data available."

    total = sentiment["total_analyzed"]
    pos = sentiment["positive"]
    neu = sentiment["neutral"]
    neg = sentiment["negative"]
    platform = social.get("primary_platform", "unknown platform")
    posts = social.get("total_posts_analyzed", 0)

    return (
        f"VADER compound score on {total} comments across "
        f"{posts} {platform} posts. "
        f"{pos} positive, {neu} neutral, {neg} negative. "
        f"Only posts with fetched comment text included; "
        f"caption-only and unfetched posts excluded."
    )


# ── Orchestrator ───────────────────────────────────────────────────

def build_brand_snapshot(
    conn: sqlite3.Connection,
    brand_name: str,
    ticker: str | None = None,
) -> dict:
    """
    Build the complete brand snapshot dict by querying both arms.

    All numeric fields are computed deterministically. The merged_narrative
    field is left null — the LLM layer fills it later.
    """
    today = date.today().isoformat()

    # Query each source
    financial = get_financial_summary(conn, ticker) if ticker else None
    social = get_social_summary(conn, brand_name)
    sentiment = get_sentiment_summary(conn, brand_name)
    commentaries = get_arm_commentaries(conn, brand_name, ticker)

    # Lineage
    fin_run = get_run_id(conn, "financial", ticker) if ticker else None
    soc_run = get_run_id(conn, "social", brand_name)

    # Derived: engagement rate
    engagement_rate = None
    if social and social["total_followers"] and social["total_followers"] > 0:
        engagement_rate = round(
            (social["total_likes"] + social["total_comments"])
            / social["total_followers"],
            6,
        )

    # Derived: sentiment percentages
    sentiment_positive_pct = None
    sentiment_neutral_pct = None
    sentiment_negative_pct = None
    avg_sentiment_score = None
    comments_analyzed = 0

    if sentiment and sentiment["total_analyzed"] > 0:
        total = sentiment["total_analyzed"]
        comments_analyzed = total
        sentiment_positive_pct = round(sentiment["positive"] / total, 4)
        sentiment_neutral_pct = round(sentiment["neutral"] / total, 4)
        sentiment_negative_pct = round(sentiment["negative"] / total, 4)
        avg_sentiment_score = sentiment["avg_sentiment"]

    methodology = build_methodology_string(sentiment, social)

    return {
        "brand_name":             brand_name,
        "ticker":                 ticker,
        "analysis_date":          today,

        "financial_run_id":       fin_run["run_id"] if fin_run else None,
        "social_run_id":          soc_run["run_id"] if soc_run else None,
        "financial_fiscal_year":  financial["fiscal_year"] if financial else None,
        "social_data_as_of":      soc_run["started_at"] if soc_run else None,

        "filing_date":            financial["filing_date"] if financial else None,
        "revenue":                financial["revenue"] if financial else None,
        "gross_margin":           financial["gross_margin"] if financial else None,
        "operating_margin":       financial["operating_margin"] if financial else None,
        "net_margin":             financial["net_margin"] if financial else None,
        "ebitda":                 financial["ebitda"] if financial else None,
        "ebitda_source":          financial["ebitda_source"] if financial else None,
        "free_cash_flow":         financial["free_cash_flow"] if financial else None,
        "eps_diluted":            financial["eps_diluted"] if financial else None,
        "roe":                    financial["roe"] if financial else None,
        "debt_to_equity":         financial["debt_to_equity"] if financial else None,
        "wacc":                   financial["wacc"] if financial else None,

        "primary_platform":       social["primary_platform"] if social else None,
        "total_followers":        social["total_followers"] if social else None,
        "total_posts_analyzed":   social["total_posts_analyzed"] if social else None,
        "total_likes":            social["total_likes"] if social else None,
        "total_comments":         social["total_comments"] if social else None,
        "avg_likes_per_post":     social["avg_likes_per_post"] if social else None,
        "avg_comments_per_post":  social["avg_comments_per_post"] if social else None,
        "engagement_rate":        engagement_rate,

        "sentiment_positive_pct": sentiment_positive_pct,
        "sentiment_neutral_pct":  sentiment_neutral_pct,
        "sentiment_negative_pct": sentiment_negative_pct,
        "avg_sentiment_score":    avg_sentiment_score,
        "comments_analyzed":      comments_analyzed,
        "sentiment_methodology":  methodology,

        "financial_commentary":   commentaries["financial_commentary"],
        "social_commentary":      commentaries["social_commentary"],

        "merged_narrative":       None,
        "narrative_model":        None,
        "narrative_generated_at": None,
    }


# ── Persistence ────────────────────────────────────────────────────

# Column order must match the INSERT statement below.
_SNAPSHOT_COLUMNS = [
    "brand_name", "ticker", "analysis_date",
    "financial_run_id", "social_run_id",
    "financial_fiscal_year", "social_data_as_of",
    "filing_date",
    "revenue", "gross_margin", "operating_margin", "net_margin",
    "ebitda", "ebitda_source", "free_cash_flow", "eps_diluted",
    "roe", "debt_to_equity", "wacc",
    "primary_platform", "total_followers", "total_posts_analyzed",
    "total_likes", "total_comments",
    "avg_likes_per_post", "avg_comments_per_post", "engagement_rate",
    "sentiment_positive_pct", "sentiment_neutral_pct", "sentiment_negative_pct",
    "avg_sentiment_score", "comments_analyzed", "sentiment_methodology",
    "financial_commentary", "social_commentary",
    "merged_narrative", "narrative_model", "narrative_generated_at",
]


def upsert_snapshot(conn: sqlite3.Connection, snapshot: dict) -> None:
    """
    Insert or update a brand snapshot.

    Uses INSERT ... ON CONFLICT(brand_name, analysis_date) DO UPDATE
    so that re-running on the same day for the same brand overwrites
    the existing row in place without deleting and recreating it.
    """
    cols = ", ".join(_SNAPSHOT_COLUMNS)
    placeholders = ", ".join(["?"] * len(_SNAPSHOT_COLUMNS))

    # Build the SET clause for ON CONFLICT — update every column except the
    # unique key pair (brand_name, analysis_date) and snapshot_built_at (reset
    # via DEFAULT on insert, updated explicitly here).
    update_cols = [c for c in _SNAPSHOT_COLUMNS if c not in ("brand_name", "analysis_date")]
    set_clause = ", ".join(f"{c} = excluded.{c}" for c in update_cols)
    set_clause += ", snapshot_built_at = CURRENT_TIMESTAMP"

    values = [snapshot.get(c) for c in _SNAPSHOT_COLUMNS]

    conn.execute(
        f"""INSERT INTO brand_snapshots ({cols})
            VALUES ({placeholders})
            ON CONFLICT(brand_name, analysis_date) DO UPDATE SET
            {set_clause}""",
        values,
    )
    conn.commit()
    logger.info(
        "Snapshot upserted: %s @ %s",
        snapshot["brand_name"], snapshot["analysis_date"],
    )


# ── JSON export ────────────────────────────────────────────────────

def export_snapshot_json(snapshot: dict, output_dir: str = "output") -> str:
    """
    Write snapshot dict to a JSON file for later LLM consumption.

    If merged_narrative is a JSON string (from the DB), parse it to a dict
    so it nests as an object in the export rather than a string-in-a-string.

    Path: {output_dir}/snapshots/{brand_name}_{analysis_date}.json
    """
    snapshots_dir = os.path.join(output_dir, "snapshots")
    os.makedirs(snapshots_dir, exist_ok=True)

    # Nest merged_narrative as an object if it's a JSON string
    export = dict(snapshot)
    if isinstance(export.get("merged_narrative"), str):
        try:
            export["merged_narrative"] = json.loads(export["merged_narrative"])
        except json.JSONDecodeError:
            pass  # leave as string if not valid JSON

    filename = f"{snapshot['brand_name']}_{snapshot['analysis_date']}.json"
    path = os.path.join(snapshots_dir, filename)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(export, f, indent=2, ensure_ascii=False, default=str)

    logger.info("Snapshot exported: %s", path)
    return path
