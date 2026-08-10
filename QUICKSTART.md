# Brandy — Quick Start

Getting from clone to a working first run.

---

## 1. Clone and enter the repo

```bash
git clone https://github.com/<your-org>/brandy.git
cd brandy
```

---

## 2. Create a virtual environment and install dependencies

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

NLTK data (used for sentiment analysis) is downloaded automatically on first run. If it fails, run:
```bash
python3 -c "import nltk; nltk.download('vader_lexicon')"
```

---

## 3. Set up environment variables

```bash
cp .env.example .env
```

Open `.env` and fill in:

```ini
SCRAPECREATORS_API_KEY=your_key_here   # Required only for live social scraping
OPENAI_API_KEY=your_key_here           # Optional — enables LLM social commentary
```

If you only plan to use local data files and Ollama, both keys can be left as placeholders.

---

## 4. Configure SEC EDGAR access

Open `config/settings.yaml` and set your email address. The SEC requires a valid email in the User-Agent header:

```yaml
sec:
  email: "yourname@example.com"
```

---

## 5. Install and start Ollama

Ollama is required for the narrative stage. Download from [ollama.com](https://ollama.com), then:

```bash
ollama serve                      # Start the Ollama server (keep this running)
ollama pull deepseek-r1:8b        # Pull the default model (~5GB download)
```

The narrative stage is optional — pass `--skip-narrative` to any workflow command to skip it.

---

## 6. Add social data files (local_file provider)

The pipeline reads social post data from `data/raw/`. Files must follow this naming convention:

```
{BrandName}{Platform}PostData.json
{BrandName}{Platform}PostData.csv
```

| Platform token | Matches |
|---|---|
| `Instagram` | Instagram posts |
| `FaceBook` | Facebook posts |
| `LinkedIn` | LinkedIn posts |

Examples:
```
data/raw/NikeInstagramPostData.json
data/raw/NikeFaceBookPostData.csv
data/raw/NikeLinkedInPostData.json
```

The brand name in the filename must match exactly what you pass to the CLI (case-sensitive).

> If you prefer to fetch live data, skip this step and use `--provider scrapecreators` instead.

---

## 7. Run your first analysis

**Full pipeline (financial + social):**
```bash
python run_workflow.py "Starbucks" --ticker SBUX
```

**Social only (no SEC filing):**
```bash
python run_workflow.py "Rolex" --mode social
```

**Financial only:**
```bash
python run_workflow.py "Apple" --ticker AAPL --mode financial
```

**Skip narrative (faster, no Ollama needed):**
```bash
python run_workflow.py "Starbucks" --ticker SBUX --skip-narrative
```

---

## 8. Launch the Streamlit UI (optional)

```bash
streamlit run app.py
```

Open [http://localhost:8501](http://localhost:8501) in your browser.

---

## Output locations

| File | Location |
|---|---|
| Brand snapshot JSON | `output/snapshots/{Brand}_{date}.json` |
| HTML report | `output/reports/{Brand}_{date}.html` |
| Social Excel | `output/social_analysis_{timestamp}.xlsx` |
| Financial Excel | `output/financial_analysis_{timestamp}.xlsx` |
| Database | `brandy.db` (created automatically, stays local) |

---

## Troubleshooting

**"Cannot connect to Ollama"** — Run `ollama serve` in a separate terminal.

**"No data found for [Brand]"** — Check that your file in `data/raw/` matches the naming convention exactly, including platform and `PostData` suffix.

**SEC rate limit errors** — The pipeline respects a 0.4s delay between requests. If you see HTTP 429 errors, increase `sec.rate_limit_delay` in `config/settings.yaml`.

**Social section empty in report** — The brand name passed to `run_social.py` must match the filename prefix exactly. Use `Brand:handle` syntax to override the social handle:
```bash
python run_social.py "McDonald's:mcdonalds" --provider scrapecreators
```
