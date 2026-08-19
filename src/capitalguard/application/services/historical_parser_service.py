"""Non-operational parser for historical channel messages."""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from capitalguard.application.services.financial_consistency_service import FinancialConsistencyService
from capitalguard.application.services.parsing_service import ParsingService


@dataclass(frozen=True)
class HistoricalParseResult:
    parse_status: str
    parser_path: str
    confidence_score: Decimal
    data: dict[str, Any]
    errors: tuple[str, ...]


class HistoricalParserService:
    """Uses shared normalization rules but never calls CreationService."""

    _NUMBER = r"[0-9٠-٩][0-9٠-٩,]*(?:\.[0-9٠-٩]+)?[KkMmBb]?"

    def __init__(
        self,
        parsing_service: ParsingService,
        consistency_service: FinancialConsistencyService | None = None,
    ):
        self.parsing_service = parsing_service
        self.consistency_service = consistency_service or FinancialConsistencyService()

    def _number_after(self, text: str, labels: str) -> Decimal | None:
        match = re.search(rf"(?:{labels})\s*[:=\-]?\s*({self._NUMBER})", text, re.IGNORECASE)
        return self.parsing_service._parse_one_number(match.group(1)) if match else None

    def _target_tokens(self, text: str) -> list[str]:
        matches = re.findall(
            rf"(?:TP\s*\d*|TARGET\s*\d*)\s*[:=\-]?\s*({self._NUMBER}(?:\s*@\s*\d+(?:\.\d+)?\s*%?)?)",
            text,
            re.IGNORECASE,
        )
        return [re.sub(r"\s+", "", match) for match in matches]

    def parse(self, raw_text: str) -> HistoricalParseResult:
        cleaned = self.parsing_service._normalize_text(raw_text)
        asset, side = self.parsing_service._find_asset_and_side(cleaned)
        if not side:
            asset = None
        entry = self._number_after(cleaned, r"ENTRY|IN|دخول")
        stop_loss = self._number_after(cleaned, r"STOP(?:\s*LOSS)?|SL|وقف")
        target_tokens = self._target_tokens(cleaned)
        targets = self.parsing_service._parse_targets_list(target_tokens)
        errors: list[str] = []
        if not asset:
            errors.append("ASSET_NOT_FOUND")
        if not side:
            errors.append("SIDE_NOT_FOUND")
        if entry is None:
            errors.append("ENTRY_NOT_FOUND")
        if stop_loss is None:
            errors.append("STOP_NOT_FOUND")
        if not targets:
            errors.append("TARGETS_NOT_FOUND")

        consistency = self.consistency_service.check(
            side=side,
            entry=entry,
            stop_loss=stop_loss,
            targets=targets,
        )
        errors.extend(consistency.errors)
        fields_present = int(bool(asset and side)) + int(entry is not None) + int(stop_loss is not None) + int(bool(targets))
        confidence = Decimal(str(min(1.0, fields_present / 4))).quantize(Decimal("0.0001"))
        if asset and side and entry is not None and stop_loss is not None and targets and consistency.is_consistent:
            status = "PARSED"
        elif fields_present:
            status = "PARTIAL"
        else:
            status = "UNPARSED"
        return HistoricalParseResult(
            parse_status=status,
            parser_path="shared_normalization_regex",
            confidence_score=confidence,
            data={
                "asset": asset,
                "side": side,
                "entry": entry,
                "stop_loss": stop_loss,
                "targets": targets,
                "raw_text": raw_text,
                "financial_consistency": {
                    "is_consistent": consistency.is_consistent,
                    "errors": list(consistency.errors),
                    "warnings": list(consistency.warnings),
                },
            },
            errors=tuple(errors),
        )
