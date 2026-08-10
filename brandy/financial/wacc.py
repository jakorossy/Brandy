"""
WACC input assembly.

`metrics.WaccInputs` does the arithmetic; this module decides what numbers
go into it. Each input is resolved through a preference chain — company
filing first, then market data, then industry average — and every fallback
is recorded so the resulting WACC can be audited back to its sources.

Preference chains:
  risk_free_rate        FRED 10Y Treasury      → config default
  beta                  Yahoo Finance          → industry average
  equity_risk_premium   industry (Damodaran)   → config default
  market_cap            Yahoo Finance          → (none: weights fall back to all-equity)
  total_debt            XBRL short+long debt   → Yahoo Finance → industry
  pre_tax_cost_of_debt  interest expense / debt → industry average
  tax_rate              effective from filing  → industry marginal rate
"""

import logging
import os

import yaml

from brandy.financial.market_data import get_market_data, get_risk_free_rate
from brandy.financial.metrics import WaccInputs

logger = logging.getLogger(__name__)

# An effective tax rate outside this band usually means a one-off (loss year,
# valuation-allowance release, repatriation charge) rather than the rate the
# company will actually pay on future cash flows, so we ignore it.
_MIN_TAX_RATE = 0.0
_MAX_TAX_RATE = 0.45

# Likewise for implied cost of debt: outside this band the interest-expense
# and debt-balance figures are almost certainly measuring different things.
_MIN_COST_OF_DEBT = 0.005
_MAX_COST_OF_DEBT = 0.25

_INDUSTRY_CONFIG_PATH = "config/industry_data.yaml"


def load_industry_data(path: str = _INDUSTRY_CONFIG_PATH) -> dict:
    """Load the industry fallback table; return an empty dict if absent."""
    if not os.path.exists(path):
        logger.warning("Industry data file not found: %s", path)
        return {}
    with open(path, "r") as f:
        return yaml.safe_load(f) or {}


def resolve_industry_defaults(ticker: str, industry_data: dict) -> dict:
    """Return the industry fallback values applicable to a ticker."""
    defaults = dict(industry_data.get("default", {}))
    sector_key = (industry_data.get("ticker_sectors", {}) or {}).get(ticker.upper())
    if sector_key:
        sector = (industry_data.get("sectors", {}) or {}).get(sector_key, {})
        defaults.update(sector)
        defaults["_sector"] = sector_key
    return defaults


def compute_total_debt(metrics: dict, include_operating_leases: bool = True) -> float | None:
    """
    Sum interest-bearing debt from the filing.

    Capitalised operating leases are included by default. For apparel
    retailers this is the difference between a meaningful capital structure
    and a nonsense one — Lululemon carries essentially no borrowed debt but
    billions in store leases, and treating it as unlevered would understate
    its WACC badly. It also matches how Yahoo Finance reports totalDebt, so
    the XBRL path and the market-data fallback stay comparable.

    Returns None only when nothing debt-like was tagged at all, so the caller
    can fall back to market data rather than assuming a debt-free balance sheet.
    """
    components = [
        metrics.get("short_term_debt"),
        metrics.get("long_term_debt"),
    ]
    if include_operating_leases:
        components += [
            metrics.get("operating_lease_liability_current"),
            metrics.get("operating_lease_liability_noncurrent"),
        ]

    if all(c is None for c in components):
        return None
    return sum(c or 0.0 for c in components)


def compute_effective_tax_rate(metrics: dict) -> float | None:
    """
    Effective tax rate = tax expense / pre-tax income.

    Falls back to reconstructing pre-tax income as net income + tax expense
    when the filer didn't tag it directly. Returns None if the result isn't
    within a plausible band.
    """
    tax_expense = metrics.get("tax_expense")
    if tax_expense is None:
        return None

    pretax = metrics.get("pretax_income")
    if pretax is None:
        net_income = metrics.get("net_income")
        if net_income is None:
            return None
        pretax = net_income + tax_expense

    if not pretax or pretax <= 0:
        return None

    rate = tax_expense / pretax
    if not _MIN_TAX_RATE <= rate <= _MAX_TAX_RATE:
        logger.info("Effective tax rate %.3f outside plausible band — ignoring", rate)
        return None
    return rate


def compute_cost_of_debt(metrics: dict, total_debt: float | None) -> float | None:
    """
    Implied pre-tax cost of debt = interest expense / total debt.

    Returns None when either input is missing or the result is implausible.
    """
    interest_expense = metrics.get("interest_expense")
    if interest_expense is None or not total_debt or total_debt <= 0:
        return None

    rate = abs(interest_expense) / total_debt
    if not _MIN_COST_OF_DEBT <= rate <= _MAX_COST_OF_DEBT:
        logger.info("Implied cost of debt %.3f outside plausible band — ignoring", rate)
        return None
    return rate


