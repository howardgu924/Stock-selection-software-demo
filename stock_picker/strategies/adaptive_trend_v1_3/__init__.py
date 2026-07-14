"""T+1 soft-adaptive medium/short-term trend strategy, version 1.3.x.

Phase 1 exposes OpportunityScore. Phase 2 adds independent pure calculators
for market, divergence, risk, and execution overlays without depending on the
legacy thermostat implementation.
"""

from stock_picker.strategies.adaptive_trend_v1_3.divergence import (
    calculate_divergence,
    calculate_macd,
    find_swing_points,
)
from stock_picker.strategies.adaptive_trend_v1_3.execution_gate import (
    REQUIRED_BAR_STARTS,
    calculate_emergency_market_gate,
    calculate_execution_gate,
)
from stock_picker.strategies.adaptive_trend_v1_3.market_overlay import (
    MARKET_INDEX_WEIGHTS,
    calculate_market_overlay,
    effective_exposure_cap,
    score_index_factors,
    wilder_atr,
)

from stock_picker.strategies.adaptive_trend_v1_3.opportunity_score import (
    BENCHMARK_WEIGHTS,
    MIN_HISTORY_DAYS,
    OPPORTUNITY_OUTPUT_COLUMNS,
    OpportunityInputError,
    OpportunityStatus,
    abs_rs,
    calculate_opportunity_scores,
    calculate_weighted_benchmark_returns,
    clip01,
    score_close_ma20,
    score_ma20_ma60,
    score_ma60_slope,
    score_signed_er,
    score_signed_er_change,
    signed_er_series,
)
from stock_picker.strategies.adaptive_trend_v1_3.phase2_models import (
    DivergenceSignal,
    DivergenceSnapshot,
    DivergenceStrength,
    DivergenceType,
    EmergencyIndexInput,
    EmergencyMarketResult,
    EmergencyStatus,
    ExecutionGateResult,
    ExecutionGateStatus,
    ExecutionStatus,
    HoldingRiskAction,
    Phase2Status,
    RiskOverlayResult,
    RiskStatus,
    SecurityStatus,
)
from stock_picker.strategies.adaptive_trend_v1_3.risk_overlay import (
    calculate_risk_overlay,
)

__all__ = [
    "BENCHMARK_WEIGHTS",
    "DivergenceSignal",
    "DivergenceSnapshot",
    "DivergenceStrength",
    "DivergenceType",
    "EmergencyIndexInput",
    "EmergencyMarketResult",
    "EmergencyStatus",
    "ExecutionGateResult",
    "ExecutionGateStatus",
    "ExecutionStatus",
    "HoldingRiskAction",
    "MARKET_INDEX_WEIGHTS",
    "MIN_HISTORY_DAYS",
    "OPPORTUNITY_OUTPUT_COLUMNS",
    "OpportunityInputError",
    "OpportunityStatus",
    "Phase2Status",
    "REQUIRED_BAR_STARTS",
    "RiskOverlayResult",
    "RiskStatus",
    "SecurityStatus",
    "abs_rs",
    "calculate_opportunity_scores",
    "calculate_divergence",
    "calculate_emergency_market_gate",
    "calculate_execution_gate",
    "calculate_macd",
    "calculate_market_overlay",
    "calculate_risk_overlay",
    "calculate_weighted_benchmark_returns",
    "clip01",
    "effective_exposure_cap",
    "find_swing_points",
    "score_close_ma20",
    "score_ma20_ma60",
    "score_ma60_slope",
    "score_signed_er",
    "score_signed_er_change",
    "score_index_factors",
    "signed_er_series",
    "wilder_atr",
]
