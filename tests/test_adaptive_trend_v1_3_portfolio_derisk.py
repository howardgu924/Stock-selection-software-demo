from decimal import Decimal

from stock_picker.strategies.adaptive_trend_v1_3 import (
    DeriskHoldingInput,
    ReplacementCandidate,
    ReplacementIncumbent,
    plan_portfolio_derisk,
    select_replacement_exit,
)


D = Decimal


def _holding(symbol, score, *, value="20000", price="10", qty=2000,
             sellable=2000, lot=100, protected=False, higher=False):
    return DeriskHoldingInput(symbol, qty, sellable, D(value), D(price), D(score),
                              D("0.1"), D("0.05"), lot, protected, higher)


def _derisk(holdings, **overrides):
    args = dict(
        decision_trade_date="2025-01-06",
        portfolio_equity=D("100000"),
        existing_exposure=D("0.8"),
        effective_exposure_cap=D("0.5"),
        higher_priority_planned_sell_weight=D("0.1"),
    )
    args.update(overrides)
    return plan_portfolio_derisk(holdings, **args)


def test_derisk_uses_weakest_first_stable_sort():
    result = _derisk([_holding("600002", "20"), _holding("600001", "10")])
    assert [i.symbol for i in result.intents] == ["600001.SH"]
    assert result.intents[0].requested_target_qty == 2000
    assert result.residual_excess == D("0")


def test_derisk_ties_use_rs_then_symbol():
    a = _holding("600002", "10")
    b = _holding("600001", "10")
    result = _derisk([a, b], existing_exposure=D("0.6"), higher_priority_planned_sell_weight=D("0"))
    assert result.intents[0].symbol == "600001.SH"


def test_derisk_skips_higher_priority_full_exit():
    result = _derisk([_holding("600001", "1", higher=True), _holding("600002", "2")])
    assert all(intent.symbol != "600001.SH" for intent in result.intents)


def test_derisk_ignores_protection_and_rounds_down_lot_without_reallocation():
    result = _derisk(
        [_holding("600001", "1", value="10000", price="33", qty=300,
                  sellable=0, protected=True)],
        existing_exposure=D("0.51"), higher_priority_planned_sell_weight=D("0"),
    )
    assert result.intents == ()
    assert result.residual_excess == D("0.01")


def test_no_excess_has_no_intents():
    result = _derisk([_holding("600001", "1")], existing_exposure=D("0.4"))
    assert result.status == "NO_ACTION"
    assert result.intents == ()


def test_invalid_decimal_and_alias_conflict_are_invalid():
    assert _derisk([_holding("600001", "1")], existing_exposure=0.8).status == "INVALID"
    assert _derisk([_holding("600001", "1"), _holding("600001.SH", "2")]).status == "INVALID"


def _inc(symbol="600001", score="40", threshold="50", **flags):
    return ReplacementIncumbent(symbol, 1000, D(score), D(threshold), D("0.1"),
                                D("0.05"), flags.get("protected", False),
                                flags.get("pending", False), flags.get("higher", False))


def _candidate(symbol="600002", score="52", threshold="50", qty=1000, **flags):
    return ReplacementCandidate(symbol, D(score), D(threshold), D("0.2"),
                                D("0.1"), D("0.3"), qty,
                                flags.get("cooldown", False))


def _replace(incumbents=None, candidates=None, **flags):
    return select_replacement_exit(
        incumbents or [_inc()], candidates or [_candidate()],
        decision_trade_date="2025-01-06", current_holding_symbols=["600001.SH"],
        market_allows_new=flags.get("market", True),
        emergency_normal=flags.get("emergency", True), no_new_slots=flags.get("slots", True),
    )


def test_replacement_exact_gap_12_triggers_sell_only():
    result = _replace()
    assert result.status == "TRIGGERED"
    assert result.intent.symbol == "600001.SH"
    assert result.intent.requested_target_qty == 1000
    assert result.candidate_symbol == "600002.SH"


def test_replacement_below_gap_or_incumbent_above_threshold_does_not_trigger():
    assert _replace(candidates=[_candidate(score="51.999")]).status == "NO_ACTION"
    assert _replace(incumbents=[_inc(score="50")], candidates=[_candidate(score="70")]).status == "NO_ACTION"


def test_replacement_preconditions_are_all_required():
    assert _replace(market=False).status == "NO_ACTION"
    assert _replace(emergency=False).status == "NO_ACTION"
    assert _replace(slots=False).status == "NO_ACTION"


def test_replacement_excludes_protected_pending_higher_and_cooldown():
    for incumbent in (_inc(protected=True), _inc(pending=True), _inc(higher=True)):
        assert _replace(incumbents=[incumbent]).status == "NO_ACTION"
    assert _replace(candidates=[_candidate(cooldown=True)]).status == "NO_ACTION"


def test_replacement_stable_candidate_tiebreak_and_no_buy_order():
    result = _replace(candidates=[_candidate("600003"), _candidate("600002")])
    assert result.candidate_symbol == "600002.SH"
    assert result.intent.reason == "REPLACEMENT_EXIT"


def test_replacement_rejects_float_decimal_and_normalized_duplicates():
    bad = ReplacementCandidate("600002", 52.0, D("50"), D("0.2"), D("0.1"),
                               D("0.3"), 1000, False)
    assert _replace(candidates=[bad]).failure_reason == "invalid_replacement_candidate"
    assert _replace(incumbents=[_inc("600001"), _inc("600001.SH")]).status == "INVALID"
