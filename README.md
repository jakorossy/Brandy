# Brandy — Brand Equity Intelligence Pipeline

Brandy combines SEC financial filings, social media engagement data, and local LLM narrative generation into a single brand equity report. It runs entirely on your machine — no cloud model calls required for the core pipeline.

---

## What it does

| Stage | Script | Output |
|---|---|---|
| Financial | `run_financial.py` | SEC 10-K metrics → SQLite + Excel |
| Social | `run_social.py` | Post/engagement data → SQLite + Excel |
| Analysis | `run_analysis.py` | Merged brand snapshot → JSON |
| Narrative | `run_narrative.py` | LLM narrative via Ollama → snapshot |
| Report | `run_report.py` | HTML brand report |
| All-in-one | `run_workflow.py` | Orchestrates all stages |
| UI | `app.py` | Streamlit interface for the full pipeline |
| Industry data | `run_industry_refresh.py` | Damodaran industry averages → `config/industry_data.yaml` |

---

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com) running locally with a model pulled (default: `deepseek-r1:8b`)
- A SEC EDGAR user-agent email in `config/settings.yaml`
- API keys in `.env` (see `.env.example`)

Market data for WACC needs no API key: market cap and beta come from Yahoo
Finance via `yfinance`, and the risk-free rate from FRED's public CSV endpoint.
Both degrade gracefully — if either is unreachable the pipeline falls back to
the values in `config/financial_config.yaml` and records that it did so.

### Refreshing industry data

Industry-average WACC fallbacks come from Aswath Damodaran's free NYU Stern
datasets, which he updates each January:

```bash
python run_industry_refresh.py              # rewrite config/industry_data.yaml
python run_industry_refresh.py --dry-run    # preview without writing
```

The pipeline reads the committed YAML and never downloads at run time, so a
valuation doesn't depend on an external site being reachable and the exact
inputs behind any result stay pinned in version control.

---

## Quick setup

See **[QUICKSTART.md](QUICKSTART.md)** for step-by-step instructions from clone to first run.

---

## Running the pipeline

```bash
# Full pipeline — both arms
python run_workflow.py "Starbucks" --ticker SBUX

# Social data only (no SEC filing needed)
python run_workflow.py "Rolex" --mode social

# Financial data only
python run_workflow.py "Apple" --ticker AAPL --mode financial

# Skip the Ollama narrative step
python run_workflow.py "McDonald's" --ticker MCD --skip-narrative

# Streamlit UI
streamlit run app.py
```

---

## Social data input

The social arm reads from local files in `data/raw/`. The expected filename format is:

```
{BrandName}{Platform}PostData.json   (preferred)
{BrandName}{Platform}PostData.csv
```

Examples:
```
data/raw/StarbucksInstagramPostData.json
data/raw/StarbucksFaceBookPostData.csv
data/raw/StarbucksLinkedInPostData.json
```

Supported platforms: `Instagram`, `FaceBook`, `LinkedIn`

Alternatively, use the ScrapeCreators live provider:
```bash
python run_social.py "Starbucks:starbucks" --provider scrapecreators
```

---

## Project structure

```
brandy/                  Core pipeline library
  analysis/              Snapshot builder (merges financial + social)
  db/                    SQLite schema and engine
  export/                Excel workbook builder
  financial/             SEC EDGAR client, XBRL extractor, metrics
  llm/                   Ollama narrative generation, OpenAI commentary
  report/                Jinja2 HTML report builder + formatters
  social/                Social ingest, sentiment, providers, storage

run_*.py                 CLI entry points for each pipeline stage
run_workflow.py          Master orchestrator
app.py                   Streamlit UI

config/
  settings.yaml          SEC email, LLM model, DB path, output dir
  social_config.yaml     Provider, platforms, sentiment thresholds
  financial_config.yaml  WACC fallbacks, lease treatment, XBRL preferences
  industry_data.yaml     Damodaran industry averages (refresh annually)

data/raw/                Input social data files (not versioned — see QUICKSTART.md)
output/
  snapshots/             Brand snapshot JSON files
  reports/               Generated HTML reports
```

---

## Configuration

Copy and edit the config files before first run:

```bash
cp .env.example .env          # Add your API keys
# Edit config/settings.yaml   # Add your SEC EDGAR email
```

The database (`brandy.db`) is created automatically on first run.

---

## Modes

| Mode | What runs | Requires |
|---|---|---|
| `both` (default) | Financial + social + analysis + narrative + report | Brand + ticker |
| `financial` | Financial only → Excel | Ticker |
| `social` | Social + analysis + narrative + report | Brand |
