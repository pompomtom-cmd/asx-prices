"""Back-adjusting raw prices for corporate actions.

Each corporate action produces a factor applied to every price *before* its
ex-date. The factor for a date is the product of the factors of all actions
with a later ex-date, so the most recent prices are unchanged and older prices
are scaled to be comparable.

  split / consolidation / bonus:  ratio_old / ratio_new
  cash (dividends, capital returns):  (C - cash) / C,
      where C is the raw close on the last trading day before the ex-date.
  anything with adj_factor_override:  that value.

Adjusting for dividends gives a total-return series (dividends reinvested).
Pass include_cash=False for a price-only series that adjusts for share-count
changes alone.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

SHARE_ACTIONS = {"split", "consolidation", "bonus"}
CASH_ACTIONS = {"dividend", "special_dividend", "capital_return"}


@dataclass(frozen=True)
class Bar:
    date: str
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: int | None


def action_factors(
    conn: sqlite3.Connection, security_id: int, *, include_cash: bool = True
) -> list[tuple[str, float, float]]:
    """Return (ex_date, price_factor, volume_factor) for each action, oldest first."""
    actions = conn.execute(
        """SELECT ex_date, action_type, cash_amount, ratio_old, ratio_new,
                  adj_factor_override
           FROM corporate_actions WHERE security_id = ? ORDER BY ex_date""",
        (security_id,),
    ).fetchall()

    out = []
    for a in actions:
        price_f, vol_f = 1.0, 1.0
        if a["adj_factor_override"] is not None:
            price_f = a["adj_factor_override"]
            if a["action_type"] in SHARE_ACTIONS:
                vol_f = 1.0 / price_f
        elif a["action_type"] in SHARE_ACTIONS:
            if a["ratio_old"] is None or a["ratio_new"] is None:
                raise ValueError(f"{a['action_type']} on {a['ex_date']} has no ratio")
            price_f = a["ratio_old"] / a["ratio_new"]
            vol_f = a["ratio_new"] / a["ratio_old"]
        elif a["action_type"] in CASH_ACTIONS and include_cash and a["cash_amount"]:
            prev = conn.execute(
                """SELECT close FROM daily_prices
                   WHERE security_id = ? AND date < ? ORDER BY date DESC LIMIT 1""",
                (security_id, a["ex_date"]),
            ).fetchone()
            if prev is None:
                continue  # no price history before the ex-date; nothing to adjust
            close = prev["close"]
            if a["cash_amount"] >= close:
                raise ValueError(
                    f"cash {a['cash_amount']} on {a['ex_date']} >= prior close {close}"
                )
            price_f = (close - a["cash_amount"]) / close
        out.append((a["ex_date"], price_f, vol_f))
    return out


def adjusted_prices(
    conn: sqlite3.Connection,
    security_id: int,
    start: str | None = None,
    end: str | None = None,
    *,
    include_cash: bool = True,
) -> list[Bar]:
    """Back-adjusted daily bars for one security between start and end (inclusive)."""
    factors = action_factors(conn, security_id, include_cash=include_cash)
    rows = conn.execute(
        """SELECT date, open, high, low, close, volume FROM daily_prices
           WHERE security_id = ? AND date >= COALESCE(?, '0000-00-00')
             AND date <= COALESCE(?, '9999-12-31')
           ORDER BY date""",
        (security_id, start, end),
    ).fetchall()

    # Walk newest to oldest, multiplying in each factor once its ex-date is passed.
    out: list[Bar] = []
    price_cum, vol_cum = 1.0, 1.0
    i = len(factors) - 1
    for r in reversed(rows):
        while i >= 0 and factors[i][0] > r["date"]:
            price_cum *= factors[i][1]
            vol_cum *= factors[i][2]
            i -= 1

        def scale(x):
            return None if x is None else x * price_cum

        out.append(
            Bar(
                date=r["date"],
                open=scale(r["open"]),
                high=scale(r["high"]),
                low=scale(r["low"]),
                close=r["close"] * price_cum,
                volume=None if r["volume"] is None else round(r["volume"] * vol_cum),
            )
        )
    out.reverse()
    return out
