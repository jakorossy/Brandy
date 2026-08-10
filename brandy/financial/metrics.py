"""
Deterministic financial calculations.

ALL arithmetic happens here — never in the LLM.
Covers: EBITDA (with provenance), margins, ratios, free cash flow, WACC.
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ── EBITDA with provenance ──────────────────────────────────────────

@dataclass
class EbitdaResult:
    value: float | None = None
    source: str = "unavailable"    # reported | calculated | approximated
    notes: str = ""


def compute_ebitda(
    operating_income: float | None = None,
    depreciation_amortization: float | None = None,
    net_income: float | None = None,
    tax_expense: float | None = None,
    reported_ebitda: float | None = None,
) -> EbitdaResult:
    """
    Compute EBITDA with provenance tracking.

    Priority:
    1. If EBITDA was directly reported in XBRL, use it.
    2. If operating_income + D&A are available, calculate it.
    3. If net_income + tax + D&A are available, calculate it.
    4. If only operating_income is available, use it as an approximation.
    """
    # 1. Directly reported
    if reported_ebitda is not None:
        return EbitdaResult(
            value=reported_ebitda,
            source="reported",
            notes="EBITDA directly reported in filing",
        )

    # 2. Operating Income + D&A
    if operating_income is not None and depreciation_amortization is not None:
        return EbitdaResult(
            value=operating_income + depreciation_amortization,
            source="calculated",
            notes="EBITDA = Operating Income + D&A",
        )

    # 3. Net Income + Tax + D&A
    if all(v is not None for v in [net_income, tax_expense, depreciation_amortization]):
        return EbitdaResult(
            value=net_income + tax_expense + depreciation_amortization,
            source="calculated",
            notes="EBITDA = Net Income + Tax + D&A",
        )

    # 4. Approximation: just operating income
    if operating_income is not None:
        return EbitdaResult(
            value=operating_income,
            source="approximated",
            notes="D&A not available; using Operating Income as proxy",
        )

    return EbitdaResult()


# ── Gross profit with provenance ────────────────────────────────────

@dataclass
class GrossProfitResult:
    value: float | None = None
    source: str = "unavailable"    # reported | calculated
    notes: str = ""


def compute_gross_profit(
    reported_gross_profit: float | None = None,
    revenue: float | None = None,
    cost_of_revenue: float | None = None,
) -> GrossProfitResult:
    """
    Compute gross profit with provenance tracking.

    Most filers never tag GrossProfit directly — they report revenue and
    cost of revenue separately and leave the subtraction to the reader.
    Without this fallback, gross_margin is None for the majority of companies.

    Priority:
    1. If GrossProfit was directly reported in XBRL, use it.
    2. Otherwise derive it as Revenue - Cost of Revenue.
    """
    if reported_gross_profit is not None:
        return GrossProfitResult(
            value=reported_gross_profit,
            source="reported",
            notes="Gross profit directly reported in filing",
        )

    if revenue is not None and cost_of_revenue is not None:
        return GrossProfitResult(
            value=revenue - cost_of_revenue,
            source="calculated",
            notes="Gross Profit = Revenue - Cost of Revenue",
        )

    return GrossProfitResult()


# ── Operating income with provenance ────────────────────────────────

@dataclass
class OperatingIncomeResult:
    value: float | None = None
    source: str = "unavailable"    # reported | calculated
    notes: str = ""


def compute_operating_income(
    reported_operating_income: float | None = None,
    gross_profit: float | None = None,
    sga_expense: float | None = None,
) -> OperatingIncomeResult:
    """
    Compute operating income with provenance tracking.

    Some filers (Nike among them) never tag OperatingIncomeLoss, running
    their income statement straight from gross profit through SG&A to
    pre-tax income. Deriving it keeps operating_margin populated.

    Priority:
    1. If OperatingIncomeLoss was reported in XBRL, use it.
    2. Otherwise derive it as Gross Profit - SG&A.
    """
    if reported_operating_income is not None:
        return OperatingIncomeResult(
            value=reported_operating_income,
            source="reported",
            notes="Operating income directly reported in filing",
        )

    if gross_profit is not None and sga_expense is not None:
        return OperatingIncomeResult(
            value=gross_profit - sga_expense,
            source="calculated",
            notes="Operating Income = Gross Profit - SG&A",
        )

    return OperatingIncomeResult()


# ── Ratios and margins ──────────────────────────────────────────────

def safe_divide(numerator: float | None, denominator: float | None) -> float | None:
    """Divide two values, returning None if either is missing or denominator is zero."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def compute_ratios(metrics: dict) -> dict:
    """
    Compute all deterministic ratios from raw metric values.

    Input dict keys match the financial_metrics table columns.
    Returns a dict of computed ratios.
    """
    revenue = metrics.get("revenue")
    gross_profit = metrics.get("gross_profit")
    operating_income = metrics.get("operating_income")
    net_income = metrics.get("net_income")
    shareholders_equity = metrics.get("shareholders_equity")
    total_liabilities = metrics.get("total_liabilities")
    operating_cash_flow = metrics.get("operating_cash_flow")
    capital_expenditures = metrics.get("capital_expenditures")

    # Free cash flow
    fcf = None
    if operating_cash_flow is not None and capital_expenditures is not None:
        # CapEx from XBRL is typically positive (payments), so subtract it
        fcf = operating_cash_flow - abs(capital_expenditures)

    return {
        "free_cash_flow": fcf,
        "gross_margin": safe_divide(gross_profit, revenue),
        "operating_margin": safe_divide(operating_income, revenue),
        "net_margin": safe_divide(net_income, revenue),
        "roe": safe_divide(net_income, shareholders_equity),
        "debt_to_equity": safe_divide(total_liabilities, shareholders_equity),
    }


