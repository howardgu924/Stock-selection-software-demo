from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stock_picker.data import DataSourceConfig, MarketDataService
from stock_picker.data.models import StockInfo
from stock_picker.strategies import TurtleConfig, backtest_turtle_system


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the full turtle state machine.")
    symbol_group = parser.add_mutually_exclusive_group(required=True)
    symbol_group.add_argument("--symbol", help="Single stock code, e.g. 600519")
    symbol_group.add_argument("--symbols", help="Comma separated stock codes")
    symbol_group.add_argument("--symbols-file", help="One stock code per line")
    symbol_group.add_argument("--all", action="store_true", help="Use all A-share symbols")
    parser.add_argument("--start", required=True, help="Start date, e.g. 20260228")
    parser.add_argument("--end", required=True, help="End date, e.g. 20260527")
    parser.add_argument("--cash", type=float, default=100_000.0)
    _config_args(parser)
    parser.add_argument("--source", choices=["baostock", "akshare", "joinquant"], help="History source")
    parser.add_argument("--stock-source", choices=["akshare", "baostock", "joinquant"], help="Stock list source")
    parser.add_argument("--fallback", action="append", choices=["baostock", "akshare", "joinquant"], default=[])
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--refresh-symbols", action="store_true")
    parser.add_argument("--limit", type=int, help="Use only first N symbols with --all")
    parser.add_argument("--output", help="Summary CSV")
    parser.add_argument("--equity-output", help="Daily equity CSV")
    parser.add_argument("--trades-output", help="Trade blotter CSV")
    parser.add_argument("--positions-output", help="Daily positions CSV")
    parser.add_argument("--drawdowns-output", help="Drawdown detail CSV")
    parser.add_argument("--symbol-pnl-output", help="Symbol PnL ranking CSV")
    parser.add_argument("--sweep-output", help="Parameter robustness summary CSV")
    parser.add_argument("--sweep-risk-pct", help="Comma separated risk_pct values")
    parser.add_argument("--sweep-slippage-rate", help="Comma separated slippage values")
    parser.add_argument("--sweep-s1-entry", help="Comma separated S1 entry windows")
    parser.add_argument("--sweep-s1-exit", help="Comma separated S1 exit windows")
    parser.add_argument("--error-log", default="data/turtle_system_backtest_errors.csv")
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    try:
        if args.cash <= 0:
            parser.error("--cash must be greater than 0")
        service = MarketDataService(
            data_source_config=_data_source_config(args.source, args.stock_source, args.fallback)
        )
        symbols = _resolve_symbols(service, args)
        config = _config_from_args(args)
        result = backtest_turtle_system(
            service=service,
            symbols=symbols,
            start_date=args.start,
            end_date=args.end,
            initial_cash=args.cash,
            config=config,
            refresh=args.refresh,
            skip_errors=not args.stop_on_error,
        )
        _print_result(result)
        _write_csv(result.summary, args.output, "summary_csv")
        _write_csv(result.equity, args.equity_output, "equity_csv")
        _write_csv(result.trades, args.trades_output, "trades_csv")
        _write_csv(result.positions, args.positions_output, "positions_csv")
        _write_csv(result.drawdowns, args.drawdowns_output, "drawdowns_csv")
        _write_csv(result.symbol_pnl, args.symbol_pnl_output, "symbol_pnl_csv")
        if args.sweep_output:
            sweep = _run_sweep(service, symbols, args, config)
            _write_csv(sweep, args.sweep_output, "sweep_csv")
        if not result.errors.empty:
            _write_csv(result.errors, args.error_log, "errors_csv", stream=sys.stderr)
    except Exception as exc:
        if args.debug:
            raise
        print(f"Error: {exc}", file=sys.stderr)
        print("Run again with --debug to show the full Python traceback.", file=sys.stderr)
        sys.exit(1)


