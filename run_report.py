#!/usr/bin/env python3
"""
Brandy — HTML Report Generator.

Reads a brand snapshot JSON (produced by run_analysis.py and enriched by
run_narrative.py) and writes a clean human-readable HTML report.

Usage:
    python run_report.py "Starbucks"
    python run_report.py "Starbucks,Nike" --date 2026-05-19
    python run_report.py "Starbucks" --output-dir output
"""

import json
import logging
import os
import sys
from datetime import date

import yaml

from brandy.report.builder import build_report


def load_config():
    try:
        with open("config/settings.yaml", "r") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def load_snapshot_json(brand: str, analysis_date: str, output_dir: str) -> dict | None:
    """Load a brand's snapshot JSON file."""
    path = os.path.join(output_dir, "snapshots", f"{brand}_{analysis_date}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Brandy HTML report generator")
    parser.add_argument(
        "brands", nargs="?",
        help="Comma-separated brand names (must have an existing snapshot JSON)",
    )
    parser.add_argument(
        "--date", default=None,
        help="Analysis date (YYYY-MM-DD, default: today)",
    )
    parser.add_argument(
        "--output-dir", default=None,
        help="Output directory (default: from settings.yaml or 'output')",
    )
    args = parser.parse_args()

    if args.brands:
        brands = [b.strip() for b in args.brands.split(",") if b.strip()]
    else:
        raw = input("Enter brand names (comma-separated): ").strip()
        if not raw:
            print("No brands entered.")
            sys.exit(1)
        brands = [b.strip() for b in raw.split(",") if b.strip()]

    analysis_date = args.date or date.today().isoformat()

    settings = load_config()
    log_level = settings.get("logging", {}).get("level", "INFO")
    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    logger = logging.getLogger("brandy.report")

    output_dir = args.output_dir or settings.get("output", {}).get("directory", "output")

    print(f"\n{'='*70}")
    print(f"  Brandy — Report Generator")
    print(f"  Brands: {', '.join(brands)}")
    print(f"  Date:   {analysis_date}")
    print(f"{'='*70}")

    rendered = 0
    for brand in brands:
        snapshot = load_snapshot_json(brand, analysis_date, output_dir)
        if snapshot is None:
            logger.error(
                "No snapshot found for %s on %s. "
                "Run: python3 run_analysis.py \"%s\" first.",
                brand, analysis_date, brand,
            )
            continue

        path = build_report(snapshot, output_dir=output_dir)
        print(f"\n  {brand}  →  {path}")
        rendered += 1

    print(f"\n{'='*70}")
    print(f"  Reports written: {rendered}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
