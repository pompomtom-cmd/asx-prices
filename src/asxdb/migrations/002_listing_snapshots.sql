-- Schema version 2: raw copies of each ASX listed-companies file we load.
-- Lets us see what was listed on any snapshot date and when a code was
-- last seen, which is how delistings are dated.

CREATE TABLE IF NOT EXISTS listing_snapshots (
    as_of           TEXT    NOT NULL,   -- date printed on the file
    ticker          TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    gics_industry   TEXT,
    PRIMARY KEY (as_of, ticker)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_listing_snapshots_ticker
    ON listing_snapshots (ticker, as_of);
