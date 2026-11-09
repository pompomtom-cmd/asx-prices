"""Command-line entry point: `asxdb init`, `asxdb info`."""

from __future__ import annotations

import argparse

from .db import DEFAULT_DB_PATH, connect


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="asxdb", description="ASX price history database")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="path to the SQLite file")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="create the database or upgrade its schema")
    sub.add_parser("info", help="show row counts")
    args = parser.parse_args(argv)

    conn = connect(args.db)
    if args.command == "init":
        print(f"Database ready at {args.db}")
    elif args.command == "info":
        for table in ("securities", "ticker_history", "daily_prices",
                      "corporate_actions", "ingest_runs"):
            (n,) = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            print(f"{table:20} {n:>12,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
