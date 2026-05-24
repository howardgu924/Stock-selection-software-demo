from stock_picker.data.models import normalize_symbol, symbol_code


def test_normalize_symbol() -> None:
    assert normalize_symbol("600519") == "600519.SH"
    assert normalize_symbol("000001") == "000001.SZ"
    assert normalize_symbol("300750") == "300750.SZ"
    assert normalize_symbol("600519.SH") == "600519.SH"


def test_symbol_code() -> None:
    assert symbol_code("600519.SH") == "600519"
    assert symbol_code("000001") == "000001"
