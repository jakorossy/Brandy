"""
Brandy — Streamlit UI.

Local app-style interface for the Brandy pipeline. Calls the existing runner
scripts via subprocess; no backend logic is duplicated in the UI.

Run from the project root:
    streamlit run app.py
"""

import json
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import streamlit as st


# ── Page config ────────────────────────────────────────────────────

st.set_page_config(
    page_title="Brandy",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Session state ──────────────────────────────────────────────────

if "last_run" not in st.session_state:
    st.session_state.last_run = None

# Project root — where run_*.py scripts live and where Streamlit is launched.
PROJECT_ROOT = Path(__file__).resolve().parent


# ── Sidebar form ───────────────────────────────────────────────────

with st.sidebar:
    st.title("Brandy")
    st.caption("Brand equity intelligence")
    st.divider()

    mode = st.radio(
        "Mode",
        options=["both", "financial", "social"],
        format_func=lambda x: {
            "both": "Both arms",
            "financial": "Financial only",
            "social": "Social only",
        }[x],
        horizontal=False,
    )

    is_financial_mode = mode == "financial"
    is_social_mode = mode == "social"

    brand = st.text_input(
        "Brand name",
        placeholder="e.g. Starbucks",
        help="Primary identity anchor for social, snapshot, narrative and report stages.",
    )

    ticker = st.text_input(
        "Ticker",
        placeholder="e.g. SBUX",
        disabled=is_social_mode,
        help="Required for financial / both modes. Ignored in social-only mode.",
    )

    analysis_date = st.date_input(
        "Analysis date",
        value=date.today(),
        help="Used to name snapshot and report files.",
    )

    with st.expander("Social options", expanded=False):
        social_handle = st.text_input(
            "Social handle (optional)",
            placeholder="e.g. starbucks",
            disabled=is_financial_mode,
            help="Override the social handle. Empty = use brand name.",
        )
        start_date = st.date_input(
            "Start date (optional)",
            value=None,
            disabled=is_financial_mode,
        )
        end_date = st.date_input(
            "End date (optional)",
            value=None,
            disabled=is_financial_mode,
        )
        fetch_comments = st.checkbox(
            "Fetch comments",
            value=False,
            disabled=is_financial_mode,
            help="Fetch comment text for sentiment analysis (slower).",
        )
        comment_post_limit = st.number_input(
            "Comment post limit",
            min_value=1, max_value=100, value=3, step=1,
            disabled=(is_financial_mode or not fetch_comments),
        )
        top_comments = st.number_input(
            "Top comments per post",
            min_value=1, max_value=50, value=5, step=1,
            disabled=(is_financial_mode or not fetch_comments),
        )

    with st.expander("Narrative options", expanded=False):
        skip_narrative = st.checkbox(
            "Skip narrative",
            value=False,
            disabled=is_financial_mode,
            help="Skip Ollama call. Report still renders with empty narrative sections.",
        )
        model = st.text_input(
            "Ollama model",
            value="deepseek-r1:8b",
            disabled=(is_financial_mode or skip_narrative),
        )

    skip_report = st.checkbox(
        "Skip report",
        value=False,
        disabled=is_financial_mode,
    )

    st.divider()

    run_button = st.button(
        "▶  Run Workflow",
        type="primary",
        use_container_width=True,
    )


# ── Validation ─────────────────────────────────────────────────────

def validate_inputs() -> list[str]:
    errors = []
    if not brand.strip():
        errors.append("Brand name is required.")
    if mode in ("financial", "both") and not ticker.strip():
        errors.append(f"Ticker is required for '{mode}' mode.")
    if ticker.strip() and not ticker.strip().isalpha():
        errors.append("Ticker should contain only letters (e.g. SBUX, MCD).")
    if start_date and end_date and start_date > end_date:
        errors.append("Start date must be on or before end date.")
    return errors


# ── Stage preview ──────────────────────────────────────────────────

def stage_names_for_preview() -> list[str]:
    """Return display names of stages that will run given current settings."""
    names = []
    if mode in ("financial", "both"):
        names.append("Financial")
    if mode == "financial":
        return names
    names.append("Social")
    names.append("Analysis")
    if not skip_narrative:
        names.append("Narrative")
    if not skip_report:
        names.append("Report")
    return names


# ── Stage builder ──────────────────────────────────────────────────

def build_stages() -> list[tuple]:
    """Return list of (stage_name, command_list) tuples in execution order."""
    py = sys.executable
    date_str = analysis_date.isoformat()
    brand_clean = brand.strip()
    ticker_clean = ticker.strip()
    stages = []

    # 1. Financial
    if mode in ("financial", "both"):
        stages.append(("Financial", [py, "run_financial.py", ticker_clean]))

    # Financial-only mode stops after financial
    if mode == "financial":
        return stages

    # 2. Social
    social_target = (
        f"{brand_clean}:{social_handle.strip()}"
        if social_handle.strip()
        else brand_clean
    )
    social_cmd = [py, "run_social.py", social_target]
    if start_date:
        social_cmd += ["--since", start_date.isoformat()]
    if end_date:
        social_cmd += ["--end-date", end_date.isoformat()]
    if fetch_comments:
        social_cmd += [
            "--fetch-comments",
            "--comment-post-limit", str(int(comment_post_limit)),
            "--top-comments", str(int(top_comments)),
        ]
    stages.append(("Social", social_cmd))

    # 3. Analysis (snapshot)
    if mode == "both" and ticker_clean:
        analysis_spec = f"{brand_clean}:{ticker_clean}"
    else:
        analysis_spec = brand_clean
    stages.append(("Analysis", [py, "run_analysis.py", analysis_spec]))

    # 4. Narrative
    if not skip_narrative:
        stages.append((
            "Narrative",
            [py, "run_narrative.py", brand_clean,
             "--model", (model.strip() or "deepseek-r1:8b"),
             "--date", date_str],
        ))

    # 5. Report
    if not skip_report:
        stages.append((
            "Report",
            [py, "run_report.py", brand_clean, "--date", date_str],
        ))

    return stages


# ── Subprocess runner ──────────────────────────────────────────────

FATAL_STAGES = {"Financial", "Social", "Analysis"}


def run_subprocess(cmd: list) -> dict:
    """Run a subprocess to completion. Returns a result dict."""
    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        duration = time.monotonic() - start
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
            "duration": duration,
        }
    except FileNotFoundError as e:
        return {
            "ok": False,
            "returncode": -1,
            "stdout": "",
            "stderr": f"Command not found: {e}",
            "duration": time.monotonic() - start,
        }


