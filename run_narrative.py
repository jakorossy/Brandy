#!/usr/bin/env python3
"""
Brandy — Local LLM Narrative Layer.

Reads brand snapshot JSON files and generates structured narratives
via a local Ollama model. Writes results back to brand_snapshots
and re-exports the updated JSON.

Usage:
    python run_narrative.py "Starbucks"
    python run_narrative.py "Starbucks,Nike" --model llama3.1:8b
    python run_narrative.py "Starbucks" --date 2026-04-12
"""

import json
import logging
import os
import sys
from datetime import date, datetime

import yaml

from brandy.db.engine import init_db
from brandy.llm.local_narrative import generate_narrative, NARRATIVE_FIELDS


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


def update_snapshot_db(
    conn,
    brand: str,
    analysis_date: str,
    narrative_dict: dict,
    model: str,
):
    """Write the structured narrative back to brand_snapshots."""
    conn.execute(
        """UPDATE brand_snapshots
           SET merged_narrative = ?,
               narrative_model = ?,
               narrative_generated_at = CURRENT_TIMESTAMP
           WHERE brand_name = ? AND analysis_date = ?""",
        (
            json.dumps(narrative_dict, ensure_ascii=False),
            model,
            brand,
            analysis_date,
        ),
    )
    conn.commit()


def export_updated_snapshot(snapshot: dict, output_dir: str) -> str:
    """Re-export the snapshot JSON with the narrative nested as an object."""
    snapshots_dir = os.path.join(output_dir, "snapshots")
    os.makedirs(snapshots_dir, exist_ok=True)
    filename = f"{snapshot['brand_name']}_{snapshot['analysis_date']}.json"
    path = os.path.join(snapshots_dir, filename)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False, default=str)

    return path


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Brandy local LLM narrative generator")
    parser.add_argument(
        "brands", nargs="?",
        help="Comma-separated brand names (must have existing snapshot JSONs)",
    )
    parser.add_argument(
        "--model", default="deepseek-r1:8b",
        help="Ollama model name (default: deepseek-r1:8b)",
    )
    parser.add_argument(
        "--date", default=None,
        help="Analysis date (YYYY-MM-DD, default: today)",
    )
    parser.add_argument(
        "--host", default="http://localhost:11434",
        help="Ollama host URL (default: http://localhost:11434)",
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
    logger = logging.getLogger("brandy.narrative")

    db_path = settings.get("database", {}).get("path", "brandy.db")
    conn = init_db(db_path)
    output_dir = settings.get("output", {}).get("directory", "output")

    print(f"\n{'='*70}")
    print(f"  Brandy — Narrative Generator")
    print(f"  Brands: {', '.join(brands)}")
    print(f"  Model:  {args.model}")
    print(f"  Date:   {analysis_date}")
    print(f"{'='*70}")

    # Load all snapshots
    snapshots = []
    for brand in brands:
        snapshot = load_snapshot_json(brand, analysis_date, output_dir)
        if snapshot is None:
            logger.error(
                "No snapshot found for %s on %s. "
                "Run: python3 run_analysis.py \"%s\" first.",
                brand, analysis_date, brand,
            )
            continue
        snapshots.append(snapshot)

    if not snapshots:
        print("\n  No snapshots found. Nothing to do.")
        conn.close()
        sys.exit(1)

    # Generate narratives
    narratives = generate_narrative(
        snapshots,
        model=args.model,
        host=args.host,
    )

    # Write results
    for snapshot, narrative in zip(snapshots, narratives):
        brand = snapshot["brand_name"]

        # Update DB
        update_snapshot_db(conn, brand, analysis_date, narrative, args.model)

        # Update snapshot dict and re-export JSON
        snapshot["merged_narrative"] = narrative
        snapshot["narrative_model"] = args.model
        snapshot["narrative_generated_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        json_path = export_updated_snapshot(snapshot, output_dir)

        # Print to stdout
        print(f"\n{'─'*70}")
        print(f"  {brand}")
        print(f"{'─'*70}")
        print(json.dumps(narrative, indent=2, ensure_ascii=False))
        print(f"\n  Saved → {json_path}")

    conn.close()

    print(f"\n{'='*70}")
    print(f"  Narratives generated: {len(narratives)}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
