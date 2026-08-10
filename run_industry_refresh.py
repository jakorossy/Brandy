#!/usr/bin/env python3
"""
Brandy — Industry Data Refresh.

Downloads Aswath Damodaran's free NYU Stern datasets and rewrites
config/industry_data.yaml with the current industry averages used as WACC
fallbacks.

Run this once a year. Damodaran updates the datasets each January, so the
values drift slowly and there is no reason to hit the network on every
pipeline run — that would make every valuation depend on a professor's web
server being up. Keeping the refresh explicit also means the numbers behind
any given valuation are pinned in version control.

    python run_industry_refresh.py

Sources:
    betas.xls  — levered/unlevered beta, D/E, effective tax rate by industry
    wacc.xls   — pre-tax cost of debt by industry, plus the ERP and
                 risk-free rate Damodaran used for that vintage
"""

import argparse
import datetime
import logging
import sys

import requests
import yaml

BASE_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/{name}"
BETAS_FILE = "betas.xls"
WACC_FILE = "wacc.xls"

OUTPUT_PATH = "config/industry_data.yaml"

# Damodaran's industry names are more granular than "apparel". Mapping the
# comp set to the closest-fitting industry gives a better fallback beta than
# lumping footwear and off-mall specialty retail together.
SECTOR_INDUSTRIES = {
    "apparel": "Apparel",
    "footwear": "Shoe",
    "specialty_retail": "Retail (Special Lines)",
    "general_retail": "Retail (General)",
    "market": "Total Market",
}

# Ticker → sector key. Preserved across refreshes; edit here to extend.
DEFAULT_TICKER_SECTORS = {
    "NKE": "footwear",
    "LULU": "apparel",
    "UAA": "apparel",
    "UA": "apparel",
    "GAP": "specialty_retail",
    "AEO": "specialty_retail",
    "VSCO": "specialty_retail",
    "PVH": "apparel",
    "RL": "apparel",
    "LEVI": "apparel",
    "VFC": "apparel",
}

_REQUEST_TIMEOUT = 60
_EXCEL_EPOCH = datetime.date(1899, 12, 30)

logger = logging.getLogger("brandy.industry")


def download(name: str, dest: str):
    """Fetch one dataset to a local path."""
    url = BASE_URL.format(name=name)
    logger.info("Downloading %s", url)
    response = requests.get(
        url, timeout=_REQUEST_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"}
    )
    response.raise_for_status()
    with open(dest, "wb") as f:
        f.write(response.content)
    logger.info("Saved %s (%d bytes)", dest, len(response.content))


def _serial_to_date(serial: float) -> str:
    """Convert an Excel date serial to an ISO date string."""
    return (_EXCEL_EPOCH + datetime.timedelta(days=int(float(serial)))).isoformat()


def _find_header_row(sheet, first_col_value: str = "Industry Name") -> int:
    """Locate the header row; the sheets carry several rows of preamble."""
    for r in range(sheet.nrows):
        if str(sheet.cell_value(r, 0)).strip() == first_col_value:
            return r
    raise ValueError(f"Could not find header row starting with {first_col_value!r}")


def _row_lookup(sheet, header_row: int) -> tuple[dict, dict]:
    """Return (industry_name -> {column: value}, header index map)."""
    headers = [str(sheet.cell_value(header_row, c)).strip() for c in range(sheet.ncols)]
    idx = {h: i for i, h in enumerate(headers)}

    rows = {}
    for r in range(header_row + 1, sheet.nrows):
        name = str(sheet.cell_value(r, 0)).strip()
        if name:
            rows[name] = [sheet.cell_value(r, c) for c in range(sheet.ncols)]
    return rows, idx


