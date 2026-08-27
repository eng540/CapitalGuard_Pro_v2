"""Non-operational parser for historical channel messages."""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from capitalguard.application.services.financial_consistency_service import FinancialConsistencyService
from capitalguard.application.services.historical_outcome_reconciliation_service import HistoricalOutcomeReconciliationService
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
        self.outcome_service = HistoricalOutcomeReconciliationService()

    def _number_after(self, text: str, labels: str) -> Decimal | None:
        match = re.search(rf"(?:{labels})\s*[:=\-]?\s*({self._NUMBER})", text, re.IGNORECASE)
        return self.parsing_service._parse_one_number(match.group(1)) if match else None

    def _signed_number_after(self, text: str, labels: str) -> Decimal | None:
        match = re.search(rf"(?:{labels})\s*[:=\-]?\s*([+-]?{self._NUMBER})", text, re.IGNORECASE)
        return self.parsing_service._parse_one_number(match.group(1)) if match else None

    def _reported_pnl(self, text: str) -> Decimal | None:
        match = re.search(
            r"(?:RESULT|PNL|PROFIT|LOSS|النتيجة|الربح|الخسارة)[^%\n]*?([+-]?" + self._NUMBER + r")\s*%",
            text,
            re.IGNORECASE,
        )
        return self.parsing_service._parse_one_number(match.group(1)) if match else None

    def _target_tokens(self, text: str) -> list[str]:
        # Consume a target index only when it is structurally separated from
        # the price (for example `TP1:78K` or `TP 1 78K`).  Without the
        # lookahead, `TP 78K` consumes the leading `7` as an index and leaves
        # `8K`, corrupting the canonical target value.
        target_marker = r"(?:(?:TP|TARGET)(?:[ \t]*\d+)?(?=[ \t]*[:=\-])|(?:TP|TARGET)\d+(?=[ \t]+[0-9٠-٩])|(?:TP|TARGET)[ \t]+\d+(?=[ \t]+[0-9٠-٩])|(?:TP|TARGET)(?=[ \t]+[0-9٠-٩]))"
        matches = re.findall(
            rf"{target_marker}\s*[:=\-]?\s*({self._NUMBER}(?:\s*@\s*\d+(?:\.\d+)?\s*%?)?)",
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
        exit_price = self._number_after(cleaned, r"EXIT(?:\s*PRICE)?|CLOSE(?:\s*PRICE)?|الخروج|إغلاق")
        reported_pnl_pct = self._reported_pnl(cleaned)
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

        outcome = None
        if exit_price is not None or reported_pnl_pct is not None:
            outcome = self.outcome_service.check_reported_outcome(
                side=side,
                entry=entry,
                exit_price=exit_price,
                reported_pnl_pct=reported_pnl_pct,
            )

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
                "exit_price": exit_price,
                "reported_pnl_pct": reported_pnl_pct,
                "financial_outcome": {
                    "status": outcome.status,
                    "derived_pnl_pct": str(outcome.derived_pnl_pct) if outcome and outcome.derived_pnl_pct is not None else None,
                    "reported_pnl_pct": str(outcome.reported_pnl_pct) if outcome and outcome.reported_pnl_pct is not None else None,
                    "errors": list(outcome.errors) if outcome else [],
                    "warnings": list(outcome.warnings) if outcome else [],
                } if outcome else None,
                "raw_text": raw_text,
                "financial_consistency": {
                    "is_consistent": consistency.is_consistent,
                    "errors": list(consistency.errors),
                    "warnings": list(consistency.warnings),
                },
            },
            errors=tuple(errors),
        )