def build_wacc_inputs(
    ticker: str,
    metrics: dict,
    wacc_defaults: dict = None,
    industry_data: dict = None,
    market_data=None,
    risk_free_rate: float = None,
    risk_free_note: str = "",
    include_operating_leases: bool = True,
    shares_outstanding: float = None,
) -> WaccInputs:
    """
    Assemble WACC inputs for a company, resolving each through its
    preference chain and recording the provenance of every value.

    `metrics` is the latest fiscal year's metric dict. `market_data` and
    `risk_free_rate` may be passed in to avoid refetching when building
    WACC for several companies in one run.
    """
    wacc_defaults = wacc_defaults or {}
    industry = resolve_industry_defaults(ticker, industry_data or {})
    notes = []

    if market_data is None:
        market_data = get_market_data(ticker)

    # ── Risk-free rate ──────────────────────────────────────────────
    if risk_free_rate is None:
        risk_free_rate, risk_free_note = get_risk_free_rate()
    if risk_free_rate is not None:
        notes.append(f"risk-free {risk_free_rate:.4f} ({risk_free_note or 'FRED'})")
    else:
        risk_free_rate = wacc_defaults.get("risk_free_rate", 0.043)
        notes.append(f"risk-free {risk_free_rate:.4f} (config default — FRED unavailable)")

    # ── Beta ────────────────────────────────────────────────────────
    if market_data.beta is not None:
        beta = market_data.beta
        notes.append(f"beta {beta:.3f} (Yahoo Finance)")
    else:
        beta = industry.get("beta", wacc_defaults.get("beta", 1.0))
        sector = industry.get("_sector", "market average")
        notes.append(f"beta {beta:.3f} (industry fallback: {sector})")

    # ── Equity risk premium ─────────────────────────────────────────
    erp = industry.get("equity_risk_premium", wacc_defaults.get("equity_risk_premium", 0.055))
    notes.append(f"ERP {erp:.4f} (Damodaran industry table)")

    # ── Market cap ──────────────────────────────────────────────────
    # Without this the capital-structure weights are meaningless, so when
    # Yahoo omits it we rebuild it from the 10-K cover-page share count.
    market_cap = market_data.market_cap
    if market_cap is not None:
        notes.append(f"market cap {market_cap:,.0f} (Yahoo Finance)")
    elif shares_outstanding and market_data.share_price:
        market_cap = shares_outstanding * market_data.share_price
        notes.append(
            f"market cap {market_cap:,.0f} "
            f"(reconstructed: {shares_outstanding:,.0f} SEC shares "
            f"x {market_data.share_price:,.2f} price)"
        )
    else:
        notes.append("market cap unavailable — capital structure assumed unlevered")

    # ── Total debt ──────────────────────────────────────────────────
    total_debt = compute_total_debt(metrics, include_operating_leases)
    lease_basis = "incl. operating leases" if include_operating_leases else "excl. operating leases"
    if total_debt is not None:
        notes.append(f"total debt {total_debt:,.0f} (XBRL borrowings, {lease_basis})")
    elif market_data.total_debt is not None:
        total_debt = market_data.total_debt
        notes.append(f"total debt {total_debt:,.0f} (Yahoo Finance — not tagged in filing)")
    else:
        notes.append("total debt unavailable")

    # ── Cost of debt ────────────────────────────────────────────────
    # Measured against borrowings only: interest expense doesn't cover the
    # lease liabilities, so including them in the denominator would drag the
    # implied rate artificially low.
    borrowings_only = compute_total_debt(metrics, include_operating_leases=False)
    cost_of_debt = compute_cost_of_debt(metrics, borrowings_only)
    if cost_of_debt is not None:
        notes.append(f"pre-tax cost of debt {cost_of_debt:.4f} (interest expense / total debt)")
    else:
        cost_of_debt = industry.get(
            "pre_tax_cost_of_debt", wacc_defaults.get("pre_tax_cost_of_debt", 0.05)
        )
        notes.append(f"pre-tax cost of debt {cost_of_debt:.4f} (industry fallback)")

    # ── Tax rate ────────────────────────────────────────────────────
    tax_rate = compute_effective_tax_rate(metrics)
    if tax_rate is not None:
        notes.append(f"tax rate {tax_rate:.4f} (effective, from filing)")
    else:
        tax_rate = industry.get("marginal_tax_rate", wacc_defaults.get("tax_rate", 0.21))
        notes.append(f"tax rate {tax_rate:.4f} (statutory fallback)")

    inputs = WaccInputs(
        risk_free_rate=risk_free_rate,
        equity_risk_premium=erp,
        beta=beta,
        pre_tax_cost_of_debt=cost_of_debt,
        tax_rate=tax_rate,
        market_cap=market_cap,
        total_debt=total_debt,
        assumptions_note="; ".join(notes),
    )

    if inputs.capital_structure_estimated:
        inputs.assumptions_note += (
            "; WARNING: equity value unknown, WACC computed unlevered "
            "(equals cost of equity) and is an upper-bound estimate"
        )
        logger.warning(
            "%s WACC is unlevered — equity value could not be determined", ticker
        )

    logger.info(
        "WACC for %s = %.4f (Ke %.4f, Kd(after-tax) %.4f, We %.2f, Wd %.2f)",
        ticker, inputs.wacc, inputs.cost_of_equity,
        inputs.after_tax_cost_of_debt, inputs.weight_equity, inputs.weight_debt,
    )
    return inputs
