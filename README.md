# asxdb

A local SQLite database of ASX end-of-day prices and corporate actions, for
backtesting trading strategies. It covers all listed securities and keeps
delisted ones, so backtests aren't skewed by survivorship bias. It uses free
data sources only.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
asxdb init          # creates data/asx.sqlite
asxdb info          # row counts
pytest
```

## How the data is modelled

- **Raw prices only.** `daily_prices` holds prices exactly as traded. Adjusted
  series are calculated from `corporate_actions` when you ask for them
  (`asxdb.adjusted_prices`), so a corrected dividend or split fixes all history
  at once.
- **Stable ids, dated tickers.** Each security has an internal `security_id`.
  ASX codes are in `ticker_history` with `valid_from` and `valid_to`, because
  codes change and get reused by different companies.
- **Nothing is deleted.** Delisting sets `securities.delisted_on`. Use
  `asxdb.securities.universe(conn, date)` to get exactly what was trading on a
  given day.
- **Adjustment.** Splits and consolidations scale prices by
  `ratio_old / ratio_new`. Cash payouts scale by `(C - cash) / C` using the
  prior close, which gives a total-return series. Pass `include_cash=False`
  for price-only.

## Roadmap

1. Repo and schema (this release)
2. Security master: daily ASX listed-companies snapshot, recording new
   listings, delistings and code changes
3. Backfill from free sources plus a daily update job with data-quality checks
4. A backtest-friendly query API
