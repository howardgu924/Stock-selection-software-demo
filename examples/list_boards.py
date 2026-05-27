from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stock_picker.data import MarketDataService


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch industry or concept boards.")
    parser.add_argument(
        "--type",
        required=True,
        choices=["industry", "concept"],
        help="Board type",
    )
    parser.add_argument(
        "--members",
        help="Board name or BK code; print board members instead of board list",
    )
    parser.add_argument(
        "--minutes",
        help="Board name or BK code; print board minute data instead of board list",
    )
    parser.add_argument(
        "--period",
        default="5",
        choices=["1", "5", "15", "30", "60"],
        help="Minute period when using --minutes",
    )
    parser.add_argument("--debug", action="store_true", help="Show full Python traceback")
    args = parser.parse_args()

    try:
        service = MarketDataService()
        if args.members:
            frame = service.get_board_members(args.type, args.members)
        elif args.minutes:
            frame = service.get_board_minute_history(args.type, args.minutes, args.period)
        else:
            frame = service.get_boards(args.type)
    except Exception as exc:
        if args.debug:
            raise
        print(f"Error: {exc}", file=sys.stderr)
        print("Run again with --debug to show the full Python traceback.", file=sys.stderr)
        sys.exit(1)

    print(frame.head(50).to_string(index=False))
    print(f"\nrows={len(frame)}")


if __name__ == "__main__":
    main()
