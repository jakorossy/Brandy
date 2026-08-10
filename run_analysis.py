#!/usr/bin/env python3
"""
Brandy — Merged Analysis Layer Entry Point.

Builds brand-level snapshots by querying the financial and social arms.
All calculations are deterministic. The merged_narrative field is left
null for the LangChain layer to fill later.

Usage:
    python run_analysis.py "Starbucks:SBUX"
    python run_analysis.py "Starbucks:SBUX,Nike:NKE"
    python run_analysis.py "Starbucks"                  # social data only
"""

import logging
import sys

import yaml

from brandy.db.engine import init_db
from brandy.analysis.snapshot import (
    build_brand_snapshot,
    upsert_snapshot,
    export_snapshot_json,
)


def load_config():
    try:
        with open("config/settings.yaml", "r") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def parse_brand_specs(raw: str) -> list[tuple[str, str | None]]:
    """
    Parse 'Brand:TICKER,Brand2' into [(brand_name, ticker|None), ...].

    Examples:
        "Starbucks:SBUX"       → [("Starbucks", "SBUX")]
        "Starbucks:SBUX,Nike"  → [("Starbucks", "SBUX"), ("Nike", None)]
        "Starbucks"            → [("Starbucks", None)]
    """
    specs = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            brand, ticker = item.split(":", 1)
            specs.append((brand.strip(), ticker.strip()))
        else:
            specs.append((item, None))
    return specs


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Brandy merged brand snapshot builder")
    parser.add_argument(
        "brands", nargs="?",
        help="Brand specs: 'Brand:TICKER,Brand2' — ticker is optional",
    )
    args = parser.parse_args()

    if args.brands:
        specs = parse_brand_specs(args.brands)
    else:
        raw = input("Enter brand specs (e.g. Starbucks:SBUX,Nike:NKE): ").strip()
        if not raw:
            print("No brands entered.")
            sys.exit(1)
        specs = parse_brand_specs(raw)

    settings = load_config()

    log_level = settings.get("logging", {}).get("level", "INFO")
    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    logger = logging.getLogger("brandy.analysis")

    db_path = settings.get("database", {}).get("path", "brandy.db")
    conn = init_db(db_path)

    output_dir = settings.get("output", {}).get("directory", "output")

    print(f"\n{'='*70}")
    print(f"  Brandy — Brand Snapshot Builder")
    print(f"  Brands: {', '.join(b for b, _ in specs)}")
    print(f"{'='*70}")

    results = []

    for brand_name, ticker in specs:
        logger.info("Building snapshot for %s (ticker=%s)", brand_name, ticker or "—")

        snapshot = build_brand_snapshot(conn, brand_name, ticker)
        upsert_snapshot(conn, snapshot)
        json_path = export_snapshot_json(snapshot, output_dir)

        results.append({
            "brand":      brand_name,
            "ticker":     ticker or "—",
            "fy":         snapshot.get("financial_fiscal_year") or "—",
            "posts":      snapshot.get("total_posts_analyzed") or "—",
            "comments":   snapshot.get("comments_analyzed") or "—",
            "sentiment":  f"{snapshot['avg_sentiment_score']:.3f}" if snapshot.get("avg_sentiment_score") is not None else "—",
            "json_path":  json_path,
        })

    # Summary
    print(f"\n  {'Brand':<16} {'Ticker':<8} {'FY':<6} {'Posts':<7} {'Comments':<10} {'Sentiment':<10} JSON")
    print(f"  {'-'*16} {'-'*8} {'-'*6} {'-'*7} {'-'*10} {'-'*10} {'-'*30}")
    for r in results:
        print(
            f"  {r['brand']:<16} {r['ticker']:<8} {str(r['fy']):<6} "
            f"{str(r['posts']):<7} {str(r['comments']):<10} {r['sentiment']:<10} "
            f"{r['json_path']}"
        )

    conn.close()

    print(f"\n{'='*70}")
    print(f"  Snapshots built: {len(results)}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
