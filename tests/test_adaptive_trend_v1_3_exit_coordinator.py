from dataclasses import replace
from decimal import Decimal

from stock_picker.strategies.adaptive_trend_v1_3 import (
    ExecutionType,
    ExitCycleHoldingInput,
    ExitIntent,
    PendingSellStatus,
    ReplacementCandidate,
    coordinate_1430_exit_cycle,
    create_or_merge_pending,
    evaluate_hard_exit,
    initialize_exit_control,
)
from stock_picker.strategies.adaptive_trend_v1_3.position_state import (
    empty_position_state,
)


D = Decimal
CALENDAR = ["2025-01-06", "2025-01-07", "2025-01-09"]


def _position(symbol="600001", total=1000, sellable=1000):
    return replace(
        empty_position_state(symbol),
        total_qty=total,
        sellable_qty=sellable,
        today_bought_qty=total - sellable,
    )


def _control(symbol="600001"):
    return initialize_exit_control(
        symbol=symbol,
        entry_trade_date="2025-01-02",
        entry_price="10",
        effective_risk_pct="0.08",
        price_basis_id="RAW",
    ).new_state


def _holding(
    symbol="600001",
    *,
    score="40",
    threshold="50",
    strong=False,
    normal=False,
    total=1000,
    sellable=1000,
    value="20000",
):
    return ExitCycleHoldingInput(
        position=_position(symbol, total, sellable),
        control=_control(symbol),
        p1430=D("10"),
        previous_ma20=D("9.5"),
        previous_ma60=D("9"),
        ma20_slope5=D("0.1"),
        opportunity_status="VALID",
        opportunity_score=D(score),
        entry_threshold=D(threshold),
        strong_top_divergence=strong,
        normal_top_divergence=normal,
        divergence_episode_id="DIV:1" if normal else "",
        partial_sell_lot_size=100,
        protected=False,
        market_data_valid=True,
        market_value=D(value),
        rs60=D("0.1"),
        rs20=D("0.05"),
        pending_signal_valid=True,
    )


def _candidate(symbol="600010", score="52"):
    return ReplacementCandidate(
        symbol=symbol,
        opportunity_score=D(score),
        entry_threshold=D("50"),
        rs60=D("0.2"),
        rs20=D("0.1"),
        signed_er20=D("0.3"),
        final_order_qty=1000,
        cooldown_blocked=False,
    )


def _coordinate(holdings, pending=(), candidates=(), **overrides):
    values = dict(
        decision_trade_date="2025-01-06",
        portfolio_equity=D("100000"),
        existing_exposure=D("0.5"),
        effective_exposure_cap=D("0.5"),
        market_allows_new=True,
        emergency_normal=True,
        no_new_slots=True,
        trading_calendar=CALENDAR,
    )
    values.update(overrides)
    return coordinate_1430_exit_cycle(
        holdings,
        pending,
        candidates,
        **values,
    )


def _sticky_pending(symbol="600001"):
    position = _position(symbol, 1000, 0)
    decision = evaluate_hard_exit(
        position,
        _control(symbol),
        trigger_bar_start="2025-01-06 10:00",
        completed_bar_low="9",
        emergency_status="NORMAL",
        price_basis_id="RAW",
    )
    return create_or_merge_pending(
        None,
        decision.selected_intent,
        total_qty=1000,
        remaining_qty=1000,
        next_attempt_at="2025-01-06 14:35",
    ).new_state


def test_risk_exit_wins_over_episode_derisk_and_replacement():
    result = _coordinate(
        [_holding(strong=True, normal=True)],
        candidates=[_candidate()],
        existing_exposure=D("0.8"),
    )
    assert result.status == "VALID"
    assert len(result.intents_by_symbol) == 1
    assert result.intents_by_symbol[0][1].reason == "STRONG_TOP_DIVERGENCE"
    assert result.replacement_symbol == ""
    assert len(result.fill_requests) == 1


def test_sticky_hard_pending_wins_over_all_1430_signals():
    result = _coordinate(
        [_holding(strong=True)],
        pending=[_sticky_pending()],
        candidates=[_candidate()],
        existing_exposure=D("0.8"),
    )
    assert result.intents_by_symbol[0][1].reason == "INITIAL_STOP"
    assert len(result.fill_requests) == 1
    assert result.fill_requests[0].execution_type == ExecutionType.HARD_EXIT


def test_normalized_holding_alias_conflict_is_invalid():
    result = _coordinate([_holding("600001"), _holding("600001.SH")])
    assert result.status == "INVALID"
    assert result.reasons == ("duplicate_or_invalid_holding_symbol",)


