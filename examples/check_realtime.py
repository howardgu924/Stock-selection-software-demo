from __future__ import annotations

import argparse

from stock_picker.data import MarketDataService


def main() -> None:
    parser = argparse.ArgumentParser(description="Check A-share realtime quotes.")
    parser.add_argument(
        "--symbols",
        default="",
        help="Comma separated stock codes, e.g. 600519,000001. Empty means all.",
    )
    args = parser.parse_args()

    symbols = [item.strip() for item in args.symbols.split(",") if item.strip()]
    service = MarketDataService()
    frame = service.get_realtime_quotes(symbols=symbols or None)
    print(frame.head(20).to_string(index=False))
    print(f"\nrows={len(frame)}")


if __name__ == "__main__":
    main()
