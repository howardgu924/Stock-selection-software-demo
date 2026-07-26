"""Small JSON persistence adapter for Phase 6 account defaults."""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any, Mapping

from .phase5_models import AccountProfile, Phase5Error, UniverseSpec


class AccountProfileStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, AccountProfile]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return {key: _profile(value) for key, value in raw.items()}
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise Phase5Error("INVALID_CONFIG", "account_profile_store_invalid") from exc

    def save(self, profile: AccountProfile) -> None:
        profiles = self.load()
        profiles[profile.account_profile_id] = profile
        payload = {
            key: _encode(value)
            for key, value in sorted(profiles.items())
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)


def validate_decimal_text(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, Decimal)):
        raise Phase5Error("INVALID_CONFIG", f"invalid_{field}")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        raise Phase5Error("INVALID_CONFIG", f"invalid_{field}") from None
    if not parsed.is_finite() or parsed < 0:
        raise Phase5Error("INVALID_CONFIG", f"invalid_{field}")
    return parsed


def _profile(raw: Mapping[str, Any]) -> AccountProfile:
    universe = raw["default_universe"]
    return AccountProfile(
        account_profile_id=str(raw["account_profile_id"]),
        backtest_initial_cash=validate_decimal_text(str(raw["backtest_initial_cash"]), "backtest_initial_cash"),
        paper_cash=validate_decimal_text(str(raw["paper_cash"]), "paper_cash"),
        fee_schedule_id=str(raw["fee_schedule_id"]),
        base_currency=str(raw["base_currency"]),
        default_universe=UniverseSpec(
            kind=universe["kind"],
            manual_symbols=tuple(universe.get("manual_symbols", ())),
            watchlist_names=tuple(universe.get("watchlist_names", ())),
            market_scopes=tuple(universe.get("market_scopes", ())),
        ),
        provider_priority=tuple(raw.get("provider_priority", ())),
        data_directory=str(raw["data_directory"]),
        report_directory=str(raw["report_directory"]),
    )


def _encode(profile: AccountProfile) -> dict[str, Any]:
    raw = asdict(profile)
    raw["backtest_initial_cash"] = str(profile.backtest_initial_cash)
    raw["paper_cash"] = str(profile.paper_cash)
    raw["data_directory"] = str(profile.data_directory)
    raw["report_directory"] = str(profile.report_directory)
    raw["default_universe"]["kind"] = str(profile.default_universe.kind)
    return raw