# ── Main area ──────────────────────────────────────────────────────

st.title("Brandy — Brand Intelligence Pipeline")
st.caption(
    "Run financial, social, and narrative analysis on a brand. "
    "Each stage shells out to the existing CLI runners."
)

# ── Stage preview panel ────────────────────────────────────────────

preview_names = stage_names_for_preview()
arrow_chain = "  →  ".join(preview_names)

mode_desc = {
    "both": "Financial + social data, merged snapshot, narrative, and HTML report.",
    "financial": "SEC/XBRL financial data only. Outputs an Excel file.",
    "social": "Social data, merged snapshot, narrative, and HTML report. No financial arm.",
}

with st.container(border=True):
    col_label, col_stages = st.columns([1, 3])
    with col_label:
        st.markdown(f"**Mode:** {mode}")
        st.caption(mode_desc[mode])
    with col_stages:
        st.markdown(f"**Stages:** {arrow_chain}")
        if mode in ("financial", "both") and not ticker.strip():
            st.caption("⚠ Ticker required to run.")
        elif not brand.strip():
            st.caption("⚠ Brand name required to run.")
        else:
            st.caption(f"Ready to run {len(preview_names)} stage{'s' if len(preview_names) != 1 else ''}.")

# ── Run + validation ───────────────────────────────────────────────

