"""UDiFF cash-market bhavcopy layout, shared by NSE (zip) and BSE (plain CSV): both exchanges publish the same
34-column file. One parser, so the two can never drift apart."""
from __future__ import annotations

from . import _util as u

CASH_COLUMNS = ["TradDt", "FinInstrmId", "ISIN", "TckrSymb", "SctySrs", "FinInstrmNm", "OpnPric", "HghPric", "LwPric",
                "ClsPric", "LastPric", "PrvsClsgPric", "TtlTradgVol", "TtlTrfVal", "TtlNbOfTxsExctd"]


def parse_cash(text: str, label: str, min_rows: int) -> list[dict]:
    """CSV text -> one row per instrument/series. instrument_id is NSE's token or BSE's scrip code."""
    rows = []
    for r in u.csv_rows(text, CASH_COLUMNS, label):
        rows.append({
            "trade_date": u.iso_date(r["TradDt"], f"{label}.TradDt"),
            "symbol": r["TckrSymb"], "series": r["SctySrs"], "isin": u.opt_str(r["ISIN"]), "name": u.opt_str(r["FinInstrmNm"]),
            "instrument_id": r["FinInstrmId"],
            "open": u.num(r["OpnPric"], "open"), "high": u.num(r["HghPric"], "high"), "low": u.num(r["LwPric"], "low"),
            "close": u.num(r["ClsPric"], "close"), "last": u.num(r["LastPric"], "last"),
            "prev_close": u.num(r["PrvsClsgPric"], "prev_close"),
            "volume": u.integer(r["TtlTradgVol"], "volume"), "turnover": u.num(r["TtlTrfVal"], "turnover"),
            "trades": u.integer(r["TtlNbOfTxsExctd"], "trades"),
        })
    u.require_rows(rows, label, min_rows)
    u.single_date(rows, "trade_date", label)
    u.check_ohlc(rows, label)
    return rows
