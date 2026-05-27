from __future__ import annotations

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
    def _empty_realtime_frame(cls) -> pd.DataFrame:
        return pd.DataFrame(columns=["symbol", *cls._realtime_columns()])

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
        while True:
            page_params = {
                **params,
                "pn": str(page),
                "pz": str(page_size),
            }
            response = requests.get(
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
            return original_request(session, method, url, **kwargs)

        requests.sessions.Session.request = patched_request
        try:
            yield
        finally:
            requests.sessions.Session.request = original_request
