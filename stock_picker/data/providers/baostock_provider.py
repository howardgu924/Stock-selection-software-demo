from __future__ import annotations

import pandas as pd

from stock_picker.data.models import baostock_symbol, normalize_symbol


class BaoStockProvider:
    """BaoStock provider for A-share historical daily prices."""

    FIELDS = ",".join(
        [
            "date",
            "code",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "turn",
            "pctChg",
        ]
    )

    def __init__(self) -> None:
        try:
            import baostock as bs
        except ImportError as exc:
            raise RuntimeError(
                "BaoStock is not installed. Run: pip install -r requirements.txt"
            ) from exc
        self._bs = bs

    def get_history(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        period: str = "daily",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        if period != "daily":
            raise ValueError("BaoStockProvider currently supports only daily history.")

        login = self._bs.login()
        if login.error_code != "0":
            raise RuntimeError(f"BaoStock login failed: {login.error_msg}")

        try:
            result = self._bs.query_history_k_data_plus(
                baostock_symbol(symbol),
                self.FIELDS,
                start_date=self._format_date(start_date),
                end_date=self._format_date(end_date),
                frequency="d",
                adjustflag=self._adjust_flag(adjust),
            )
            if result.error_code != "0":
                raise RuntimeError(f"BaoStock query failed: {result.error_msg}")

            rows = []
            while result.next():
                rows.append(result.get_row_data())

            if not rows:
                return self._empty_history_frame()

            df = pd.DataFrame(rows, columns=result.fields)
            df = df.rename(
                columns={
                    "turn": "turnover",
                    "pctChg": "pct_chg",
                }
            )
            df.insert(0, "symbol", normalize_symbol(symbol))
            df["change"] = pd.NA
            df["amplitude"] = pd.NA

            numeric_columns = [
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "turnover",
                "pct_chg",
            ]
            for column in numeric_columns:
                df[column] = pd.to_numeric(df[column], errors="coerce")

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
        finally:
            self._bs.logout()

    @staticmethod
    def _format_date(value: str) -> str:
        if "-" in value:
            return value
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"

    @staticmethod
    def _adjust_flag(adjust: str) -> str:
        if adjust == "qfq":
            return "2"
        if adjust == "hfq":
            return "1"
        if adjust in {"", "none", "bfq"}:
            return "3"
        raise ValueError("adjust must be one of: qfq, hfq, bfq, none")

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
