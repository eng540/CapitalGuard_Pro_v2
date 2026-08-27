from __future__ import annotations
import re
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.orm import Session
from capitalguard.application.services.parsing_service import ParsingService
from capitalguard.infrastructure.db.models import HistoricalContentInterpretation, HistoricalFinancialCandidate
from capitalguard.infrastructure.db.repository import ParsingRepository


class HistoricalFinancialCandidateService:
    EXTRACTOR_VERSION = "g3-rules-v1"
    def __init__(self): self.parser = ParsingService(ParsingRepository)
    def extract(self, session: Session, *, interpretation_id: int):
        interpretation = session.get(HistoricalContentInterpretation, interpretation_id)
        if interpretation is None: raise ValueError("Content interpretation does not exist")
        text = interpretation.revision.raw_text or ""
        normalized = self.parser._normalize_text(text)
        candidates = []
        asset, side = self.parser._find_asset_and_side(normalized)
        for field, value in (("ASSET", asset), ("DIRECTION", side)):
            if value:
                candidates.append((field, value, value, Decimal("0.9000")))
        labels = {"ENTRY": r"ENTRY|IN|دخول", "STOP_LOSS": r"STOP(?:\s*LOSS)?|SL|وقف", "LEVERAGE": r"LEVERAGE|رافعة", "RISK_PERCENT": r"RISK|مخاطرة"}
        for field, labels_pattern in labels.items():
            matches = list(re.finditer(rf"(?:{labels_pattern})\s*(?::|=|\-|TO|إلى)?\s*([0-9٠-٩][0-9٠-٩,.]*[KkMmBb]?%?x?)", normalized, re.I))
            for match in matches:
                span = match.group(0); token = match.group(1).rstrip("%xX")
                value = self.parser._parse_one_number(token)
                if value is not None: candidates.append((field, {"value": str(value)}, span, Decimal("0.8500")))
        zone = re.search(r"(?:ENTRY\s*(?:ZONE|RANGE)?|دخول)\s*[:=\-]?\s*([0-9٠-٩][0-9٠-٩,.]*[KkMmBb]?)\s*(?:-|TO|إلى)\s*([0-9٠-٩][0-9٠-٩,.]*[KkMmBb]?)", normalized, re.I)
        if zone:
            lower = self.parser._parse_one_number(zone.group(1)); upper = self.parser._parse_one_number(zone.group(2))
            if lower is not None and upper is not None:
                candidates.append(("ENTRY_ZONE", {"lower": str(min(lower, upper)), "upper": str(max(lower, upper))}, zone.group(0), Decimal("0.8000")))
        target_marker = r"(?:(?:TP|TARGET)(?:[ \t]*\d+)?(?=[ \t]*[:=\-])|(?:TP|TARGET)\d+(?=[ \t]+[0-9٠-٩])|(?:TP|TARGET)[ \t]+\d+(?=[ \t]+[0-9٠-٩])|(?:TP|TARGET)(?=[ \t]+[0-9٠-٩]))"
        for index, match in enumerate(re.finditer(rf"{target_marker}\s*[:=\-]?\s*([0-9٠-٩][0-9٠-٩,.]*[KkMmBb]?)", normalized, re.I), start=1):
            value = self.parser._parse_one_number(match.group(1))
            if value is not None: candidates.append(("TARGET", {"index": index, "value": str(value)}, match.group(0), Decimal("0.8500")))
        timeframe = re.search(r"\b(1m|5m|15m|30m|1h|4h|1d|1w)\b", normalized, re.I)
        if timeframe:
            candidates.append(("TIMEFRAME", timeframe.group(1).upper(), timeframe.group(0), Decimal("0.8000")))
        condition = re.search(r"(?:IF|WHEN|إذا|عند)\s+([^\n.]+)", normalized, re.I)
        if condition:
            candidates.append(("CONDITION", condition.group(1).strip(), condition.group(0), Decimal("0.6500")))
        for match in re.finditer(r"\b([0-9٠-٩][0-9٠-٩,.]*)\s*%", normalized):
            value = self.parser._parse_one_number(match.group(1))
            if value is not None and value <= 100:
                candidates.append(("PERCENTAGE", {"value": str(value)}, match.group(0), Decimal("0.8000")))
        for match in re.finditer(r"\b(USD|USDT|EUR|BTC|ETH)\b", normalized, re.I):
            candidates.append(("CURRENCY", match.group(1).upper(), match.group(0), Decimal("0.7500")))
        strategy = re.search(r"(?:STRATEGY|SETUP|استراتيجية)\s*[:=\-]?\s*([^\n.]+)", normalized, re.I)
        if strategy:
            candidates.append(("STRATEGY", strategy.group(1).strip(), strategy.group(0), Decimal("0.6000")))
        saved=[]
        conflicting = {field for field in {candidate[0] for candidate in candidates} if len({str(candidate[1]) for candidate in candidates if candidate[0] == field}) > 1 and field in {"ENTRY", "STOP_LOSS", "LEVERAGE", "RISK_PERCENT"}}
        for field, value, span, confidence in candidates:
            normalized_value = str(value if isinstance(value, str) else value.get("value") or f"{value.get('lower')}:{value.get('upper')}")
            existing=session.execute(select(HistoricalFinancialCandidate).where(HistoricalFinancialCandidate.interpretation_id==interpretation.id, HistoricalFinancialCandidate.field_type==field, HistoricalFinancialCandidate.normalized_value==normalized_value, HistoricalFinancialCandidate.span_text==span, HistoricalFinancialCandidate.extractor_version==self.EXTRACTOR_VERSION)).scalar_one_or_none()
            if existing: saved.append(existing); continue
            item=HistoricalFinancialCandidate(interpretation_id=interpretation.id, field_type=field, value_json={"value": value} if isinstance(value,str) else value, normalized_value=normalized_value, span_text=span, confidence_score=confidence, status="CONFLICT" if field in conflicting else "CANDIDATE", extractor_version=self.EXTRACTOR_VERSION, review_status="REVIEW_REQUIRED" if field in conflicting else "PENDING", provenance_json={"revision_id": interpretation.revision_id,"interpretation_id": interpretation.id,"content_hash": interpretation.revision.content_hash,"span": span})
            session.add(item); saved.append(item)
        session.flush(); return saved