if run_button:
    errors = validate_inputs()
    if errors:
        for err in errors:
            st.error(err)
    else:
        stages = build_stages()
        results = []

        with st.status(f"Running {len(stages)} stage{'s' if len(stages) > 1 else ''}…",
                       expanded=True) as status:
            halted = False
            for i, (name, cmd) in enumerate(stages, start=1):
                if halted:
                    st.write(f"⏭  **[{i}/{len(stages)}] {name}** — skipped (upstream failure)")
                    results.append({"name": name, "ok": False, "duration": 0.0,
                                    "stdout": "", "stderr": "skipped",
                                    "skipped": True})
                    continue

                st.write(f"▸  **[{i}/{len(stages)}] {name}**")
                result = run_subprocess(cmd)
                result["name"] = name

                if result["ok"]:
                    st.write(f"   ✓  done in {result['duration']:.1f}s")
                else:
                    fatal = name in FATAL_STAGES
                    icon = "✗" if fatal else "⚠"
                    st.write(f"   {icon}  failed (exit {result['returncode']})"
                             f"{' — halting' if fatal else ' — continuing'}")
                    if fatal:
                        halted = True

                with st.expander(f"{name} output", expanded=not result["ok"]):
                    if result["stdout"]:
                        st.code(result["stdout"], language="text")
                    if result["stderr"]:
                        st.markdown("**stderr**")
                        st.code(result["stderr"], language="text")

                results.append(result)

            # Final status badge
            failed = [r for r in results if not r.get("ok") and not r.get("skipped")]
            skipped = [r for r in results if r.get("skipped")]
            if not failed and not skipped:
                status.update(label=f"✓ Completed — {len(stages)} stages", state="complete")
            elif failed and not any(r["name"] in FATAL_STAGES for r in failed):
                status.update(
                    label=f"⚠ Completed with {len(failed)} non-fatal failure(s)",
                    state="complete",
                )
            else:
                status.update(label="✗ Pipeline halted", state="error")

        st.session_state.last_run = {
            "brand": brand.strip(),
            "ticker": ticker.strip() if not is_social_mode else None,
            "mode": mode,
            "analysis_date": analysis_date.isoformat(),
            "stages": results,
            "completed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }


# ── Output display ─────────────────────────────────────────────────

run = st.session_state.last_run
if run:
    st.divider()
    st.subheader("Last Run")

    brand_name = run["brand"]
    date_str = run["analysis_date"]
    mode_label = run["mode"]
    ticker_label = run["ticker"] or "—"

    st.caption(
        f"Brand: **{brand_name}**  ·  Ticker: **{ticker_label}**  ·  "
        f"Mode: **{mode_label}**  ·  Date: **{date_str}**  ·  "
        f"Completed: {run['completed_at']}"
    )

    # ── Stage summary table ────────────────────────────────────────

    STATUS_ICON = {"ok": "✓", "failed": "✗", "skipped": "–"}

    def _stage_icon(r: dict) -> str:
        if r.get("skipped"):
            return "–"
        return "✓" if r.get("ok") else "✗"

    rows = []
    for r in run["stages"]:
        icon = _stage_icon(r)
        dur = f"{r['duration']:.1f}s" if not r.get("skipped") else "—"
        note = ""
        if r.get("skipped"):
            note = "skipped (upstream failure)"
        elif not r.get("ok"):
            note = f"exit {r.get('returncode', '?')}"
        rows.append(f"| {icon} | **{r['name']}** | {dur} | {note} |")

    st.markdown(
        "| | Stage | Duration | Note |\n"
        "|---|---|---|---|\n" +
        "\n".join(rows)
    )

    # ── Output paths ───────────────────────────────────────────────

    snapshot_path = PROJECT_ROOT / "output" / "snapshots" / f"{brand_name}_{date_str}.json"
    report_path = PROJECT_ROOT / "output" / "reports" / f"{brand_name}_{date_str}.html"

    snap_exists = snapshot_path.exists()
    report_exists = report_path.exists()

    if snap_exists or report_exists:
        st.markdown("**Output files**")
        if snap_exists:
            rel = snapshot_path.relative_to(PROJECT_ROOT)
            st.code(str(rel), language=None)
        if report_exists:
            rel = report_path.relative_to(PROJECT_ROOT)
            st.code(str(rel), language=None)

    # ── File download + preview ────────────────────────────────────

    col_snap, col_report = st.columns(2)

    with col_snap:
        st.markdown("**Snapshot JSON**")
        if snap_exists:
            raw = snapshot_path.read_text(encoding="utf-8")
            st.download_button(
                "Download snapshot",
                data=raw,
                file_name=snapshot_path.name,
                mime="application/json",
                use_container_width=True,
            )
            with st.expander("View JSON", expanded=False):
                try:
                    st.json(json.loads(raw))
                except json.JSONDecodeError:
                    st.code(raw, language="json")
        else:
            st.info("No snapshot generated for this run.")

    with col_report:
        st.markdown("**HTML Report**")
        if report_exists:
            html = report_path.read_text(encoding="utf-8")
            st.download_button(
                "Download report",
                data=html,
                file_name=report_path.name,
                mime="text/html",
                use_container_width=True,
            )
            with st.expander("Preview report", expanded=False):
                st.components.v1.html(html, height=1200, scrolling=True)
        else:
            st.info("No report generated for this run.")

else:
    st.info(
        "Configure inputs in the sidebar and click **Run Workflow** to begin. "
        "Outputs will appear here after the run completes."
    )
