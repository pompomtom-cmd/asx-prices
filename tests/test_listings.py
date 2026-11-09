import pytest

from asxdb import connect
from asxdb.cli import main
from asxdb.listings import Listing, import_delisted, parse_listed_companies, sync_listings
from asxdb.securities import resolve_ticker, universe

SAMPLE = '''ASX listed companies as at Mon Nov 09 18:00:01 AEDT 2026

Company name,ASX code,GICS industry group
"BHP GROUP LIMITED",BHP,Materials
"COMMONWEALTH BANK OF AUSTRALIA.",CBA,Banks
"ACME MINING LTD",AMX,Materials
"NOT APPLIC CO",NAP,Not Applic
'''


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()


def L(ticker, name, gics="Materials"):
    return Listing(ticker, name, gics)


def test_parse_sample():
    as_of, rows = parse_listed_companies(SAMPLE)
    assert as_of == "2026-11-09"
    assert [r.ticker for r in rows] == ["BHP", "CBA", "AMX", "NAP"]
    assert rows[1].name == "COMMONWEALTH BANK OF AUSTRALIA."
    assert rows[1].gics_industry == "Banks"


def test_parse_rejects_wrong_file():
    with pytest.raises(ValueError):
        parse_listed_companies("<html>Not found</html>")


def test_first_load_has_unknown_listing_dates(conn):
    _, rows = parse_listed_companies(SAMPLE)
    r = sync_listings(conn, "2026-11-09", rows)
    assert len(r.listed) == 4 and not r.delisted
    assert conn.execute("SELECT COUNT(*) FROM securities WHERE listed_on IS NULL").fetchone()[0] == 4
    # Loading the same day again is a no-op.
    assert sync_listings(conn, "2026-11-09", rows).listed == []


def test_listing_delisting_and_code_change(conn):
    base = [L(f"C{i:02}", f"Company {i} Ltd") for i in range(40)]
    sync_listings(conn, "2026-01-05", base + [L("OLD", "Renamed Code Ltd"), L("BYE", "Gone Ltd")])
    bye_id = resolve_ticker(conn, "BYE", "2026-01-05")
    old_id = resolve_ticker(conn, "OLD", "2026-01-05")

    r = sync_listings(conn, "2026-01-06",
                      base + [L("NEW", "Renamed Code Limited"), L("IPO", "Fresh Float Ltd")])

    assert r.code_changes == [("OLD", "NEW")]
    assert r.delisted == ["BYE"]
    assert r.listed == ["IPO"]
    # The code change keeps the same security, so its price history carries on.
    assert resolve_ticker(conn, "NEW", "2026-01-06") == old_id
    assert resolve_ticker(conn, "OLD", "2026-01-05") == old_id
    # Delisted on the last day it was seen; still in the universe for that day.
    row = conn.execute("SELECT delisted_on FROM securities WHERE security_id = ?", (bye_id,)).fetchone()
    assert row[0] == "2026-01-05"
    assert (bye_id, "BYE") in universe(conn, "2026-01-05")
    assert all(t != "BYE" for _, t in universe(conn, "2026-01-06"))
    ipo = conn.execute("SELECT listed_on FROM securities WHERE name = 'Fresh Float Ltd'").fetchone()
    assert ipo[0] == "2026-01-06"

    runs = conn.execute("SELECT status FROM ingest_runs ORDER BY run_id").fetchall()
    assert [x[0] for x in runs] == ["ok", "ok"]


def test_renames_update_in_place(conn):
    sync_listings(conn, "2026-01-05", [L("AAA", "Alpha Ltd")])
    r = sync_listings(conn, "2026-01-06", [L("AAA", "Alpha Resources Ltd", "Energy")])
    assert r.renamed == ["AAA"]
    row = conn.execute("SELECT name, gics_industry FROM securities").fetchone()
    assert tuple(row) == ("Alpha Resources Ltd", "Energy")


def test_refuses_truncated_file(conn):
    full = [L(f"C{i:03}", f"Company {i}") for i in range(100)]
    sync_listings(conn, "2026-01-05", full)
    with pytest.raises(ValueError, match="incomplete"):
        sync_listings(conn, "2026-01-06", full[:50])
    # Nothing changed, and force applies it.
    assert len(universe(conn, "2026-01-06")) == 100
    r = sync_listings(conn, "2026-01-06", full[:50], force=True)
    assert len(r.delisted) == 50


def test_refuses_older_snapshot(conn):
    sync_listings(conn, "2026-01-06", [L("AAA", "Alpha")])
    with pytest.raises(ValueError, match="older"):
        sync_listings(conn, "2026-01-05", [L("AAA", "Alpha")])


def test_import_delisted(conn):
    csv_text = (
        "ticker,name,listed_on,delisted_on,reason\n"
        "ABC,Old ABC Ltd,1999-03-01,2008-10-15,administration\n"
        "XYZ,Takeover Target Ltd,,2015-06-30,takeover\n"
    )
    assert import_delisted(conn, csv_text) == 2
    assert import_delisted(conn, csv_text) == 0  # idempotent
    abc = resolve_ticker(conn, "ABC", "2005-01-01")
    assert abc is not None
    assert resolve_ticker(conn, "ABC", "2008-10-16") is None
    # A later company can reuse the code.
    sync_listings(conn, "2026-01-05", [L("ABC", "New ABC Ltd")])
    assert resolve_ticker(conn, "ABC", "2026-01-05") != abc


def test_cli_sync_from_file(tmp_path, capsys):
    f = tmp_path / "listed.csv"
    f.write_text(SAMPLE)
    db = tmp_path / "asx.sqlite"
    assert main(["--db", str(db), "sync-listings", "--file", str(f)]) == 0
    assert "4 new" in capsys.readouterr().out
    assert main(["--db", str(db), "info"]) == 0
    assert "listed now" in capsys.readouterr().out