def _num(value) -> float | None:
    """Coerce a cell to float, returning None for blanks and text."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _col(row: list, idx: dict, *names: str) -> float | None:
    """
    Read the first matching column by name, case-insensitively.

    Damodaran hand-maintains these sheets, so header text drifts between
    vintages — 'Beta ' carries a trailing space in the current file. Matching
    loosely keeps an annual refresh from breaking on cosmetic edits.
    """
    normalised = {k.strip().lower(): v for k, v in idx.items()}
    for name in names:
        pos = normalised.get(name.strip().lower())
        if pos is not None:
            return _num(row[pos])
    return None


def parse_datasets(betas_path: str, wacc_path: str) -> dict:
    """Extract the industry averages Brandy uses from both workbooks."""
    import xlrd

    betas_wb = xlrd.open_workbook(betas_path)
    betas_sh = betas_wb.sheet_by_name("Industry Averages")
    as_of = _serial_to_date(betas_sh.cell_value(0, 1))

    b_header = _find_header_row(betas_sh)
    b_rows, b_idx = _row_lookup(betas_sh, b_header)

    wacc_wb = xlrd.open_workbook(wacc_path)
    wacc_sh = wacc_wb.sheet_by_name("Industry Averages")

    # The ERP and risk-free rate sit in the preamble as labelled inputs. The
    # label is in column 0 but the value is several columns to its right
    # (currently column 3), so scan the row rather than assuming a position.
    def _labelled_input(label_prefix: str) -> float | None:
        for r in range(wacc_sh.nrows):
            if not str(wacc_sh.cell_value(r, 0)).strip().lower().startswith(label_prefix):
                continue
            for c in range(1, wacc_sh.ncols):
                value = _num(wacc_sh.cell_value(r, c))
                if value is not None and 0 < value < 1:
                    return value
        return None

    erp = _labelled_input("risk premium to use for equity")
    risk_free = _labelled_input("long term treasury bond rate")

    w_header = _find_header_row(wacc_sh)
    w_rows, w_idx = _row_lookup(wacc_sh, w_header)

    sectors = {}
    for sector_key, industry in SECTOR_INDUSTRIES.items():
        brow = b_rows.get(industry)
        wrow = w_rows.get(industry)
        if brow is None or wrow is None:
            logger.warning("Industry %r not found — skipping sector %r", industry, sector_key)
            continue

        # Prefer the cash-corrected unlevered beta: the plain unlevered figure
        # is dragged toward zero by corporate cash, which isn't an operating asset.
        unlevered = _col(
            brow, b_idx, "Unlevered beta corrected for cash", "Unlevered beta"
        )

        sectors[sector_key] = {
            "damodaran_industry": industry,
            "number_of_firms": int(_col(brow, b_idx, "Number of firms") or 0),
            "beta_levered": round(_col(brow, b_idx, "Beta") or 0, 4),
            "beta_unlevered": round(unlevered or 0, 4),
            "debt_to_equity": round(_col(brow, b_idx, "D/E Ratio") or 0, 4),
            "effective_tax_rate": round(_col(brow, b_idx, "Effective Tax rate") or 0, 4),
            "pre_tax_cost_of_debt": round(_col(wrow, w_idx, "Cost of Debt") or 0, 4),
            "cost_of_capital": round(_col(wrow, w_idx, "Cost of Capital") or 0, 4),
        }
        logger.info(
            "%-18s -> %-24s beta(lev) %.4f  beta(unlev) %.4f  Kd %.4f",
            sector_key, industry,
            sectors[sector_key]["beta_levered"],
            sectors[sector_key]["beta_unlevered"],
            sectors[sector_key]["pre_tax_cost_of_debt"],
        )

    return {
        "as_of": as_of,
        "equity_risk_premium": round(erp, 4) if erp else None,
        "damodaran_risk_free_rate": round(risk_free, 4) if risk_free else None,
        "sectors": sectors,
    }


def build_config(parsed: dict, ticker_sectors: dict) -> dict:
    """Assemble the YAML document written to config/industry_data.yaml."""
    market = parsed["sectors"].get("market", {})

    return {
        "as_of": parsed["as_of"],
        "verified": True,
        "source": "Aswath Damodaran, NYU Stern — betas.xls + wacc.xls",
        "refreshed_by": "run_industry_refresh.py",
        "refreshed_on": datetime.date.today().isoformat(),
        "equity_risk_premium": parsed["equity_risk_premium"],
        "damodaran_risk_free_rate": parsed["damodaran_risk_free_rate"],
        "default": {
            "equity_risk_premium": parsed["equity_risk_premium"],
            "beta": market.get("beta_levered", 1.0),
            "beta_unlevered": market.get("beta_unlevered", 1.0),
            "pre_tax_cost_of_debt": market.get("pre_tax_cost_of_debt", 0.055),
            "marginal_tax_rate": 0.21,
        },
        "sectors": {
            key: {
                **values,
                "equity_risk_premium": parsed["equity_risk_premium"],
                "beta": values["beta_levered"],
                "marginal_tax_rate": 0.21,
            }
            for key, values in parsed["sectors"].items()
        },
        "ticker_sectors": ticker_sectors,
    }


HEADER_COMMENT = """\
# ── Industry-average fallbacks for WACC inputs ────────────────────────
#
# GENERATED FILE — do not hand-edit the values.
# Regenerate with:  python run_industry_refresh.py
#
# Used only when a company-specific value can't be sourced (e.g. Yahoo has
# no beta for a thinly-traded name, or a filer doesn't tag interest expense).
# Every substituted value is recorded in the WACC assumptions_note so the
# audit trail shows it wasn't company-specific.
#
# Source: Aswath Damodaran, NYU Stern — updated each January.
#   https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls
#   https://pages.stern.nyu.edu/~adamodar/pc/datasets/wacc.xls
#
# beta_unlevered is the cash-corrected asset beta. Prefer re-levering it to
# the company's own D/E over using beta_levered, which reflects the average
# leverage of the industry rather than of the company being valued.
#
# To add a ticker, edit DEFAULT_TICKER_SECTORS in run_industry_refresh.py.
"""


def main():
    parser = argparse.ArgumentParser(description="Refresh Damodaran industry data")
    parser.add_argument("--output", default=OUTPUT_PATH, help="Config file to write")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the result without writing"
    )
    parser.add_argument(
        "--reset-tickers",
        action="store_true",
        help="Overwrite the ticker->sector map with the built-in defaults "
             "instead of preserving what's on disk",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    try:
        import xlrd  # noqa: F401
    except ImportError:
        logger.error("xlrd is required to read Damodaran's .xls files: pip install xlrd")
        sys.exit(1)

    import tempfile, os
    workdir = tempfile.mkdtemp(prefix="brandy_industry_")
    betas_path = os.path.join(workdir, BETAS_FILE)
    wacc_path = os.path.join(workdir, WACC_FILE)

    try:
        download(BETAS_FILE, betas_path)
        download(WACC_FILE, wacc_path)
    except requests.RequestException as exc:
        logger.error("Download failed: %s", exc)
        logger.error("Existing %s left unchanged.", args.output)
        sys.exit(1)

    parsed = parse_datasets(betas_path, wacc_path)

    # Keep whatever ticker mapping is already on disk so local additions
    # survive a refresh; fall back to the built-in defaults.
    ticker_sectors = dict(DEFAULT_TICKER_SECTORS)
    if args.reset_tickers:
        logger.info("Reset %d ticker mappings to built-in defaults", len(ticker_sectors))
    else:
        try:
            with open(args.output, "r") as f:
                existing = yaml.safe_load(f) or {}
            if existing.get("ticker_sectors"):
                ticker_sectors = existing["ticker_sectors"]
                logger.info("Preserved %d existing ticker mappings", len(ticker_sectors))
        except FileNotFoundError:
            pass

    # A mapping that points at a sector we no longer publish would silently
    # fall through to the market average.
    unknown = {t: s for t, s in ticker_sectors.items() if s not in parsed["sectors"]}
    if unknown:
        logger.warning(
            "Tickers mapped to unknown sectors (will use market average): %s", unknown
        )

    config = build_config(parsed, ticker_sectors)
    rendered = HEADER_COMMENT + "\n" + yaml.safe_dump(config, sort_keys=False)

    if args.dry_run:
        print(rendered)
        return

    with open(args.output, "w") as f:
        f.write(rendered)

    print(f"\n{'='*60}")
    print("  Industry data refreshed")
    print(f"  Damodaran vintage : {parsed['as_of']}")
    print(f"  ERP               : {parsed['equity_risk_premium']}")
    print(f"  Sectors           : {len(parsed['sectors'])}")
    print(f"  Written to        : {args.output}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
