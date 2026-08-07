"""Small JSON persistence adapter for Phase 6 account defaults."""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from threading import RLock
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


class Phase6PreferenceStore:
    """Persist per-account Phase 6 presentation preferences outside run snapshots."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = RLock()

    def show_legacy_experimental(self, account_profile_id: str) -> bool:
        identifier = _preference_identifier(account_profile_id)
        with self._lock:
            raw = self._load()
            value = raw.get(identifier, {}).get("show_legacy_experimental", False)
        if type(value) is not bool:
            raise Phase5Error("INVALID_CONFIG", "invalid_show_legacy_experimental")
        return value

    def set_show_legacy_experimental(
        self, account_profile_id: str, enabled: bool,
    ) -> None:
        identifier = _preference_identifier(account_profile_id)
        if type(enabled) is not bool:
            raise Phase5Error("INVALID_CONFIG", "invalid_show_legacy_experimental")
        with self._lock:
            raw = self._load()
            raw[identifier] = {"show_legacy_experimental": enabled}
            payload = {
                key: raw[key]
                for key in sorted(raw)
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.path)

    def _load(self) -> dict[str, dict[str, bool]]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise TypeError
            result: dict[str, dict[str, bool]] = {}
            for key, value in raw.items():
                if not isinstance(key, str) or not isinstance(value, dict):
                    raise TypeError
                enabled = value.get("show_legacy_experimental", False)
                if type(enabled) is not bool:
                    raise TypeError
                result[key] = {"show_legacy_experimental": enabled}
            return result
        except (OSError, ValueError, TypeError) as exc:
            raise Phase5Error("INVALID_CONFIG", "phase6_preference_store_invalid") from exc


class Phase6PreparedInputStore:
    """Persist the minimal READY input-to-snapshot association across restarts."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = RLock()

    def load(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            if not self.path.exists():
                return {}
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise TypeError
                result: dict[str, dict[str, Any]] = {}
                for signature, value in raw.items():
                    if not isinstance(signature, str) or not isinstance(value, dict):
                        raise TypeError
                    result[signature] = dict(value)
                return result
            except (OSError, ValueError, TypeError) as exc:
                raise Phase5Error(
                    "INVALID_CONFIG", "phase6_prepared_input_store_invalid"
                ) from exc

    def save(self, signature: str, value: Mapping[str, Any]) -> None:
        if not signature:
            raise Phase5Error("INVALID_CONFIG", "prepared_input_signature_required")
        with self._lock:
            raw = self.load()
            raw[signature] = dict(value)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            temporary.replace(self.path)


def _preference_identifier(value: object) -> str:
    identifier = str(value).strip()
    if not identifier:
        raise Phase5Error("INVALID_CONFIG", "account_profile_id_required")
    return identifier


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
