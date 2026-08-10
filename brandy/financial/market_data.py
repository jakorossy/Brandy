"""
Market data lookups for WACC inputs.

SEC filings give us the balance sheet but not the market's view of a company:
market capitalisation, beta, and the prevailing risk-free rate all have to
come from outside EDGAR. This module wraps those lookups behind a single
dataclass so the WACC builder never has to care where a number came from.

Sources — all free, no paid API keys:
  - yfinance (Yahoo Finance)  → market cap, beta, total debt
  - FRED fredgraph.csv        → 10-year Treasury yield (no API key required)

Every lookup degrades gracefully: on any network or parsing failure the
field is left as None and the caller falls back to a configured default.
The pipeline must never fail because Yahoo had a bad day.
"""

import csv
import io
import logging

import requests

logger = logging.getLogger(__name__)

# FRED's CSV download endpoint is public and unauthenticated, unlike the
# JSON API which requires a registered key.
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
TREASURY_10Y_SERIES = "DGS10"

_REQUEST_TIMEOUT = 15


class MarketData:
    """Market-derived WACC inputs for a single ticker, with provenance."""

    def __init__(self, ticker: str):
        self.ticker = ticker
        self.market_cap: float | None = None
        self.beta: float | None = None
        self.total_debt: float | None = None
        self.share_price: float | None = None
        self.sources: dict[str, str] = {}

    def __repr__(self):
        return (
            f"MarketData({self.ticker}, market_cap={self.market_cap}, "
            f"beta={self.beta}, total_debt={self.total_debt}, "
            f"share_price={self.share_price})"
        )


def get_risk_free_rate() -> tuple[float | None, str]:
    """
    Fetch the current 10-year Treasury constant-maturity yield from FRED.

    Returns (rate_as_decimal, source_note). The series carries '.' for
    non-trading days, so we walk backwards to the most recent real value.
    """
    url = FRED_CSV_URL.format(series=TREASURY_10Y_SERIES)
    try:
        response = requests.get(url, timeout=_REQUEST_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("FRED risk-free rate lookup failed: %s", exc)
        return None, ""

    try:
        rows = list(csv.DictReader(io.StringIO(response.text)))
    except csv.Error as exc:
        logger.warning("Could not parse FRED CSV: %s", exc)
        return None, ""

    if not rows:
        logger.warning("FRED returned no rows for %s", TREASURY_10Y_SERIES)
        return None, ""

    # Column name varies in case across FRED revisions ("DATE"/"observation_date").
    value_key = next(
        (k for k in rows[-1] if k and k.upper() == TREASURY_10Y_SERIES), None
    )
    if value_key is None:
        logger.warning("FRED CSV missing %s column", TREASURY_10Y_SERIES)
        return None, ""

    for row in reversed(rows):
        raw = (row.get(value_key) or "").strip()
        if raw and raw != ".":
            try:
                # FRED publishes percent (e.g. 4.28), we want a decimal.
                rate = float(raw) / 100.0
            except ValueError:
                continue
            date_key = next((k for k in row if k and "DATE" in k.upper()), None)
            as_of = row.get(date_key, "") if date_key else ""
            logger.info("Risk-free rate (FRED %s, %s): %.4f", TREASURY_10Y_SERIES, as_of, rate)
            return rate, f"FRED {TREASURY_10Y_SERIES} 10Y Treasury as of {as_of}"

    logger.warning("No usable observations in FRED %s series", TREASURY_10Y_SERIES)
    return None, ""


def get_market_data(ticker: str) -> MarketData:
    """
    Look up market cap, beta, and total debt for a ticker via yfinance.

    yfinance scrapes undocumented Yahoo endpoints, so any field may be
    missing for smaller or foreign-listed names. Missing fields stay None
    and are reported through `sources` so the caller can substitute an
    industry average and say so in the audit trail.
    """
    data = MarketData(ticker)

    try:
        import yfinance
    except ImportError:
        logger.warning("yfinance not installed — skipping market data for %s", ticker)
        return data

    try:
        info = yfinance.Ticker(ticker).info or {}
    except Exception as exc:  # yfinance raises a wide range of network/parse errors
        logger.warning("yfinance lookup failed for %s: %s", ticker, exc)
        return data

    if not info:
        logger.warning("yfinance returned no info for %s", ticker)
        return data

    market_cap = info.get("marketCap")
    if isinstance(market_cap, (int, float)) and market_cap > 0:
        data.market_cap = float(market_cap)
        data.sources["market_cap"] = "Yahoo Finance"

    beta = info.get("beta")
    if isinstance(beta, (int, float)) and beta > 0:
        data.beta = float(beta)
        data.sources["beta"] = "Yahoo Finance"

    total_debt = info.get("totalDebt")
    if isinstance(total_debt, (int, float)) and total_debt >= 0:
        data.total_debt = float(total_debt)
        data.sources["total_debt"] = "Yahoo Finance"

    # Yahoo sometimes omits marketCap and sharesOutstanding while still
    # quoting a price. Keep the price so the caller can rebuild market cap
    # from the share count on the 10-K cover page.
    for price_key in ("currentPrice", "regularMarketPrice", "previousClose"):
        price = info.get(price_key)
        if isinstance(price, (int, float)) and price > 0:
            data.share_price = float(price)
            data.sources["share_price"] = f"Yahoo Finance ({price_key})"
            break

    logger.info("Market data for %s: %s", ticker, data)
    return data
