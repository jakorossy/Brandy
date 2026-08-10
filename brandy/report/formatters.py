"""
Deterministic report-time formatters.

Pure functions, no I/O, no LLM. Missing values (None) always render as "—".
"""

MISSING = "—"


def fmt_dash(value):
    """Pass-through with "—" for None / empty string."""
    if value is None or value == "":
        return MISSING
    return str(value)


def fmt_money(value):
    """
    Format a numeric currency value with B / M / K suffixes.
    None → "—". Negative values render as "-$1.2B".
    """
    if value is None:
        return MISSING
    try:
        v = float(value)
    except (TypeError, ValueError):
        return MISSING

    sign = "-" if v < 0 else ""
    v = abs(v)

    if v >= 1_000_000_000:
        return f"{sign}${v / 1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"{sign}${v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"{sign}${v / 1_000:.2f}K"
    return f"{sign}${v:,.2f}"


def fmt_pct(value, decimals=2):
    """
    Format a ratio (0.0789) as a percent string ("7.89%"). None → "—".
    Multiplies by 100. Use fmt_ratio for raw ratios like debt_to_equity.
    """
    if value is None:
        return MISSING
    try:
        return f"{float(value) * 100:.{decimals}f}%"
    except (TypeError, ValueError):
        return MISSING


def fmt_int(value):
    """Format an integer with thousands separators. None → "—"."""
    if value is None:
        return MISSING
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return MISSING


def fmt_ratio(value, decimals=2):
    """Format a bare numeric ratio (e.g. debt_to_equity = -4.95). None → "—"."""
    if value is None:
        return MISSING
    try:
        return f"{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return MISSING


def fmt_score(value, decimals=3):
    """Format a signed sentiment score (-1 to +1) with explicit sign. None → "—"."""
    if value is None:
        return MISSING
    try:
        v = float(value)
    except (TypeError, ValueError):
        return MISSING
    return f"{v:+.{decimals}f}"


def fmt_compact_int(value):
    """
    Format a count using K / M / B suffixes for compact display.
    Used for follower counts and engagement totals. None → "—".
    """
    if value is None:
        return MISSING
    try:
        v = float(value)
    except (TypeError, ValueError):
        return MISSING

    sign = "-" if v < 0 else ""
    v = abs(v)

    if v >= 1_000_000_000:
        return f"{sign}{v / 1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"{sign}{v / 1_000_000:.2f}M"
    if v >= 10_000:
        return f"{sign}{v / 1_000:.1f}K"
    return f"{sign}{int(v):,}"


# ── Derived labels ─────────────────────────────────────────────────

def sentiment_label(pos_pct, neu_pct, neg_pct):
    """
    Deterministic label from the three sentiment shares.

    Follows the same dominant-share rule the narrative prompt enforces:
      - "Predominantly negative" only if negative is the strict largest
      - "Predominantly positive" only if positive is the strict largest
      - "Predominantly neutral" only if neutral is the strict largest
      - Ties between positive/neutral leading → "Mixed-positive"
      - Ties between negative/anything else leading → "Mixed-negative"
      - Otherwise → "Mixed"

    Returns None if any share is None.
    """
    if pos_pct is None or neu_pct is None or neg_pct is None:
        return None

    pos, neu, neg = float(pos_pct), float(neu_pct), float(neg_pct)
    tol = 0.02  # shares within 2 percentage points are treated as tied

    def close(a, b):
        return abs(a - b) <= tol

    # Strict single leader
    if pos > neu + tol and pos > neg + tol:
        return "Predominantly positive"
    if neg > pos + tol and neg > neu + tol:
        return "Predominantly negative"
    if neu > pos + tol and neu > neg + tol:
        return "Predominantly neutral"

    # Tied leaders
    if close(pos, neu) and pos >= neg and neu >= neg:
        return "Mixed-positive"
    if close(pos, neg) and pos >= neu and neg >= neu:
        return "Mixed (polarised)"
    if close(neu, neg) and neu >= pos and neg >= pos:
        return "Mixed-negative"

    return "Mixed"


def tagline(summary, max_chars=120):
    """Extract the first sentence of the summary as a short header tagline."""
    if not summary:
        return None
    text = summary.strip()
    for terminator in (". ", "! ", "? "):
        idx = text.find(terminator)
        if idx != -1:
            first = text[: idx + 1].strip()
            if len(first) <= max_chars:
                return first
            return first[: max_chars - 1].rstrip() + "…"
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


# ── Section availability ───────────────────────────────────────────

def has_financial(snapshot):
    """True if any financial field is populated."""
    keys = ("revenue", "operating_margin", "net_margin", "ebitda", "free_cash_flow")
    return any(snapshot.get(k) is not None for k in keys)


def has_social(snapshot):
    """True if social posts were analyzed."""
    return snapshot.get("total_posts_analyzed") is not None


def has_sentiment(snapshot):
    """True if any comment-basis sentiment was computed."""
    c = snapshot.get("comments_analyzed")
    return c is not None and c > 0
