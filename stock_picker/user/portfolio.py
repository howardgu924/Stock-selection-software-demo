from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from pathlib import Path

import pandas as pd

from stock_picker.data.models import normalize_symbol, symbol_code


POSITION_COLUMNS = [
    "symbol",
    "code",
    "name",
    "shares",
    "avg_cost",
    "target_sell_price",
    "strategy",
    "system",
    "entry_reason",
    "signal_date",
    "execution_date",
]
TRADE_COLUMNS = [
    "timestamp",
    "symbol",
    "code",
    "name",
    "side",
    "price",
    "shares",
    "fees",
    "tax",
    "cash_after",
    "realized_pnl",
    "realized_pnl_pct",
    "strategy",
    "system",
    "entry_reason",
    "exit_reason",
    "signal_date",
    "execution_date",
    "holding_days",
    "note",
]


@dataclass
class ManualPortfolio:
    principal: float
    cash: float
    commission_rate: float
    min_commission: float
    stamp_tax_rate: float
    positions: pd.DataFrame
    trades: pd.DataFrame
    slippage_pct: float = 0.0
    max_total_position_pct: float = 0.95

    def __post_init__(self) -> None:
        validate_account_risk_settings(
            self.slippage_pct,
            self.max_total_position_pct,
        )

    def summary(self, marks: dict[str, float] | None = None) -> dict[str, object]:
        marks = {normalize_symbol(k): float(v) for k, v in (marks or {}).items()}
        position_value = 0.0
        cost_value = 0.0
        for row in self.positions.itertuples(index=False):
            mark = marks.get(row.symbol, float(row.avg_cost))
            position_value += float(row.shares) * mark
            cost_value += float(row.shares) * float(row.avg_cost)
        total_asset = self.cash + position_value
        sells = self.trades[self.trades["side"] == "sell"].copy()
        realized = pd.to_numeric(sells["realized_pnl"], errors="coerce").fillna(0.0)
        wins = realized[realized > 0]
        losses = realized[realized < 0]
        holding_days = pd.to_numeric(sells["holding_days"], errors="coerce").dropna()
        sell_count = int(len(sells))
        win_count = int(len(wins))
        return {
            "principal": self.principal,
            "cash": self.cash,
            "commission_rate": self.commission_rate,
            "min_commission": self.min_commission,
            "stamp_tax_rate": self.stamp_tax_rate,
            "position_value": position_value,
            "total_asset": total_asset,
            "unrealized_pnl": position_value - cost_value,
            "realized_pnl": float(realized.sum()),
            "total_pnl": total_asset - self.principal,
            "total_return": total_asset / self.principal - 1 if self.principal else 0.0,
            "sell_count": sell_count,
            "win_count": win_count,
            "win_rate": win_count / sell_count if sell_count else 0.0,
            "profit_loss_ratio": abs(wins.mean() / losses.mean())
            if len(wins) and len(losses) and losses.mean()
            else 0.0,
            "average_holding_days": float(holding_days.mean()) if len(holding_days) else 0.0,
        }

    def buy_fee(self, price: float, shares: int) -> float:
        amount = float(price) * int(shares)
        return max(amount * self.commission_rate, self.min_commission) if amount > 0 else 0.0

    def sell_fee(self, price: float, shares: int) -> float:
        return self.buy_fee(price, shares)


