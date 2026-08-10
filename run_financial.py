#!/usr/bin/env python3
"""
Brandy — Financial Arm Entry Point.

Pipeline: SEC EDGAR → XBRL extraction (+ HTML fallback) → deterministic metrics
        → SQLite storage → optional LLM commentary → Excel export.
"""

import json
import logging
import sys
import uuid
from datetime import datetime

import pandas as pd
import yaml

from brandy.db.engine import init_db
from brandy.financial.sec_client import (
    get_cik,
    get_10k_filings,
    get_company_facts,
    get_submissions_raw,
    download_main_10k_doc,
)
from brandy.financial.xbrl_extractor import (
    extract_metrics_from_xbrl,
    get_available_fiscal_years,
    get_shares_outstanding,
)
from brandy.financial.html_extractor import extract_financial_tables
from brandy.financial.metrics import (
    compute_ebitda,
    compute_gross_profit,
    compute_operating_income,
    compute_ratios,
)
from brandy.financial.market_data import get_risk_free_rate
from brandy.financial.wacc import build_wacc_inputs, compute_total_debt, load_industry_data
from brandy.financial.storage import (
    upsert_company,
    upsert_filing,
    insert_financial_metrics,
    insert_wacc,
    get_company_metrics,
    store_raw_payload,
    start_run,
    finish_run,
)
from brandy.llm.commentary import generate_financial_commentary
from brandy.export.excel import build_financial_workbook


def load_config():
    try:
        with open("config/settings.yaml", "r") as f:
            settings = yaml.safe_load(f)
    except FileNotFoundError:
        settings = {}
    try:
        with open("config/financial_config.yaml", "r") as f:
            fin_config = yaml.safe_load(f)
    except FileNotFoundError:
        fin_config = {}
    return settings, fin_config