# ── WACC ────────────────────────────────────────────────────────────

@dataclass
class WaccInputs:
    """All inputs needed for WACC calculation, stored for auditability."""
    risk_free_rate: float = 0.0
    equity_risk_premium: float = 0.0
    beta: float = 1.0
    pre_tax_cost_of_debt: float = 0.0
    tax_rate: float = 0.21           # US corporate default
    market_cap: float | None = None
    total_debt: float | None = None
    assumptions_note: str = ""

    # Calculated fields
    cost_of_equity: float = field(init=False, default=0.0)
    after_tax_cost_of_debt: float = field(init=False, default=0.0)
    weight_equity: float = field(init=False, default=0.0)
    weight_debt: float = field(init=False, default=0.0)
    wacc: float = field(init=False, default=0.0)
    # True when the capital structure had to be assumed rather than measured,
    # which makes the resulting WACC an estimate rather than a calculation.
    capital_structure_estimated: bool = field(init=False, default=False)

    def __post_init__(self):
        self.calculate()

    def calculate(self):
        """Run the WACC calculation from inputs."""
        # Cost of equity (CAPM)
        self.cost_of_equity = self.risk_free_rate + self.beta * self.equity_risk_premium

        # After-tax cost of debt
        self.after_tax_cost_of_debt = self.pre_tax_cost_of_debt * (1 - self.tax_rate)

        # Capital structure weights.
        #
        # Market cap is what makes these weights meaningful. Treating a missing
        # market cap as zero equity would imply a 100%-debt company and collapse
        # WACC onto the after-tax cost of debt — a number low enough to look
        # plausible while being badly wrong. When equity value is unknown we
        # fall back to an unlevered structure and say so, rather than inferring
        # leverage we cannot observe.
        if self.market_cap is None:
            self.capital_structure_estimated = True
            self.weight_equity = 1.0
            self.weight_debt = 0.0
        else:
            total_capital = self.market_cap + (self.total_debt or 0)
            if total_capital > 0:
                self.weight_equity = self.market_cap / total_capital
                self.weight_debt = (self.total_debt or 0) / total_capital
            else:
                self.capital_structure_estimated = True
                self.weight_equity = 1.0
                self.weight_debt = 0.0

        # WACC
        self.wacc = (
            self.weight_equity * self.cost_of_equity
            + self.weight_debt * self.after_tax_cost_of_debt
        )

    def to_dict(self) -> dict:
        """Return all inputs + outputs as a flat dict for DB storage."""
        return {
            "risk_free_rate": self.risk_free_rate,
            "equity_risk_premium": self.equity_risk_premium,
            "beta": self.beta,
            "cost_of_equity": self.cost_of_equity,
            "pre_tax_cost_of_debt": self.pre_tax_cost_of_debt,
            "tax_rate": self.tax_rate,
            "after_tax_cost_of_debt": self.after_tax_cost_of_debt,
            "market_cap": self.market_cap,
            "total_debt": self.total_debt,
            "weight_equity": self.weight_equity,
            "weight_debt": self.weight_debt,
            "wacc": self.wacc,
            "assumptions_note": self.assumptions_note,
        }