class ManualPortfolioStore:
    def __init__(self, path: str | Path = "data/user/default") -> None:
        self.path = Path(path)
        self.account_path = self.path / "account.json"
        self.positions_path = self.path / "positions.csv"
        self.trades_path = self.path / "trades.csv"

    def initialize(
        self,
        principal: float,
        cash: float | None = None,
        commission_rate: float = 0.0003,
        min_commission: float = 5.0,
        stamp_tax_rate: float = 0.001,
        slippage_pct: float = 0.0,
        max_total_position_pct: float = 0.95,
    ) -> ManualPortfolio:
        if principal <= 0:
            raise ValueError("principal must be greater than 0")
        if commission_rate < 0 or min_commission < 0 or stamp_tax_rate < 0:
            raise ValueError("fee rates must be greater than or equal to 0")
        validate_account_risk_settings(slippage_pct, max_total_position_pct)
        portfolio = ManualPortfolio(
            principal=float(principal),
            cash=float(principal if cash is None else cash),
            commission_rate=float(commission_rate),
            min_commission=float(min_commission),
            stamp_tax_rate=float(stamp_tax_rate),
            positions=_empty_positions(),
            trades=_empty_trades(),
            slippage_pct=float(slippage_pct),
            max_total_position_pct=float(max_total_position_pct),
        )
        self.save(portfolio)
        return portfolio

    def load(self) -> ManualPortfolio:
        if not self.account_path.exists():
            raise FileNotFoundError(
                f"account does not exist: {self.account_path}. Run init first."
            )
        account = json.loads(self.account_path.read_text(encoding="utf-8"))
        positions = (
            pd.read_csv(self.positions_path)
            if self.positions_path.exists()
            else _empty_positions()
        )
        trades = (
            pd.read_csv(self.trades_path)
            if self.trades_path.exists()
            else _empty_trades()
        )
        return ManualPortfolio(
            principal=float(account["principal"]),
            cash=float(account["cash"]),
            commission_rate=float(account.get("commission_rate", 0.0003)),
            min_commission=float(account.get("min_commission", 5.0)),
            stamp_tax_rate=float(account.get("stamp_tax_rate", 0.001)),
            positions=_normalize_positions(positions),
            trades=_normalize_trades(trades),
            slippage_pct=float(account.get("slippage_pct", 0.0)),
            max_total_position_pct=float(account.get("max_total_position_pct", 0.95)),
        )

    def save(self, portfolio: ManualPortfolio) -> None:
        validate_account_risk_settings(
            portfolio.slippage_pct,
            portfolio.max_total_position_pct,
        )
        self.path.mkdir(parents=True, exist_ok=True)
        self.account_path.write_text(
            json.dumps(
                {
                    "principal": portfolio.principal,
                    "cash": portfolio.cash,
                    "commission_rate": portfolio.commission_rate,
                    "min_commission": portfolio.min_commission,
                    "stamp_tax_rate": portfolio.stamp_tax_rate,
                    "slippage_pct": portfolio.slippage_pct,
                    "max_total_position_pct": portfolio.max_total_position_pct,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        portfolio.positions.to_csv(self.positions_path, index=False)
        portfolio.trades.to_csv(self.trades_path, index=False)

    def buy(
        self,
        symbol: str,
        price: float,
        shares: int,
        name: str = "",
        fees: float | None = None,
        target_sell_price: float | None = None,
        timestamp: str | None = None,
        strategy: str = "",
        system: str = "",
        entry_reason: str = "",
        signal_date: str | None = None,
        execution_date: str | None = None,
        note: str = "",
    ) -> ManualPortfolio:
        if price <= 0 or shares <= 0:
            raise ValueError("price and shares must be greater than 0")
        portfolio = self.load()
        symbol = normalize_symbol(symbol)
        fees = portfolio.buy_fee(price, shares) if fees is None else float(fees)
        cost = price * shares + fees
        if cost > portfolio.cash:
            raise ValueError(f"insufficient cash: need {cost:.2f}, have {portfolio.cash:.2f}")

        positions = portfolio.positions.copy()
        current = positions[positions["symbol"] == symbol]
        if current.empty:
            positions = pd.concat(
                [
                    positions,
                    pd.DataFrame(
                        [
                            {
                                "symbol": symbol,
                                "code": symbol_code(symbol),
                                "name": name,
                                "shares": shares,
                                "avg_cost": cost / shares,
                                "target_sell_price": target_sell_price,
                                "strategy": strategy,
                                "system": system,
                                "entry_reason": entry_reason,
                                "signal_date": signal_date,
                                "execution_date": execution_date,
                            }
                        ]
                    ),
                ],
                ignore_index=True,
            )
        else:
            idx = current.index[0]
            old_shares = int(positions.at[idx, "shares"])
            old_cost = float(positions.at[idx, "avg_cost"]) * old_shares
            new_shares = old_shares + shares
            positions.at[idx, "shares"] = new_shares
            positions.at[idx, "avg_cost"] = (old_cost + cost) / new_shares
            if name:
                positions.at[idx, "name"] = name
            if target_sell_price is not None:
                positions.at[idx, "target_sell_price"] = target_sell_price
            for column, value in {
                "strategy": strategy,
                "system": system,
                "entry_reason": entry_reason,
                "signal_date": signal_date,
                "execution_date": execution_date,
            }.items():
                if value:
                    positions.at[idx, column] = value

        portfolio.cash -= cost
        portfolio.positions = _normalize_positions(positions)
        portfolio.trades = _append_trade(
            portfolio.trades,
            symbol=symbol,
            name=name,
            side="buy",
            price=price,
            shares=shares,
            fees=fees,
            tax=0.0,
            cash_after=portfolio.cash,
            realized_pnl=None,
            realized_pnl_pct=None,
            timestamp=timestamp,
            strategy=strategy,
            system=system,
            entry_reason=entry_reason,
            exit_reason="",
            signal_date=signal_date,
            execution_date=execution_date,
            holding_days=None,
            note=note,
        )
        self.save(portfolio)
        return portfolio

    def sell(
        self,
        symbol: str,
        price: float,
        shares: int,
        fees: float | None = None,
        tax_rate: float | None = None,
        timestamp: str | None = None,
        strategy: str = "",
        system: str = "",
        exit_reason: str = "",
        signal_date: str | None = None,
        execution_date: str | None = None,
        note: str = "",
    ) -> ManualPortfolio:
        if price <= 0 or shares <= 0:
            raise ValueError("price and shares must be greater than 0")
        portfolio = self.load()
        symbol = normalize_symbol(symbol)
        fees = portfolio.sell_fee(price, shares) if fees is None else float(fees)
        tax_rate = portfolio.stamp_tax_rate if tax_rate is None else float(tax_rate)
        positions = portfolio.positions.copy()
        current = positions[positions["symbol"] == symbol]
        if current.empty:
            raise ValueError(f"no position for {symbol}")
        idx = current.index[0]
        held = int(positions.at[idx, "shares"])
        if shares > held:
            raise ValueError(f"cannot sell {shares}; only {held} shares held")

        avg_cost = float(positions.at[idx, "avg_cost"])
        name = str(positions.at[idx, "name"] or "")
        position_strategy = str(positions.at[idx, "strategy"] or "")
        position_system = str(positions.at[idx, "system"] or "")
        entry_reason = str(positions.at[idx, "entry_reason"] or "")
        entry_execution_date = positions.at[idx, "execution_date"]
        tax = price * shares * tax_rate
        proceeds = price * shares - fees - tax
        realized_pnl = proceeds - avg_cost * shares
        realized_pnl_pct = realized_pnl / (avg_cost * shares) if avg_cost else 0.0
        remaining = held - shares
        if remaining == 0:
            positions = positions.drop(index=idx)
        else:
            positions.at[idx, "shares"] = remaining

        portfolio.cash += proceeds
        portfolio.positions = _normalize_positions(positions)
        portfolio.trades = _append_trade(
            portfolio.trades,
            symbol=symbol,
            name=name,
            side="sell",
            price=price,
            shares=shares,
            fees=fees,
            tax=tax,
            cash_after=portfolio.cash,
            realized_pnl=realized_pnl,
            realized_pnl_pct=realized_pnl_pct,
            timestamp=timestamp,
            strategy=strategy or position_strategy,
            system=system or position_system,
            entry_reason=entry_reason,
            exit_reason=exit_reason,
            signal_date=signal_date,
            execution_date=execution_date,
            holding_days=_holding_days(entry_execution_date, execution_date or timestamp),
            note=note,
        )
        self.save(portfolio)
        return portfolio

    def adjust_cost(
        self,
        symbol: str,
        avg_cost: float,
        timestamp: str | None = None,
        note: str = "",
    ) -> ManualPortfolio:
        if avg_cost <= 0:
            raise ValueError("avg_cost must be greater than 0")
        portfolio = self.load()
        symbol = normalize_symbol(symbol)
        positions = portfolio.positions.copy()
        current = positions[positions["symbol"] == symbol]
        if current.empty:
            raise ValueError(f"no position for {symbol}")
        idx = current.index[0]
        old_cost = float(positions.at[idx, "avg_cost"])
        shares = int(positions.at[idx, "shares"])
        name = str(positions.at[idx, "name"] or "")
        strategy = str(positions.at[idx, "strategy"] or "")
        system = str(positions.at[idx, "system"] or "")
        entry_reason = str(positions.at[idx, "entry_reason"] or "")
        signal_date = positions.at[idx, "signal_date"]
        execution_date = positions.at[idx, "execution_date"]
        positions.at[idx, "avg_cost"] = float(avg_cost)
        portfolio.positions = _normalize_positions(positions)
        portfolio.trades = _append_trade(
            portfolio.trades,
            symbol=symbol,
            name=name,
            side="adjust_cost",
            price=float(avg_cost),
            shares=shares,
            fees=0.0,
            tax=0.0,
            cash_after=portfolio.cash,
            realized_pnl=None,
            realized_pnl_pct=None,
            timestamp=timestamp,
            strategy=strategy,
            system=system,
            entry_reason=entry_reason,
            exit_reason="",
            signal_date=signal_date,
            execution_date=execution_date,
            holding_days=None,
            note=note or f"adjust avg_cost from {old_cost:.6f} to {float(avg_cost):.6f}",
        )
        self.save(portfolio)
        return portfolio


def _append_trade(
    trades: pd.DataFrame,
    symbol: str,
    name: str,
    side: str,
    price: float,
    shares: int,
    fees: float,
    tax: float,
    cash_after: float,
    realized_pnl: float | None,
    realized_pnl_pct: float | None,
    timestamp: str | None,
    strategy: str,
    system: str,
    entry_reason: str,
    exit_reason: str,
    signal_date: str | None,
    execution_date: str | None,
    holding_days: int | None,
    note: str,
) -> pd.DataFrame:
    row = {
        "timestamp": timestamp or datetime.now().isoformat(timespec="seconds"),
        "symbol": symbol,
        "code": symbol_code(symbol),
        "name": name,
        "side": side,
        "price": price,
        "shares": shares,
        "fees": fees,
        "tax": tax,
        "cash_after": cash_after,
        "realized_pnl": realized_pnl,
        "realized_pnl_pct": realized_pnl_pct,
        "strategy": strategy,
        "system": system,
        "entry_reason": entry_reason,
        "exit_reason": exit_reason,
        "signal_date": signal_date,
        "execution_date": execution_date,
        "holding_days": holding_days,
        "note": note,
    }
    return _normalize_trades(pd.concat([trades, pd.DataFrame([row])], ignore_index=True))


def _empty_positions() -> pd.DataFrame:
    return pd.DataFrame(columns=POSITION_COLUMNS)


def _empty_trades() -> pd.DataFrame:
    return pd.DataFrame(columns=TRADE_COLUMNS)


def _normalize_positions(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    for column in POSITION_COLUMNS:
        if column not in data:
            data[column] = pd.NA
    if data.empty:
        return _empty_positions()
    data["symbol"] = data["symbol"].map(normalize_symbol)
    data["code"] = data["symbol"].map(symbol_code)
    data["shares"] = pd.to_numeric(data["shares"], errors="coerce").fillna(0).astype(int)
    for column in ["avg_cost", "target_sell_price"]:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    return data[data["shares"] > 0][POSITION_COLUMNS].reset_index(drop=True)


def _normalize_trades(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    for column in TRADE_COLUMNS:
        if column not in data:
            data[column] = pd.NA
    if data.empty:
        return _empty_trades()
    data["symbol"] = data["symbol"].map(normalize_symbol)
    data["code"] = data["symbol"].map(symbol_code)
    data["shares"] = pd.to_numeric(data["shares"], errors="coerce").fillna(0).astype(int)
    for column in [
        "price",
        "fees",
        "tax",
        "cash_after",
        "realized_pnl",
        "realized_pnl_pct",
        "holding_days",
    ]:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    return data[TRADE_COLUMNS].reset_index(drop=True)


def _holding_days(entry_date: object, exit_date: object) -> int | None:
    if pd.isna(entry_date) or pd.isna(exit_date):
        return None
    try:
        entry = pd.to_datetime(entry_date)
        exit_ = pd.to_datetime(exit_date)
    except Exception:
        return None
    return max(int((exit_ - entry).days), 0)


def validate_account_risk_settings(
    slippage_pct: float,
    max_total_position_pct: float,
) -> None:
    slippage = float(slippage_pct)
    total_cap = float(max_total_position_pct)
    if not isfinite(slippage) or slippage < 0:
        raise ValueError("slippage_pct must be finite and greater than or equal to 0")
    if not isfinite(total_cap) or not 0 < total_cap <= 1:
        raise ValueError("max_total_position_pct must be finite and in (0, 1]")
