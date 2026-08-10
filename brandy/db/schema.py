"""SQLite schema definitions for the Brandy pipeline."""

CREATE_TABLES = [
    # ── Pipeline tracking ────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS pipeline_runs (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id          TEXT NOT NULL UNIQUE,
        arm             TEXT NOT NULL,
        input_targets   TEXT,
        config_snapshot TEXT,
        status          TEXT DEFAULT 'started',
        output_path     TEXT,
        error_message   TEXT,
        started_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        completed_at    TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS raw_payloads (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id          TEXT REFERENCES pipeline_runs(run_id),
        source          TEXT NOT NULL,
        entity_ref      TEXT,
        payload         TEXT NOT NULL,
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,

    # ── Financial arm ────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS companies (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker          TEXT NOT NULL UNIQUE,
        company_name    TEXT,
        cik             TEXT,
        sic_code        TEXT,
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sec_filings (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id      INTEGER NOT NULL REFERENCES companies(id),
        accession       TEXT NOT NULL,
        filing_date     TEXT NOT NULL,
        fiscal_year     INTEGER,
        fiscal_period   TEXT DEFAULT 'FY',
        form_type       TEXT DEFAULT '10-K',
        UNIQUE(company_id, accession)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS financial_metrics (
        id                        INTEGER PRIMARY KEY AUTOINCREMENT,
        filing_id                 INTEGER NOT NULL REFERENCES sec_filings(id),
        revenue                   REAL,
        cost_of_revenue           REAL,
        gross_profit              REAL,
        operating_expenses        REAL,
        operating_income          REAL,
        net_income                REAL,
        eps_diluted               REAL,
        total_assets              REAL,
        total_liabilities         REAL,
        shareholders_equity       REAL,
        cash_and_equivalents      REAL,
        operating_cash_flow       REAL,
        capital_expenditures      REAL,
        depreciation_amortization REAL,
        tax_expense               REAL,
        pretax_income             REAL,
        sga_expense               REAL,
        short_term_debt           REAL,
        long_term_debt            REAL,
        total_debt                REAL,
        interest_expense          REAL,
        operating_lease_liability_current    REAL,
        operating_lease_liability_noncurrent REAL,
        ebitda                    REAL,
        ebitda_source             TEXT,
        ebitda_notes              TEXT,
        gross_profit_source       TEXT,
        gross_profit_notes        TEXT,
        operating_income_source   TEXT,
        free_cash_flow            REAL,
        gross_margin              REAL,
        operating_margin          REAL,
        net_margin                REAL,
        roe                       REAL,
        debt_to_equity            REAL,
        data_source               TEXT DEFAULT 'xbrl',
        created_at                TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS wacc_inputs (
        id                     INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id             INTEGER NOT NULL REFERENCES companies(id),
        fiscal_year            INTEGER,
        risk_free_rate         REAL,
        equity_risk_premium    REAL,
        beta                   REAL,
        cost_of_equity         REAL,
        pre_tax_cost_of_debt   REAL,
        tax_rate               REAL,
        after_tax_cost_of_debt REAL,
        market_cap             REAL,
        total_debt             REAL,
        weight_equity          REAL,
        weight_debt            REAL,
        wacc                   REAL,
        assumptions_note       TEXT,
        created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS financial_commentary (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        company_id      INTEGER NOT NULL REFERENCES companies(id),
        run_id          TEXT REFERENCES pipeline_runs(run_id),
        commentary      TEXT,
        model_used      TEXT,
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,

    # ── Social arm ───────────────────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS brands (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        brand_name      TEXT NOT NULL UNIQUE,
        industry        TEXT,
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS social_accounts (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        brand_id        INTEGER NOT NULL REFERENCES brands(id),
        platform        TEXT NOT NULL,
        account_handle  TEXT,
        followers       INTEGER,
        following       INTEGER,
        bio             TEXT,
        last_updated    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(brand_id, platform)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS social_posts (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        brand_id         INTEGER NOT NULL REFERENCES brands(id),
        platform         TEXT NOT NULL,
        post_id_external TEXT,
        post_text        TEXT,
        likes            INTEGER DEFAULT 0,
        num_comments     INTEGER DEFAULT 0,
        post_date        TEXT,
        source_file      TEXT,
        created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS social_comments (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id         INTEGER REFERENCES social_posts(id),
        brand_id        INTEGER NOT NULL REFERENCES brands(id),
        platform        TEXT NOT NULL,
        comment_text    TEXT,
        commenter       TEXT,
        comment_like_count INTEGER DEFAULT 0,
        is_spam         INTEGER DEFAULT 0,
        is_irrelevant   INTEGER DEFAULT 0,
        sentiment_score REAL,
        sentiment_label TEXT,
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS social_post_sentiment (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id                 INTEGER NOT NULL REFERENCES social_posts(id),
        total_comments_analyzed INTEGER DEFAULT 0,
        positive_count          INTEGER DEFAULT 0,
        neutral_count           INTEGER DEFAULT 0,
        negative_count          INTEGER DEFAULT 0,
        spam_count              INTEGER DEFAULT 0,
        irrelevant_count        INTEGER DEFAULT 0,
        avg_sentiment           REAL,
        caption_sentiment       REAL,
        sentiment_basis         TEXT DEFAULT 'comments',
        created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS social_commentary (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        brand_id        INTEGER NOT NULL REFERENCES brands(id),
        run_id          TEXT REFERENCES pipeline_runs(run_id),
        commentary      TEXT,
        model_used      TEXT,
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,

    # ── Merged analysis layer ───────────────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS brand_snapshots (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,

        -- Identity
        brand_name              TEXT NOT NULL,
        ticker                  TEXT,
        analysis_date           TEXT NOT NULL,

        -- Lineage
        financial_run_id        TEXT,
        social_run_id           TEXT,
        financial_fiscal_year   INTEGER,
        social_data_as_of       TEXT,
        snapshot_built_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

        -- Financial summary
        filing_date             TEXT,
        revenue                 REAL,
        gross_margin            REAL,
        operating_margin        REAL,
        net_margin              REAL,
        ebitda                  REAL,
        ebitda_source           TEXT,
        free_cash_flow          REAL,
        eps_diluted             REAL,
        roe                     REAL,
        debt_to_equity          REAL,
        wacc                    REAL,

        -- Social summary
        primary_platform        TEXT,
        total_followers         INTEGER,
        total_posts_analyzed    INTEGER,
        total_likes             INTEGER,
        total_comments          INTEGER,
        avg_likes_per_post      REAL,
        avg_comments_per_post   REAL,
        engagement_rate         REAL,

        -- Sentiment
        sentiment_positive_pct  REAL,
        sentiment_neutral_pct   REAL,
        sentiment_negative_pct  REAL,
        avg_sentiment_score     REAL,
        comments_analyzed       INTEGER,
        sentiment_methodology   TEXT,

        -- Arm commentaries (pass-through)
        financial_commentary    TEXT,
        social_commentary       TEXT,

        -- LLM output (null until LangChain layer is built)
        merged_narrative        TEXT,
        narrative_model         TEXT,
        narrative_generated_at  TEXT,

        UNIQUE(brand_name, analysis_date)
    )
    """,
]
