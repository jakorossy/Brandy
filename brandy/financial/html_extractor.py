"""
HTML table extractor — FALLBACK path for when XBRL data is incomplete.

Reused directly from the original financial_analyzer.py extract_financial_tables().
This parses the raw 10-K HTML with BeautifulSoup to identify the three
major financial statement tables.
"""

import re
import logging

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def extract_financial_tables(doc_text: str, max_chars: int = 30000) -> str:
    """
    Parse 10-K HTML and extract Income Statement, Balance Sheet,
    and Cash Flow Statement tables.

    Returns a text block of the identified tables, or truncated plain text
    of the full document as a last resort.

    Reused from financial_analyzer.py L124-177.
    """
    soup = BeautifulSoup(doc_text, "html.parser")
    tables = soup.find_all("table")

    income_stmt = None
    balance_sheet = None
    cash_flow = None

    for table in tables:
        txt = table.get_text(separator=" | ", strip=True)
        txt_lower = txt.lower()

        # Income statement: has net sales/revenue AND cost of sales/revenue
        if income_stmt is None:
            if (
                ("net sales" in txt_lower or "total net revenue" in txt_lower or "revenues" in txt_lower)
                and ("cost of sales" in txt_lower or "cost of revenue" in txt_lower or "gross margin" in txt_lower)
                and re.search(r'\b\d{4,}\b', txt)
            ):
                income_stmt = txt

        # Balance sheet: has total assets AND total liabilities
        if balance_sheet is None:
            if (
                "total assets" in txt_lower
                and ("total liabilities" in txt_lower or "shareholders" in txt_lower)
                and re.search(r'\b\d{4,}\b', txt)
            ):
                balance_sheet = txt

        # Cash flow: has operating activities AND investing activities
        if cash_flow is None:
            if (
                "operating activities" in txt_lower
                and "investing activities" in txt_lower
                and re.search(r'\b\d{4,}\b', txt)
            ):
                cash_flow = txt

    sections = []
    if income_stmt:
        sections.append("=== INCOME STATEMENT ===\n" + income_stmt)
    if balance_sheet:
        sections.append("=== BALANCE SHEET ===\n" + balance_sheet)
    if cash_flow:
        sections.append("=== CASH FLOW STATEMENT ===\n" + cash_flow)

    if not sections:
        logger.warning("Could not isolate financial tables; using full document text")
        return soup.get_text(separator="\n", strip=True)[:max_chars]

    result = "\n\n".join(sections)
    logger.info("Extracted %d financial statement(s) (%d chars)", len(sections), len(result))
    return result[:max_chars]
