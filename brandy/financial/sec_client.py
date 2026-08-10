"""
SEC EDGAR API client.

Handles CIK lookup, filing list retrieval, XBRL companyfacts download,
and raw 10-K document download (fallback path).

Reuses working logic from the original financial_analyzer.py.
"""

import re
import time
import json
import logging
import requests

logger = logging.getLogger(__name__)

DEFAULT_SEC_EMAIL = "user@example.com"
RATE_LIMIT_DELAY = 0.4  # SEC asks for max 10 req/sec


def _headers(email: str = None) -> dict:
    agent = f"Brandy Pipeline {email or DEFAULT_SEC_EMAIL}"
    return {
        "User-Agent": agent,
        "Accept-Encoding": "gzip, deflate",
    }


# ── CIK lookup (reused from financial_analyzer.py L41-58) ───────────

def get_cik(ticker: str, email: str = None) -> tuple[str, str] | None:
    """
    Look up SEC CIK number for a stock ticker.
    Returns (cik_padded, company_name) or None.
    """
    url = "https://www.sec.gov/files/company_tickers.json"
    try:
        r = requests.get(url, headers=_headers(email), timeout=15)
        r.raise_for_status()
        data = r.json()
        for entry in data.values():
            if entry["ticker"].upper() == ticker.upper():
                cik = str(entry["cik_str"]).zfill(10)
                name = entry["title"]
                logger.info("CIK for %s: %s (%s)", ticker, cik, name)
                return cik, name
        logger.warning("No CIK found for ticker: %s", ticker)
        return None
    except Exception:
        logger.exception("CIK lookup failed for %s", ticker)
        return None


# ── Filing list (reused from financial_analyzer.py L61-88) ──────────

def get_10k_filings(cik: str, years: int = 3, email: str = None) -> list[dict]:
    """
    Fetch recent 10-K filings for a CIK.
    Returns list of {"accession": ..., "date": ..., "fiscal_year": ...}.
    """
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    try:
        time.sleep(RATE_LIMIT_DELAY)
        r = requests.get(url, headers=_headers(email), timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception:
        logger.exception("Could not fetch filing list for CIK %s", cik)
        return []

    filings = data.get("filings", {}).get("recent", {})
    forms = filings.get("form", [])
    accessions = filings.get("accessionNumber", [])
    dates = filings.get("filingDate", [])

    from datetime import datetime
    cutoff_year = datetime.now().year - years

    results = []
    for form, acc, date in zip(forms, accessions, dates):
        if form == "10-K" and int(date[:4]) >= cutoff_year:
            results.append({
                "accession": acc,
                "date": date,
                "fiscal_year": int(date[:4]),
            })
            if len(results) >= years:
                break

    logger.info("Found %d 10-K filing(s) for CIK %s", len(results), cik)
    return results


# ── XBRL CompanyFacts (NEW — structured data, no AI needed) ────────

def get_company_facts(cik: str, email: str = None) -> dict | None:
    """
    Fetch the full XBRL companyfacts JSON for a CIK.
    This is the structured data source — machine-readable financial values
    tagged by US-GAAP taxonomy. Preferred over HTML table parsing.
    Returns the raw JSON dict or None on failure.
    """
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
    try:
        time.sleep(RATE_LIMIT_DELAY)
        r = requests.get(url, headers=_headers(email), timeout=30)
        r.raise_for_status()
        data = r.json()
        logger.info(
            "XBRL companyfacts retrieved for CIK %s (%d fact categories)",
            cik, len(data.get("facts", {}).get("us-gaap", {})),
        )
        return data
    except Exception:
        logger.exception("XBRL companyfacts fetch failed for CIK %s", cik)
        return None


# ── SEC submissions JSON (raw payload for storage) ──────────────────

def get_submissions_raw(cik: str, email: str = None) -> str | None:
    """Fetch raw SEC submissions JSON as a string for raw_payloads storage."""
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    try:
        time.sleep(RATE_LIMIT_DELAY)
        r = requests.get(url, headers=_headers(email), timeout=15)
        r.raise_for_status()
        return r.text
    except Exception:
        logger.exception("Raw submissions fetch failed for CIK %s", cik)
        return None


# ── Raw 10-K download (reused from financial_analyzer.py L91-121) ──
# This is the FALLBACK path: only needed when XBRL doesn't cover a field.

def download_main_10k_doc(cik: str, accession: str, email: str = None) -> str | None:
    """
    Download the main 10-K HTML document from the full submission.
    Used as fallback when XBRL data is incomplete.
    """
    cik_plain = str(int(cik))
    acc_clean = accession.replace("-", "")
    url = (
        f"https://www.sec.gov/Archives/edgar/data/"
        f"{cik_plain}/{acc_clean}/{accession}.txt"
    )
    try:
        time.sleep(RATE_LIMIT_DELAY)
        r = requests.get(url, headers=_headers(email), timeout=45)
        r.raise_for_status()
        full_text = r.text

        # Extract just the 10-K document from the multi-document submission
        docs = re.findall(r'<DOCUMENT>(.*?)</DOCUMENT>', full_text, re.DOTALL)
        for doc in docs:
            type_match = re.search(r'<TYPE>(.*?)\n', doc)
            if type_match and type_match.group(1).strip() == "10-K":
                logger.info("Extracted main 10-K document (%d chars)", len(doc))
                return doc

        # Fallback: return full text if we can't split it
        logger.warning("Could not isolate main doc; using full submission (%d chars)", len(full_text))
        return full_text

    except Exception:
        logger.exception("10-K download failed for accession %s", accession)
        return None