def run(tickers: list[str], years: int = None):
    settings, fin_config = load_config()

    # Logging
    log_level = settings.get("logging", {}).get("level", "INFO")
    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    logger = logging.getLogger("brandy.financial")

    # DB
    db_path = settings.get("database", {}).get("path", "brandy.db")
    conn = init_db(db_path)

    # Config
    sec_email = settings.get("sec", {}).get("email", "user@example.com")
    years = years or settings.get("sec", {}).get("years_of_filings", 3)
    use_xbrl = fin_config.get("xbrl", {}).get("preferred_source", True)
    fallback_html = fin_config.get("xbrl", {}).get("fallback_to_html", True)
    llm_model = settings.get("llm", {}).get("model", "gpt-4.1-mini")
    wacc_defaults = fin_config.get("wacc_defaults", {})
    include_leases = wacc_defaults.get("include_operating_leases", True)
    industry_data = load_industry_data()

    # Fetched once per run rather than per ticker — the 10Y Treasury yield
    # is the same for every company being valued.
    risk_free_rate, risk_free_note = get_risk_free_rate()

    # Pipeline run
    run_id = str(uuid.uuid4())
    start_run(conn, run_id, "financial", tickers, {"years": years, "xbrl": use_xbrl})
    logger.info("=== Financial pipeline run %s ===", run_id)
    logger.info("Tickers: %s | Years: %d", tickers, years)

    all_metrics = []
    commentaries = []
    wacc_rows = []

    for ticker in tickers:
        logger.info("── Processing %s ──", ticker)

        # 1. CIK lookup
        result = get_cik(ticker, email=sec_email)
        if not result:
            logger.error("Skipping %s — CIK not found", ticker)
            continue
        cik, company_name = result
        company_id = upsert_company(conn, ticker, company_name, cik)

        # 2. XBRL companyfacts (primary structured source)
        company_facts = None
        if use_xbrl:
            company_facts = get_company_facts(cik, email=sec_email)
            if company_facts:
                store_raw_payload(conn, run_id, "sec_companyfacts", ticker, company_facts)

        # 3. Filing list
        filings = get_10k_filings(cik, years=years, email=sec_email)
        if not filings:
            logger.warning("No 10-K filings found for %s", ticker)
            continue

        # Store raw submissions payload
        raw_subs = get_submissions_raw(cik, email=sec_email)
        if raw_subs:
            store_raw_payload(conn, run_id, "sec_submissions", ticker, raw_subs)

        company_rows = []

        for filing in filings:
            acc = filing["accession"]
            date = filing["date"]
            fy = filing["fiscal_year"]

            filing_id = upsert_filing(conn, company_id, acc, date, fy)

            # 4. Extract metrics — XBRL first
            metrics = {}
            data_source = "xbrl"

            if company_facts and use_xbrl:
                metrics = extract_metrics_from_xbrl(company_facts, fy)

            # 5. Fallback to HTML parsing if XBRL is incomplete
            if fallback_html and len(metrics) < 8:
                logger.info("XBRL incomplete (%d fields) — falling back to HTML for FY%d", len(metrics), fy)
                doc_text = download_main_10k_doc(cik, acc, email=sec_email)
                if doc_text:
                    html_tables = extract_financial_tables(doc_text)
                    if html_tables:
                        store_raw_payload(conn, run_id, "sec_html_tables", f"{ticker}_FY{fy}", html_tables)
                    data_source = "xbrl+html_fallback" if metrics else "html_parsed"

            if not metrics:
                logger.warning("No metrics extracted for %s FY%d", ticker, fy)
                continue

            # 6. Deterministic calculations — NEVER from LLM
            # Gross profit first: most filers don't tag GrossProfit directly,
            # so without deriving it here gross_margin comes out empty.
            gross_profit_result = compute_gross_profit(
                reported_gross_profit=metrics.get("gross_profit"),
                revenue=metrics.get("revenue"),
                cost_of_revenue=metrics.get("cost_of_revenue"),
            )
            metrics["gross_profit"] = gross_profit_result.value
            metrics["gross_profit_source"] = gross_profit_result.source
            metrics["gross_profit_notes"] = gross_profit_result.notes

            # Operating income next — EBITDA consumes it, so it has to be
            # derived first for filers that run gross profit through SG&A.
            operating_income_result = compute_operating_income(
                reported_operating_income=metrics.get("operating_income"),
                gross_profit=metrics.get("gross_profit"),
                sga_expense=metrics.get("sga_expense"),
            )
            metrics["operating_income"] = operating_income_result.value
            metrics["operating_income_source"] = operating_income_result.source

            ebitda_result = compute_ebitda(
                operating_income=metrics.get("operating_income"),
                depreciation_amortization=metrics.get("depreciation_amortization"),
                net_income=metrics.get("net_income"),
                tax_expense=metrics.get("tax_expense"),
            )
            metrics["ebitda"] = ebitda_result.value
            metrics["ebitda_source"] = ebitda_result.source
            metrics["ebitda_notes"] = ebitda_result.notes

            metrics["total_debt"] = compute_total_debt(metrics, include_leases)

            ratios = compute_ratios(metrics)
            metrics.update(ratios)
            metrics["data_source"] = data_source

            # 7. Store in DB
            insert_financial_metrics(conn, filing_id, metrics)
            metrics["fiscal_year"] = fy
            metrics["filing_date"] = date
            metrics["ticker"] = ticker
            company_rows.append(metrics)
            all_metrics.append(metrics)
            logger.info("✓ %s FY%d — %d metrics stored (EBITDA: %s)", ticker, fy, len(metrics), ebitda_result.source)

        # 8. WACC — built from the most recent fiscal year's balance sheet
        #    combined with live market data. Deterministic, never from LLM.
        if company_rows:
            latest = max(company_rows, key=lambda r: r["fiscal_year"])
            try:
                wacc_inputs = build_wacc_inputs(
                    ticker,
                    latest,
                    wacc_defaults=wacc_defaults,
                    industry_data=industry_data,
                    risk_free_rate=risk_free_rate,
                    risk_free_note=risk_free_note,
                    include_operating_leases=include_leases,
                    shares_outstanding=(
                        get_shares_outstanding(company_facts) if company_facts else None
                    ),
                )
            except Exception as exc:
                logger.warning("WACC calculation failed for %s: %s", ticker, exc)
            else:
                wacc_dict = wacc_inputs.to_dict()
                insert_wacc(conn, company_id, latest["fiscal_year"], dict(wacc_dict))
                wacc_rows.append({
                    "ticker": ticker,
                    "fiscal_year": latest["fiscal_year"],
                    **wacc_dict,
                })
                logger.info("✓ %s WACC = %.2f%%", ticker, wacc_inputs.wacc * 100)

        # 9. LLM commentary (qualitative only)
        if company_rows:
            commentary = generate_financial_commentary(ticker, company_rows, model=llm_model)
            if commentary:
                conn.execute(
                    "INSERT INTO financial_commentary (company_id, run_id, commentary, model_used) VALUES (?, ?, ?, ?)",
                    (company_id, run_id, commentary, llm_model),
                )
                conn.commit()
                store_raw_payload(conn, run_id, "llm_response", f"{ticker}_commentary", commentary)
                commentaries.append({"ticker": ticker, "commentary": commentary})
                logger.info("✓ Commentary generated for %s", ticker)

    # 10. Excel export
    output_path = None
    if all_metrics:
        metrics_df = pd.DataFrame(all_metrics)
        commentary_df = pd.DataFrame(commentaries) if commentaries else None
        wacc_df = pd.DataFrame(wacc_rows) if wacc_rows else None

        output_dir = settings.get("output", {}).get("directory", "output")
        output_path = build_financial_workbook(
            metrics_df, commentary_df,
            wacc_df=wacc_df,
            output_dir=output_dir,
        )
        logger.info("Excel report: %s", output_path)

    # 11. Finalize run
    finish_run(conn, run_id, "completed", output_path)
    conn.close()

    print(f"\n{'='*60}")
    print(f"  Financial pipeline complete")
    print(f"  Tickers: {', '.join(tickers)}")
    print(f"  Rows: {len(all_metrics)}")
    if output_path:
        print(f"  Output: {output_path}")
    print(f"{'='*60}")

    return output_path


def main():
    if len(sys.argv) > 1:
        tickers = [t.strip().upper() for t in sys.argv[1].split(",")]
    else:
        raw = input("Enter stock tickers (comma-separated, e.g. AAPL,MSFT): ").strip()
        if not raw:
            print("No tickers entered.")
            sys.exit(1)
        tickers = [t.strip().upper() for t in raw.split(",") if t.strip()]

    years = None
    if len(sys.argv) > 2:
        years = int(sys.argv[2])

    run(tickers, years)


if __name__ == "__main__":
    main()
