from __future__ import annotations

from typing import Iterable

import pandas as pd

from stock_picker.data.models import normalize_symbol, symbol_code


class AkShareProvider:
    """AkShare based A-share market data provider."""

    def __init__(self) -> None:
        try:
            import akshare as ak
        except ImportError as exc:
            raise RuntimeError(
                "AkShare is not installed. Run: pip install -r requirements.txt"
            ) from exc
        self._ak = ak

    def get_history(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        period: str = "daily",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        raw = self._ak.stock_zh_a_hist(
            symbol=symbol_code(symbol),
            period=period,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
        )
        if raw.empty:
            return self._empty_history_frame()

        df = raw.rename(
            columns={
                "日期": "date",
                "开盘": "open",
                "收盘": "close",
                "最高": "high",
                "最低": "low",
                "成交量": "volume",
                "成交额": "amount",
                "振幅": "amplitude",
                "涨跌幅": "pct_chg",
                "涨跌额": "change",
                "换手率": "turnover",
            }
        )
        df.insert(0, "symbol", normalize_symbol(symbol))
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        return df[
            [
                "symbol",
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "amplitude",
                "pct_chg",
                "change",
                "turnover",
            ]
        ]

    def get_realtime_quotes(self, symbols: Iterable[str] | None = None) -> pd.DataFrame:
        raw = self._ak.stock_zh_a_spot_em()
        if raw.empty:
            return self._empty_realtime_frame()

        df = raw.rename(
            columns={
                "代码": "code",
                "名称": "name",
                "最新价": "price",
                "涨跌幅": "pct_chg",
                "涨跌额": "change",
                "成交量": "volume",
                "成交额": "amount",
                "最高": "high",
                "最低": "low",
                "今开": "open",
                "昨收": "prev_close",
                "换手率": "turnover",
            }
        )
        df["symbol"] = df["code"].map(normalize_symbol)

        if symbols:
            wanted = {normalize_symbol(item) for item in symbols}
            df = df[df["symbol"].isin(wanted)]

        keep = [
            "symbol",
            "name",
            "price",
            "pct_chg",
            "change",
            "volume",
            "amount",
            "high",
            "low",
            "open",
            "prev_close",
            "turnover",
        ]
        return df[keep].reset_index(drop=True)

    @staticmethod
    def _empty_history_frame() -> pd.DataFrame:
        return pd.DataFrame(
            columns=[
                "symbol",
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "amplitude",
                "pct_chg",
                "change",
                "turnover",
            ]
        )

    @staticmethod
    def _empty_realtime_frame() -> pd.DataFrame:
        return pd.DataFrame(
            columns=[
                "symbol",
                "name",
                "price",
                "pct_chg",
                "change",
                "volume",
                "amount",
                "high",
                "low",
                "open",
                "prev_close",
                "turnover",
            ]
        )
