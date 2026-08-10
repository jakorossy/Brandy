"""
Report builder — thin wrapper around the Jinja2 template.

Loads a brand snapshot dict, applies deterministic formatters as Jinja
filters, and writes a self-contained HTML file.
"""

import logging
import os
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from brandy.report import formatters as fmt

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"
TEMPLATE_NAME = "single_brand.html.j2"


def _make_env() -> Environment:
    """Build a Jinja2 environment with our deterministic filters registered."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["money"] = fmt.fmt_money
    env.filters["pct"] = fmt.fmt_pct
    env.filters["intfmt"] = fmt.fmt_int
    env.filters["ratio"] = fmt.fmt_ratio
    env.filters["score"] = fmt.fmt_score
    env.filters["compact"] = fmt.fmt_compact_int
    env.filters["dash"] = fmt.fmt_dash
    return env


def render(snapshot: dict) -> str:
    """
    Render a snapshot dict to an HTML string.

    The snapshot may contain merged_narrative as a dict (preferred) or as
    a JSON string (legacy); only the dict form is supported here.
    """
    env = _make_env()
    template = env.get_template(TEMPLATE_NAME)

    narrative = snapshot.get("merged_narrative") or {}
    if not isinstance(narrative, dict):
        narrative = {}

    context = {
        "snapshot": snapshot,
        "narrative": narrative,
        "has_financial": fmt.has_financial(snapshot),
        "has_social": fmt.has_social(snapshot),
        "has_sentiment": fmt.has_sentiment(snapshot),
        "sentiment_label_text": fmt.sentiment_label(
            snapshot.get("sentiment_positive_pct"),
            snapshot.get("sentiment_neutral_pct"),
            snapshot.get("sentiment_negative_pct"),
        ),
        "tagline": fmt.tagline(narrative.get("summary")),
    }
    return template.render(**context)


def build_report(snapshot: dict, output_dir: str = "output") -> str:
    """
    Render the snapshot and write it to {output_dir}/reports/{brand}_{date}.html.
    Returns the written path.
    """
    reports_dir = os.path.join(output_dir, "reports")
    os.makedirs(reports_dir, exist_ok=True)

    filename = f"{snapshot['brand_name']}_{snapshot['analysis_date']}.html"
    path = os.path.join(reports_dir, filename)

    html = render(snapshot)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)

    logger.info("Report written: %s", path)
    return path
