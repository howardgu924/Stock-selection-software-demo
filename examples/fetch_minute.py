from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stock_picker.data import DataSourceConfig, MarketDataService


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch A-share minute data.")
    parser.add_argument("--symbol", required=True, help="Stock code, e.g. 600519")
    parser.add_argument(
        "--start",
        required=True,
        help='Start datetime, e.g. "2024-05-01 09:30:00"',
    )
    parser.add_argument(
        "--end",
        required=True,
        help='End datetime, e.g. "2024-05-01 15:00:00"',
    )
    parser.add_argument(
        "--period",
        default="5",
        choices=["1", "5", "15", "30", "60"],
        help="Minute period",
    )
    parser.add_argument(
        "--adjust",
        default="",
        choices=["", "qfq", "hfq"],
        help="Adjustment for stock minute data",
    )
    parser.add_argument(
        "--source",
        choices=["baostock", "akshare", "joinquant"],
        help="Minute data source. Defaults to the current provider workflow.",
    )
    parser.add_argument(
        "--fallback",
        action="append",
        choices=["baostock", "akshare", "joinquant"],
        default=[],
        help="Explicit fallback minute data source. Can be provided more than once.",
    )
    parser.add_argument("--debug", action="store_true", help="Show full Python traceback")
    args = parser.parse_args()

    try:
        service = MarketDataService(
            data_source_config=_data_source_config(args.source, args.fallback)
        )
        frame = service.get_minute_history(
            symbol=args.symbol,
            start_datetime=args.start,
            end_datetime=args.end,
            period=args.period,
            adjust=args.adjust,
        )
        _print_source_result(service, "minute")
    except Exception as exc:
        if args.debug:
            raise
        print(f"Error: {exc}", file=sys.stderr)
        print("Run again with --debug to show the full Python traceback.", file=sys.stderr)
        sys.exit(1)

    print(frame.tail(20).to_string(index=False))
    print(f"\nrows={len(frame)}")


def _data_source_config(source: str | None, fallbacks: list[str]) -> DataSourceConfig | None:
    if not source and not fallbacks:
        return None
    return DataSourceConfig(
        minute_source=source,
        fallback_sources={"minute": fallbacks},
    )


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
