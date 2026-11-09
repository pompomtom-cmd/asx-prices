"""Security master updates from the ASX listed-companies file.

The ASX publishes a CSV of every listed company each trading day. Loading it
regularly and comparing it with the previous load tells us:

  * new codes            -> new listings (first seen on this snapshot)
  * codes that vanished  -> delistings, dated to the last snapshot they were on,
                            unless a vanished and a new code share a company
                            name, which is treated as a code change
  * name / GICS changes  -> updated in place

Listings are only known from the day you start loading snapshots. Older
delistings come from `import_delisted` (a CSV you supply).
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
import urllib.request
from dataclasses import dataclass
from datetime import datetime

from .securities import add_security, change_ticker, delist

LISTED_COMPANIES_URL = "https://www.asx.com.au/asx/research/ASXListedCompanies.csv"

# Refuse a snapshot that would delist more than this share of current codes:
# a truncated or changed file shouldn't wipe out the security master.
MAX_DELIST_FRACTION = 0.05


@dataclass(frozen=True)
class Listing:
    ticker: str
    name: str
    gics_industry: str | None


@dataclass
class SyncResult:
    as_of: str
    listed: list[str]
    delisted: list[str]
    code_changes: list[tuple[str, str]]
    renamed: list[str]


def fetch_listed_companies(url: str = LISTED_COMPANIES_URL, timeout: int = 60) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "asxdb/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8-sig", errors="replace")


_DATE_FORMATS = ("%a %b %d %H:%M:%S %Z %Y", "%a %b %d %Y", "%d %B %Y", "%d/%m/%Y", "%Y-%m-%d")


def _parse_as_of(line: str) -> str | None:
    """Pull the date out of a title line like 'ASX listed companies as at Mon Nov 09 ...'."""
    m = re.search(r"as at\s+(.+)", line, re.IGNORECASE)
    if not m:
        return None
    text = m.group(1).strip().strip('"').strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    # Fall back to dropping a time and timezone, e.g. 'Mon Nov 09 18:00:01 AEDT 2026'.
    parts = text.split()
    if len(parts) >= 4:
        try:
            return datetime.strptime(" ".join(parts[:3] + parts[-1:]), "%a %b %d %Y").date().isoformat()
        except ValueError:
            pass
    return None


def parse_listed_companies(text: str) -> tuple[str | None, list[Listing]]:
    """Parse the ASX CSV. Returns (as_of date if printed, listings)."""
    lines = text.splitlines()
    as_of = None
    header_idx = None
    for i, line in enumerate(lines[:20]):
        if as_of is None:
            as_of = _parse_as_of(line)
        if "asx code" in line.lower():
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("no header row containing 'ASX code' found")

    reader = csv.DictReader(io.StringIO("\n".join(lines[header_idx:])))
    cols = {c.strip().lower(): c for c in reader.fieldnames or []}
    code_col = cols.get("asx code")
    name_col = cols.get("company name")
    gics_col = cols.get("gics industry group")
    if not code_col or not name_col:
        raise ValueError(f"unexpected columns: {reader.fieldnames}")

    listings: dict[str, Listing] = {}
    for row in reader:
        code = (row.get(code_col) or "").strip().upper()
        name = (row.get(name_col) or "").strip()
        if not code or not name:
            continue
        gics = (row.get(gics_col) or "").strip() if gics_col else ""
        listings[code] = Listing(code, name, gics or None)
    return as_of, list(listings.values())


def _norm(name: str) -> str:
    name = name.upper()
    name = re.sub(r"\b(LIMITED|LTD|HOLDINGS|GROUP|CORPORATION|CORP|INC|PLC|NL|TRUST)\b", "", name)
    return re.sub(r"[^A-Z0-9]", "", name)


def sync_listings(
    conn: sqlite3.Connection,
    as_of: str,
    listings: list[Listing],
    *,
    source: str = "asx_listed_companies",
    force: bool = False,
) -> SyncResult:
    """Apply one listed-companies snapshot to the security master."""
    latest = conn.execute("SELECT MAX(as_of) FROM listing_snapshots").fetchone()[0]
    if latest is not None and as_of < latest:
        raise ValueError(f"snapshot {as_of} is older than the latest loaded ({latest})")
    if latest == as_of and not force:
        return SyncResult(as_of, [], [], [], [])  # already loaded

    current = {
        r["ticker"]: r
        for r in conn.execute(
            """SELECT th.ticker, th.security_id, s.name, s.gics_industry
               FROM ticker_history th JOIN securities s USING (security_id)
               WHERE th.valid_to IS NULL"""
        )
    }
    incoming = {l.ticker: l for l in listings}

    gone = sorted(set(current) - set(incoming))
    new = sorted(set(incoming) - set(current))

    if current and not force and len(gone) > MAX_DELIST_FRACTION * len(current):
        raise ValueError(
            f"snapshot {as_of} would delist {len(gone)} of {len(current)} codes; "
            "the file is probably incomplete. Re-run with force=True if it is right."
        )

    # Pair a vanished code with a new one when the company name matches.
    new_by_name: dict[str, list[str]] = {}
    for t in new:
        new_by_name.setdefault(_norm(incoming[t].name), []).append(t)
    code_changes = []
    for old in gone:
        candidates = new_by_name.get(_norm(current[old]["name"]))
        if candidates and len(candidates) == 1:
            code_changes.append((old, candidates.pop()))

    result = SyncResult(as_of, [], [], [], [])
    run_id = conn.execute(
        "INSERT INTO ingest_runs (job, source) VALUES ('listings', ?)", (source,)
    ).lastrowid
    conn.commit()
    try:
        changed_old = {o for o, _ in code_changes}
        changed_new = {n for _, n in code_changes}

        for old, new_code in code_changes:
            change_ticker(conn, current[old]["security_id"], new_code, as_of, commit=False)
            result.code_changes.append((old, new_code))

        last_seen = latest  # the vanished codes were on the previous snapshot
        for t in gone:
            if t in changed_old:
                continue
            delist(conn, current[t]["security_id"], last_seen or as_of, commit=False)
            result.delisted.append(t)

        for t in new:
            if t in changed_new:
                continue
            l = incoming[t]
            # On the very first load, every code is "new" but its listing date is unknown.
            sid = add_security(
                conn, t, l.name, as_of, listed_on=as_of if latest else None, commit=False
            )
            conn.execute(
                "UPDATE securities SET gics_industry = ? WHERE security_id = ?",
                (l.gics_industry, sid),
            )
            result.listed.append(t)

        for t, l in incoming.items():
            if t in current and t not in changed_old:
                row = current[t]
                if row["name"] != l.name or (l.gics_industry and row["gics_industry"] != l.gics_industry):
                    conn.execute(
                        "UPDATE securities SET name = ?, gics_industry = COALESCE(?, gics_industry) "
                        "WHERE security_id = ?",
                        (l.name, l.gics_industry, row["security_id"]),
                    )
                    if row["name"] != l.name:
                        result.renamed.append(t)
            elif t in changed_new:
                old = next(o for o, n in code_changes if n == t)
                conn.execute(
                    "UPDATE securities SET name = ?, gics_industry = COALESCE(?, gics_industry) "
                    "WHERE security_id = ?",
                    (l.name, l.gics_industry, current[old]["security_id"]),
                )

        conn.execute("DELETE FROM listing_snapshots WHERE as_of = ?", (as_of,))
        conn.executemany(
            "INSERT INTO listing_snapshots (as_of, ticker, name, gics_industry) VALUES (?, ?, ?, ?)",
            [(as_of, l.ticker, l.name, l.gics_industry) for l in listings],
        )
        conn.execute(
            """UPDATE ingest_runs SET status = 'ok', finished_at = strftime('%Y-%m-%dT%H:%M:%SZ','now'),
                      rows_written = ?, message = ?
               WHERE run_id = ?""",
            (len(listings),
             f"{as_of}: +{len(result.listed)} -{len(result.delisted)} "
             f"~{len(result.code_changes)} code changes",
             run_id),
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.execute(
            "UPDATE ingest_runs SET status = 'failed', finished_at = strftime('%Y-%m-%dT%H:%M:%SZ','now'), "
            "message = ? WHERE run_id = ?",
            (str(e), run_id),
        )
        conn.commit()
        raise
    return result


def import_delisted(conn: sqlite3.Connection, csv_text: str, source: str = "manual") -> int:
    """Load historical delisted securities from a CSV.

    Columns: ticker, name, delisted_on (required); listed_on, reason, isin (optional).
    Dates as YYYY-MM-DD. Rows already present (same ticker and delisted_on) are skipped.
    Returns the number of securities added.
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    missing = {"ticker", "name", "delisted_on"} - set(reader.fieldnames or [])
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")
    added = 0
    for row in reader:
        ticker = row["ticker"].strip().upper()
        delisted_on = row["delisted_on"].strip()
        datetime.strptime(delisted_on, "%Y-%m-%d")  # validate
        exists = conn.execute(
            "SELECT 1 FROM ticker_history WHERE ticker = ? AND valid_to = ?",
            (ticker, delisted_on),
        ).fetchone()
        if exists:
            continue
        listed_on = (row.get("listed_on") or "").strip() or None
        add_security(
            conn, ticker, row["name"].strip(),
            valid_from=listed_on or "1900-01-01",
            isin=(row.get("isin") or "").strip() or None,
            listed_on=listed_on,
            delisted_on=delisted_on,
            delisting_reason=(row.get("reason") or "").strip() or None,
            commit=False,
        )
        added += 1
    conn.commit()
    return added
