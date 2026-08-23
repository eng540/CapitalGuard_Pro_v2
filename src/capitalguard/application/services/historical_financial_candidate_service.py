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
            match = re.search(rf"(?:{labels_pattern})\s*[:=\-]?\s*([0-9٠-٩][0-9٠-٩,.]*[KkMmBb]?%?x?)", normalized, re.I)
            if not match: continue
            span = match.group(0); token = match.group(1).rstrip("%xX")
            value = self.parser._parse_one_number(token)
            if value is not None: candidates.append((field, {"value": str(value)}, span, Decimal("0.8500")))
        for index, match in enumerate(re.finditer(r"(?:TP|TARGET)\s*\d*\s*[:=\-]?\s*([0-9٠-٩][0-9٠-٩,.]*[KkMmBb]?)", normalized, re.I), start=1):
            value = self.parser._parse_one_number(match.group(1))
            if value is not None: candidates.append(("TARGET", {"index": index, "value": str(value)}, match.group(0), Decimal("0.8500")))
        saved=[]
        for field, value, span, confidence in candidates:
            normalized_value = str(value if isinstance(value, str) else value["value"])
            existing=session.execute(select(HistoricalFinancialCandidate).where(HistoricalFinancialCandidate.interpretation_id==interpretation.id, HistoricalFinancialCandidate.field_type==field, HistoricalFinancialCandidate.normalized_value==normalized_value, HistoricalFinancialCandidate.span_text==span, HistoricalFinancialCandidate.extractor_version==self.EXTRACTOR_VERSION)).scalar_one_or_none()
            if existing: saved.append(existing); continue
            item=HistoricalFinancialCandidate(interpretation_id=interpretation.id, field_type=field, value_json={"value": value} if isinstance(value,str) else value, normalized_value=normalized_value, span_text=span, confidence_score=confidence, extractor_version=self.EXTRACTOR_VERSION, provenance_json={"revision_id": interpretation.revision_id,"interpretation_id": interpretation.id,"content_hash": interpretation.revision.content_hash,"span": span})
            session.add(item); saved.append(item)
        session.flush(); return saved
