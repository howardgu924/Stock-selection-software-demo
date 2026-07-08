from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stock_picker.data import DataSourceConfig, MarketDataService
from stock_picker.execution import build_execution_plan


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a manual execution plan from thermostat signals.")
    parser.add_argument("--signals", required=True, help="CSV exported from thermostat candidate or advice results")
    parser.add_argument("--cash", type=float, required=True, help="Available cash")
    parser.add_argument("--lot-size", type=int, default=100, help="A-share lot size")
    parser.add_argument("--max-positions", type=int, default=1, help="Cash allocation slots")
    parser.add_argument("--commission-rate", type=float, default=0.0003)
    parser.add_argument("--min-commission", type=float, default=5.0)
    parser.add_argument(
        "--next-day-premium",
        type=float,
        default=0.02,
        help="Maximum next-day premium over today's limit-up price",
    )
    parser.add_argument(
        "--volume-limit-pct",
        type=float,
        default=0.10,
        help="Maximum suggested shares as a fraction of quoted volume",
    )
    parser.add_argument(
        "--realtime-source",
        choices=["sina", "akshare"],
        default="sina",
        help="Realtime quote provider",
    )
    parser.add_argument("--top", type=int, help="Print/export only first N rows")
    parser.add_argument("--output", help="Optional CSV path for execution plan")
    parser.add_argument("--debug", action="store_true", help="Show full traceback")
    args = parser.parse_args()

    try:
        if args.cash <= 0:
            parser.error("--cash must be greater than 0")
        signals = pd.read_csv(args.signals)
        symbols = signals["symbol"].dropna().astype(str).tolist()
        service = MarketDataService(
            data_source_config=DataSourceConfig(realtime_source=args.realtime_source)
        )
        quotes = service.get_realtime_quotes(symbols)
        plan = build_execution_plan(
            signals,
            quotes,
            cash=args.cash,
            lot_size=args.lot_size,
            max_positions=args.max_positions,
            commission_rate=args.commission_rate,
            min_commission=args.min_commission,
            next_day_premium=args.next_day_premium,
            volume_limit_pct=args.volume_limit_pct,
        )
        output = plan.head(args.top) if args.top else plan
        if output.empty:
            print("No buy signals to plan.")
        else:
            print(output.to_string(index=False))
        if args.output:
            path = Path(args.output)
            path.parent.mkdir(parents=True, exist_ok=True)
            plan.to_csv(path, index=False)
            print(f"plan_csv={path}")
    except Exception as exc:
        if args.debug:
            raise
        print(f"Error: {exc}", file=sys.stderr)
        print("Run again with --debug to show the full Python traceback.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
