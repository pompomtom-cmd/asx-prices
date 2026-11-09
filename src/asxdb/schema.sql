-- ASX price history database, schema version 1.
--
-- Design rules:
--   * Prices are stored RAW (as traded, unadjusted). Adjusted series are
--     derived from corporate_actions at query time, never stored as fact.
--   * A security has a stable internal id. ASX codes can change or be
--     reused by a different company, so codes live in ticker_history with
--     validity dates.
--   * Delisted securities are kept forever (delisted_on is set, nothing is
--     deleted) so backtests are free of survivorship bias.
--   * Dates are ISO-8601 text (YYYY-MM-DD).

CREATE TABLE IF NOT EXISTS securities (
    security_id     INTEGER PRIMARY KEY,
    name            TEXT    NOT NULL,
    isin            TEXT    UNIQUE,
    security_type   TEXT    NOT NULL DEFAULT 'ordinary'
                    CHECK (security_type IN ('ordinary', 'etf', 'reit', 'lic',
                                             'preference', 'hybrid', 'option',
                                             'other')),
    gics_industry   TEXT,
    listed_on       TEXT,               -- first trading day, if known
    delisted_on     TEXT,               -- last trading day; NULL while listed
    delisting_reason TEXT,              -- e.g. 'takeover', 'administration'
    notes           TEXT,
    CHECK (delisted_on IS NULL OR listed_on IS NULL OR delisted_on >= listed_on)
);

-- Which ASX code a security traded under, and when.
-- valid_to is the last day the code applied (inclusive); NULL means current.
CREATE TABLE IF NOT EXISTS ticker_history (
    security_id     INTEGER NOT NULL REFERENCES securities(security_id),
    ticker          TEXT    NOT NULL,
    valid_from      TEXT    NOT NULL,
    valid_to        TEXT,
    PRIMARY KEY (security_id, valid_from),
    CHECK (valid_to IS NULL OR valid_to >= valid_from)
);
CREATE INDEX IF NOT EXISTS ix_ticker_history_ticker
    ON ticker_history (ticker, valid_from);

-- Raw end-of-day prices, exactly as traded on the day.
CREATE TABLE IF NOT EXISTS daily_prices (
    security_id     INTEGER NOT NULL REFERENCES securities(security_id),
    date            TEXT    NOT NULL,
    open            REAL,
    high            REAL,
    low             REAL,
    close           REAL    NOT NULL CHECK (close > 0),
    volume          INTEGER CHECK (volume >= 0),
    source          TEXT    NOT NULL,
    fetched_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (security_id, date)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_daily_prices_date ON daily_prices (date);

-- Corporate actions, keyed on ex-date.
--   dividend / capital_return / special_dividend: cash_amount per share (AUD).
--   split / consolidation / bonus: ratio_old -> ratio_new shares
--     (a 1-for-2 split is ratio_old=1, ratio_new=2; a 10-to-1 consolidation
--      is ratio_old=10, ratio_new=1).
--   rights / spinoff / other: stored for reference; use adj_factor_override
--     to give the price adjustment when it is known.
CREATE TABLE IF NOT EXISTS corporate_actions (
    action_id       INTEGER PRIMARY KEY,
    security_id     INTEGER NOT NULL REFERENCES securities(security_id),
    ex_date         TEXT    NOT NULL,
    action_type     TEXT    NOT NULL
                    CHECK (action_type IN ('dividend', 'special_dividend',
                                           'capital_return', 'split',
                                           'consolidation', 'bonus', 'rights',
                                           'spinoff', 'other')),
    cash_amount     REAL    CHECK (cash_amount IS NULL OR cash_amount >= 0),
    franking_pct    REAL    CHECK (franking_pct IS NULL OR franking_pct BETWEEN 0 AND 100),
    ratio_old       REAL    CHECK (ratio_old IS NULL OR ratio_old > 0),
    ratio_new       REAL    CHECK (ratio_new IS NULL OR ratio_new > 0),
    adj_factor_override REAL CHECK (adj_factor_override IS NULL
                                    OR adj_factor_override > 0),
    pay_date        TEXT,
    source          TEXT    NOT NULL,
    notes           TEXT,
    UNIQUE (security_id, ex_date, action_type)
);
CREATE INDEX IF NOT EXISTS ix_corporate_actions_ex_date
    ON corporate_actions (ex_date);

-- One row per ingestion run, for auditing and spotting failed updates.
CREATE TABLE IF NOT EXISTS ingest_runs (
    run_id          INTEGER PRIMARY KEY,
    job             TEXT    NOT NULL,       -- e.g. 'listed_companies', 'prices'
    source          TEXT    NOT NULL,
    started_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    finished_at     TEXT,
    status          TEXT    NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'ok', 'failed')),
    rows_written    INTEGER,
    message         TEXT
);
