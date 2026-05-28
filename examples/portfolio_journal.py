from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stock_picker.user import ManualPortfolioStore
from stock_picker.data.models import normalize_symbol


def main() -> None:
    parser = argparse.ArgumentParser(description="Manual portfolio and trade journal.")
    parser.add_argument("--path", default="data/user/default", help="Portfolio storage path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="Initialize account")
    init.add_argument("--principal", type=float, required=True)
    init.add_argument("--cash", type=float)
    init.add_argument("--commission-rate", type=float, default=0.0003)
    init.add_argument("--min-commission", type=float, default=5.0)
    init.add_argument("--stamp-tax-rate", type=float, default=0.001)

    buy = subparsers.add_parser("buy", help="Record a manual buy")
    _trade_args(buy)
    buy.add_argument("--name", default="")
    buy.add_argument("--target-sell-price", type=float)
    buy.add_argument("--from-plan", help="Optional execution plan CSV to prefill metadata")

    sell = subparsers.add_parser("sell", help="Record a manual sell")
    _trade_args(sell)
    sell.add_argument("--tax-rate", type=float)
    sell.add_argument("--exit-reason", default="")

    subparsers.add_parser("positions", help="Print positions")
    subparsers.add_parser("trades", help="Print trade records")

    summary = subparsers.add_parser("summary", help="Print account summary")
    summary.add_argument(
        "--mark",
        action="append",
        default=[],
        help="Mark price as SYMBOL=PRICE. Can be repeated.",
    )

    args = parser.parse_args()
    store = ManualPortfolioStore(args.path)
    try:
        if args.command == "init":
            portfolio = store.initialize(
                args.principal,
                cash=args.cash,
                commission_rate=args.commission_rate,
                min_commission=args.min_commission,
                stamp_tax_rate=args.stamp_tax_rate,
            )
            print_summary(portfolio.summary())
            return
        if args.command == "buy":
            plan = _plan_row(args.from_plan, args.symbol) if args.from_plan else {}
            if args.price is None and "suggested_price" not in plan:
                raise ValueError("--price is required unless --from-plan provides suggested_price")
            if args.shares is None and "suggested_shares" not in plan:
                raise ValueError("--shares is required unless --from-plan provides suggested_shares")
            portfolio = store.buy(
                args.symbol,
                price=args.price if args.price is not None else float(plan.get("suggested_price")),
                shares=args.shares if args.shares is not None else int(plan.get("suggested_shares")),
                name=args.name or str(plan.get("name") or ""),
                fees=args.fees,
                target_sell_price=args.target_sell_price,
                timestamp=args.timestamp,
                strategy=args.strategy or str(plan.get("strategy") or ""),
                system=args.system or str(plan.get("system") or ""),
                entry_reason=args.entry_reason or str(plan.get("reason") or ""),
                signal_date=args.signal_date or str(plan.get("signal_date") or "") or None,
                execution_date=args.execution_date,
                note=args.note,
            )
            print_summary(portfolio.summary())
            return
        if args.command == "sell":
            if args.price is None or args.shares is None:
                raise ValueError("--price and --shares are required for sell")
            portfolio = store.sell(
                args.symbol,
                price=args.price,
                shares=args.shares,
                fees=args.fees,
                tax_rate=args.tax_rate,
                timestamp=args.timestamp,
                strategy=args.strategy,
                system=args.system,
                exit_reason=args.exit_reason,
                signal_date=args.signal_date,
                execution_date=args.execution_date,
                note=args.note,
            )
            print_summary(portfolio.summary())
            return

        portfolio = store.load()
        if args.command == "positions":
            print(portfolio.positions.to_string(index=False))
        elif args.command == "trades":
            print(portfolio.trades.to_string(index=False))
        elif args.command == "summary":
            print_summary(portfolio.summary(_parse_marks(args.mark)))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _trade_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--price", type=float)
    parser.add_argument("--shares", type=int)
    parser.add_argument("--fees", type=float)
    parser.add_argument("--timestamp")
    parser.add_argument("--strategy", default="")
    parser.add_argument("--system", default="")
    parser.add_argument("--entry-reason", default="")
    parser.add_argument("--signal-date")
    parser.add_argument("--execution-date")
    parser.add_argument("--note", default="")


def _plan_row(path_value: str, symbol: str) -> dict[str, object]:
    import pandas as pd

    frame = pd.read_csv(path_value)
    if frame.empty:
        raise ValueError(f"empty execution plan: {path_value}")
    normalized = normalize_symbol(symbol).upper()
    rows = frame[frame["symbol"].astype(str).str.upper() == normalized]
    if rows.empty:
        raise ValueError(f"symbol {symbol} not found in execution plan")
    return rows.iloc[0].to_dict()


def _parse_marks(values: list[str]) -> dict[str, float]:
    marks: dict[str, float] = {}
    for item in values:
        if "=" not in item:
            raise ValueError(f"invalid mark: {item}")
        symbol, price = item.split("=", 1)
        marks[symbol.strip()] = float(price)
    return marks


def print_summary(summary: dict[str, object]) -> None:
    for key, value in summary.items():
        if isinstance(value, float):
            print(f"{key}={value:.6f}")
        else:
            print(f"{key}={value}")


if __name__ == "__main__":
    main()
