"""Non-replaceable bridges that call the frozen Phase 1--4B engines directly."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time
from decimal import Decimal
from typing import Any, Callable, Mapping

import pandas as pd

from .cooldown import cooldown_blocked
from .divergence import calculate_divergence
from .execution_gate import calculate_execution_gate
from .exit_coordinator import coordinate_1430_exit_cycle
from .exit_engine import build_exit_fill_request, evaluate_hard_exit
from .market_overlay import calculate_market_overlay
from .opportunity_score import calculate_opportunity_scores
from .pending_sell import create_or_merge_pending, initial_pending_attempt
from .phase3_models import ExecutionType, FillRequest
from .phase4_models import CandidateInput
from .portfolio_allocator import allocate_portfolio
from .position_sizing import calculate_candidate_sizing
from .risk_overlay import calculate_risk_overlay
from .t1_tail_risk import calculate_t1_risk


ORDER_1000 = (
    "market_overlay","opportunity_score","divergence","risk_overlay",
    "execution_gate","t1_risk","position_sizing","portfolio_allocator",
)


def run_1000_strategy(
    raw: Mapping[str, Any], state: Mapping[str, Any], event,
    observe: Callable[[str, Mapping[str, Any]], None],
) -> Mapping[str, Any]:
    results: dict[str, Any] = {}
    observe("market_overlay",state)
    results["market_overlay"] = calculate_market_overlay(**dict(raw["market_overlay"]))
    observe("opportunity_score",state)
    results["opportunity_score"] = calculate_opportunity_scores(**dict(raw["opportunity_score"]))
    divergences = {}
    observe("divergence",state)
    for item in raw.get("divergence",()):
        values=dict(item); symbol=values.pop("symbol")
        divergences[symbol]=calculate_divergence(**values)
    results["divergence"]=divergences
    risks={}
    observe("risk_overlay",state)
    opportunities = _opportunity_rows(results["opportunity_score"])
    for item in raw.get("risk_overlay",()):
        values=dict(item); symbol=values.pop("symbol")
        values["opportunity"]=opportunities[symbol]
        values["divergence"]=divergences[symbol]
        risks[symbol]=calculate_risk_overlay(**values)
    results["risk_overlay"]=risks
    gates={}
    observe("execution_gate",state)
    for item in raw.get("execution_gate",()):
        values=dict(item); symbol=values.pop("symbol")
        gates[symbol]=calculate_execution_gate(**values)
    results["execution_gate"]=gates
    t1={}
    observe("t1_risk",state)
    for item in raw.get("t1_risk",()):
        values=dict(item); symbol=values.pop("symbol")
        t1[symbol]=calculate_t1_risk(**values)
    results["t1_risk"]=t1
    sizing={}
    derived_candidates=[]
    observe("position_sizing",state)
    for item in raw.get("position_sizing",()):
        values=dict(item)
        candidate=values.get("candidate")
        if not isinstance(candidate,CandidateInput):
            raise ValueError("invalid_candidate_input")
        symbol=candidate.symbol
        candidate=_derived_candidate(
            candidate,opportunities[symbol],results["market_overlay"],
            risks[symbol],gates[symbol],t1[symbol],state,event,
        )
        values["candidate"]=candidate
        sizing[symbol]=calculate_candidate_sizing(**values)
        derived_candidates.append(candidate)
    results["position_sizing"]=sizing
    observe("portfolio_allocator",state)
    allocation_kwargs=dict(raw["portfolio_allocator"])
    allocation_kwargs["candidates"]=tuple(derived_candidates)
    valid_market=results["market_overlay"][
        results["market_overlay"]["status"]=="VALID"
    ]
    if valid_market.empty:
        raise ValueError("market_overlay_invalid")
    allocation_kwargs["effective_exposure_cap"]=Decimal(
        str(valid_market.iloc[-1]["effective_exposure_cap"])
    )
    allocation=allocate_portfolio(**allocation_kwargs)
    results["portfolio_allocator"]=allocation
    candidates={item.symbol:item for item in allocation_kwargs["candidates"]}
    requests=[]
    for sizing_result in allocation.sizing_results:
        if sizing_result.order_qty <= 0 or sizing_result.symbol not in allocation.selected_symbols:
            continue
        candidate=candidates[sizing_result.symbol]
        current=state.get("positions",{}).get(sizing_result.symbol)
        requests.append(FillRequest(
            ExecutionType.ENTRY_BUY,sizing_result.symbol,sizing_result.order_qty,
            pd.Timestamp(datetime.combine(event.trade_date,time(10,0)),tz="Asia/Shanghai"),
            Decimal(str(state.get("cash","0"))),
            0 if current is None else current.total_qty,
            0 if current is None else current.sellable_qty,
        ))
    decisions=tuple(
        (*risks.values(),*gates.values(),*t1.values(),*sizing.values(),allocation)
    )
    # Raw engine outputs can contain DataFrames and are persisted through the
    # normalized decision/fill-request tables, not copied into checkpoint state.
    return {"decisions":decisions,"fill_requests":tuple(requests)}


def run_hard_exit_strategy(raw, state, event, observe):
    output={"decisions":[],"fill_requests":[],"exit_intents":[],"pending_states":[],"exit_controls":[]}
    controls=state.get("exit_controls",{})
    pendings=state.get("pending_sells",{})
    for symbol,position in sorted(state.get("positions",{}).items()):
        observe("hard_exit",{"symbol":symbol,**state})
        if symbol not in controls or symbol not in raw:
            raise ValueError(f"missing_hard_exit_data:{symbol}")
        decision=evaluate_hard_exit(position,controls[symbol],**dict(raw[symbol]))
        output["decisions"].append(decision)
        output["exit_controls"].append(decision.new_control_state)
        intent=decision.selected_intent
        if intent is None:
            continue
        output["exit_intents"].append(intent)
        executable=min(decision.executable_qty,position.sellable_qty)
        if executable > 0:
            output["fill_requests"].append(build_exit_fill_request(
                intent,executable_qty=executable,position_qty=position.total_qty,
                sellable_qty=position.sellable_qty,
            ))
        next_attempt = (
            pd.Timestamp(intent.trigger_bar_start) + pd.Timedelta(minutes=5)
            if executable > 0 else initial_pending_attempt(intent,raw.get("_trading_calendar",()))
        )
        update=create_or_merge_pending(
            pendings.get(symbol),intent,total_qty=position.total_qty,
            remaining_qty=intent.requested_target_qty,next_attempt_at=next_attempt,
        )
        if update.new_state is not None:
            output["pending_states"].append(update.new_state)
    return output


def run_1430_strategy(raw, state, event, trading_calendar, observe):
    observe("coordinate_1430",state)
    values=dict(raw)
    values["pending_sells"]=tuple(state.get("pending_sells",{}).values())
    values["decision_trade_date"]=event.trade_date
    values["trading_calendar"]=trading_calendar
    result=coordinate_1430_exit_cycle(**values)
    return {
        "decisions":(result,),
        "fill_requests":result.fill_requests,
        "exit_intents":tuple(value for _,value in result.intents_by_symbol),
        "pending_states":tuple(
            update.new_state for update in result.pending_updates if update.new_state is not None
        ),
        "exit_controls":tuple(value for _,value in result.control_states),
    }


def _opportunity_rows(frame):
    return {
        str(row["symbol"]):row
        for row in frame.to_dict("records")
        if str(row.get("status",""))=="VALID"
    }


def _derived_candidate(candidate, opportunity, market_frame, risk, gate, t1, state, event):
    valid_market=market_frame[market_frame["status"]=="VALID"]
    if valid_market.empty:
        raise ValueError("market_overlay_invalid")
    market=valid_market.iloc[-1]
    return replace(
        candidate,
        opportunity_status=str(opportunity["status"]),
        opportunity_score=Decimal(str(opportunity["opportunity_score"])),
        entry_threshold=Decimal(str(market["entry_threshold"])),
        opportunity_rank=int(opportunity["opportunity_rank"]),
        rs60=Decimal(str(opportunity["rs60"])),
        rs20=Decimal(str(opportunity["rs20"])),
        signed_er20=Decimal(str(opportunity["signed_er20"])),
        market_paused=bool(market["pause_new_entries"]),
        risk_overlay=risk.risk_status.value,
        execution_gate=gate.execution_gate.value,
        t1_risk_status=t1.status.value,
        t1_loss_q=t1.t1_loss_q,
        cooldown_blocked=(
            cooldown_blocked(
                state.get("cooldowns",{}).get(candidate.symbol),event.trade_date
            )
            or getattr(
                getattr(state.get("pending_sells",{}).get(candidate.symbol),"status",None),
                "value","",
            ) == "ACTIVE"
        ),
    )
