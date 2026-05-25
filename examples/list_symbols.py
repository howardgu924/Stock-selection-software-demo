from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stock_picker.data import MarketDataService


def main() -> None:
    parser = argparse.ArgumentParser(description="List all A-share stock codes.")
    parser.add_argument("--refresh", action="store_true", help="Force provider fetch")
    parser.add_argument("--debug", action="store_true", help="Show full Python traceback")
    args = parser.parse_args()

    try:
        service = MarketDataService()
        symbols = service.get_stock_symbols(refresh=args.refresh)
    except Exception as exc:
        if args.debug:
            raise
        print(f"Error: {exc}", file=sys.stderr)
        print("Run again with --debug to show the full Python traceback.", file=sys.stderr)
        sys.exit(1)

    frame = pd.DataFrame(
        [
            {"symbol": item.symbol, "code": item.code, "name": item.name}
            for item in symbols
        ]
    )
    print(frame.head(50).to_string(index=False))
    print(f"\nrows={len(frame)}")


if __name__ == "__main__":
    main()
