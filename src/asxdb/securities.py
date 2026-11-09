"""Security master: adding securities and resolving ASX codes by date."""

from __future__ import annotations

import sqlite3


def add_security(
    conn: sqlite3.Connection,
    ticker: str,
    name: str,
    valid_from: str,
    *,
    isin: str | None = None,
    security_type: str = "ordinary",
    listed_on: str | None = None,
    delisted_on: str | None = None,
    delisting_reason: str | None = None,
) -> int:
    """Insert a security with its first ASX code and return its id."""
    with conn:
        cur = conn.execute(
            """INSERT INTO securities
                   (name, isin, security_type, listed_on, delisted_on, delisting_reason)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, isin, security_type, listed_on, delisted_on, delisting_reason),
        )
        security_id = cur.lastrowid
        conn.execute(
            """INSERT INTO ticker_history (security_id, ticker, valid_from, valid_to)
               VALUES (?, ?, ?, ?)""",
            (security_id, ticker.upper(), valid_from, delisted_on),
        )
    return security_id


def change_ticker(
    conn: sqlite3.Connection, security_id: int, new_ticker: str, effective: str
) -> None:
    """Record a code change: the old code ends the day before `effective`."""
    with conn:
        cur = conn.execute(
            """UPDATE ticker_history
               SET valid_to = date(?, '-1 day')
               WHERE security_id = ? AND valid_to IS NULL""",
            (effective, security_id),
        )
        if cur.rowcount != 1:
            raise ValueError(f"security {security_id} has no current ticker")
        conn.execute(
            "INSERT INTO ticker_history (security_id, ticker, valid_from) VALUES (?, ?, ?)",
            (security_id, new_ticker.upper(), effective),
        )


def delist(
    conn: sqlite3.Connection, security_id: int, last_trading_day: str, reason: str | None = None
) -> None:
    """Mark a security delisted. Nothing is deleted."""
    with conn:
        conn.execute(
            "UPDATE securities SET delisted_on = ?, delisting_reason = ? WHERE security_id = ?",
            (last_trading_day, reason, security_id),
        )
        conn.execute(
            """UPDATE ticker_history SET valid_to = ?
               WHERE security_id = ? AND valid_to IS NULL""",
            (last_trading_day, security_id),
        )


def resolve_ticker(conn: sqlite3.Connection, ticker: str, on_date: str) -> int | None:
    """Return the security that traded as `ticker` on `on_date`, if any."""
    row = conn.execute(
        """SELECT security_id FROM ticker_history
           WHERE ticker = ? AND valid_from <= ?
             AND (valid_to IS NULL OR valid_to >= ?)""",
        (ticker.upper(), on_date, on_date),
    ).fetchone()
    return row[0] if row else None


def universe(conn: sqlite3.Connection, on_date: str) -> list[tuple[int, str]]:
    """All (security_id, ticker) pairs trading on `on_date`, including later-delisted ones."""
    rows = conn.execute(
        """SELECT security_id, ticker FROM ticker_history
           WHERE valid_from <= ? AND (valid_to IS NULL OR valid_to >= ?)
           ORDER BY ticker""",
        (on_date, on_date),
    ).fetchall()
    return [(r[0], r[1]) for r in rows]
