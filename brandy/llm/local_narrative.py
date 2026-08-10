"""
Local LLM narrative generation via Ollama.

Reads brand snapshot dicts and produces structured JSON narratives.
Direct HTTP to Ollama — no LangChain dependency.

The LLM performs synthesis only. All numbers in the snapshot are
pre-computed deterministically; the model is explicitly instructed
not to recalculate or verify them.
"""

import json
import logging
import requests

logger = logging.getLogger(__name__)

# Required keys in the structured narrative output
NARRATIVE_FIELDS = [
    "summary",
    "financial_takeaway",
    "social_takeaway",
    "key_tension",
    "data_limitations",
    "confidence_note",
]


# ── Formatting helpers ─────────────────────────────────────────────

def _fmt(value, prefix="", suffix="", fallback="not available"):
    """Format a snapshot value for prompt insertion."""
    if value is None:
        return fallback
    if isinstance(value, float):
        if abs(value) >= 1_000_000:
            return f"{prefix}{value:,.0f}{suffix}"
        if abs(value) < 0.01:
            return f"{prefix}{value:.4f}{suffix}"
        return f"{prefix}{value:.2f}{suffix}"
    return f"{prefix}{value}{suffix}"


def _pct(value, fallback="not available"):
    """Format a ratio as a percentage string."""
    if value is None:
        return fallback
    return f"{value * 100:.1f}%" if isinstance(value, float) else str(value)


# ── Prompt builders ────────────────────────────────────────────────

