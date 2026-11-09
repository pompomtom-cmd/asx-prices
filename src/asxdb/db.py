"""Opening the database and keeping its schema up to date."""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path

DEFAULT_DB_PATH = Path("data/asx.sqlite")

# Each entry upgrades the schema by one version. Never edit a released
# entry; add a new one instead.
MIGRATIONS = [
    "schema.sql",                           # v1
    "migrations/002_listing_snapshots.sql",  # v2
]
SCHEMA_VERSION = len(MIGRATIONS)


def connect(path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open (creating if needed) the database and bring the schema up to date."""
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Apply any migrations newer than the database's user_version."""
    (version,) = conn.execute("PRAGMA user_version").fetchone()
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema v{version} is newer than this code (v{SCHEMA_VERSION})."
        )
    pkg = resources.files("asxdb")
    for target in range(version + 1, SCHEMA_VERSION + 1):
        sql = pkg.joinpath(MIGRATIONS[target - 1]).read_text()
        with conn:
            conn.executescript(sql)
            conn.execute(f"PRAGMA user_version = {target}")
