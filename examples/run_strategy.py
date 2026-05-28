from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stock_picker.data import DataSourceConfig, MarketDataService
from stock_picker.data.models import StockInfo
from stock_picker.strategies import STRATEGY_NAMES, run_strategy


def main() -> None:
    parser = argparse.ArgumentParser(description="Run stock selection strategies.")
    parser.add_argument(
        "--strategy",
        choices=STRATEGY_NAMES,
        required=True,
        help="Strategy to run",
    )
    symbol_group = parser.add_mutually_exclusive_group()
    symbol_group.add_argument("--symbol", help="Single stock code, e.g. 600519")
    symbol_group.add_argument(
        "--symbols",
        help="Comma separated stock codes, e.g. 600519,000001",
    )
    symbol_group.add_argument(
        "--all",
        action="store_true",
        help="Use all A-share symbols for history-based strategies",
    )
    parser.add_argument("--start", help="Start date, e.g. 20250527")
    parser.add_argument("--end", help="End date, e.g. 20260527")
    parser.add_argument("--as-of", help="Snapshot date label, e.g. 20260527")
    parser.add_argument("--top", type=int, help="Print/export only the first N rows")
    parser.add_argument(
        "--source",
        choices=["baostock", "akshare", "joinquant"],
        help="History data source for history-based strategies",
    )
    parser.add_argument(
        "--stock-source",
        choices=["akshare", "baostock", "joinquant"],
        help="Stock list source for --all",
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
    parser.add_argument("--output", help="Optional CSV path for strategy results")
    parser.add_argument(
        "--error-log",
        default="data/strategy_errors.csv",
        help="CSV path for failed strategy rows",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop at the first failed stock instead of logging and continuing",
    )
    parser.add_argument("--debug", action="store_true", help="Show full Python traceback")
    args = parser.parse_args()

    try:
        _validate_args(parser, args)
        service = MarketDataService(
            data_source_config=_data_source_config(
                history_source=args.source,
                stock_source=args.stock_source,
                history_fallbacks=args.fallback,
            )
        )
        symbols = _resolve_symbols(service, args)
        result = run_strategy(
            service=service,
            strategy=args.strategy,
            start_date=args.start,
            end_date=args.end,
            as_of=args.as_of,
            symbols=symbols,
            top=args.top,
            refresh=args.refresh,
            skip_errors=not args.stop_on_error,
        )
        _print_source_result(service, "history")
        _print_source_result(service, "market")
        _write_outputs(result, args)
    except Exception as exc:
        if args.debug:
            raise
        print(f"Error: {exc}", file=sys.stderr)
        print("Run again with --debug to show the full Python traceback.", file=sys.stderr)
        sys.exit(1)


def _validate_args(parser: argparse.ArgumentParser, args) -> None:
    history_based = args.strategy in {"ma_cross", "turtle"}
    if history_based:
        selected = [bool(args.symbol), bool(args.symbols), bool(args.all)]
        if sum(selected) != 1:
            parser.error("ma_cross and turtle require one of --symbol, --symbols, or --all")
        if not args.start:
            parser.error("--start is required for ma_cross and turtle")
        if not args.end:
            parser.error("--end is required for ma_cross and turtle")
    if args.strategy in {"small_cap", "undervalued"}:
        selected = [bool(args.symbol), bool(args.symbols), bool(args.all)]
        if sum(selected) != 1:
            parser.error(
                f"{args.strategy} requires one of --symbol, --symbols, or --all"
            )
    if args.top is not None and args.top < 1:
        parser.error("--top must be greater than 0")


def _data_source_config(
    history_source: str | None,
    stock_source: str | None,
    history_fallbacks: list[str],
) -> DataSourceConfig | None:
    if not history_source and not stock_source and not history_fallbacks:
        return None
    return DataSourceConfig(
        history_source=history_source,
        stock_source=stock_source,
        fallback_sources={"history": history_fallbacks},
    )


def _resolve_symbols(service: MarketDataService, args) -> list[str | StockInfo] | None:
    if not args.strategy in {
        "ma_cross",
        "turtle",
        "small_cap",
        "undervalued",
        "bank_rotation",
    }:
        return None
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
    output = result.results
    if args.top is not None:
        output = output.head(args.top)
    if output.empty:
        print("No matching strategy rows.")
    else:
        print(output.to_string(index=False))
    print(f"\nrows={len(result.results)} errors={len(result.errors)}")

    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        result.results.to_csv(path, index=False)
        print(f"results_csv={path}")

    if not result.errors.empty:
        error_path = Path(args.error_log)
        error_path.parent.mkdir(parents=True, exist_ok=True)
        result.errors.to_csv(error_path, index=False)
        print(f"errors_csv={error_path}", file=sys.stderr)


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