def test_input_order_does_not_change_coordinated_outputs():
    holdings = [
        _holding("600002", score="20"),
        _holding("600001", score="10"),
    ]
    first = _coordinate(
        holdings,
        existing_exposure=D("0.7"),
        effective_exposure_cap=D("0.5"),
    )
    second = _coordinate(
        list(reversed(holdings)),
        existing_exposure=D("0.7"),
        effective_exposure_cap=D("0.5"),
    )
    assert first.intents_by_symbol == second.intents_by_symbol
    assert first.fill_requests == second.fill_requests
    assert first.projected_exposure == second.projected_exposure
    assert first.residual_excess == second.residual_excess


def test_two_replacement_candidates_produce_only_one_exit():
    result = _coordinate(
        [_holding()],
        candidates=[_candidate("600011", "53"), _candidate("600010", "53")],
    )
    assert result.replacement_symbol == "600001.SH"
    assert len(result.intents_by_symbol) == 1
    assert result.intents_by_symbol[0][1].reason == "REPLACEMENT_EXIT"
    assert result.intents_by_symbol[0][1].episode_id.endswith("600010.SH")


def test_higher_priority_planned_sell_enters_projected_exposure():
    result = _coordinate(
        [
            _holding(
                "600001",
                strong=True,
                value="20000",
                total=2000,
                sellable=2000,
            ),
            _holding("600002", score="20", value="30000"),
        ],
        existing_exposure=D("0.8"),
        effective_exposure_cap=D("0.5"),
    )
    assert result.projected_exposure == D("0.6")
    assert any(
        intent.reason == "PORTFOLIO_EXPOSURE_REDUCTION"
        for _, intent in result.intents_by_symbol
    )


def test_t1_split_outputs_one_request_and_one_active_pending():
    holding = _holding(strong=True, total=1000, sellable=400)
    result = _coordinate([holding])
    assert len(result.fill_requests) == 1
    assert result.fill_requests[0].requested_qty == 400
    active = [
        update.new_state
        for update in result.pending_updates
        if update.new_state is not None
        and update.new_state.status == PendingSellStatus.ACTIVE
    ]
    assert len(active) == 1
    assert active[0].remaining_qty == 600
    assert active[0].next_attempt_at.isoformat().endswith(
        "2025-01-07T14:30:00+08:00"
    )


def test_coordinator_does_not_mutate_inputs():
    holdings = [_holding(strong=True, total=1000, sellable=400)]
    pending = []
    candidates = [_candidate()]
    calendar = list(CALENDAR)
    snapshots = (
        tuple(holdings),
        tuple(pending),
        tuple(candidates),
        tuple(calendar),
    )
    _coordinate(
        holdings,
        pending=pending,
        candidates=candidates,
        trading_calendar=calendar,
    )
    assert tuple(holdings) == snapshots[0]
    assert tuple(pending) == snapshots[1]
    assert tuple(candidates) == snapshots[2]
    assert tuple(calendar) == snapshots[3]


def test_duplicate_active_pending_alias_is_invalid():
    pending = _sticky_pending()
    alias = replace(pending, symbol="600001")
    result = _coordinate([_holding()], pending=[pending, alias])
    assert result.status == "INVALID"
    assert result.reasons == ("duplicate_active_pending",)


def test_replacement_candidate_alias_conflict_is_invalid_before_market_gate():
    first = _candidate("600010")
    alias = replace(first, symbol="600010.SH")
    result = _coordinate(
        [_holding()],
        candidates=[first, alias],
        market_allows_new=False,
    )
    assert result.status == "INVALID"
    assert result.reasons == ("duplicate_or_invalid_candidate_symbol",)


def test_each_symbol_has_at_most_one_intent_request_and_active_pending():
    result = _coordinate(
        [
            _holding("600001", strong=True, total=1000, sellable=400),
            _holding("600002", score="20"),
        ],
        candidates=[_candidate()],
        existing_exposure=D("0.8"),
        effective_exposure_cap=D("0.5"),
    )
    intent_symbols = [symbol for symbol, _ in result.intents_by_symbol]
    request_symbols = [request.symbol for request in result.fill_requests]
    active_symbols = [
        update.new_state.symbol
        for update in result.pending_updates
        if update.new_state is not None
        and update.new_state.status == PendingSellStatus.ACTIVE
    ]
    assert len(intent_symbols) == len(set(intent_symbols))
    assert len(request_symbols) == len(set(request_symbols))
    assert len(active_symbols) == len(set(active_symbols))