def build_single_brand_prompt(snapshot: dict) -> str:
    """
    Build the narrative prompt for one brand snapshot.

    Presents all non-null fields as structured context and instructs
    the model to return a JSON object with exactly 6 keys.
    """
    s = snapshot

    financial_section = "  No financial data available for this brand."
    if s.get("revenue") is not None or s.get("operating_margin") is not None:
        financial_section = f"""  Filing date:       {_fmt(s.get('filing_date'))}
  Fiscal year:       {_fmt(s.get('financial_fiscal_year'))}
  Revenue:           {_fmt(s.get('revenue'), prefix='$')}
  Gross margin:      {_pct(s.get('gross_margin'))}
  Operating margin:  {_pct(s.get('operating_margin'))}
  Net margin:        {_pct(s.get('net_margin'))}
  EBITDA:            {_fmt(s.get('ebitda'), prefix='$')} ({_fmt(s.get('ebitda_source'))})
  EPS (diluted):     {_fmt(s.get('eps_diluted'))}
  Free cash flow:    {_fmt(s.get('free_cash_flow'), prefix='$')}
  ROE:               {_pct(s.get('roe'))}
  Debt-to-equity:    {_fmt(s.get('debt_to_equity'))}
  WACC:              {_pct(s.get('wacc'))}"""

    social_section = "  No social data available for this brand."
    if s.get("total_posts_analyzed") is not None:
        social_section = f"""  Platform:          {_fmt(s.get('primary_platform'))}
  Data as of:        {_fmt(s.get('social_data_as_of'))}
  Followers:         {_fmt(s.get('total_followers'))}
  Posts analysed:    {_fmt(s.get('total_posts_analyzed'))}
  Total likes:       {_fmt(s.get('total_likes'))}
  Total comments:    {_fmt(s.get('total_comments'))}
  Avg likes/post:    {_fmt(s.get('avg_likes_per_post'))}
  Avg comments/post: {_fmt(s.get('avg_comments_per_post'))}
  Engagement rate:   {_fmt(s.get('engagement_rate'))}"""

    sentiment_section = "  No sentiment data available."
    if s.get("comments_analyzed") and s["comments_analyzed"] > 0:
        sentiment_section = f"""  Avg score:         {_fmt(s.get('avg_sentiment_score'))} (scale: -1 negative to +1 positive)
  Positive:          {_pct(s.get('sentiment_positive_pct'))}
  Neutral:           {_pct(s.get('sentiment_neutral_pct'))}
  Negative:          {_pct(s.get('sentiment_negative_pct'))}
  Comments analysed: {_fmt(s.get('comments_analyzed'))}
  Methodology:       {_fmt(s.get('sentiment_methodology'))}"""

    fields_json = json.dumps({f: f"<{f} here>" for f in NARRATIVE_FIELDS}, indent=2)

    return f"""You are a brand equity analyst writing a concise executive summary.

Below is a structured brand snapshot combining financial and social data.
All numbers are pre-computed. Do NOT recalculate, verify, or second-guess
any figures. Your job is to synthesise what the numbers mean together.

BRAND: {_fmt(s.get('brand_name'))}
TICKER: {_fmt(s.get('ticker'))}

FINANCIAL:
{financial_section}

SOCIAL:
{social_section}

SENTIMENT:
{sentiment_section}

Respond with a JSON object containing exactly these 6 keys:
{fields_json}

Rules — field content:
- "summary": 2-3 sentence overall brand position synthesis
- "financial_takeaway": 1-2 sentences on what the financials reveal
- "social_takeaway": 1-2 sentences on engagement and sentiment picture
- "key_tension": 1-2 sentences on a genuine directional conflict between the financial arm and the social arm — meaning the two sets of signals point in opposing directions (e.g. strong financials but deeply negative sentiment, or very weak financials but enthusiastic social engagement). Write "None identified" if no such cross-arm conflict exists. Conflicts within the financial data alone (e.g. negative ROE alongside positive FCF) are not a tension — they belong in financial_takeaway or data_limitations. A small sample size is a data limitation, not a tension.
- "data_limitations": 1 sentence listing only the specific field names from the snapshot above that show "not available". Do NOT list any field that has a value in the snapshot, even if that value looks unusual. Do NOT describe post counts or comment counts as a limitation — if data was analyzed, the count is the dataset, not a gap. Write "None" if no fields show "not available".
- "confidence_note": 1 sentence on how reliable the interpretation is given the available data

Rules — sentiment interpretation (strictly follow these):
- Treat positive_pct, neutral_pct, and negative_pct as three independent figures. Do NOT add neutral and negative together.
- Only describe sentiment as negative if negative_pct is strictly the largest of the three shares.
- If positive_pct >= neutral_pct and positive_pct >= negative_pct, describe sentiment as positive or mixed-positive.
- If positive_pct and neutral_pct are roughly equal and both exceed negative_pct, describe sentiment as mixed or balanced.
- If comments_analyzed is below 50, note the small sample in data_limitations and use cautious language such as "in this limited sample".

Rules — financial interpretation (strictly follow these):
- A negative ROE may result from share buybacks reducing book equity below zero, not from operating losses. Do not infer financial distress from negative ROE alone.
- A high debt_to_equity ratio is common in capital-intensive and buyback-heavy businesses. Do not characterize it as a stress signal unless net_margin or free_cash_flow also show deterioration.
- If free_cash_flow is positive, the business is generating cash regardless of accounting-based ratios like ROE or D/E.
- When operating_margin and net_margin are both positive AND free_cash_flow is positive, negative ROE and high D/E are structural/accounting characteristics of capital return strategy, not the main financial story. In that case: make the operating performance the headline of financial_takeaway, and mention ROE/D/E in at most one subordinate clause (e.g. "the negative ROE reflects equity reduction from buybacks rather than operating weakness"). Do NOT open financial_takeaway with ROE or D/E.
- Only flag negative ROE or high D/E as a primary concern when net_margin is also negative OR free_cash_flow is zero or negative.
- Prefer cautious, hedged language whenever sample sizes are small or fields show "not available".

Rules — temporal language (strictly follow these):
- This snapshot above contains ONLY single point-in-time fields. It has NO prior-period, year-over-year, change, or growth fields (no prior_*, yoy_*, growth_*, *_change, *_prior keys exist in the data shown to you).
- Because no time-series field is present in this snapshot, you may NOT use temporal or directional language: growth, grew, growing, declined, declining, falling, rising, increased, decreased, improving, deteriorating, trend, trajectory, momentum, recovery, expansion, contraction, "stronger than last year", "weaker than", "compared to last year", "year-over-year", "year on year", "up from", "down from".
- Temporal/directional language is permitted ONLY if an explicit time-series field is shown above. Verify by checking the snapshot for a prior-period, yoy, growth, or change field before using any such word. If you cannot point to that field, do not use that word.
- Describe only the current state: "maintains", "shows", "has", "reports", "stands at" — never "grew to", "has grown", or "shows growth".

Do NOT invent numbers. If a field shows "not available", say the data is unavailable.
Keep each field to 1-2 sentences maximum. Be concise.
Respond with the JSON object ONLY. No markdown code fences, no explanation outside the JSON, no trailing text."""


def build_comparison_prompt(snapshots: list[dict]) -> str:
    """Comparison mode — not implemented in v1."""
    raise NotImplementedError(
        "Comparison mode is not implemented yet. "
        "Use single-brand mode: python3 run_narrative.py \"Brand\""
    )


