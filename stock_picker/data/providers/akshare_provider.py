from __future__ import annotations

import os
from contextlib import contextmanager, redirect_stderr
from io import StringIO
from typing import Iterable

import pandas as pd

from stock_picker.data.models import StockInfo, normalize_symbol, symbol_code


class AkShareProvider:
    """AkShare based A-share market data provider."""

    EASTMONEY_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/120 Safari/537.36"
        ),
        "Referer": "https://quote.eastmoney.com/",
    }

    HISTORY_COLUMNS = {
        "\u65e5\u671f": "date",
        "\u5f00\u76d8": "open",
        "\u6536\u76d8": "close",
        "\u6700\u9ad8": "high",
        "\u6700\u4f4e": "low",
        "\u6210\u4ea4\u91cf": "volume",
        "\u6210\u4ea4\u989d": "amount",
        "\u632f\u5e45": "amplitude",
        "\u6da8\u8dcc\u5e45": "pct_chg",
        "\u6da8\u8dcc\u989d": "change",
        "\u6362\u624b\u7387": "turnover",
    }

    REALTIME_COLUMNS = {
        "\u4ee3\u7801": "code",
        "\u540d\u79f0": "name",
        "\u6700\u65b0\u4ef7": "price",
        "\u6da8\u8dcc\u5e45": "pct_chg",
        "\u6da8\u8dcc\u989d": "change",
        "\u6210\u4ea4\u91cf": "volume",
        "\u6210\u4ea4\u989d": "amount",
        "\u6700\u9ad8": "high",
        "\u6700\u4f4e": "low",
        "\u4eca\u5f00": "open",
        "\u6628\u6536": "prev_close",
        "\u6362\u624b\u7387": "turnover",
    }

    MARKET_SNAPSHOT_COLUMNS = {
        **REALTIME_COLUMNS,
        "\u603b\u5e02\u503c": "market_cap",
        "\u5e02\u76c8\u7387-\u52a8\u6001": "pe_dynamic",
        "\u5e02\u51c0\u7387": "pb",
    }

    MINUTE_COLUMNS = {
        "\u65f6\u95f4": "datetime",
        "\u65e5\u671f\u65f6\u95f4": "datetime",
        "\u5f00\u76d8": "open",
        "\u6536\u76d8": "close",
        "\u6700\u9ad8": "high",
        "\u6700\u4f4e": "low",
        "\u6210\u4ea4\u91cf": "volume",
        "\u6210\u4ea4\u989d": "amount",
        "\u5747\u4ef7": "average_price",
        "\u6700\u65b0\u4ef7": "price",
        "\u632f\u5e45": "amplitude",
        "\u6da8\u8dcc\u5e45": "pct_chg",
        "\u6da8\u8dcc\u989d": "change",
        "\u6362\u624b\u7387": "turnover",
    }

    BOARD_COLUMNS = {
        "\u6392\u540d": "rank",
        "\u677f\u5757\u540d\u79f0": "name",
        "\u677f\u5757\u4ee3\u7801": "code",
        "\u6700\u65b0\u4ef7": "price",
        "\u6da8\u8dcc\u989d": "change",
        "\u6da8\u8dcc\u5e45": "pct_chg",
        "\u603b\u5e02\u503c": "market_cap",
        "\u6362\u624b\u7387": "turnover",
        "\u4e0a\u6da8\u5bb6\u6570": "up_count",
        "\u4e0b\u8dcc\u5bb6\u6570": "down_count",
        "\u9886\u6da8\u80a1\u7968": "leader",
        "\u9886\u6da8\u80a1\u7968-\u6da8\u8dcc\u5e45": "leader_pct_chg",
    }

    BOARD_MEMBER_COLUMNS = {
        "\u5e8f\u53f7": "rank",
        "\u4ee3\u7801": "code",
        "\u540d\u79f0": "name",
        "\u6700\u65b0\u4ef7": "price",
        "\u6da8\u8dcc\u5e45": "pct_chg",
        "\u6da8\u8dcc\u989d": "change",
        "\u6210\u4ea4\u91cf": "volume",
        "\u6210\u4ea4\u989d": "amount",
        "\u632f\u5e45": "amplitude",
        "\u6700\u9ad8": "high",
        "\u6700\u4f4e": "low",
        "\u4eca\u5f00": "open",
        "\u6628\u6536": "prev_close",
        "\u6362\u624b\u7387": "turnover",
        "\u5e02\u76c8\u7387-\u52a8\u6001": "pe_dynamic",
        "\u5e02\u51c0\u7387": "pb",
    }

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
        with self._eastmoney_request_context():
            raw = self._ak.stock_zh_a_hist(
                symbol=symbol_code(symbol),
                period=period,
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
        if raw.empty:
            return self._empty_history_frame()

        df = raw.rename(columns=self.HISTORY_COLUMNS)
        missing = [column for column in self._history_columns() if column not in df]
        if missing:
            raise ValueError(f"AkShare history response missing columns: {missing}")

        df.insert(0, "symbol", normalize_symbol(symbol))
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        return df[["symbol", *self._history_columns()]]

    def get_index_history(
        self,
        index_code: str,
        start_date: str,
        end_date: str,
        period: str = "daily",
    ) -> pd.DataFrame:
        code = symbol_code(index_code)
        try:
            raw = self._ak.stock_zh_index_daily_em(
                symbol=self._akshare_index_symbol(code),
                start_date=start_date,
                end_date=end_date,
            )
        except Exception:
            raw = self._ak.stock_zh_index_daily(symbol=self._sina_index_symbol(code))
            if not raw.empty:
                start = pd.to_datetime(start_date, errors="coerce")
                end = pd.to_datetime(end_date, errors="coerce")
                raw = raw.copy()
                raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
                if pd.notna(start):
                    raw = raw[raw["date"] >= start]
                if pd.notna(end):
                    raw = raw[raw["date"] <= end]
        if raw.empty:
            return self._empty_index_history_frame()

        df = raw.rename(columns=self.HISTORY_COLUMNS)
        for column in self._index_history_columns():
            if column not in df:
                df[column] = pd.NA
        df.insert(0, "index_code", code)
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        return df[["index_code", *self._index_history_columns()]].reset_index(drop=True)

    def get_realtime_quotes(self, symbols: Iterable[str] | None = None) -> pd.DataFrame:
        with self._eastmoney_request_context():
            raw = self._ak.stock_zh_a_spot_em()
        if raw.empty:
            return self._empty_realtime_frame()

        df = raw.rename(columns=self.REALTIME_COLUMNS)
        missing = [column for column in ["code", *self._realtime_columns()] if column not in df]
        if missing:
            raise ValueError(f"AkShare realtime response missing columns: {missing}")

        df["symbol"] = df["code"].map(normalize_symbol)

        if symbols:
            wanted = {normalize_symbol(item) for item in symbols}
            df = df[df["symbol"].isin(wanted)]

        return df[["symbol", *self._realtime_columns()]].reset_index(drop=True)

    def get_market_snapshot(self, symbols: Iterable[str] | None = None) -> pd.DataFrame:
        raw = self._fetch_market_snapshot_rows()
        if raw.empty:
            return self._empty_market_snapshot_frame()

        df = raw.rename(columns=self.MARKET_SNAPSHOT_COLUMNS)
        missing_required = [column for column in ["code", "name"] if column not in df]
        if missing_required:
            raise ValueError(
                f"AkShare market snapshot response missing columns: {missing_required}"
            )

        for column in self._market_snapshot_columns():
            if column not in df:
                df[column] = pd.NA

        df["symbol"] = df["code"].map(normalize_symbol)

        if symbols:
            wanted = {normalize_symbol(item) for item in symbols}
            df = df[df["symbol"].isin(wanted)]

        for column in [
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
            "market_cap",
            "pe_dynamic",
            "pb",
        ]:
            df[column] = pd.to_numeric(df[column], errors="coerce")

        return df[["symbol", *self._market_snapshot_columns()]].reset_index(drop=True)

    def get_financial_indicators(
        self,
        symbol: str,
        start_year: str = "1900",
    ) -> pd.DataFrame:
        raw = self._ak.stock_financial_analysis_indicator(
            symbol=symbol_code(symbol),
            start_year=str(start_year),
        )
        if raw.empty:
            return self._empty_financial_indicators_frame()

        frame = pd.DataFrame(
            {
                "date": self._first_existing_column(
                    raw,
                    ["\u65e5\u671f", "\u62a5\u544a\u671f", "\u516c\u544a\u65e5\u671f"],
                ),
                "current_ratio": self._first_existing_column(
                    raw,
                    ["\u6d41\u52a8\u6bd4\u7387", "\u6d41\u52a8\u6bd4\u7387(%)"],
                ),
                "debt_asset_ratio": self._first_existing_column(
                    raw,
                    [
                        "\u8d44\u4ea7\u8d1f\u503a\u7387(%)",
                        "\u8d44\u4ea7\u8d1f\u503a\u7387",
                    ],
                ),
                "total_assets": self._first_existing_column(
                    raw,
                    [
                        "\u603b\u8d44\u4ea7(\u5143)",
                        "\u603b\u8d44\u4ea7",
                    ],
                ),
            }
        )
        frame.insert(0, "symbol", normalize_symbol(symbol))
        if "date" in frame:
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime(
                "%Y-%m-%d"
            )
            frame = frame.sort_values("date")
        for column in ["current_ratio", "debt_asset_ratio", "total_assets"]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        return frame[["symbol", *self._financial_indicator_columns()]].reset_index(
            drop=True
        )

    def get_valuation_history(
        self,
        symbol: str,
        indicator: str = "\u603b\u5e02\u503c",
        period: str = "\u8fd1\u4e00\u5e74",
    ) -> pd.DataFrame:
        raw = self._ak.stock_zh_valuation_baidu(
            symbol=symbol_code(symbol),
            indicator=indicator,
            period=period,
        )
        if raw.empty:
            return self._empty_valuation_history_frame()

        frame = raw.rename(columns={"value": "value", "date": "date"}).copy()
        frame.insert(0, "symbol", normalize_symbol(symbol))
        frame.insert(1, "indicator", indicator)
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime(
            "%Y-%m-%d"
        )
        frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
        return frame[["symbol", *self._valuation_history_columns()]].reset_index(
            drop=True
        )

    def get_index_members(self, index_code: str) -> pd.DataFrame:
        raw = self._ak.index_stock_cons_csindex(symbol=symbol_code(index_code))
        if raw.empty:
            return self._empty_index_members_frame()

        frame = pd.DataFrame(
            {
                "code": self._first_existing_column(
                    raw,
                    [
                        "\u6210\u5206\u5238\u4ee3\u7801",
                        "\u54c1\u79cd\u4ee3\u7801",
                        "\u8bc1\u5238\u4ee3\u7801",
                        "\u4ee3\u7801",
                    ],
                ),
                "name": self._first_existing_column(
                    raw,
                    [
                        "\u6210\u5206\u5238\u540d\u79f0",
                        "\u54c1\u79cd\u540d\u79f0",
                        "\u8bc1\u5238\u7b80\u79f0",
                        "\u540d\u79f0",
                    ],
                ),
                "weight": self._first_existing_column(
                    raw,
                    [
                        "\u6743\u91cd",
                        "\u6743\u91cd(%)",
                        "\u6743\u91cd\uff08%\uff09",
                    ],
                ),
            }
        )
        frame.insert(0, "index_code", symbol_code(index_code))
        frame["code"] = frame["code"].astype(str).str.extract(r"(\d{6})", expand=False)
        frame = frame.dropna(subset=["code"])
        frame["symbol"] = frame["code"].map(normalize_symbol)
        frame["weight"] = pd.to_numeric(frame["weight"], errors="coerce")
        return frame[["index_code", "symbol", *self._index_member_columns()]].reset_index(
            drop=True
        )

    def get_minute_history(
        self,
        symbol: str,
        start_datetime: str,
        end_datetime: str,
        period: str = "5",
        adjust: str = "",
    ) -> pd.DataFrame:
        self._validate_minute_period(period)
        with self._eastmoney_request_context():
            raw = self._ak.stock_zh_a_hist_min_em(
                symbol=symbol_code(symbol),
                start_date=start_datetime,
                end_date=end_datetime,
                period=period,
                adjust=adjust,
            )
        return self._normalize_minute_frame(raw, symbol=normalize_symbol(symbol))

    def get_boards(self, board_type: str) -> pd.DataFrame:
        board_type = self._validate_board_type(board_type)
        raw = self._fetch_board_rows(board_type)

        if raw.empty:
            return self._empty_boards_frame()

        df = raw.rename(columns=self.BOARD_COLUMNS)
        missing = [column for column in self._board_columns() if column not in df]
        if missing:
            raise ValueError(f"AkShare board response missing columns: {missing}")

        df.insert(0, "board_type", board_type)
        return df[["board_type", *self._board_columns()]].reset_index(drop=True)

    def get_board_members(self, board_type: str, board: str) -> pd.DataFrame:
        board_type = self._validate_board_type(board_type)
        raw = self._fetch_board_member_rows(board_type, board)

        if raw.empty:
            return self._empty_board_members_frame()

        df = raw.rename(columns=self.BOARD_MEMBER_COLUMNS)
        missing = [column for column in self._board_member_columns() if column not in df]
        if missing:
            raise ValueError(f"AkShare board member response missing columns: {missing}")

        df.insert(0, "board_type", board_type)
        df.insert(1, "board", board)
        df["symbol"] = df["code"].map(lambda value: normalize_symbol(str(value).zfill(6)))
        return df[
            ["board_type", "board", "symbol", *self._board_member_columns()]
        ].reset_index(drop=True)

    def get_board_minute_history(
        self,
        board_type: str,
        board: str,
        period: str = "5",
    ) -> pd.DataFrame:
        board_type = self._validate_board_type(board_type)
        self._validate_minute_period(period)
        with self._eastmoney_request_context():
            if board_type == "industry":
                raw = self._ak.stock_board_industry_hist_min_em(
                    symbol=board,
                    period=period,
                )
            else:
                raw = self._ak.stock_board_concept_hist_min_em(
                    symbol=board,
                    period=period,
                )
        return self._normalize_minute_frame(raw, board_type=board_type, board=board)

    def get_stock_symbols(self) -> list[StockInfo]:
        with redirect_stderr(StringIO()):
            raw = self._ak.stock_info_a_code_name()
        if raw.empty:
            return []

        df = raw.rename(columns={"\u4ee3\u7801": "code", "\u540d\u79f0": "name"})
        missing = [column for column in ["code", "name"] if column not in df]
        if missing:
            raise ValueError(f"AkShare stock list response missing columns: {missing}")

        return [
            StockInfo.from_code_name(str(row.code).zfill(6), str(row.name))
            for row in df[["code", "name"]].itertuples(index=False)
        ]

    @staticmethod
    def _history_columns() -> list[str]:
        return [
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

    @staticmethod
    def _index_history_columns() -> list[str]:
        return [
            "date",
            "open",
            "close",
            "high",
            "low",
            "volume",
            "amount",
            "amplitude",
            "pct_chg",
            "change",
            "turnover",
        ]

    @staticmethod
    def _realtime_columns() -> list[str]:
        return [
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

    @staticmethod
    def _market_snapshot_columns() -> list[str]:
        return [
            "code",
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
            "market_cap",
            "pe_dynamic",
            "pb",
        ]

    @staticmethod
    def _financial_indicator_columns() -> list[str]:
        return [
            "date",
            "current_ratio",
            "debt_asset_ratio",
            "total_assets",
        ]

    @staticmethod
    def _index_member_columns() -> list[str]:
        return [
            "code",
            "name",
            "weight",
        ]

    @staticmethod
    def _valuation_history_columns() -> list[str]:
        return [
            "indicator",
            "date",
            "value",
        ]

    @staticmethod
    def _minute_columns() -> list[str]:
        return [
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

    @staticmethod
    def _board_columns() -> list[str]:
        return [
            "rank",
            "name",
            "code",
            "price",
            "change",
            "pct_chg",
            "market_cap",
            "turnover",
            "up_count",
            "down_count",
            "leader",
            "leader_pct_chg",
        ]

    @staticmethod
    def _board_member_columns() -> list[str]:
        return [
            "rank",
            "code",
            "name",
            "price",
            "pct_chg",
            "change",
            "volume",
            "amount",
            "amplitude",
            "high",
            "low",
            "open",
            "prev_close",
            "turnover",
            "pe_dynamic",
            "pb",
        ]

    @classmethod
    def _empty_history_frame(cls) -> pd.DataFrame:
        return pd.DataFrame(columns=["symbol", *cls._history_columns()])

    @classmethod
    def _empty_index_history_frame(cls) -> pd.DataFrame:
        return pd.DataFrame(columns=["index_code", *cls._index_history_columns()])

    @classmethod
    def _empty_realtime_frame(cls) -> pd.DataFrame:
        return pd.DataFrame(columns=["symbol", *cls._realtime_columns()])

    @classmethod
    def _empty_market_snapshot_frame(cls) -> pd.DataFrame:
        return pd.DataFrame(columns=["symbol", *cls._market_snapshot_columns()])

    @classmethod
    def _empty_financial_indicators_frame(cls) -> pd.DataFrame:
        return pd.DataFrame(columns=["symbol", *cls._financial_indicator_columns()])

    @classmethod
    def _empty_index_members_frame(cls) -> pd.DataFrame:
        return pd.DataFrame(columns=["index_code", "symbol", *cls._index_member_columns()])

    @classmethod
    def _empty_valuation_history_frame(cls) -> pd.DataFrame:
        return pd.DataFrame(columns=["symbol", *cls._valuation_history_columns()])

    @classmethod
    def _empty_minute_frame(cls) -> pd.DataFrame:
        return pd.DataFrame(columns=["symbol", *cls._minute_columns()])

    @classmethod
    def _empty_board_minute_frame(cls) -> pd.DataFrame:
        return pd.DataFrame(columns=["board_type", "board", *cls._minute_columns()])

    @classmethod
    def _empty_boards_frame(cls) -> pd.DataFrame:
        return pd.DataFrame(columns=["board_type", *cls._board_columns()])

    @classmethod
    def _empty_board_members_frame(cls) -> pd.DataFrame:
        return pd.DataFrame(
            columns=["board_type", "board", "symbol", *cls._board_member_columns()]
        )

    @classmethod
    def _normalize_minute_frame(
        cls,
        raw: pd.DataFrame,
        symbol: str | None = None,
        board_type: str | None = None,
        board: str | None = None,
    ) -> pd.DataFrame:
        if raw.empty:
            if symbol is not None:
                return cls._empty_minute_frame()
            return cls._empty_board_minute_frame()

        df = raw.rename(columns=cls.MINUTE_COLUMNS)
        for column in cls._minute_columns():
            if column not in df:
                df[column] = pd.NA

        if symbol is not None:
            df.insert(0, "symbol", symbol)
            return df[["symbol", *cls._minute_columns()]].reset_index(drop=True)

        df.insert(0, "board_type", board_type)
        df.insert(1, "board", board)
        return df[["board_type", "board", *cls._minute_columns()]].reset_index(drop=True)

    @staticmethod
    def _validate_minute_period(period: str) -> None:
        if period not in {"1", "5", "15", "30", "60"}:
            raise ValueError("period must be one of: 1, 5, 15, 30, 60")

    @staticmethod
    def _validate_board_type(board_type: str) -> str:
        normalized = board_type.strip().lower()
        if normalized not in {"industry", "concept"}:
            raise ValueError("board_type must be one of: industry, concept")
        return normalized

    @staticmethod
    def _first_existing_column(raw: pd.DataFrame, candidates: list[str]) -> pd.Series:
        for column in candidates:
            if column in raw:
                return raw[column]
        return pd.Series([pd.NA] * len(raw), index=raw.index)

    @staticmethod
    def _akshare_index_symbol(code: str) -> str:
        if code.startswith(("0", "9")):
            return f"sh{code}"
        return f"sz{code}"

    @staticmethod
    def _sina_index_symbol(code: str) -> str:
        if code.startswith(("0", "9")):
            return f"sh{code}"
        return f"sz{code}"

    @classmethod
    def _fetch_board_rows(cls, board_type: str) -> pd.DataFrame:
        if board_type == "industry":
            url = "https://17.push2.eastmoney.com/api/qt/clist/get"
            fs = "m:90 t:2 f:!50"
            fid = "f3"
        else:
            url = "https://79.push2.eastmoney.com/api/qt/clist/get"
            fs = "m:90 t:3 f:!50"
            fid = "f12"

        rows = cls._fetch_eastmoney_pages(
            url,
            {
                "po": "1",
                "np": "1",
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": "2",
                "invt": "2",
                "fid": fid,
                "fs": fs,
                "fields": (
                    "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,"
                    "f17,f18,f20,f21,f23,f24,f25,f26,f22,f33,f11,f62,f128,"
                    "f136,f115,f152,f124,f107,f104,f105,f140,f141,f207,f208,"
                    "f209,f222"
                ),
            },
        )
        return pd.DataFrame(
            [
                {
                    "排名": index,
                    "板块名称": row.get("f14"),
                    "板块代码": row.get("f12"),
                    "最新价": row.get("f2"),
                    "涨跌额": row.get("f4"),
                    "涨跌幅": row.get("f3"),
                    "总市值": row.get("f20"),
                    "换手率": row.get("f8"),
                    "上涨家数": row.get("f104"),
                    "下跌家数": row.get("f105"),
                    "领涨股票": row.get("f128"),
                    "领涨股票-涨跌幅": row.get("f136"),
                }
                for index, row in enumerate(rows, start=1)
            ]
        )

    @classmethod
    def _fetch_market_snapshot_rows(cls) -> pd.DataFrame:
        rows = cls._fetch_eastmoney_pages(
            "https://82.push2.eastmoney.com/api/qt/clist/get",
            {
                "po": "1",
                "np": "1",
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": "2",
                "invt": "2",
                "fid": "f12",
                "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
                "fields": (
                    "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,"
                    "f17,f18,f20,f21,f23,f24,f25,f22,f11,f62,f128,f136,"
                    "f115,f152"
                ),
            },
        )
        return pd.DataFrame(
            [
                {
                    "\u4ee3\u7801": str(row.get("f12", "")).zfill(6),
                    "\u540d\u79f0": row.get("f14"),
                    "\u6700\u65b0\u4ef7": row.get("f2"),
                    "\u6da8\u8dcc\u5e45": row.get("f3"),
                    "\u6da8\u8dcc\u989d": row.get("f4"),
                    "\u6210\u4ea4\u91cf": row.get("f5"),
                    "\u6210\u4ea4\u989d": row.get("f6"),
                    "\u6700\u9ad8": row.get("f15"),
                    "\u6700\u4f4e": row.get("f16"),
                    "\u4eca\u5f00": row.get("f17"),
                    "\u6628\u6536": row.get("f18"),
                    "\u6362\u624b\u7387": row.get("f8"),
                    "\u603b\u5e02\u503c": row.get("f20"),
                    "\u5e02\u76c8\u7387-\u52a8\u6001": row.get("f9"),
                    "\u5e02\u51c0\u7387": row.get("f23"),
                }
                for row in rows
            ]
        )

    @classmethod
    def _fetch_board_member_rows(cls, board_type: str, board: str) -> pd.DataFrame:
        board_code = board
        if not board_code.upper().startswith("BK"):
            boards = cls._fetch_board_rows(board_type)
            matches = boards[boards["板块名称"] == board]
            if matches.empty:
                raise ValueError(f"Board not found: {board}")
            board_code = str(matches.iloc[0]["板块代码"])
        else:
            board_code = board_code.upper()

        rows = cls._fetch_eastmoney_pages(
            "https://29.push2.eastmoney.com/api/qt/clist/get",
            {
                "po": "1",
                "np": "1",
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": "2",
                "invt": "2",
                "fid": "f3" if board_type == "industry" else "f12",
                "fs": f"b:{board_code} f:!50",
                "fields": (
                    "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,"
                    "f17,f18,f20,f21,f23,f24,f25,f22,f11,f62,f128,f136,"
                    "f115,f152,f45"
                ),
            },
        )
        return pd.DataFrame(
            [
                {
                    "序号": index,
                    "代码": str(row.get("f12", "")).zfill(6),
                    "名称": row.get("f14"),
                    "最新价": row.get("f2"),
                    "涨跌幅": row.get("f3"),
                    "涨跌额": row.get("f4"),
                    "成交量": row.get("f5"),
                    "成交额": row.get("f6"),
                    "振幅": row.get("f7"),
                    "最高": row.get("f15"),
                    "最低": row.get("f16"),
                    "今开": row.get("f17"),
                    "昨收": row.get("f18"),
                    "换手率": row.get("f8"),
                    "市盈率-动态": row.get("f9"),
                    "市净率": row.get("f23"),
                }
                for index, row in enumerate(rows, start=1)
            ]
        )

    @classmethod
    def _fetch_eastmoney_pages(
        cls,
        url: str,
        params: dict[str, str],
        page_size: int = 50,
    ) -> list[dict[str, object]]:
        import requests

        rows: list[dict[str, object]] = []
        page = 1
        session = requests.Session()
        trust_env = os.getenv("STOCK_PICKER_EASTMONEY_TRUST_ENV", "").lower()
        if trust_env not in {"1", "true", "yes"}:
            session.trust_env = False
        try:
            while True:
                page_params = {
                    **params,
                    "pn": str(page),
                    "pz": str(page_size),
                }
                response = session.get(
                    url,
                    params=page_params,
                    headers=cls.EASTMONEY_HEADERS,
                    timeout=20,
                )
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data") or {}
                page_rows = data.get("diff") or []
                if not page_rows:
                    break

                rows.extend(page_rows)
                total = int(data.get("total") or len(rows))
                if len(rows) >= total:
                    break
                page += 1
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(
                "Eastmoney market snapshot request failed. "
                "This usually means the current proxy or network is blocking "
                "push2.eastmoney.com. The provider bypasses environment proxies "
                "by default; set STOCK_PICKER_EASTMONEY_TRUST_ENV=1 only if your "
                "network requires a working proxy."
            ) from exc
        finally:
            session.close()

        return rows

    @classmethod
    @contextmanager
    def _eastmoney_request_context(cls):
        import requests

        original_request = requests.sessions.Session.request

        def patched_request(session, method, url, **kwargs):
            if "eastmoney.com" in str(url):
                headers = dict(kwargs.get("headers") or {})
                for key, value in cls.EASTMONEY_HEADERS.items():
                    headers.setdefault(key, value)
                kwargs["headers"] = headers
                trust_env = os.getenv("STOCK_PICKER_EASTMONEY_TRUST_ENV", "").lower()
                if trust_env not in {"1", "true", "yes"}:
                    original_trust_env = session.trust_env
                    session.trust_env = False
                    try:
                        return original_request(session, method, url, **kwargs)
                    finally:
                        session.trust_env = original_trust_env
            return original_request(session, method, url, **kwargs)

        requests.sessions.Session.request = patched_request
        try:
            yield
        finally:
            requests.sessions.Session.request = original_request
