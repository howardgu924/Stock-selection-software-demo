from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stock_picker.data import DataSourceConfig, MarketDataService
from stock_picker.data.models import StockInfo
from stock_picker.strategies.backtest import BACKTEST_STRATEGY_NAMES, EXECUTION_TIMINGS
from stock_picker.strategies import backtest_strategy


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest history-price strategies.")
    parser.add_argument(
        "--strategy",
        choices=BACKTEST_STRATEGY_NAMES,
        required=True,
        help="Strategy to backtest",
    )
    symbol_group = parser.add_mutually_exclusive_group(required=True)
    symbol_group.add_argument("--symbol", help="Single stock code, e.g. 600519")
    symbol_group.add_argument(
        "--symbols",
        help="Comma separated stock codes, e.g. 600519,000001",
    )
    symbol_group.add_argument("--all", action="store_true", help="Backtest all A-share symbols")
    parser.add_argument("--start", required=True, help="Start date, e.g. 20250101")
    parser.add_argument("--end", required=True, help="End date, e.g. 20260527")
    parser.add_argument("--cash", type=float, default=100_000.0, help="Initial cash")
    parser.add_argument(
        "--commission-rate",
        type=float,
        default=0.0003,
        help="Buy and sell commission rate",
    )
    parser.add_argument(
        "--stamp-tax-rate",
        type=float,
        default=0.001,
        help="Sell stamp tax rate",
    )
    parser.add_argument(
        "--slippage-rate",
        type=float,
        default=0.0,
        help="One-way execution slippage rate, e.g. 0.002 for 0.2%",
    )
    parser.add_argument("--lot-size", type=int, default=100, help="A-share lot size")
    parser.add_argument(
        "--max-positions",
        type=int,
        default=5,
        help="Maximum simultaneous positions for multi-symbol backtests",
    )
    parser.add_argument(
        "--execution-timing",
        choices=EXECUTION_TIMINGS,
        default="next_open",
        help="Execution timing model",
    )
    parser.add_argument(
        "--minute-period",
        choices=["5", "15", "30", "60"],
        default="5",
        help="Minute period used by same_day_pm_open",
    )
    parser.add_argument(
        "--warmup-days",
        type=int,
        default=0,
        help="Calendar days before --start used only for indicator/channel warmup",
    )
    parser.add_argument(
        "--source",
        choices=["baostock", "akshare", "joinquant"],
        help="History data source. Defaults to the current BaoStock workflow.",
    )
    parser.add_argument(
        "--stock-source",
        choices=["akshare", "baostock", "joinquant"],
        help="Stock list source for --all",
    )
    parser.add_argument(
        "--minute-source",
        choices=["baostock", "akshare", "joinquant"],
        help="Minute data source for same_day_pm_open",
    )
    parser.add_argument(
        "--fallback",
        action="append",
        choices=["baostock", "akshare", "joinquant"],
        default=[],
        help="Explicit fallback history source. Can be provided more than once.",
    )
    parser.add_argument("--refresh", action="store_true", help="Force provider fetch")
    parser.add_argument("--refresh-symbols", action="store_true", help="Refresh symbols for --all")
    parser.add_argument("--limit", type=int, help="Use only the first N symbols with --all")
    parser.add_argument("--output", help="Optional CSV path for summary")
    parser.add_argument("--equity-output", help="Optional CSV path for daily equity")
    parser.add_argument("--trades-output", help="Optional CSV path for trades")
    parser.add_argument(
        "--error-log",
        default="data/backtest_errors.csv",
        help="CSV path for failed stock rows",
    )
    parser.add_argument("--stop-on-error", action="store_true", help="Stop at first failed stock")
    parser.add_argument("--debug", action="store_true", help="Show full Python traceback")
    args = parser.parse_args()

    try:
        if args.cash <= 0:
            parser.error("--cash must be greater than 0")
        if args.lot_size < 1:
            parser.error("--lot-size must be greater than 0")
        if args.max_positions < 1:
            parser.error("--max-positions must be greater than 0")
        if args.slippage_rate < 0:
            parser.error("--slippage-rate must be greater than or equal to 0")
        if args.warmup_days < 0:
            parser.error("--warmup-days must be greater than or equal to 0")

        service = MarketDataService(
            data_source_config=_data_source_config(
                history_source=args.source,
                stock_source=args.stock_source,
                minute_source=args.minute_source,
                history_fallbacks=args.fallback,
            )
        )
        symbols = _resolve_symbols(service, args)
        result = backtest_strategy(
            service=service,
            strategy=args.strategy,
            symbols=symbols,
            start_date=args.start,
            end_date=args.end,
            initial_cash=args.cash,
            commission_rate=args.commission_rate,
            stamp_tax_rate=args.stamp_tax_rate,
            slippage_rate=args.slippage_rate,
            lot_size=args.lot_size,
            max_positions=args.max_positions,
            execution_timing=args.execution_timing,
            minute_period=args.minute_period,
            warmup_days=args.warmup_days,
            refresh=args.refresh,
            skip_errors=not args.stop_on_error,
        )
        _print_source_result(service, "history")
        _write_outputs(result, args)
    except Exception as exc:
        if args.debug:
            raise
        print(f"Error: {exc}", file=sys.stderr)
        print("Run again with --debug to show the full Python traceback.", file=sys.stderr)
        sys.exit(1)


def _data_source_config(
    history_source: str | None,
    stock_source: str | None,
    minute_source: str | None,
    history_fallbacks: list[str],
) -> DataSourceConfig | None:
    if not history_source and not stock_source and not minute_source and not history_fallbacks:
        return None
    return DataSourceConfig(
        history_source=history_source,
        stock_source=stock_source,
        minute_source=minute_source,
        fallback_sources={"history": history_fallbacks},
    )


def _resolve_symbols(service: MarketDataService, args) -> list[str | StockInfo]:
    if args.all:
        symbols = service.get_stock_symbols(refresh=args.refresh_symbols)
        if args.limit is not None:
            symbols = symbols[: args.limit]
        return symbols
    if args.symbols:
        symbols = [item.strip() for item in args.symbols.split(",") if item.strip()]
        if not symbols:
            raise ValueError("--symbols must contain at least one stock code")
        return symbols
    return [args.symbol]


def _write_outputs(result, args) -> None:
    print(result.summary.to_string(index=False))
    print(f"\nequity_rows={len(result.equity)} trades={len(result.trades)} errors={len(result.errors)}")
    if not result.trades.empty:
        print("\nTrades:")
        print(result.trades.tail(20).to_string(index=False))

    _write_csv(result.summary, args.output, "summary_csv")
    _write_csv(result.equity, args.equity_output, "equity_csv")
    _write_csv(result.trades, args.trades_output, "trades_csv")
    if not result.errors.empty:
        _write_csv(result.errors, args.error_log, "errors_csv", stream=sys.stderr)


def _write_csv(frame, path_value, label: str, stream=None) -> None:
    if not path_value:
        return
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    print(f"{label}={path}", file=stream or sys.stdout)


def _print_source_result(service: MarketDataService, feature: str) -> None:
    result = service.last_source_results.get(feature)
    if result is None or result.fallback_from is None:
        return
    print(
        f"Warning: {feature} source {result.fallback_from} failed; "
        f"used fallback {result.source}. Errors: {'; '.join(result.fallback_errors)}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
