from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stock_picker.data import DataSourceConfig, MarketDataService
from stock_picker.data.models import StockInfo
from stock_picker.screening import DEFAULT_SCREENING_RULES, SCREENING_RULES, screen_stocks


def main() -> None:
    parser = argparse.ArgumentParser(description="Screen A-share stocks with technical rules.")
    symbol_group = parser.add_mutually_exclusive_group()
    symbol_group.add_argument("--symbol", help="Single stock code, e.g. 600519")
    symbol_group.add_argument(
        "--symbols",
        help="Comma separated stock codes, e.g. 600519,000001",
    )
    symbol_group.add_argument(
        "--all",
        action="store_true",
        help="Screen all A-share stocks from the selected stock list source",
    )
    parser.add_argument("--start", help="Start date, e.g. 20240101")
    parser.add_argument("--end", help="End date, e.g. 20240501")
    parser.add_argument(
        "--rules",
        default=",".join(DEFAULT_SCREENING_RULES),
        help=f"Comma separated rules. Available: {', '.join(sorted(SCREENING_RULES))}",
    )
    parser.add_argument(
        "--source",
        choices=["baostock", "akshare", "joinquant"],
        help="History data source. Defaults to the current BaoStock workflow.",
    )
    parser.add_argument(
        "--stock-source",
        choices=["akshare", "baostock", "joinquant"],
        help="Stock list source for --all. Defaults to the current AkShare workflow.",
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
    parser.add_argument("--limit", type=int, help="Screen only the first N symbols")
    parser.add_argument("--top", type=int, default=50, help="Print only the first N matches")
    parser.add_argument(
        "--sort-by",
        default="score",
        choices=["score", "pct_chg", "amount", "volume"],
        help="Sort matches by this column before fallback sort keys",
    )
    parser.add_argument("--output", help="Optional CSV path for matched stocks")
    parser.add_argument(
        "--error-log",
        default="data/screen_errors.csv",
        help="CSV path for failed stock screening rows",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop at the first failed stock instead of logging and continuing",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=50,
        help="Print screening progress every N stocks",
    )
    parser.add_argument("--list-rules", action="store_true", help="List available rules and exit")
    parser.add_argument("--debug", action="store_true", help="Show full Python traceback")
    args = parser.parse_args()

    if args.list_rules:
        for rule in sorted(SCREENING_RULES):
            print(rule)
        return
    _validate_required_args(parser, args)

    try:
        service = MarketDataService(
            data_source_config=_data_source_config(
                history_source=args.source,
                stock_source=args.stock_source,
                history_fallbacks=args.fallback,
            )
        )
        symbols = _resolve_symbols(service, args)
        selected_rules = [item.strip() for item in args.rules.split(",") if item.strip()]
        result = screen_stocks(
            service=service,
            symbols=symbols,
            start_date=args.start,
            end_date=args.end,
            rules=selected_rules,
            refresh=args.refresh,
            skip_errors=not args.stop_on_error,
            progress_callback=_progress_printer(args.progress_every),
            sort_by=args.sort_by,
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
    history_fallbacks: list[str],
) -> DataSourceConfig | None:
    if not history_source and not stock_source and not history_fallbacks:
        return None
    return DataSourceConfig(
        history_source=history_source,
        stock_source=stock_source,
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


def _validate_required_args(parser: argparse.ArgumentParser, args) -> None:
    selected = [bool(args.symbol), bool(args.symbols), bool(args.all)]
    if sum(selected) != 1:
        parser.error("one of --symbol, --symbols, or --all is required")
    if not args.start:
        parser.error("--start is required")
    if not args.end:
        parser.error("--end is required")

def _write_outputs(result, args) -> None:
    output = result.results.head(args.top)
    if output.empty:
        print("No matching stocks.")
    else:
        print(output.to_string(index=False))
    print(f"\nmatches={len(result.results)} errors={len(result.errors)}")

    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        result.results.to_csv(path, index=False)
        print(f"matches_csv={path}")

    if not result.errors.empty:
        error_path = Path(args.error_log)
        error_path.parent.mkdir(parents=True, exist_ok=True)
        result.errors.to_csv(error_path, index=False)
        print(f"errors_csv={error_path}", file=sys.stderr)


def _progress_printer(every: int):
    every = max(every, 1)

    def print_progress(index: int, total: int, symbol: str, status: str, error: str) -> None:
        if status == "failed" or index == 1 or index == total or index % every == 0:
            message = f"[{index}/{total}] {symbol} {status}"
            if error:
                message = f"{message} error={error}"
            print(message, flush=True)

    return print_progress


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
