"""
LLM-generated commentary for financial and social data.

These functions produce QUALITATIVE summaries only.
All numbers, ratios, and scores are computed deterministically elsewhere.

Financial summary prompt reused from financial_analyzer.py generate_company_summary().
"""

import logging

from brandy.llm.client import chat_completion

logger = logging.getLogger(__name__)


def generate_financial_commentary(
    ticker: str,
    metrics_by_year: list[dict],
    model: str = "gpt-4.1-mini",
) -> str | None:
    """
    Generate a multi-year financial summary for a company.
    Reused prompt structure from financial_analyzer.py L264-316.

    Input: list of metric dicts, each with fiscal_year + computed values.
    Output: 5 bullet points of qualitative commentary.
    """
    if not metrics_by_year:
        return None

    summary_lines = []
    for row in metrics_by_year:
        year = row.get("fiscal_year", "?")
        line = (
            f"FY{year}: Revenue={_fmt(row.get('revenue'))}, "
            f"Gross Margin={_pct(row.get('gross_margin'))}, "
            f"EBITDA={_fmt(row.get('ebitda'))} ({row.get('ebitda_source', '?')}), "
            f"Operating Margin={_pct(row.get('operating_margin'))}, "
            f"Net Income={_fmt(row.get('net_income'))}, "
            f"FCF={_fmt(row.get('free_cash_flow'))}, "
            f"ROE={_pct(row.get('roe'))}, "
            f"D/E={_ratio(row.get('debt_to_equity'))}"
        )
        summary_lines.append(line)

    data_block = "\n".join(summary_lines)

    prompt = f"""You are a senior financial analyst. Based on the following extracted financial data for {ticker} across multiple fiscal years, write exactly 5 concise bullet points summarising the company's financial performance and key trends over the period.

Focus on: revenue growth, profitability trends, cash generation, balance sheet health, and any notable observations.
Be specific — reference actual numbers where relevant.
Each bullet point should be one to two sentences.

Financial Data:
{data_block}

Return exactly 5 bullet points, each starting with a dash (-)."""

    return chat_completion(
        prompt=prompt,
        system_message="You are a concise financial analyst who writes clear, data-driven summaries.",
        model=model,
        temperature=0.3,
        max_tokens=400,
    )


def generate_social_commentary(
    brand_name: str,
    platform_summaries: list[dict],
    model: str = "gpt-4.1-mini",
) -> str | None:
    """
    Generate a qualitative summary of a brand's social media presence.

    Input: list of per-platform summary dicts with:
      platform, total_posts, total_likes, total_comments,
      avg_sentiment, total_positive, total_neutral, total_negative
    """
    if not platform_summaries:
        return None

    lines = []
    for ps in platform_summaries:
        line = (
            f"{ps.get('platform', '?')}: "
            f"{ps.get('total_posts', 0)} posts, "
            f"{ps.get('total_likes', 0)} total likes, "
            f"{ps.get('total_comments', 0)} total comments, "
            f"avg sentiment={_ratio(ps.get('avg_sentiment'))}, "
            f"positive={ps.get('total_positive', 0)}, "
            f"neutral={ps.get('total_neutral', 0)}, "
            f"negative={ps.get('total_negative', 0)}, "
            f"spam={ps.get('total_spam', 0)}"
        )
        lines.append(line)

    data_block = "\n".join(lines)

    prompt = f"""You are a brand analyst. Based on the following social media metrics for {brand_name}, write 3-5 concise bullet points about the brand's social media presence and audience sentiment.

Focus on: engagement levels, sentiment patterns, platform differences, and notable observations.

Social Media Data:
{data_block}

Return bullet points, each starting with a dash (-)."""

    return chat_completion(
        prompt=prompt,
        system_message="You are a concise brand analyst who writes clear, data-driven summaries.",
        model=model,
        temperature=0.3,
        max_tokens=300,
    )


# ── Formatting helpers ──────────────────────────────────────────────

def _fmt(val) -> str:
    """Format a dollar value in millions."""
    if val is None:
        return "N/A"
    if abs(val) >= 1e9:
        return f"${val / 1e9:.1f}B"
    if abs(val) >= 1e6:
        return f"${val / 1e6:.1f}M"
    return f"${val:,.0f}"


def _pct(val) -> str:
    """Format a ratio as percentage."""
    if val is None:
        return "N/A"
    return f"{val * 100:.1f}%"


def _ratio(val) -> str:
    """Format a ratio."""
    if val is None:
        return "N/A"
    return f"{val:.2f}"
