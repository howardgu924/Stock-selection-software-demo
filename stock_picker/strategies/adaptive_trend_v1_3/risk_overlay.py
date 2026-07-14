"""Pure individual-security RiskOverlay rules frozen by V1.3.3."""

from __future__ import annotations

from collections.abc import Mapping
import math

from stock_picker.strategies.adaptive_trend_v1_3.phase2_models import (
    DivergenceSnapshot,
    HoldingRiskAction,
    Phase2Status,
    RiskOverlayResult,
    RiskStatus,
    SecurityStatus,
)


def calculate_risk_overlay(
    opportunity: Mapping[str, object],
    divergence: DivergenceSnapshot,
    security_status: SecurityStatus,
    *,
    close: float,
    ma20: float,
    ma60: float,
    atr20: float,
    rs20: float,
    rs20_t_minus_5: float | None,
    signed_er20: float,
    signed_er20_t_minus_5: float | None,
) -> RiskOverlayResult:
    """Return one immutable RiskOverlay decision without changing opportunity data."""

    reasons: list[str] = []
    hard_reasons: list[str] = []
    opportunity_status = str(opportunity.get("status", "INVALID"))
    if opportunity_status != Phase2Status.VALID.value:
        detail = str(opportunity.get("invalid_reason") or "unknown")
        hard_reasons.append(f"input_invalid:opportunity:{detail}")
    if divergence.status != Phase2Status.VALID:
        detail = ",".join(divergence.invalid_reasons) or "unknown"
        hard_reasons.append(f"input_invalid:divergence:{detail}")

    numeric = {
        "opportunity_score": opportunity.get("opportunity_score"),
        "close": close,
        "ma20": ma20,
        "ma60": ma60,
        "atr20": atr20,
        "rs20": rs20,
        "signed_er20": signed_er20,
    }
    for name, value in numeric.items():
        if not _finite(value):
            hard_reasons.append(f"input_invalid:{name}")
    if _finite(atr20) and float(atr20) <= 0:
        hard_reasons.append("input_invalid:atr20<=0")
    if all(_finite(numeric[name]) for name in ("close", "ma20", "ma60")) and any(
        float(numeric[name]) <= 0 for name in ("close", "ma20", "ma60")
    ):
        hard_reasons.append("input_invalid:price<=0")

    status_flags = (
        (security_status.is_st, "security_st"),
        (security_status.is_star_st, "security_star_st"),
        (security_status.is_delisting, "security_delisting"),
        (security_status.suspended, "security_suspended"),
        (security_status.no_price_limit, "security_no_price_limit"),
        (security_status.trade_status_unknown, "security_trade_status_unknown"),
    )
    hard_reasons.extend(reason for active, reason in status_flags if active)
    if divergence.has_strong_top:
        hard_reasons.append("strong_top_divergence")

    structure_break = False
    if all(_finite(value) for value in (close, ma20, ma60, atr20)) and float(atr20) > 0:
        structure_break = float(close) < float(ma20) - 0.5 * float(atr20) or float(
            ma20
        ) < float(ma60)

    signed_er_weakening = False
    if _finite(signed_er20):
        if float(signed_er20) < 0:
            signed_er_weakening = True
        elif _finite(signed_er20_t_minus_5):
            change = float(signed_er20) - float(signed_er20_t_minus_5)
            signed_er_weakening = change < -0.20 or math.isclose(
                change, -0.20, rel_tol=0.0, abs_tol=1e-12
            )
        elif divergence.has_strong_top:
            hard_reasons.append("input_invalid:signed_er20_t_minus_5")

    recovery_watch = divergence.has_bottom
    recovery_confirmed = False
    if recovery_watch:
        recovery_values = (close, ma20, rs20, rs20_t_minus_5, signed_er20)
        if not all(_finite(value) for value in recovery_values):
            hard_reasons.append("input_invalid:rs20_t_minus_5")
        else:
            rs20_improving = float(rs20) > 0 and (
                float(rs20) - float(rs20_t_minus_5) >= 0.02
            )
            recovery_confirmed = (
                float(close) > float(ma20)
                and rs20_improving
                and float(signed_er20) > 0
            )

    reasons.extend(_unique(hard_reasons))
    if divergence.has_normal_top:
        reasons.append("normal_top_divergence")
    if recovery_watch:
        reasons.append("bottom_divergence")
        reasons.append("recovery_confirmed" if recovery_confirmed else "recovery_unconfirmed")
    if structure_break:
        reasons.append("structure_break")
    if signed_er_weakening:
        reasons.append("signed_er_weakening")

    hard_reasons = _unique(hard_reasons)
    if hard_reasons:
        risk_status = RiskStatus.BLOCK_NEW
        risk_multiplier = 0.0
        block_reason = hard_reasons[0]
    elif divergence.has_normal_top:
        risk_status = RiskStatus.REDUCED
        risk_multiplier = 0.75
        block_reason = ""
    else:
        risk_status = RiskStatus.ALLOW
        risk_multiplier = 1.0
        block_reason = ""

    holding_action = HoldingRiskAction.NONE
    if divergence.has_strong_top:
        if structure_break:
            holding_action = HoldingRiskAction.EXIT
        elif signed_er_weakening:
            holding_action = HoldingRiskAction.REDUCE
        else:
            holding_action = HoldingRiskAction.WATCH

    return RiskOverlayResult(
        risk_status=risk_status,
        risk_multiplier=risk_multiplier,
        block_new_reason=block_reason,
        recovery_watch=recovery_watch,
        recovery_confirmed=recovery_confirmed,
        structure_break=structure_break,
        signed_er_weakening=signed_er_weakening,
        holding_risk_action=holding_action,
        reasons=tuple(_unique(reasons)),
    )


def _finite(value: object) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