def _config_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--risk-pct", type=float, default=0.01)
    parser.add_argument("--s1-entry", type=int, default=20)
    parser.add_argument("--s1-exit", type=int, default=10)
    parser.add_argument("--s2-entry", type=int, default=55)
    parser.add_argument("--s2-exit", type=int, default=20)
    parser.add_argument("--atr-period", type=int, default=20)
    parser.add_argument("--max-units", type=int, default=4)
    parser.add_argument("--add-unit-atr", type=float, default=0.5)
    parser.add_argument("--stop-atr", type=float, default=2.0)
    parser.add_argument("--lot-size", type=int, default=100)
    parser.add_argument("--commission-rate", type=float, default=0.0003)
    parser.add_argument("--min-commission", type=float, default=5.0)
    parser.add_argument("--stamp-tax-rate", type=float, default=0.001)
    parser.add_argument("--slippage-rate", type=float, default=0.0)


def _config_from_args(args, **overrides) -> TurtleConfig:
    values = {
        "s1_entry": args.s1_entry,
        "s1_exit": args.s1_exit,
        "s2_entry": args.s2_entry,
        "s2_exit": args.s2_exit,
        "atr_period": args.atr_period,
        "risk_pct": args.risk_pct,
        "add_unit_atr": args.add_unit_atr,
        "stop_atr": args.stop_atr,
        "max_units": args.max_units,
        "lot_size": args.lot_size,
        "commission_rate": args.commission_rate,
        "min_commission": args.min_commission,
        "stamp_tax_rate": args.stamp_tax_rate,
        "slippage_rate": args.slippage_rate,
    }
    values.update(overrides)
    return TurtleConfig(**values)


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
        return symbols[: args.limit] if args.limit else symbols
    if args.symbols_file:
        values = [
            line.strip()
            for line in Path(args.symbols_file).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not values:
            raise ValueError("--symbols-file is empty")
        return values
    if args.symbols:
        values = [item.strip() for item in args.symbols.split(",") if item.strip()]
        if not values:
            raise ValueError("--symbols must contain at least one stock code")
        return values
    return [args.symbol]


def _print_result(result) -> None:
    print(result.summary.to_string(index=False))
    print(
        f"\nequity_rows={len(result.equity)} trades={len(result.trades)} "
        f"positions={len(result.positions)} drawdowns={len(result.drawdowns)} "
        f"errors={len(result.errors)}"
    )
    if not result.trades.empty:
        print("\nTrades:")
        print(result.trades.tail(30).to_string(index=False))
    if not result.symbol_pnl.empty:
        print("\nSymbol PnL:")
        print(result.symbol_pnl.head(20).to_string(index=False))


def _run_sweep(service: MarketDataService, symbols: list[str | StockInfo], args, base: TurtleConfig) -> pd.DataFrame:
    risk_values = _float_list(args.sweep_risk_pct) or [base.risk_pct]
    slippage_values = _float_list(args.sweep_slippage_rate) or [base.slippage_rate]
    s1_entry_values = _int_list(args.sweep_s1_entry) or [base.s1_entry]
    s1_exit_values = _int_list(args.sweep_s1_exit) or [base.s1_exit]
    rows = []
    for risk_pct, slippage_rate, s1_entry, s1_exit in itertools.product(
        risk_values, slippage_values, s1_entry_values, s1_exit_values
    ):
        result = backtest_turtle_system(
            service=service,
            symbols=symbols,
            start_date=args.start,
            end_date=args.end,
            initial_cash=args.cash,
            config=_config_from_args(
                args,
                risk_pct=risk_pct,
                slippage_rate=slippage_rate,
                s1_entry=s1_entry,
                s1_exit=s1_exit,
            ),
            refresh=False,
            skip_errors=not args.stop_on_error,
        )
        row = result.summary.iloc[0].to_dict()
        row.update(
            {
                "risk_pct": risk_pct,
                "slippage_rate": slippage_rate,
                "s1_entry": s1_entry,
                "s1_exit": s1_exit,
                "errors": len(result.errors),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)


def _float_list(value: str | None) -> list[float]:
    return [float(item.strip()) for item in value.split(",") if item.strip()] if value else []


def _int_list(value: str | None) -> list[int]:
    return [int(item.strip()) for item in value.split(",") if item.strip()] if value else []


def _write_csv(frame: pd.DataFrame, path_value: str | None, label: str, stream=None) -> None:
    if not path_value:
        return
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    print(f"{label}={path}", file=stream or sys.stdout)


if __name__ == "__main__":
    main()
