"""Deterministic A-share event clock for the Phase 5 run loop."""

from __future__ import annotations

from datetime import date
from hashlib import sha256
from typing import Iterable

from .minute_contract import LEGAL_BAR_START_TIMES
from .phase5_models import ClockEvent


def deterministic_id(kind: str, *parts: object) -> str:
    payload = "|".join([kind, *(str(part) for part in parts)])
    return f"{kind.lower()}_{sha256(payload.encode('utf-8')).hexdigest()}"


def build_event_clock(run_id: str, trading_dates: Iterable[date]) -> tuple[ClockEvent, ...]:
    events: list[ClockEvent] = []
    global_sequence = 0
    for day in sorted(set(trading_dates)):
        rows: list[tuple[str, str, str]] = [("09:25", "SESSION_START", "")]
        for bar_time in LEGAL_BAR_START_TIMES:
            stamp = bar_time.strftime("%H:%M")
            rows.append((stamp, "BAR_OPEN", stamp))
            if stamp == "10:00":
                rows.append((stamp, "DECISION_1000", stamp))
            if stamp == "14:30":
                rows.append((stamp, "DECISION_1430", stamp))
            rows.append((stamp, "BAR_CLOSE", stamp))
        rows.append(("15:00", "SESSION_CLOSE", ""))
        for event_time, event_type, bar_start in rows:
            events.append(
                ClockEvent(
                    event_id=deterministic_id("event", run_id, day.isoformat(), global_sequence, event_type),
                    trade_date=day,
                    event_time=event_time,
                    event_type=event_type,
                    sequence_number=global_sequence,
                    bar_start=bar_start,
                )
            )
            global_sequence += 1
    return tuple(events)
