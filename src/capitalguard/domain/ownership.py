"""G7 ownership contract for truth, decisions, and side effects.

This module is deliberately infrastructure-free. It describes ownership only; it
must not perform persistence, commits, network calls, or runtime orchestration.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


OWNERSHIP_CONTRACT_VERSION = "g7.own.01"


class TruthLayer(StrEnum):
    SOURCE_TRUTH = "SOURCE_TRUTH"
    SEMANTIC_TRUTH = "SEMANTIC_TRUTH"
    MARKET_FACT = "MARKET_FACT"
    ANALYTICAL_RESULT = "ANALYTICAL_RESULT"
    RECOMMENDATION = "RECOMMENDATION"
    RISK_DECISION = "RISK_DECISION"
    EXECUTION_STATE = "EXECUTION_STATE"
    PERFORMANCE_RESULT = "PERFORMANCE_RESULT"


class Responsibility(StrEnum):
    SOURCE_RECEIPT = "SOURCE_RECEIPT"
    SEMANTIC_MATERIALIZATION = "SEMANTIC_MATERIALIZATION"
    SEMANTIC_ACCEPTANCE = "SEMANTIC_ACCEPTANCE"
    HISTORICAL_SIGNAL = "HISTORICAL_SIGNAL"
    REPLAY_MARKET_EVIDENCE = "REPLAY_MARKET_EVIDENCE"
    RECOMMENDATION_CREATION = "RECOMMENDATION_CREATION"
    LIFECYCLE_TRANSITION = "LIFECYCLE_TRANSITION"
    MONITORING_ACTION = "MONITORING_ACTION"
    PUBLICATION_DELIVERY = "PUBLICATION_DELIVERY"
    COMMAND_AUTHORIZATION = "COMMAND_AUTHORIZATION"
    RISK_SIZING = "RISK_SIZING"
    LIVE_EXECUTION = "LIVE_EXECUTION"
    PERFORMANCE_READ_MODEL = "PERFORMANCE_READ_MODEL"
    HISTORICAL_TRUST_RELEASE = "HISTORICAL_TRUST_RELEASE"


@dataclass(frozen=True)
class OwnershipEntry:
    """One explicit owner for one system responsibility."""

    responsibility: Responsibility
    truth_layer: TruthLayer
    owner: str
    write_scope: str
    read_scope: str
    side_effect_scope: str


OWNERSHIP_CONTRACT: tuple[OwnershipEntry, ...] = (
    OwnershipEntry(
        Responsibility.SOURCE_RECEIPT,
        TruthLayer.SOURCE_TRUTH,
        "HistoricalForwardingService / HistoricalMessageFoundationService",
        "raw receipt, revision, source identity, source timestamps",
        "historical ingestion and semantic services",
        "none beyond source persistence",
    ),
    OwnershipEntry(
        Responsibility.SEMANTIC_MATERIALIZATION,
        TruthLayer.SEMANTIC_TRUTH,
        "HistoricalSemanticMaterializationService",
        "candidate projection and field evidence in the existing G4 draft chain",
        "G4 adjudication and owner review",
        "none to live recommendation or execution state",
    ),
    OwnershipEntry(
        Responsibility.SEMANTIC_ACCEPTANCE,
        TruthLayer.SEMANTIC_TRUTH,
        "HistoricalAdjudicationService",
        "draft review/adjudication state",
        "G5 materialization boundary",
        "none to public or commercial release",
    ),
    OwnershipEntry(
        Responsibility.HISTORICAL_SIGNAL,
        TruthLayer.SEMANTIC_TRUTH,
        "HistoricalSignalMaterializationService",
        "G5 HistoricalSignal and its materialization evidence",
        "G6 replay and historical quality read models",
        "no automatic ranking or trust release",
    ),
    OwnershipEntry(
        Responsibility.REPLAY_MARKET_EVIDENCE,
        TruthLayer.MARKET_FACT,
        "HistoricalMarketReplayService",
        "ReplayRun, replay events, and market evidence",
        "outcome reconciliation and historical read models",
        "none to live trading",
    ),
    OwnershipEntry(
        Responsibility.RECOMMENDATION_CREATION,
        TruthLayer.RECOMMENDATION,
        "CreationService",
        "Recommendation identity, validation, shadow creation, and publication enqueue",
        "lifecycle, monitoring, and read models",
        "outbox enqueue only; no direct delivery in foreground",
    ),
    OwnershipEntry(
        Responsibility.LIFECYCLE_TRANSITION,
        TruthLayer.RECOMMENDATION,
        "LifecycleService",
        "Recommendation and UserTrade state transitions and lifecycle events",
        "monitoring and performance consumers",
        "tracked-trade synchronization and notification orchestration",
    ),
    OwnershipEntry(
        Responsibility.MONITORING_ACTION,
        TruthLayer.ANALYTICAL_RESULT,
        "AlertService + StrategyEngine",
        "runtime trigger index and strategy actions",
        "active recommendation and UserTrade state",
        "lifecycle action requests only; no direct database truth mutation by engine",
    ),
    OwnershipEntry(
        Responsibility.PUBLICATION_DELIVERY,
        TruthLayer.RECOMMENDATION,
        "PublicationOutboxService",
        "delivery ledger, attempts, retries, and sent state",
        "publication and operations read models",
        "Telegram delivery through the notifier",
    ),
    OwnershipEntry(
        Responsibility.COMMAND_AUTHORIZATION,
        TruthLayer.RECOMMENDATION,
        "WebCommandService",
        "typed command audit and idempotency records",
        "owner/trader command handlers",
        "invokes the owning application service; does not become domain truth owner",
    ),
    OwnershipEntry(
        Responsibility.RISK_SIZING,
        TruthLayer.RISK_DECISION,
        "RiskService + CreationService validation",
        "risk calculation and domain validation result",
        "execution boundary and owner review",
        "none unless an explicit execution command is authorized",
    ),
    OwnershipEntry(
        Responsibility.LIVE_EXECUTION,
        TruthLayer.EXECUTION_STATE,
        "AutoTradeService + Binance executors",
        "order outcome events and execution state",
        "lifecycle and operations read models",
        "external exchange calls only behind existing live gates",
    ),
    OwnershipEntry(
        Responsibility.PERFORMANCE_READ_MODEL,
        TruthLayer.PERFORMANCE_RESULT,
        "PerformanceService / PerformanceRepository",
        "activated-portfolio performance projection",
        "authenticated user read models",
        "none to source, recommendation, or execution state",
    ),
    OwnershipEntry(
        Responsibility.HISTORICAL_TRUST_RELEASE,
        TruthLayer.PERFORMANCE_RESULT,
        "HistoricalReputationService / HistoricalTrustReleaseService",
        "historical quality and fail-closed release readiness",
        "owner historical quality/readiness endpoints",
        "cannot enable public ranking or commerce by itself",
    ),
)


def ownership_for(responsibility: Responsibility) -> OwnershipEntry:
    """Return the unique owner for a responsibility or raise ``KeyError``."""

    for entry in OWNERSHIP_CONTRACT:
        if entry.responsibility == responsibility:
            return entry
    raise KeyError(responsibility)


def validate_ownership_contract() -> None:
    """Validate invariants that make the ownership map safe to consume."""

    responsibilities = [entry.responsibility for entry in OWNERSHIP_CONTRACT]
    if len(responsibilities) != len(set(responsibilities)):
        raise ValueError("Every G7 responsibility must have exactly one owner")
    if any(not entry.owner.strip() for entry in OWNERSHIP_CONTRACT):
        raise ValueError("Every G7 responsibility must name a non-empty owner")
    if any(not entry.write_scope.strip() for entry in OWNERSHIP_CONTRACT):
        raise ValueError("Every G7 responsibility must define its write scope")
