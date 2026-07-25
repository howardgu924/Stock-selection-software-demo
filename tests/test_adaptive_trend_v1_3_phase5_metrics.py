from decimal import Decimal

import pytest

from stock_picker.strategies.adaptive_trend_v1_3 import (
    Phase5Error, build_benchmark_curve, calculate_performance_metrics,
)


def daily(values,exposures=None):
    exposures=exposures or [Decimal("0.5")]*len(values)
    return [{"equity":value,"exposure":exposure} for value,exposure in zip(values,exposures)]


@pytest.mark.parametrize(
    ("values","expected"),
    [
        ([100,110],Decimal("0.1")),([100,90],Decimal("-0.1")),
        ([100,100],Decimal("0")),([100,120,90],Decimal("-0.1")),
    ],
)
def test_total_return(values,expected):
    assert calculate_performance_metrics(daily(values)).total_return==expected


def test_drawdown_and_exposure_formulas():
    metrics=calculate_performance_metrics(daily([100,120,90],[Decimal("0.2"),Decimal("0.8"),Decimal("0.5")]))
    assert metrics.max_drawdown==Decimal("-0.25")
    assert metrics.average_exposure==Decimal("0.5")
    assert metrics.max_exposure==Decimal("0.8")


def test_volatility_uses_sample_ddof_and_zero_sharpe_none():
    flat=calculate_performance_metrics(daily([100,100,100]))
    assert flat.annualized_volatility==Decimal("0.0")
    assert flat.sharpe is None


def test_turnover_fees_winrate_and_profit_factor():
    fills=[
        {"side":"BUY","gross_amount":"100","total_fees":"1"},
        {"side":"SELL","gross_amount":"110","total_fees":"2","realized_pnl_delta":"10"},
        {"side":"SELL","gross_amount":"90","total_fees":"2","realized_pnl_delta":"-5"},
        {"side":"SELL","gross_amount":"20","total_fees":"1","realized_pnl_delta":"0"},
    ]
    metrics=calculate_performance_metrics(daily([100,110]),fills)
    assert metrics.buy_count==1 and metrics.sell_count==3
    assert metrics.total_fees==Decimal("6")
    assert metrics.realized_win_rate==Decimal("0.5")
    assert metrics.profit_factor==Decimal("2")


def test_open_positions_do_not_enter_realized_stats():
    metrics=calculate_performance_metrics(daily([100,110]),[{"side":"BUY","gross_amount":"50","total_fees":"1","unrealized_pnl":"10"}])
    assert metrics.realized_win_rate is None


@pytest.mark.parametrize("invalid",[[0,1],[-1,1],["NaN",1],["Infinity",1]])
def test_invalid_equity_rejected(invalid):
    with pytest.raises(Phase5Error):
        calculate_performance_metrics(daily(invalid))


def test_benchmark_40_40_20():
    dates=["2025-01-01","2025-01-02"]
    data={"000300.SH":{dates[0]:100,dates[1]:110},"000852.SH":{dates[0]:200,dates[1]:220},"399006.SZ":{dates[0]:50,dates[1]:55}}
    curve,stale=build_benchmark_curve(dates,data)
    assert curve[-1]["benchmark_value"]==Decimal("1.10")
    assert stale==()


def test_benchmark_stale_forward_fill_is_reported():
    dates=["2025-01-01","2025-01-02"]
    data={"000300.SH":{dates[0]:100},"000852.SH":{dates[0]:200,dates[1]:220},"399006.SZ":{dates[0]:50,dates[1]:55}}
    _,stale=build_benchmark_curve(dates,data)
    assert stale==("000300.SH:2025-01-02",)


def test_benchmark_start_missing_is_invalid():
    dates=["2025-01-01","2025-01-02"]
    data={"000300.SH":{dates[1]:100},"000852.SH":{dates[0]:200},"399006.SZ":{dates[0]:50}}
    with pytest.raises(Phase5Error) as caught:
        build_benchmark_curve(dates,data)
    assert caught.value.code=="DATA_NOT_READY"
