from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stock_picker.data import MarketDataService


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch A-share historical data.")
    symbol_group = parser.add_mutually_exclusive_group(required=True)
    symbol_group.add_argument("--symbol", help="Single stock code, e.g. 600519")
    symbol_group.add_argument(
        "--symbols",
        help="Comma separated stock codes, e.g. 600519,000001",
    )
    symbol_group.add_argument(
        "--all",
        action="store_true",
        help="Update all A-share stocks from the stock symbol list",
    )
    parser.add_argument("--start", required=True, help="Start date, e.g. 20240101")
    parser.add_argument("--end", required=True, help="End date, e.g. 20240501")
    parser.add_argument("--refresh", action="store_true", help="Force provider fetch")
    parser.add_argument(
        "--refresh-symbols",
        action="store_true",
        help="Refresh the all-A-share symbol list before --all",
    )
    parser.add_argument(
        "--error-log",
        default="data/history_errors.csv",
        help="CSV path for failed stock updates",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Stop at the first failed stock instead of logging and continuing",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Update only the first N stocks when using --all",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=50,
        help="Print batch progress every N stocks",
    )
    parser.add_argument("--debug", action="store_true", help="Show full Python traceback")
    args = parser.parse_args()

    try:
        service = MarketDataService()
        progress_callback = _progress_printer(args.progress_every)
        if args.all:
            frame = service.update_all_history(
                start_date=args.start,
                end_date=args.end,
                refresh=args.refresh,
                refresh_symbols=args.refresh_symbols,
                skip_errors=not args.stop_on_error,
                error_log_path=args.error_log,
                limit=args.limit,
                progress_callback=progress_callback,
            )
        elif args.symbols:
            symbols = [item.strip() for item in args.symbols.split(",") if item.strip()]
            if not symbols:
                parser.error("--symbols must contain at least one stock code")
            frame = service.update_history(
                symbols=symbols,
                start_date=args.start,
                end_date=args.end,
                refresh=args.refresh,
                skip_errors=not args.stop_on_error,
                error_log_path=args.error_log,
                progress_callback=progress_callback,
            )
        else:
            frame = service.get_history(
                symbol=args.symbol,
                start_date=args.start,
                end_date=args.end,
                refresh=args.refresh,
            )
    except Exception as exc:
        if args.debug:
            raise
        print(f"Error: {exc}", file=sys.stderr)
        print("Run again with --debug to show the full Python traceback.", file=sys.stderr)
        sys.exit(1)

    if args.symbol:
        print(frame.tail(10).to_string(index=False))
    else:
        print(frame.to_string(index=False))
    print(f"\nrows={len(frame)}")


def _progress_printer(every: int):
    every = max(every, 1)

    def print_progress(
        index: int,
        total: int,
        symbol: str,
        status: str,
        rows: int,
        error: str,
    ) -> None:
        if status == "failed" or index == 1 or index == total or index % every == 0:
            message = f"[{index}/{total}] {symbol} {status} rows={rows}"
            if error:
                message = f"{message} error={error}"
            print(message, flush=True)

    return print_progress


if __name__ == "__main__":
    main()
