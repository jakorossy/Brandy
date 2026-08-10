#!/usr/bin/env python3
"""
Brandy — Master Workflow Runner.

Orchestrates the full pipeline by shelling out to the existing runners in order.
No pipeline internals are imported; each stage is a subprocess.run call.

Modes:
    financial   run_financial only → Excel output, then stop
    social      run_social → run_analysis → run_narrative → run_report
    both        run_financial → run_social → run_analysis → run_narrative → run_report

Usage:
    python run_workflow.py "Starbucks" --ticker SBUX
    python run_workflow.py "Starbucks" --ticker SBUX --mode financial
    python run_workflow.py "Rolex" --mode social
    python run_workflow.py "Starbucks" --ticker SBUX --skip-narrative
"""

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date


# ── Stage result ───────────────────────────────────────────────────

@dataclass
class StageResult:
    name: str
    status: str       # "ok" | "failed" | "skipped"
    duration: float = 0.0
    output_hint: str = ""   # predictable output path, shown in summary
    error_message: str = ""


# ── Subprocess helper ──────────────────────────────────────────────

def run_stage(cmd: list) -> tuple:
    """
    Run a subprocess, stream stdout live, capture stderr for error reporting.
    Returns (success: bool, duration: float, error_snippet: str).
    """
    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            stderr=subprocess.PIPE,
            text=True,
        )
        duration = time.monotonic() - start

        if proc.returncode != 0:
            lines = [l for l in (proc.stderr or "").strip().splitlines() if l.strip()]
            snippet = " | ".join(lines[-3:]) if lines else f"exit {proc.returncode}"
            return False, duration, snippet

        return True, duration, ""

    except FileNotFoundError:
        duration = time.monotonic() - start
        return False, duration, f"command not found: {cmd[0]}"


# ── Summary printer ────────────────────────────────────────────────

def print_summary(
    brand: str,
    ticker: str | None,
    mode: str,
    analysis_date: str,
    results: list,
) -> bool:
    """Print the final stage summary. Returns True if all stages succeeded."""
    W = 70
    sep = "=" * W

    print(f"\n{sep}")
    print(f"  Brandy — Workflow Summary")
    print(sep)
    print(f"  Brand:  {brand}")
    if ticker:
        print(f"  Ticker: {ticker}")
    print(f"  Mode:   {mode}")
    print(f"  Date:   {analysis_date}")
    print()

    failed = 0
    for r in results:
        if r.status == "ok":
            hint = f"  →  {r.output_hint}" if r.output_hint else ""
            print(f"  ✓  {r.name:<20} {r.duration:5.1f}s{hint}")
        elif r.status == "skipped":
            print(f"  –  {r.name:<20}        (skipped)")
        else:
            failed += 1
            msg = r.error_message[:55] if r.error_message else "failed"
            print(f"  ✗  {r.name:<20}        ({msg})")

    print()
    active = [r for r in results if r.status != "skipped"]
    if failed == 0:
        print("  Completed successfully.")
    elif failed == len(active):
        print("  Pipeline failed.")
    else:
        noun = "stage" if failed == 1 else "stages"
        print(f"  Completed with {failed} {noun} failed.")
    print(sep)

    return failed == 0


# ── Argument validation ────────────────────────────────────────────

def validate(args: argparse.Namespace) -> None:
    if args.mode in ("financial", "both") and not args.ticker:
        print(f"Error: --mode {args.mode} requires --ticker.")
        sys.exit(1)


# ── Stage counter helper ───────────────────────────────────────────

def stage_label(current: int, total: int, name: str) -> str:
    return f"[{current}/{total}] {name}"


