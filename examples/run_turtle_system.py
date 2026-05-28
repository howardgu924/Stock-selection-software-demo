from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stock_picker.data import DataSourceConfig, MarketDataService
from stock_picker.data.models import StockInfo
from stock_picker.execution import build_execution_plan
from stock_picker.strategies import TurtleConfig, run_turtle_system


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the full long-only A-share turtle system.")
    symbol_group = parser.add_mutually_exclusive_group(required=True)
    symbol_group.add_argument("--symbol", help="Single stock code, e.g. 600519")
    symbol_group.add_argument("--symbols", help="Comma separated stock codes")
    symbol_group.add_argument("--symbols-file", help="One stock code per line")
    symbol_group.add_argument("--all", action="store_true", help="Use all A-share symbols")
    parser.add_argument("--start", help="Warmup/start date, e.g. 20250101")
    parser.add_argument("--end", help="End date, e.g. 20260528")
    parser.add_argument("--as-of", help="As-of date. Used as --end when --end is omitted")
    parser.add_argument("--cash", type=float, required=True, help="Available cash/account equity")
    parser.add_argument("--risk-pct", type=float, default=0.01)
    parser.add_argument("--max-units", type=int, default=4)
    parser.add_argument("--lot-size", type=int, default=100)
    parser.add_argument("--slippage-rate", type=float, default=0.0)
    parser.add_argument("--commission-rate", type=float, default=0.0003)
    parser.add_argument("--min-commission", type=float, default=5.0)
    parser.add_argument("--next-day-premium", type=float, default=0.02)
    parser.add_argument("--volume-limit-pct", type=float, default=0.10)
    parser.add_argument("--top", type=int, help="Print/export only the first N signals")
    parser.add_argument("--source", choices=["baostock", "akshare", "joinquant"], help="History source")
    parser.add_argument("--stock-source", choices=["akshare", "baostock", "joinquant"], help="Stock list source")
    parser.add_argument("--realtime-source", choices=["sina", "akshare"], default="sina")
    parser.add_argument("--fallback", action="append", choices=["baostock", "akshare", "joinquant"], default=[])
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--refresh-symbols", action="store_true")
    parser.add_argument("--limit", type=int, help="Use only first N symbols with --all")
    parser.add_argument("--signals-output", help="Optional signal CSV path")
    parser.add_argument("--plan-output", help="Optional execution plan CSV path")
    parser.add_argument("--error-log", default="data/turtle_system_errors.csv")
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    try:
        if args.cash <= 0:
            parser.error("--cash must be greater than 0")
        end = args.end or args.as_of
        if not end:
            parser.error("one of --end or --as-of is required")
        start = args.start or (pd.to_datetime(end) - pd.Timedelta(days=180)).strftime("%Y%m%d")
        config = TurtleConfig(
            risk_pct=args.risk_pct,
            max_units=args.max_units,
            lot_size=args.lot_size,
            slippage_rate=args.slippage_rate,
            commission_rate=args.commission_rate,
            min_commission=args.min_commission,
        )
        service = MarketDataService(
            data_source_config=_data_source_config(
                history_source=args.source,
                stock_source=args.stock_source,
                realtime_source=args.realtime_source,
                history_fallbacks=args.fallback,
            )
        )
        symbols = _resolve_symbols(service, args)
        result = run_turtle_system(
            service=service,
            symbols=symbols,
            start_date=start,
            end_date=end,
            cash=args.cash,
            config=config,
            refresh=args.refresh,
            skip_errors=not args.stop_on_error,
        )
        signals = result.signals.head(args.top) if args.top else result.signals
        if signals.empty:
            print("No turtle buy signals.")
        else:
            print("Signals:")
            print(signals.to_string(index=False))
        print(f"\nsignals={len(result.signals)} errors={len(result.errors)}")

        _write_csv(result.signals, args.signals_output, "signals_csv")
        if not result.signals.empty:
            plan = _build_plan(service, result.signals, args)
            if plan is not None:
                output = plan.head(args.top) if args.top else plan
                print("\nExecution plan:")
                print(output.to_string(index=False))
                _write_csv(plan, args.plan_output, "plan_csv")
        if not result.errors.empty:
            _write_csv(result.errors, args.error_log, "errors_csv", stream=sys.stderr)
    except Exception as exc:
        if args.debug:
            raise
        print(f"Error: {exc}", file=sys.stderr)
        print("Run again with --debug to show the full Python traceback.", file=sys.stderr)
        sys.exit(1)


def _data_source_config(
    history_source: str | None,
    stock_source: str | None,
    realtime_source: str | None,
    history_fallbacks: list[str],
) -> DataSourceConfig | None:
    if not history_source and not stock_source and not realtime_source and not history_fallbacks:
        return None
    return DataSourceConfig(
        history_source=history_source,
        stock_source=stock_source,
        realtime_source=realtime_source,
        fallback_sources={"history": history_fallbacks},
    )


def _resolve_symbols(service: MarketDataService, args) -> list[str | StockInfo]:
    if args.all:
        symbols = service.get_stock_symbols(refresh=args.refresh_symbols)
        return symbols[: args.limit] if args.limit else symbols
    if args.symbols_file:
        values = [
            line.strip()
            for line in Path(args.symbols_file).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not values:
            raise ValueError("--symbols-file is empty")
        return values
    if args.symbols:
        values = [item.strip() for item in args.symbols.split(",") if item.strip()]
        if not values:
            raise ValueError("--symbols must contain at least one stock code")
        return values
    return [args.symbol]


def _build_plan(service: MarketDataService, signals: pd.DataFrame, args) -> pd.DataFrame | None:
    try:
        quotes = service.get_realtime_quotes(signals["symbol"].dropna().astype(str).tolist())
        return build_execution_plan(
            signals,
            quotes,
            cash=args.cash,
            lot_size=args.lot_size,
            commission_rate=args.commission_rate,
            min_commission=args.min_commission,
            next_day_premium=args.next_day_premium,
            volume_limit_pct=args.volume_limit_pct,
        )
    except Exception as exc:
        print(f"Warning: execution plan skipped: {exc}", file=sys.stderr)
        return None


def _write_csv(frame: pd.DataFrame, path_value: str | None, label: str, stream=None) -> None:
    if not path_value:
        return
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    print(f"{label}={path}", file=stream or sys.stdout)


if __name__ == "__main__":
    main()
