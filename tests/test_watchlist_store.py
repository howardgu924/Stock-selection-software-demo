from __future__ import annotations

from stock_picker.user import ManualPortfolioStore, WatchlistStore


def test_watchlist_store_creates_lists_and_persists_empty_groups(tmp_path) -> None:
    store = WatchlistStore(tmp_path / "user")

    created = store.create("短线观察")
    reloaded = WatchlistStore(tmp_path / "user")

    assert created.name == "短线观察"
    assert created.symbols == []
    assert [(item.name, item.count) for item in reloaded.list()] == [("短线观察", 0)]


def test_watchlist_store_adds_deduplicates_and_removes_symbols(tmp_path) -> None:
    store = WatchlistStore(tmp_path / "user")
    store.create("短线观察")

    result = store.add_symbols("短线观察", ["600519", "000001", "600519"])
    after_remove = store.remove_symbol("短线观察", "000001")
    missing = store.remove_symbol("短线观察", "300001")

    assert result.symbols == ["600519.SH", "000001.SZ"]
    assert result.duplicates == ["600519.SH"]
    assert after_remove.symbols == ["600519.SH"]
    assert missing.symbols == ["600519.SH"]
    assert "不存在" in missing.message


def test_watchlist_store_splits_batch_input_and_rejects_unsupported_codes(tmp_path) -> None:
    store = WatchlistStore(tmp_path / "user")
    store.create("短线观察")

    result = store.add_symbols("短线观察", ["600519, 000001 300750", "516650"])
    saved = store.get("短线观察")

    assert saved is not None
    assert saved.symbols == ["600519.SH", "000001.SZ", "300750.SZ"]
    assert result.symbols == ["600519.SH", "000001.SZ", "300750.SZ"]
    assert result.invalid_symbols == ["516650"]
    assert "516650" in result.message


def test_watchlist_store_rejects_batch_remove_without_mutating(tmp_path) -> None:
    store = WatchlistStore(tmp_path / "user")
    store.create("短线观察")
    store.add_symbols("短线观察", ["600519", "000001"])

    result = store.remove_symbol("短线观察", "600519,000001")
    saved = store.get("短线观察")

    assert saved is not None
    assert saved.symbols == ["600519.SH", "000001.SZ"]
    assert result.symbols == ["600519.SH", "000001.SZ"]
    assert result.status == "invalid_symbol"
    assert "一次只能删除一只股票" in result.message


def test_watchlist_store_renames_deletes_and_rejects_duplicate_names(tmp_path) -> None:
    store = WatchlistStore(tmp_path / "user")
    store.create("短线观察")
    store.add_symbols("短线观察", ["600519"])
    store.create("高关注")

    renamed = store.rename("短线观察", "长期观察")
    duplicate = store.rename("长期观察", "高关注")
    store.delete("长期观察")

    assert renamed.name == "长期观察"
    assert renamed.symbols == ["600519.SH"]
    assert duplicate.status == "name_conflict"
    assert [item.name for item in store.list()] == ["高关注"]


def test_watchlist_changes_do_not_touch_portfolio_or_trades(tmp_path) -> None:
    user_path = tmp_path / "user"
    portfolio_store = ManualPortfolioStore(user_path)
    portfolio_store.initialize(100000.0)
    portfolio_store.buy("600519", price=100.0, shares=100, fees=0.0)
    before = portfolio_store.load()

    watchlists = WatchlistStore(user_path)
    watchlists.create("短线观察")
    watchlists.add_symbols("短线观察", ["600519", "000001"])
    watchlists.remove_symbol("短线观察", "600519")
    watchlists.delete("短线观察")
    after = portfolio_store.load()

    assert after.cash == before.cash
    assert after.positions.equals(before.positions)
    assert after.trades.equals(before.trades)


def test_last_manual_input_persists_until_clear_or_replace(tmp_path) -> None:
    store = WatchlistStore(tmp_path / "user")

    store.save_last_manual_input("600519,000001")
    assert WatchlistStore(tmp_path / "user").load_last_manual_input() == "600519,000001"

    store.save_last_manual_input("600036")
    assert WatchlistStore(tmp_path / "user").load_last_manual_input() == "600036"

    store.clear_last_manual_input()
    assert WatchlistStore(tmp_path / "user").load_last_manual_input() == ""
