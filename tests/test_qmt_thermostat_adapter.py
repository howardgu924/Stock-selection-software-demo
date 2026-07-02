from __future__ import annotations

import ast
import importlib.util
from pathlib import Path


ADAPTER_PATH = Path(__file__).resolve().parents[1] / "qmt_thermostat_adapter_20260701.py"


def _load_adapter():
    spec = importlib.util.spec_from_file_location("qmt_thermostat_adapter_20260701", ADAPTER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_qmt_adapter_is_single_file_without_third_party_imports() -> None:
    tree = ast.parse(ADAPTER_PATH.read_text(encoding="gbk"))
    imports = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    assert imports <= {"math"}


def test_qmt_adapter_exposes_qmt_entrypoints_and_normalizes_symbols() -> None:
    adapter = _load_adapter()

    assert callable(adapter.init)
    assert callable(adapter.handlebar)
    assert adapter.normalize_symbol("SH600519") == "600519.SH"
    assert adapter.normalize_symbol("000001") == "000001.SZ"


def test_qmt_adapter_parses_common_qmt_market_data_shapes() -> None:
    adapter = _load_adapter()

    nested = {
        "600001.SH": {
            "open": [10.0, 10.2],
            "close": [10.1, 10.3],
            "high": [10.4, 10.5],
            "low": [9.9, 10.0],
            "volume": [1000, 1200],
            "amount": [10100, 12360],
        }
    }
    flat = {
        "open": [10.0],
        "close": [10.1],
        "high": [10.2],
        "low": [9.9],
        "volume": [1000],
        "amount": [10100],
    }

    assert adapter.parse_market_data(nested, "600001.SH", adapter.QMT_FIELDS)["close"] == [10.1, 10.3]
    assert adapter.parse_market_data(flat, "600001.SH", adapter.QMT_FIELDS)["high"] == [10.2]


def test_qmt_adapter_uses_same_limited_stock_pool_inputs_as_reference_script() -> None:
    adapter = _load_adapter()

    assert adapter.SELF_SELECT_SECTORS == [
        "我的自选",
        "自选",
        "自选股",
        "自选一",
        "全部自选股",
        "中证1000",
        "中证1000成份股",
        "中证1000成分股",
        "中证1000指数",
        "000852",
        "000852.SH",
    ]
    assert adapter.FALLBACK_STOCKS == [
        "300497.SZ",
        "601969.SH",
        "002797.SZ",
        "605369.SH",
        "002597.SZ",
        "002250.SZ",
        "603309.SH",
        "003520.SZ",
        "300636.SZ",
        "000739.SZ",
        "688488.SH",
        "002940.SZ",
        "003020.SZ",
        "300759.SZ",
        "603276.SH",
    ]


def test_qmt_adapter_falls_back_when_qmt_sector_pool_is_too_small() -> None:
    adapter = _load_adapter()

    class FakeContext:
        def get_stock_list_in_sector(self, sector_name):
            if sector_name == "我的自选":
                return ["600001.SH", "000001.SZ", "600001.SH"]
            return []

        def get_sector(self, sector_name):
            return []

    pool = adapter.get_stock_pool(FakeContext())

    assert pool[:2] == ["600001.SH", "000001.SZ"]
    assert "300497.SZ" in pool
    assert len(pool) == 17
