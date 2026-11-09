import sqlite3

import pytest

from asxdb import adjusted_prices, connect
from asxdb.db import SCHEMA_VERSION
from asxdb.securities import add_security, change_ticker, delist, resolve_ticker, universe


@pytest.fixture
def conn():
    c = connect(":memory:")
    yield c
    c.close()


def add_prices(conn, sid, rows):
    with conn:
        conn.executemany(
            """INSERT INTO daily_prices (security_id, date, open, high, low, close, volume, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'test')""",
            [(sid, d, c, c, c, c, v) for d, c, v in rows],
        )


def add_action(conn, sid, ex_date, action_type, **kw):
    cols = ["security_id", "ex_date", "action_type", "source", *kw]
    with conn:
        conn.execute(
            f"INSERT INTO corporate_actions ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            (sid, ex_date, action_type, "test", *kw.values()),
        )


def test_schema_version_and_reopen(tmp_path):
    path = tmp_path / "asx.sqlite"
    connect(path).close()
    c = connect(path)  # second open must not re-run or fail
    assert c.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_rejects_bad_rows(conn):
    sid = add_security(conn, "ABC", "ABC Ltd", "2020-01-01")
    with pytest.raises(sqlite3.IntegrityError):
        add_prices(conn, sid, [("2020-01-02", 0.0, 100)])  # zero close
    with pytest.raises(sqlite3.IntegrityError):
        add_prices(conn, 999, [("2020-01-02", 1.0, 100)])  # unknown security
    with pytest.raises(sqlite3.IntegrityError):
        add_action(conn, sid, "2020-01-02", "merger")  # unknown action type


def test_ticker_change_and_reuse(conn):
    old = add_security(conn, "XYZ", "Old XYZ Ltd", "2010-01-01")
    change_ticker(conn, old, "NEW", "2015-06-01")
    # A different company later takes the freed-up code.
    reuse = add_security(conn, "XYZ", "Unrelated XYZ Corp", "2018-03-01")

    assert resolve_ticker(conn, "XYZ", "2015-05-31") == old
    assert resolve_ticker(conn, "NEW", "2015-06-01") == old
    assert resolve_ticker(conn, "XYZ", "2016-01-01") is None
    assert resolve_ticker(conn, "xyz", "2019-01-01") == reuse


def test_universe_includes_later_delisted(conn):
    alive = add_security(conn, "AAA", "Alive", "2000-01-01")
    dead = add_security(conn, "DDD", "Dead", "2000-01-01")
    delist(conn, dead, "2010-12-31", "administration")

    assert universe(conn, "2005-01-01") == [(alive, "AAA"), (dead, "DDD")]
    assert universe(conn, "2011-01-01") == [(alive, "AAA")]
    row = conn.execute("SELECT delisted_on FROM securities WHERE security_id = ?", (dead,)).fetchone()
    assert row[0] == "2010-12-31"


def test_split_adjustment(conn):
    sid = add_security(conn, "SPL", "Split Co", "2020-01-01")
    add_prices(conn, sid, [("2020-01-02", 10.0, 100), ("2020-01-03", 5.0, 200)])
    add_action(conn, sid, "2020-01-03", "split", ratio_old=1, ratio_new=2)

    bars = adjusted_prices(conn, sid)
    assert [b.close for b in bars] == [5.0, 5.0]
    assert [b.volume for b in bars] == [200, 200]


def test_consolidation_adjustment(conn):
    sid = add_security(conn, "CON", "Consol Co", "2020-01-01")
    add_prices(conn, sid, [("2020-01-02", 0.02, 10_000), ("2020-01-03", 0.20, 1_000)])
    add_action(conn, sid, "2020-01-03", "consolidation", ratio_old=10, ratio_new=1)

    bars = adjusted_prices(conn, sid)
    assert bars[0].close == pytest.approx(0.20)
    assert bars[0].volume == 1_000


def test_dividend_adjustment_total_return_and_price_only(conn):
    sid = add_security(conn, "DIV", "Div Co", "2020-01-01")
    add_prices(conn, sid, [
        ("2020-01-02", 10.0, 100),
        ("2020-01-03", 10.0, 100),
        ("2020-01-06", 9.5, 100),   # goes ex 0.50
        ("2020-01-07", 9.6, 100),
    ])
    add_action(conn, sid, "2020-01-06", "dividend", cash_amount=0.5, franking_pct=100)

    tr = adjusted_prices(conn, sid)
    assert [round(b.close, 6) for b in tr] == [9.5, 9.5, 9.5, 9.6]
    assert tr[0].volume == 100  # dividends don't change share counts

    price_only = adjusted_prices(conn, sid, include_cash=False)
    assert [b.close for b in price_only] == [10.0, 10.0, 9.5, 9.6]


def test_multiple_actions_compound_and_date_range(conn):
    sid = add_security(conn, "MIX", "Mix Co", "2020-01-01")
    add_prices(conn, sid, [
        ("2020-01-02", 20.0, 100),
        ("2020-01-03", 10.0, 200),  # 2-for-1 split ex
        ("2020-01-06", 9.0, 200),   # 1.00 dividend ex
    ])
    add_action(conn, sid, "2020-01-03", "split", ratio_old=1, ratio_new=2)
    add_action(conn, sid, "2020-01-06", "dividend", cash_amount=1.0)

    bars = adjusted_prices(conn, sid)
    assert [round(b.close, 6) for b in bars] == [9.0, 9.0, 9.0]

    # A date range still applies actions that fall after the range.
    (first,) = adjusted_prices(conn, sid, start="2020-01-02", end="2020-01-02")
    assert round(first.close, 6) == 9.0


def test_override_factor(conn):
    sid = add_security(conn, "RTS", "Rights Co", "2020-01-01")
    add_prices(conn, sid, [("2020-01-02", 1.0, 100), ("2020-01-03", 0.9, 100)])
    add_action(conn, sid, "2020-01-03", "rights", adj_factor_override=0.95)
    assert adjusted_prices(conn, sid)[0].close == pytest.approx(0.95)
