from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace(path: str, old: str, new: str) -> None:
    p = ROOT / path
    text = p.read_text()
    if old not in text:
        raise SystemExit(f"required patch anchor not found: {path}: {old[:100]!r}")
    p.write_text(text.replace(old, new, 1))


replay = "src/capitalguard/application/services/historical_market_replay_service.py"
replace(replay,
'''from .adaptive_historical_replay import AdaptiveHistoricalReplayPlanner, LifecycleState\nfrom .historical_signal_service import HistoricalSignalService, HistoricalSignalValidationError\n\n\nREPLAY_VERSION = "G6-R1"\nREPLAY_POLICY_VERSION = "G6-OHLCV-MARKET-GRID-2"''',
'''from .adaptive_historical_replay import AdaptiveHistoricalReplayPlanner, LifecycleState\nfrom .historical_replay_decision_service import HistoricalReplayDecisionAuthority\nfrom .historical_replay_version import REPLAY_POLICY_VERSION, REPLAY_VERSION\nfrom .historical_signal_service import HistoricalSignalService, HistoricalSignalValidationError''')

replace(replay,
'''        retry_of_fingerprint: str | None = None,\n    ) -> tuple[HistoricalReplayRun, bool]:''',
'''        retry_of_fingerprint: str | None = None,\n        reprocess_of_run_id: int | None = None,\n    ) -> tuple[HistoricalReplayRun, bool]:''')
replace(replay,
'''            policy_version=REPLAY_POLICY_VERSION,\n            status="RUNNING",''',
'''            policy_version=REPLAY_POLICY_VERSION,\n            reprocess_of_run_id=reprocess_of_run_id,\n            status="RUNNING",''')