# ── Main ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Brandy master workflow runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_workflow.py "Starbucks" --ticker SBUX
  python run_workflow.py "Starbucks" --ticker SBUX --mode financial
  python run_workflow.py "Rolex" --mode social
  python run_workflow.py "McDonald's" --ticker MCD --skip-narrative
        """,
    )
    parser.add_argument(
        "brand",
        help="Brand name — primary identity anchor for social, snapshot, narrative, and report stages",
    )
    parser.add_argument(
        "--ticker",
        default=None,
        metavar="TICKER",
        help="Listed-company ticker for the financial arm (e.g. SBUX, MCD). "
             "Required for --mode financial and --mode both.",
    )
    parser.add_argument(
        "--mode",
        choices=["financial", "social", "both"],
        default="both",
        help="Pipeline mode (default: both). "
             "'financial' stops after run_financial.py. "
             "'social' skips the financial arm entirely.",
    )
    parser.add_argument(
        "--skip-narrative",
        action="store_true",
        dest="skip_narrative",
        help="Skip the Ollama narrative stage. "
             "The report will still render without narrative text.",
    )
    parser.add_argument(
        "--skip-report",
        action="store_true",
        dest="skip_report",
        help="Stop before HTML report generation.",
    )
    parser.add_argument(
        "--model",
        default="deepseek-r1:8b",
        metavar="MODEL",
        help="Ollama model for narrative generation (default: deepseek-r1:8b).",
    )
    parser.add_argument(
        "--date",
        default=None,
        metavar="YYYY-MM-DD",
        help="Analysis date passed to downstream stages (default: today). "
             "Useful when re-running over a previously built snapshot.",
    )
    args = parser.parse_args()

    validate(args)

    analysis_date = args.date or date.today().isoformat()
    py = sys.executable
    results: list[StageResult] = []

    # Pre-compute total stage count for progress labels
    if args.mode == "financial":
        total = 1
    else:
        total = 1  # social
        total += 1  # analysis
        total += 0 if args.skip_narrative else 1
        total += 0 if args.skip_report else 1
        if args.mode == "both":
            total += 1  # financial at the front

    print(f"\n{'='*70}")
    print(f"  Brandy — Workflow Runner")
    ticker_str = args.ticker or "—"
    print(f"  Brand: {args.brand}  |  Ticker: {ticker_str}  |  Mode: {args.mode}")
    print(f"  Date:  {analysis_date}")
    print(f"{'='*70}\n")

    stage = 0

    # ── 1. Financial ───────────────────────────────────────────────
    if args.mode in ("financial", "both"):
        stage += 1
        print(f"{stage_label(stage, total, 'Financial arm')} — {args.ticker}\n")

        ok, dur, err = run_stage([py, "run_financial.py", args.ticker])
        results.append(StageResult(
            name="Financial",
            status="ok" if ok else "failed",
            duration=dur,
            error_message=err,
        ))
        if not ok:
            print(f"\n  ✗ Financial stage failed. Stopping.")
            print_summary(args.brand, args.ticker, args.mode, analysis_date, results)
            sys.exit(1)

    # Financial-only mode ends here.
    if args.mode == "financial":
        print_summary(args.brand, args.ticker, args.mode, analysis_date, results)
        sys.exit(0)

    # ── 2. Social ──────────────────────────────────────────────────
    stage += 1
    print(f"\n{stage_label(stage, total, 'Social arm')} — {args.brand}\n")

    ok, dur, err = run_stage([py, "run_social.py", args.brand])
    results.append(StageResult(
        name="Social",
        status="ok" if ok else "failed",
        duration=dur,
        error_message=err,
    ))
    if not ok:
        print(f"\n  ✗ Social stage failed. Stopping.")
        print_summary(args.brand, args.ticker, args.mode, analysis_date, results)
        sys.exit(1)

    # ── 3. Analysis / snapshot ─────────────────────────────────────
    stage += 1
    print(f"\n{stage_label(stage, total, 'Analysis / snapshot')} — {args.brand}\n")

    # run_analysis.py accepts "Brand:TICKER" to thread the ticker through.
    analysis_spec = f"{args.brand}:{args.ticker}" if args.ticker else args.brand
    ok, dur, err = run_stage([py, "run_analysis.py", analysis_spec])
    results.append(StageResult(
        name="Analysis",
        status="ok" if ok else "failed",
        duration=dur,
        output_hint=f"output/snapshots/{args.brand}_{analysis_date}.json" if ok else "",
        error_message=err,
    ))
    if not ok:
        print(f"\n  ✗ Analysis stage failed. Stopping.")
        print_summary(args.brand, args.ticker, args.mode, analysis_date, results)
        sys.exit(1)

    # ── 4. Narrative ───────────────────────────────────────────────
    if args.skip_narrative:
        stage += 1
        print(f"\n{stage_label(stage, total, 'Narrative')} — skipped (--skip-narrative)\n")
        results.append(StageResult(name="Narrative", status="skipped"))
    else:
        stage += 1
        print(f"\n{stage_label(stage, total, 'Narrative')} — {args.brand} via {args.model}\n")

        ok, dur, err = run_stage([
            py, "run_narrative.py", args.brand,
            "--model", args.model,
            "--date", analysis_date,
        ])
        results.append(StageResult(
            name="Narrative",
            status="ok" if ok else "failed",
            duration=dur,
            error_message=err,
        ))
        if not ok:
            # Non-fatal: log and continue to report.
            print(f"\n  ✗ Narrative stage failed — continuing to report.\n")

    # ── 5. Report ──────────────────────────────────────────────────
    if args.skip_report:
        stage += 1
        print(f"\n{stage_label(stage, total, 'Report')} — skipped (--skip-report)\n")
        results.append(StageResult(name="Report", status="skipped"))
    else:
        stage += 1
        print(f"\n{stage_label(stage, total, 'Report')} — {args.brand}\n")

        ok, dur, err = run_stage([
            py, "run_report.py", args.brand,
            "--date", analysis_date,
        ])
        results.append(StageResult(
            name="Report",
            status="ok" if ok else "failed",
            duration=dur,
            output_hint=f"output/reports/{args.brand}_{analysis_date}.html" if ok else "",
            error_message=err,
        ))

    # ── Summary ────────────────────────────────────────────────────
    all_ok = print_summary(args.brand, args.ticker, args.mode, analysis_date, results)
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
