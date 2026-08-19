from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from capitalguard.infrastructure.db.models import HistoricalSignal, HistoricalSignalEvent


@dataclass(frozen=True)
class HistoricalReputationSummary:
    analyst_id: int | None
    channel_id: int | None
    total_signals: int
    verified_signals: int
    rank_eligible_signals: int
    excluded_signals: int
    verified_replay_events: int
    confidence_weighted_sample: Decimal
    winning_signals: int = 0
    losing_signals: int = 0
    unfilled_signals: int = 0
    pnl_sum_percent: Decimal = Decimal("0")
    win_rate_percent: Decimal = Decimal("0")


class HistoricalReputationService:
    """Read-only summary; it never writes to live AnalystStats."""

    @staticmethod
    def _pnl_percent(signal: HistoricalSignal, events: list[HistoricalSignalEvent]) -> Decimal:
        entry = Decimal(str(signal.entry)) if signal.entry is not None else None
        if entry is None or entry <= 0:
            return Decimal("0")
        side = str(signal.side or "").upper()
        targets = signal.targets if isinstance(signal.targets, list) else []
        pnl = Decimal("0")
        closed_percent = Decimal("0")
        for event in sorted(events, key=lambda item: item.event_timestamp):
            if event.event_type.startswith("TP") and event.price is not None:
                index = int(event.event_type[2:]) - 1
                close_percent = Decimal("0")
                if 0 <= index < len(targets) and isinstance(targets[index], dict):
                    close_percent = Decimal(str(targets[index].get("close_percent") or 0))
                if close_percent <= 0 and index == len(targets) - 1:
                    close_percent = Decimal("100") - closed_percent
                move = (Decimal(str(event.price)) - entry) / entry
                if side == "SHORT":
                    move = -move
                pnl += move * close_percent
                closed_percent += close_percent
            elif event.event_type == "SL" and event.price is not None:
                remaining = max(Decimal("0"), Decimal("100") - closed_percent)
                move = (Decimal(str(event.price)) - entry) / entry
                if side == "SHORT":
                    move = -move
                pnl += move * remaining
        return (pnl).quantize(Decimal("0.0001"))

    @classmethod
    def summarize(
        cls,
        session: Session,
        *,
        analyst_id: int | None = None,
        channel_id: int | None = None,
    ) -> HistoricalReputationSummary:
        statement = select(HistoricalSignal)
        if analyst_id is not None:
            statement = statement.where(HistoricalSignal.analyst_id == analyst_id)
        if channel_id is not None:
            statement = statement.where(HistoricalSignal.channel_id == channel_id)
        signals = list(session.execute(statement).scalars().all())
        verified_tiers = {"VERIFIED_LIVE", "VERIFIED_HISTORY", "RECONSTRUCTED"}
        verified = [signal for signal in signals if signal.trust_tier in verified_tiers]
        eligible = [
            signal for signal in signals
            if signal.eligible_for_ranking and signal.trust_tier in verified_tiers
        ]
        weighted = sum(
            (Decimal(str(signal.confidence_score or 0)) for signal in eligible),
            Decimal("0"),
        )
        verified_events = session.execute(
            select(HistoricalSignalEvent).where(
                HistoricalSignalEvent.signal_id.in_([signal.id for signal in signals]),
                HistoricalSignalEvent.replay_status == "VERIFIED",
            )
        ).scalars().all() if signals else []
        events_by_signal: dict[int, list[HistoricalSignalEvent]] = {signal.id: [] for signal in signals}
        for event in verified_events:
            events_by_signal.setdefault(event.signal_id, []).append(event)
        winning = 0
        losing = 0
        unfilled = 0
        pnl_sum = Decimal("0")
        terminal_count = 0
        for signal in signals:
            events = events_by_signal.get(signal.id, [])
            event_types = {event.event_type for event in events}
            if "ACTIVATED" not in event_types:
                unfilled += 1
                continue
            if "SL" in event_types:
                losing += 1
                terminal_count += 1
                pnl_sum += cls._pnl_percent(signal, events)
                continue
            target_count = len(signal.targets) if isinstance(signal.targets, list) else 0
            hit_target_count = len({event.event_type for event in events if event.event_type.startswith("TP")})
            if target_count and hit_target_count >= target_count:
                winning += 1
                terminal_count += 1
                pnl_sum += cls._pnl_percent(signal, events)
        win_rate = (Decimal(winning) / Decimal(terminal_count) * Decimal("100")) if terminal_count else Decimal("0")
        return HistoricalReputationSummary(
            analyst_id=analyst_id,
            channel_id=channel_id,
            total_signals=len(signals),
            verified_signals=len(verified),
            rank_eligible_signals=len(eligible),
            excluded_signals=len(signals) - len(eligible),
            verified_replay_events=len(verified_events),
            confidence_weighted_sample=weighted,
            winning_signals=winning,
            losing_signals=losing,
            unfilled_signals=unfilled,
            pnl_sum_percent=pnl_sum.quantize(Decimal("0.0001")),
            win_rate_percent=win_rate.quantize(Decimal("0.0001")),
        )
