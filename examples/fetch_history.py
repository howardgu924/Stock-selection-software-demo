from __future__ import annotations

import argparse

from stock_picker.data import MarketDataService


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch A-share historical data.")
    parser.add_argument("--symbol", required=True, help="Stock code, e.g. 600519")
    parser.add_argument("--start", required=True, help="Start date, e.g. 20240101")
    parser.add_argument("--end", required=True, help="End date, e.g. 20240501")
    parser.add_argument("--refresh", action="store_true", help="Force provider fetch")
    args = parser.parse_args()

    service = MarketDataService()
    frame = service.get_history(
        symbol=args.symbol,
        start_date=args.start,
        end_date=args.end,
        refresh=args.refresh,
    )
    print(frame.tail(10).to_string(index=False))
    print(f"\nrows={len(frame)}")


if __name__ == "__main__":
    main()
