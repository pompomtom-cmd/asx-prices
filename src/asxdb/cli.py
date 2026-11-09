"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from .db import DEFAULT_DB_PATH, connect
from .listings import (
    LISTED_COMPANIES_URL,
    fetch_listed_companies,
    import_delisted,
    parse_listed_companies,
    sync_listings,
)

ARCHIVE_DIR = Path("data/raw/listed_companies")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="asxdb", description="ASX price history database")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="path to the SQLite file")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the database or upgrade its schema")
    sub.add_parser("info", help="show row counts")

    p = sub.add_parser("sync-listings", help="load today's ASX listed-companies file")
    p.add_argument("--file", help="load a saved CSV instead of downloading")
    p.add_argument("--url", default=LISTED_COMPANIES_URL)
    p.add_argument("--as-of", help="snapshot date (YYYY-MM-DD) if the file doesn't print one")
    p.add_argument("--force", action="store_true",
                   help="apply even if the file would delist an unusual number of codes")

    p = sub.add_parser("import-delisted", help="load historical delistings from a CSV")
    p.add_argument("file", help="CSV with ticker,name,delisted_on[,listed_on,reason,isin]")
    p.add_argument("--source", default="manual")

    args = parser.parse_args(argv)
    conn = connect(args.db)

    if args.command == "init":
        print(f"Database ready at {args.db}")

    elif args.command == "info":
        for table in ("securities", "ticker_history", "daily_prices",
                      "corporate_actions", "listing_snapshots", "ingest_runs"):
            (n,) = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            print(f"{table:20} {n:>12,}")
        (listed,) = conn.execute(
            "SELECT COUNT(*) FROM ticker_history WHERE valid_to IS NULL").fetchone()
        (gone,) = conn.execute(
            "SELECT COUNT(*) FROM securities WHERE delisted_on IS NOT NULL").fetchone()
        print(f"{'listed now':20} {listed:>12,}\n{'delisted':20} {gone:>12,}")

    elif args.command == "sync-listings":
        text = Path(args.file).read_text(encoding="utf-8-sig") if args.file \
            else fetch_listed_companies(args.url)
        printed, listings = parse_listed_companies(text)
        as_of = args.as_of or printed or (None if args.file else date.today().isoformat())
        if as_of is None:
            print("The file has no date; pass --as-of YYYY-MM-DD.", file=sys.stderr)
            return 2
        if not args.file:  # keep the raw download so history can be rebuilt
            ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            (ARCHIVE_DIR / f"{as_of}.csv").write_text(text, encoding="utf-8")
        r = sync_listings(conn, as_of, listings, force=args.force)
        print(f"{as_of}: {len(listings)} codes; {len(r.listed)} new, {len(r.delisted)} delisted, "
              f"{len(r.code_changes)} code changes, {len(r.renamed)} renamed")
        for old, new in r.code_changes:
            print(f"  code change {old} -> {new}")
        for t in r.delisted:
            print(f"  delisted {t}")

    elif args.command == "import-delisted":
        n = import_delisted(conn, Path(args.file).read_text(encoding="utf-8-sig"), args.source)
        print(f"Added {n} delisted securities")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
