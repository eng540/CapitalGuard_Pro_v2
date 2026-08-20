from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from capitalguard.domain.temporal import TimelineRelation, utc


@dataclass(frozen=True)
class TimelineCandidate:
    signal_id: int
    source_chat_id: int | None
    source_message_id: int | None
    asset: str | None
    side: str | None
    source_time: datetime | None
    terminal: bool = False


@dataclass(frozen=True)
class TimelineResolution:
    relation: TimelineRelation
    parent_signal_id: int | None
    conflict: bool
    reason_codes: tuple[str, ...]


class SignalTimelineResolver:
    """Resolves event-to-signal causality without mutating prior events."""

    DEFAULT_MAX_GAP_SECONDS = 24 * 60 * 60

    EVENT_RELATIONS = {
        "UPDATE": TimelineRelation.AMENDMENT,
        "AMENDMENT": TimelineRelation.AMENDMENT,
        "ENTRY_UPDATE": TimelineRelation.ENTRY_UPDATE,
        "STOP_UPDATE": TimelineRelation.STOP_UPDATE,
        "TARGET_UPDATE": TimelineRelation.TARGET_UPDATE,
        "PARTIAL_EXIT": TimelineRelation.PARTIAL_EXIT,
        "TARGET_HIT": TimelineRelation.TARGET_HIT,
        "CLOSE": TimelineRelation.CLOSE,
        "CLOSED": TimelineRelation.CLOSE,
        "FINAL_CLOSE": TimelineRelation.CLOSE,
    }

    @classmethod
    def _relation(cls, event_kind: str | None) -> TimelineRelation:
        return cls.EVENT_RELATIONS.get(str(event_kind or "").strip().upper(), TimelineRelation.INITIAL_SIGNAL)

    @staticmethod
    def _same_asset_side(candidate: TimelineCandidate, asset: str | None, side: str | None) -> bool:
        return (
            (not asset or not candidate.asset or candidate.asset.upper() == asset.upper())
            and (not side or not candidate.side or candidate.side.upper() == side.upper())
        )

    def resolve(
        self,
        *,
        source_chat_id: int | None,
        source_message_id: int | None,
        reply_to_message_id: int | None,
        source_time: datetime | None,
        asset: str | None,
        side: str | None,
        event_kind: str | None,
        candidates: Iterable[TimelineCandidate] = (),
        max_gap_seconds: int = DEFAULT_MAX_GAP_SECONDS,
    ) -> TimelineResolution:
        if max_gap_seconds <= 0:
            raise ValueError("max_gap_seconds must be positive")
        relation = self._relation(event_kind)
        if relation == TimelineRelation.INITIAL_SIGNAL:
            return TimelineResolution(relation, None, False, ("NO_EVENT_RELATION",))

        items = list(candidates)
        exact = [
            item
            for item in items
            if source_chat_id is not None
            and item.source_chat_id == source_chat_id
            and reply_to_message_id is not None
            and item.source_message_id == reply_to_message_id
        ]
        if len(exact) == 1:
            return TimelineResolution(relation, exact[0].signal_id, False, ("REPLY_PARENT_MATCH",))
        if len(exact) > 1:
            return TimelineResolution(
                relation,
                None,
                True,
                ("MULTIPLE_REPLY_PARENTS", "MANUAL_RECONCILIATION_REQUIRED"),
            )

        current_time = utc(source_time)
        nearby = []
        for item in items:
            candidate_time = utc(item.source_time)
            if current_time is None or candidate_time is None:
                continue
            if candidate_time > current_time:
                continue
            if (current_time - candidate_time).total_seconds() > max_gap_seconds:
                continue
            if item.terminal:
                continue
            if self._same_asset_side(item, asset, side):
                nearby.append(item)

        if len(nearby) == 1:
            return TimelineResolution(relation, nearby[0].signal_id, False, ("ASSET_SIDE_TIME_MATCH",))
        if len(nearby) > 1:
            return TimelineResolution(
                relation,
                None,
                True,
                ("MULTIPLE_ASSET_TIME_PARENTS", "MANUAL_RECONCILIATION_REQUIRED"),
            )
        return TimelineResolution(relation, None, True, ("PARENT_NOT_FOUND", "MANUAL_RECONCILIATION_REQUIRED"))