# ── Ollama client ──────────────────────────────────────────────────

def call_ollama(
    prompt: str,
    model: str = "deepseek-r1:8b",
    host: str = "http://localhost:11434",
) -> str | None:
    """
    POST to Ollama /api/generate with stream=false.
    Returns the raw response text, or None if unreachable.
    """
    url = f"{host}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }

    try:
        resp = requests.post(url, json=payload, timeout=120)
        if not resp.ok:
            logger.error(
                "Ollama returned HTTP %s: %s",
                resp.status_code, resp.text[:300],
            )
            return None
        data = resp.json()
        return data.get("response", "")
    except requests.ConnectionError:
        logger.error(
            "Cannot connect to Ollama at %s. "
            "Is Ollama running? Try: ollama serve", host,
        )
        return None
    except requests.RequestException:
        logger.exception("Ollama request failed")
        return None


# ── Response parsing ───────────────────────────────────────────────

def parse_narrative_response(raw_text: str) -> dict:
    """
    Extract and validate the structured JSON from the model's raw output.

    Handles common model quirks:
      - Markdown code fences (```json ... ```)
      - Leading/trailing whitespace or explanation text
      - Missing keys (filled with fallback strings)

    Never raises — always returns a dict with all 6 keys.
    """
    text = raw_text.strip()

    # Strip markdown code fences if present
    if "```" in text:
        # Find JSON between first ``` and last ```
        parts = text.split("```")
        for part in parts:
            candidate = part.strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
            if candidate.startswith("{"):
                text = candidate
                break

    # Try to find JSON object in the text
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        json_str = text[start:end + 1]

        # First try: parse as-is
        try:
            parsed = json.loads(json_str)
            if isinstance(parsed, dict):
                for field in NARRATIVE_FIELDS:
                    if field not in parsed:
                        parsed[field] = "Not provided by model."
                return parsed
        except json.JSONDecodeError:
            pass

        # Second try: repair missing commas between JSON fields.
        # Small models often produce  ..."value"\n  "next_key"
        # instead of                  ..."value",\n  "next_key"
        import re
        repaired = re.sub(r'"\s*\n(\s*")', r'",\n\1', json_str)
        try:
            parsed = json.loads(repaired)
            if isinstance(parsed, dict):
                for field in NARRATIVE_FIELDS:
                    if field not in parsed:
                        parsed[field] = "Not provided by model."
                logger.info("Parsed JSON after repairing missing commas")
                return parsed
        except json.JSONDecodeError:
            pass

    # Handle truncated JSON — model output cut off before closing brace
    if start != -1 and (end == -1 or end <= start):
        json_str = text[start:] + '"\n}'
        try:
            parsed = json.loads(json_str)
            if isinstance(parsed, dict):
                for field in NARRATIVE_FIELDS:
                    if field not in parsed:
                        parsed[field] = "Not provided by model (output truncated)."
                logger.warning("Recovered truncated JSON from model output")
                return parsed
        except json.JSONDecodeError:
            pass

    # Fallback: could not parse JSON — store raw text in summary
    logger.warning("Could not parse structured JSON from model output")
    return {
        field: (raw_text if field == "summary" else "Parse error — see summary for raw model output.")
        for field in NARRATIVE_FIELDS
    }


# ── Orchestrator ───────────────────────────────────────────────────

def generate_narrative(
    snapshots: list[dict],
    model: str = "deepseek-r1:8b",
    host: str = "http://localhost:11434",
    compare: bool = False,
) -> list[dict]:
    """
    Generate structured narratives for one or more snapshots.

    compare=False (v1): one prompt per snapshot, returns one narrative dict per brand.
    compare=True (v2):  raises NotImplementedError.
    """
    if compare:
        build_comparison_prompt(snapshots)  # raises NotImplementedError

    results = []
    for snapshot in snapshots:
        brand = snapshot.get("brand_name", "unknown")
        logger.info("Generating narrative for %s with %s ...", brand, model)

        prompt = build_single_brand_prompt(snapshot)
        raw = call_ollama(prompt, model=model, host=host)

        if raw is None:
            logger.error("No response from Ollama for %s", brand)
            results.append({
                field: "Ollama did not return a response."
                for field in NARRATIVE_FIELDS
            })
            continue

        parsed = parse_narrative_response(raw)
        logger.info("Narrative generated for %s", brand)
        results.append(parsed)

    return results