old_retry = '''    def retry_g6(self, session: Session, *, receipt_id: int, provider=None) -> dict:\n        receipt = session.get(HistoricalForwardReceipt, receipt_id)\n        if receipt is None or receipt.evidence_id is None:\n            raise HistoricalSignalValidationError("Historical receipt does not have replayable evidence")\n        evidence = session.get(HistoricalSignalEvidence, receipt.evidence_id)\n        if evidence is None or not evidence.signals:\n            raise HistoricalSignalValidationError("Historical receipt does not have a materialized signal")\n        signal = sorted(evidence.signals, key=lambda item: item.id)[0]\n        materialization = session.execute(select(HistoricalSignalMaterialization).where(HistoricalSignalMaterialization.signal_id == signal.id).order_by(HistoricalSignalMaterialization.id.desc())).scalars().first()\n        if materialization is None:\n            raise HistoricalSignalValidationError("Historical signal has no G5 materialization")\n        previous = session.execute(select(HistoricalReplayRun).where(HistoricalReplayRun.signal_id == signal.id, HistoricalReplayRun.materialization_id == materialization.id).order_by(HistoricalReplayRun.started_at.desc(), HistoricalReplayRun.id.desc())).scalars().first()\n        if previous is None:\n            raise HistoricalSignalValidationError("Historical receipt has no previous G6 replay")\n        if previous.status not in {"REPLAY_PARTIAL", "FAILED"}:\n            return {"run": previous, "events": list(previous.events or []), "status": previous.status, "replayed": True, "retry_skipped": True}\n        return self.replay_g6(session, signal_id=signal.id, materialization_id=materialization.id, start=previous.window_start, replay_end=previous.window_end, interval=previous.interval, limit=previous.limit_count, provider=provider, retry_of_fingerprint=previous.request_fingerprint)\n'''
new_retry = '''    def retry_g6(self, session: Session, *, receipt_id: int, provider=None) -> dict:\n        """Re-evaluate a persisted replay through the single Decision Authority.\n\n        This method deliberately has no status-only retry gate. A duplicate receipt\n        may therefore resolve to REUSE, or create a new non-destructive ReplayRun.\n        """\n        import logging\n\n        receipt = session.get(HistoricalForwardReceipt, receipt_id)\n        if receipt is None or receipt.evidence_id is None:\n            raise HistoricalSignalValidationError("Historical receipt does not have replayable evidence")\n        evidence = session.get(HistoricalSignalEvidence, receipt.evidence_id)\n        if evidence is None or not evidence.signals:\n            raise HistoricalSignalValidationError("Historical receipt does not have a materialized signal")\n        signal = sorted(evidence.signals, key=lambda item: item.id)[0]\n        materialization = session.execute(\n            select(HistoricalSignalMaterialization)\n            .where(HistoricalSignalMaterialization.signal_id == signal.id)\n            .order_by(HistoricalSignalMaterialization.id.desc())\n        ).scalars().first()\n        if materialization is None:\n            raise HistoricalSignalValidationError("Historical signal has no G5 materialization")\n        previous = session.execute(\n            select(HistoricalReplayRun)\n            .where(\n                HistoricalReplayRun.signal_id == signal.id,\n                HistoricalReplayRun.materialization_id == materialization.id,\n            )\n            .order_by(HistoricalReplayRun.started_at.desc(), HistoricalReplayRun.id.desc())\n        ).scalars().first()\n\n        authority = HistoricalReplayDecisionAuthority()\n        decision = authority.decide(previous)\n        logging.getLogger(__name__).info(\n            "G6 replay decision action=%s reason=%s previous_run=%s previous_status=%s previous_version=%s current_version=%s previous_policy=%s current_policy=%s coverage=%s",\n            decision.action.value, decision.reason.value, decision.previous_run_id,\n            decision.previous_status, decision.previous_replay_version,\n            decision.current_replay_version, decision.previous_policy_version,\n            decision.current_policy_version, decision.coverage.reason,\n        )\n\n        if not decision.is_reprocess and previous is not None:\n            return {\n                "run": previous,\n                "events": list(previous.events or []),\n                "status": previous.status,\n                "replayed": True,\n                "retry_skipped": True,\n                "decision": decision,\n            }\n\n        if previous is None:\n            start = self._utc(signal.decision_timestamp)\n            replay_end = datetime.now(timezone.utc)\n            retry_of_fingerprint = None\n            reprocess_of_run_id = None\n            interval = "1m"\n            limit = 1500\n        else:\n            start = previous.window_start\n            replay_end = previous.window_end\n            retry_of_fingerprint = previous.request_fingerprint\n            reprocess_of_run_id = previous.id\n            interval = previous.interval\n            limit = previous.limit_count\n\n        result = self.replay_g6(\n            session,\n            signal_id=signal.id,\n            materialization_id=materialization.id,\n            start=start,\n            replay_end=replay_end,\n            interval=interval,\n            limit=limit,\n            provider=provider,\n            retry_of_fingerprint=retry_of_fingerprint,\n            reprocess_of_run_id=reprocess_of_run_id,\n        )\n        result["decision"] = decision\n        return result\n'''
replace(replay, old_retry, new_retry)

replace(replay,
'''        retry_of_fingerprint: str | None = None,\n    ) -> dict:\n        """Traverse daily candles in 365-day chunks and drill down only critical days."""''',
'''        retry_of_fingerprint: str | None = None,\n        reprocess_of_run_id: int | None = None,\n    ) -> dict:\n        """Traverse daily candles in 365-day chunks and drill down only critical days."""''')
replace(replay,
'''            start=source_time, replay_end=end_utc, interval="1m", limit=limit,\n            retry_of_fingerprint=retry_of_fingerprint,\n        )''',
'''            start=source_time, replay_end=end_utc, interval="1m", limit=limit,\n            retry_of_fingerprint=retry_of_fingerprint, reprocess_of_run_id=reprocess_of_run_id,\n        )''')

