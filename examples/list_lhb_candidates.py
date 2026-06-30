from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


EASTMONEY_LHB_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Longhu Bang candidate symbols.")
    parser.add_argument("--start", required=True, help="Start date, e.g. 20250613")
    parser.add_argument("--end", required=True, help="End date, e.g. 20250926")
    parser.add_argument("--top", type=int, default=100, help="Top N symbols by net buy amount")
    parser.add_argument("--output", required=True, help="Output txt path for comma-separated symbols")
    parser.add_argument("--detail-output", help="Optional CSV path for ranked details")
    parser.add_argument("--page-size", type=int, default=500, help="Eastmoney page size")
    args = parser.parse_args()

    if args.top < 1:
        parser.error("--top must be greater than 0")

    top, ranked = build_lhb_candidates(args.start, args.end, args.top, page_size=args.page_size)
    if top.empty:
        raise SystemExit("No Longhu Bang rows returned.")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(",".join(top["code"].tolist()), encoding="utf-8")
    if args.detail_output:
        detail = Path(args.detail_output)
        detail.parent.mkdir(parents=True, exist_ok=True)
        top.to_csv(detail, index=False)

    print(f"rows={len(ranked)} unique={ranked['code'].nunique()} top={len(top)}")
    print(f"symbols_txt={output}")
    if args.detail_output:
        print(f"ranked_csv={args.detail_output}")
    print(",".join(top["code"].head(20).tolist()))


def build_lhb_candidates(
    start_date: str,
    end_date: str,
    top: int,
    page_size: int = 500,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch and rank Longhu Bang candidates by total net buy amount."""
    if top < 1:
        raise ValueError("top must be greater than 0")
    frame = fetch_lhb_detail(start_date, end_date, page_size=page_size)
    ranked = rank_lhb_candidates(frame)
    return ranked.head(top).reset_index(drop=True), ranked


def rank_lhb_candidates(frame: pd.DataFrame) -> pd.DataFrame:
    """Rank raw Longhu Bang rows by per-symbol total net buy amount."""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["code", "name", "net_buy", "rank"])
    required = {"code", "name", "net_buy"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Longhu Bang data missing columns: {', '.join(sorted(missing))}")
    ranked = (
        frame.assign(
            code=frame["code"].astype(str).str.zfill(6),
            net_buy=pd.to_numeric(frame["net_buy"], errors="coerce").fillna(0),
        )
        .groupby(["code", "name"], as_index=False)["net_buy"]
        .sum()
        .sort_values(["net_buy", "code"], ascending=[False, True])
        .reset_index(drop=True)
    )
    ranked["rank"] = range(1, len(ranked) + 1)
    return ranked


def fetch_lhb_detail(start_date: str, end_date: str, page_size: int = 500) -> pd.DataFrame:
    start = _date_for_filter(start_date)
    end = _date_for_filter(end_date)
    session = requests.Session()
    session.trust_env = False
    try:
        rows: list[dict[str, object]] = []
        first = _get_page(session, start, end, page=1, page_size=page_size)
        total_pages = int((first.get("result") or {}).get("pages") or 0)
        rows.extend((first.get("result") or {}).get("data") or [])
        for page in range(2, total_pages + 1):
            payload = _get_page(session, start, end, page=page, page_size=page_size)
            rows.extend((payload.get("result") or {}).get("data") or [])
        return _normalize_rows(rows)
    finally:
        session.close()


def _get_page(
    session: requests.Session,
    start: str,
    end: str,
    page: int,
    page_size: int,
) -> dict[str, object]:
    params = {
        "sortColumns": "SECURITY_CODE,TRADE_DATE",
        "sortTypes": "1,-1",
        "pageSize": str(page_size),
        "pageNumber": str(page),
        "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW",
        "columns": (
            "SECURITY_CODE,SECURITY_NAME_ABBR,TRADE_DATE,BILLBOARD_NET_AMT,"
            "BILLBOARD_BUY_AMT,BILLBOARD_SELL_AMT,BILLBOARD_DEAL_AMT,EXPLANATION"
        ),
        "source": "WEB",
        "client": "WEB",
        "filter": f"(TRADE_DATE<='{end}')(TRADE_DATE>='{start}')",
    }
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            response = session.get(
                EASTMONEY_LHB_URL,
                params=params,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 Chrome/120 Safari/537.36"
                    ),
                    "Referer": "https://data.eastmoney.com/stock/tradedetail.html",
                },
                timeout=20,
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_error = exc
            time.sleep(attempt)
    raise RuntimeError(f"Longhu Bang page {page} fetch failed: {last_error}") from last_error


def _normalize_rows(rows: list[dict[str, object]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=["code", "name", "date", "net_buy"])
    return frame.rename(
        columns={
            "SECURITY_CODE": "code",
            "SECURITY_NAME_ABBR": "name",
            "TRADE_DATE": "date",
            "BILLBOARD_NET_AMT": "net_buy",
            "BILLBOARD_BUY_AMT": "buy_amount",
            "BILLBOARD_SELL_AMT": "sell_amount",
            "BILLBOARD_DEAL_AMT": "deal_amount",
            "EXPLANATION": "reason",
        }
    )


def _date_for_filter(value: str) -> str:
    if "-" in value:
        return value
    return f"{value[:4]}-{value[4:6]}-{value[6:]}"


if __name__ == "__main__":
    main()
