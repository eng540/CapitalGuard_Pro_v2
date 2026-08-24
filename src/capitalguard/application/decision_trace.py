"""Trace contract for G7 operational decisions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping


DECISION_TRACE_CONTRACT_VERSION = "g7.con.02"


@dataclass(frozen=True)
class DecisionTrace:
    """Stable provenance for a decision handoff."""

    trace_id: str
    source_ref: str
    correlation_id: str
    causation_id: str | None
    input_hash: str
    contract_version: str = DECISION_TRACE_CONTRACT_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "source_ref": self.source_ref,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "input_hash": self.input_hash,
            "contract_version": self.contract_version,
        }

    @classmethod
    def build(
        cls,
        *,
        source_ref: str,
        input_payload: Mapping[str, Any],
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> "DecisionTrace":
        normalized_source = source_ref.strip()
        if not normalized_source:
            raise ValueError("source_ref is required for decision traceability")
        encoded = json.dumps(dict(input_payload), sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        input_hash = hashlib.sha256(encoded).hexdigest()
        normalized_correlation = (correlation_id or input_hash).strip()
        if not normalized_correlation:
            raise ValueError("correlation_id must not be empty")
        trace_seed = f"{normalized_source}:{normalized_correlation}:{causation_id or ''}:{input_hash}".encode("utf-8")
        trace_id = hashlib.sha256(trace_seed).hexdigest()
        return cls(
            trace_id=trace_id,
            source_ref=normalized_source,
            correlation_id=normalized_correlation,
            causation_id=causation_id.strip() if causation_id else None,
            input_hash=input_hash,
        )