replace(replay,
'''        retry_of_fingerprint: str | None = None,\n    ) -> dict:\n        """Run G6 from an existing G5 materialization; caller owns commit/rollback."""''',
'''        retry_of_fingerprint: str | None = None,\n        reprocess_of_run_id: int | None = None,\n    ) -> dict:\n        """Run G6 from an existing G5 materialization; caller owns commit/rollback."""''')
replace(replay,
'''            return self._replay_g6_annual_adaptive(session, signal_id=signal_id, materialization_id=materialization_id, start=start_utc, replay_end=end_utc, limit=limit, provider=provider, retry_of_fingerprint=retry_of_fingerprint)''',
'''            return self._replay_g6_annual_adaptive(session, signal_id=signal_id, materialization_id=materialization_id, start=start_utc, replay_end=end_utc, limit=limit, provider=provider, retry_of_fingerprint=retry_of_fingerprint, reprocess_of_run_id=reprocess_of_run_id)''')
replace(replay,
'''        run, created = self._get_or_create_run(session, signal_id=signal_id, materialization_id=materialization_id, start=start_utc, replay_end=end_utc, interval=interval, limit=limit, retry_of_fingerprint=retry_of_fingerprint)''',
'''        run, created = self._get_or_create_run(session, signal_id=signal_id, materialization_id=materialization_id, start=start_utc, replay_end=end_utc, interval=interval, limit=limit, retry_of_fingerprint=retry_of_fingerprint, reprocess_of_run_id=reprocess_of_run_id)''')

old_status = '''        if lifecycle_state in terminal_states:\n            run.status = "COMPLETED" if resolution_quality == "VERIFIED" else "COMPLETED_UNVERIFIABLE"\n            run.termination_reason = "LIFECYCLE_COMPLETED"\n            run.exit_timestamp = getattr(lifecycle_terminal_event, "event_timestamp", None)\n        elif coverage.status in {CoverageStatus.PARTIAL_WINDOW, CoverageStatus.GAPPED}:'''
new_status = '''        if lifecycle_state in terminal_states:\n            run.status = "COMPLETED" if resolution_quality == "VERIFIED" else "COMPLETED_UNVERIFIABLE"\n            run.termination_reason = "LIFECYCLE_COMPLETED"\n            run.exit_timestamp = getattr(lifecycle_terminal_event, "event_timestamp", None)\n        elif lifecycle_state == "NOT_ACTIVATED" and coverage.status == CoverageStatus.FULL:\n            run.status = "COMPLETED"\n            run.termination_reason = "HORIZON_REACHED_UNTRIGGERED"\n            run.exit_timestamp = end_utc\n        elif coverage.status in {CoverageStatus.PARTIAL_WINDOW, CoverageStatus.GAPPED}:'''
replace(replay, old_status, new_status)

# Add explicit evidence fields to the replay result so the Decision Authority can
# distinguish a proven pending order from an activation-ambiguous gap.
replace(replay,
'''"lifecycle_status": lifecycle_state, "resolution_quality": resolution_quality, "termination_reason": run.termination_reason,''',
'''"lifecycle_status": lifecycle_state, "resolution_quality": resolution_quality, "activation_risk": bool(lifecycle_state == "NOT_ACTIVATED" and coverage.status != CoverageStatus.FULL), "entry_ambiguity": bool(lifecycle_state == "NOT_ACTIVATED" and coverage.status != CoverageStatus.FULL), "termination_reason": run.termination_reason,''')

forwarding = "src/capitalguard/application/services/historical_forwarding_service.py"
replace(forwarding,
'''                if replay_status in {"FAILED", "REPLAY_PARTIAL"} and previous_receipt_id:\n                    try:\n                        healed = self.replay_service.retry_g6(session, receipt_id=int(previous_receipt_id), provider=provider)''',
'''                # Duplicate identity is resolved before G6. The replay service is now\n                # the single Decision Authority: it may REUSE a valid current run or\n                # create a new non-destructive run for any stale/failed/partial case.\n                if previous_receipt_id:\n                    try:\n                        healed = self.replay_service.retry_g6(session, receipt_id=int(previous_receipt_id), provider=provider)''')

p = ROOT / replay
text = p.read_text()
if "reprocess_of_run_id=reprocess_of_run_id" not in text:
    raise SystemExit("replay lineage wiring was not applied")
if "HistoricalReplayDecisionAuthority" not in text:
    raise SystemExit("decision authority wiring was not applied")

print("G6 contract bootstrap patch applied")
