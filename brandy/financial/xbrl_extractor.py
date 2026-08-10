"""
XBRL structured data extractor.

Pulls financial metrics directly from SEC companyfacts JSON.
This is the PRIMARY extraction path — machine-readable, no AI needed.

The SEC companyfacts JSON contains US-GAAP tagged values across all filings.
We extract the relevant metrics for a given fiscal year from the 10-K annual reports.
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Map our internal metric names to US-GAAP XBRL taxonomy tags.
# Many companies use slightly different tags, so we try multiple candidates
# in priority order for each metric.
GAAP_TAG_MAP = {
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
    ],
    "cost_of_revenue": [
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
        "CostOfGoodsSold",
        "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization",
    ],
    "gross_profit": [
        "GrossProfit",
    ],
    "operating_expenses": [
        "OperatingExpenses",
        "CostsAndExpenses",
    ],
    "sga_expense": [
        "SellingGeneralAndAdministrativeExpense",
        "GeneralAndAdministrativeExpense",
    ],
    "operating_income": [
        "OperatingIncomeLoss",
    ],
    "net_income": [
        "NetIncomeLoss",
        "ProfitLoss",
    ],
    "eps_diluted": [
        "EarningsPerShareDiluted",
    ],
    "total_assets": [
        "Assets",
    ],
    "total_liabilities": [
        "Liabilities",
        "LiabilitiesAndStockholdersEquity",  # fallback, less ideal
    ],
    "shareholders_equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "cash_and_equivalents": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsAndShortTermInvestments",
        "Cash",
    ],
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByOperatingActivities",
    ],
    "capital_expenditures": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "CapitalExpenditureDiscontinuedOperations",
    ],
    "depreciation_amortization": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "Depreciation",
    ],
    "tax_expense": [
        "IncomeTaxExpenseBenefit",
    ],
    "pretax_income": [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ],
    # ── WACC inputs ──────────────────────────────────────────────────
    # Debt is split short/long so total_debt can be summed from the two
    # legs; falling back to `Liabilities` would badly overstate leverage
    # (it sweeps in payables, deferred revenue, and lease obligations).
    "short_term_debt": [
        "DebtCurrent",
        "LongTermDebtCurrent",      # current maturities of long-term debt
        "ShortTermBorrowings",
        "OtherShortTermBorrowings",
    ],
    "long_term_debt": [
        "LongTermDebtNoncurrent",
        "LongTermDebt",
    ],
    # Capitalised operating leases. For apparel retail these routinely dwarf
    # borrowed debt (store portfolios), so whether they count toward the
    # capital structure materially moves WACC — see `include_operating_leases`
    # in financial_config.yaml.
    "operating_lease_liability_current": [
        "OperatingLeaseLiabilityCurrent",
    ],
    "operating_lease_liability_noncurrent": [
        "OperatingLeaseLiabilityNoncurrent",
    ],
    # Deliberately excludes InterestIncomeExpenseNonoperatingNet: it nets
    # interest income against expense, so for a cash-rich filer it can be
    # negative and would imply a nonsense cost of debt.
    "interest_expense": [
        "InterestExpense",
        "InterestExpenseDebt",
        "InterestExpenseNonoperating",
        "InterestPaidNet",
        "InterestPaid",
    ],
}


def _get_annual_value(fact_data: dict, fiscal_year: int) -> float | None:
    """
    Extract the annual (10-K) value for a given fiscal year from a single
    XBRL fact's unit array.

    The companyfacts structure per tag is:
      facts -> us-gaap -> TagName -> units -> USD -> [
        {"val": 123, "fy": 2023, "fp": "FY", "form": "10-K", ...}, ...
      ]

    We prefer form="10-K" and fp="FY" for the target fiscal year.

    Note that `fy` identifies the filing the fact appeared in, not the period
    the value covers: a FY2025 10-K carries its FY2023 and FY2024 comparatives
    tagged fy=2025 too. Among the matches we therefore take the one with the
    latest `end` date, which is the filing's current year rather than a
    comparative. Relying on array order happens to work but silently yields a
    prior year's figure whenever the SEC's ordering differs.
    """
    units = fact_data.get("units", {})

    # Try USD first, then USD/shares for per-share metrics
    for unit_key in ["USD", "USD/shares"]:
        entries = units.get(unit_key, [])
        if not entries:
            continue

        # Filter to 10-K annual filings for the target year
        annual = [
            e for e in entries
            if e.get("form") == "10-K"
            and e.get("fy") == fiscal_year
            and e.get("fp") == "FY"
        ]

        if annual:
            # Latest period end = the filing's current year, not a comparative.
            # Falls back to array order for the rare entry with no `end`.
            latest = max(annual, key=lambda e: e.get("end") or "")
            return latest["val"]

    return None


def extract_metrics_from_xbrl(
    company_facts: dict,
    fiscal_year: int,
) -> dict:
    """
    Extract all available financial metrics for a fiscal year from XBRL data.

    Returns a dict with metric names as keys and values as floats.
    Missing metrics are omitted (not set to None) so the caller knows
    what was actually available vs. what needs fallback.
    """
    gaap_facts = company_facts.get("facts", {}).get("us-gaap", {})

    if not gaap_facts:
        logger.warning("No us-gaap facts in companyfacts data")
        return {}

    extracted = {}

    for metric_name, candidate_tags in GAAP_TAG_MAP.items():
        for tag in candidate_tags:
            if tag not in gaap_facts:
                continue
            val = _get_annual_value(gaap_facts[tag], fiscal_year)
            if val is not None:
                extracted[metric_name] = val
                logger.debug(
                    "XBRL %s = %s (tag: %s, fy: %d)",
                    metric_name, val, tag, fiscal_year,
                )
                break  # got a value, stop trying alternatives

    logger.info(
        "XBRL extraction for FY%d: %d/%d metrics found",
        fiscal_year, len(extracted), len(GAAP_TAG_MAP),
    )
    return extracted


def get_shares_outstanding(company_facts: dict, max_age_days: int = 550) -> float | None:
    """
    Pull the most recent common shares outstanding from the `dei` namespace.

    Yahoo Finance intermittently returns no share count (and therefore no
    market cap) for perfectly ordinary listed companies. The cover page of
    every 10-K carries this figure, so it's a way to reconstruct market cap
    as shares x price.

    Returns None if the newest value is older than `max_age_days`. Some
    filers stopped tagging this years ago — Nike's latest is from 2015 and
    counts only one share class — and multiplying a decade-old count by
    today's price would silently produce a badly wrong market cap. A stale
    figure is worse than none, because the caller can flag a missing value
    but cannot detect a plausible-looking wrong one.
    """
    dei_facts = company_facts.get("facts", {}).get("dei", {})
    tag = dei_facts.get("EntityCommonStockSharesOutstanding")
    if not tag:
        return None

    entries = [e for e in tag.get("units", {}).get("shares", []) if e.get("end")]
    if not entries:
        return None

    # Cover-page values are point-in-time; take the most recently dated.
    latest = max(entries, key=lambda e: e["end"])
    value = latest.get("val")
    if not value:
        return None

    try:
        as_of = datetime.strptime(latest["end"], "%Y-%m-%d")
    except ValueError:
        return None

    age_days = (datetime.now() - as_of).days
    if age_days > max_age_days:
        logger.warning(
            "Ignoring shares outstanding for market cap: newest value is from %s "
            "(%d days old, limit %d)",
            latest["end"], age_days, max_age_days,
        )
        return None

    logger.info("Shares outstanding: %s (as of %s)", f"{value:,}", latest["end"])
    return float(value)


def get_available_fiscal_years(company_facts: dict) -> list[int]:
    """
    Scan the XBRL data to find which fiscal years have 10-K data.
    Returns sorted list of years (descending).
    """
    gaap_facts = company_facts.get("facts", {}).get("us-gaap", {})
    years = set()

    # Check a commonly-reported tag to find available years
    for tag_name in ["Revenues", "Assets", "NetIncomeLoss", "SalesRevenueNet"]:
        if tag_name not in gaap_facts:
            continue
        units = gaap_facts[tag_name].get("units", {})
        for unit_entries in units.values():
            for entry in unit_entries:
                if entry.get("form") == "10-K" and entry.get("fp") == "FY":
                    fy = entry.get("fy")
                    if fy:
                        years.add(fy)

    return sorted(years, reverse=True)
