from __future__ import annotations

from contextlib import contextmanager, redirect_stdout
from io import StringIO

import pandas as pd

from stock_picker.data.models import StockInfo, baostock_symbol, normalize_symbol


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

    def __init__(self, quiet: bool = True) -> None:
        try:
            import baostock as bs
        except ImportError as exc:
            raise RuntimeError(
                "BaoStock is not installed. Run: pip install -r requirements.txt"
            ) from exc
        self._bs = bs
        self.quiet = quiet
        self._session_depth = 0
        self._logged_in = False

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

        with self._login_session():
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

    def get_minute_history(
        self,
        symbol: str,
        start_datetime: str,
        end_datetime: str,
        period: str = "5",
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        if period not in {"5", "15", "30", "60"}:
            raise ValueError("BaoStockProvider supports only 5, 15, 30, and 60 minute history.")

        fields = "date,time,code,open,high,low,close,volume,amount"
        start_date = self._format_date(start_datetime.split(" ", 1)[0])
        end_date = self._format_date(end_datetime.split(" ", 1)[0])

        with self._login_session():
            result = self._bs.query_history_k_data_plus(
                baostock_symbol(symbol),
                fields,
                start_date=start_date,
                end_date=end_date,
                frequency=period,
                adjustflag=self._adjust_flag(adjust),
            )
            if result.error_code != "0":
                raise RuntimeError(f"BaoStock minute query failed: {result.error_msg}")

            rows = []
            while result.next():
                rows.append(result.get_row_data())

        if not rows:
            return self._empty_minute_frame()

        df = pd.DataFrame(rows, columns=result.fields)
        df.insert(0, "symbol", normalize_symbol(symbol))
        df["datetime"] = pd.to_datetime(
            df["time"].str.slice(0, 14),
            format="%Y%m%d%H%M%S",
            errors="coerce",
        ).dt.strftime("%Y-%m-%d %H:%M:%S")
        df = df.drop(columns=["date", "time", "code"])

        numeric_columns = ["open", "high", "low", "close", "volume", "amount"]
        for column in numeric_columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")

        df["average_price"] = pd.NA
        df["price"] = pd.NA
        df["amplitude"] = pd.NA
        df["pct_chg"] = pd.NA
        df["change"] = pd.NA
        df["turnover"] = pd.NA

        start = pd.to_datetime(start_datetime)
        end = pd.to_datetime(end_datetime)
        datetimes = pd.to_datetime(df["datetime"], errors="coerce")
        df = df[(datetimes >= start) & (datetimes <= end)]

        return df[
            [
                "symbol",
                "datetime",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "average_price",
                "price",
                "amplitude",
                "pct_chg",
                "change",
                "turnover",
            ]
        ].reset_index(drop=True)

    def get_trade_dates(self, start_date: str, end_date: str) -> list[str]:
        with self._login_session():
            result = self._bs.query_trade_dates(
                start_date=self._format_date(start_date),
                end_date=self._format_date(end_date),
            )
            if result.error_code != "0":
                raise RuntimeError(f"BaoStock trade dates query failed: {result.error_msg}")

            rows = []
            while result.next():
                row = dict(zip(result.fields, result.get_row_data(), strict=False))
                if row.get("is_trading_day") == "1":
                    rows.append(row["calendar_date"])
            return rows

    def get_stock_symbols(self) -> list[StockInfo]:
        with self._login_session():
            result = self._bs.query_all_stock()
            if result.error_code != "0":
                raise RuntimeError(f"BaoStock stock list query failed: {result.error_msg}")

            rows = []
            while result.next():
                row = dict(zip(result.fields, result.get_row_data(), strict=False))
                raw_code = row.get("code", "")
                name = row.get("code_name", "")
                trade_status = row.get("tradeStatus", "1")
                if not raw_code or not name or trade_status != "1":
                    continue
                if not raw_code.startswith(("sh.", "sz.", "bj.")):
                    continue
                rows.append(StockInfo.from_code_name(raw_code.split(".", 1)[1], name))
            return rows

    @contextmanager
    def _login_session(self):
        should_login = self._session_depth == 0 and not self._logged_in
        if should_login:
            login = self._call_quietly(self._bs.login)
            if login.error_code != "0":
                raise RuntimeError(f"BaoStock login failed: {login.error_msg}")
            self._logged_in = True

        self._session_depth += 1
        try:
            yield
        finally:
            self._session_depth -= 1
            if should_login:
                self._call_quietly(self._bs.logout)
                self._logged_in = False

    def batch_session(self):
        return self._login_session()

    def _call_quietly(self, func):
        if not self.quiet:
            return func()
        with redirect_stdout(StringIO()):
            return func()

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

    @staticmethod
    def _empty_minute_frame() -> pd.DataFrame:
        return pd.DataFrame(
            columns=[
                "symbol",
                "datetime",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "average_price",
                "price",
                "amplitude",
                "pct_chg",
                "change",
                "turnover",
            ]
        )
