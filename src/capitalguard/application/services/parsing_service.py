# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/parsing_service.py ---
# src/capitalguard/application/services/parsing_service.py (v4.2.1-R2 - Stable, Snapshot + Idempotency)
"""
ParsingService v4.2.1-R2
- Solves DetachedInstanceError by snapshotting ORM templates inside session.
- Returns ParsingResult.data with Decimal objects (caller-ready).
- Idempotency via time-windowed raw_content matching.
- Safe DB interactions via session_scope and defensive repo fallbacks.
- Includes record_correction and suggest_template_save utilities.
"""
from __future__ import annotations

import logging
import re
import unicodedata
import time
import hashlib
from typing import Dict, Any, Optional, List, Tuple
from decimal import Decimal
import spacy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session
from sqlalchemy import select, and_

from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.repository import ParsingRepository
from capitalguard.infrastructure.db.models import ParsingTemplate, ParsingAttempt
# domain.value_objects may define Price/Target types; we keep Decimal usage here
# from capitalguard.domain.value_objects import Price, Target, Targets

log = logging.getLogger(__name__)

# Optional NER model (graceful fallback if unavailable)
_NLP_MODEL = None
try:
    _NLP_MODEL = spacy.load("en_core_web_sm")
    log.info("spaCy model 'en_core_web_sm' loaded.")
except Exception:
    _NLP_MODEL = None
    log.debug("spaCy model not available; NER fallback disabled.")


# Exceptions
class ParsingError(Exception):
    pass

class DatabaseError(Exception):
    pass


@dataclass
class ParsingResult:
    success: bool
    data: Optional[Dict[str, Any]] = None  # keeps Decimal for entry/stop_loss and price in targets
    parser_path_used: Optional[str] = None
    template_id_used: Optional[int] = None
    attempt_id: Optional[int] = None
    error_message: Optional[str] = None
    latency_ms: Optional[int] = None
    idempotency_hint: Optional[str] = None


class ParsingService:
    """
    ParsingService v4.2.1-R2
    - parsing_repo_class: class reference for repository (instantiated per session)
    - idempotency_window_seconds: window to consider duplicate forwarded content
    """

    def __init__(self, parsing_repo_class: type[ParsingRepository], idempotency_window_seconds: int = 300):
        self.parsing_repo_class = parsing_repo_class
        self.idempotency_window_seconds = int(idempotency_window_seconds)
        self._AR_TO_EN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
        self._SUFFIXES = {"K": Decimal("1000"), "M": Decimal("1000000"), "B": Decimal("1000000000")}
        self._side_maps = {
            'LONG': ('long', 'buy', 'شراء', 'صعود'),
            'SHORT': ('short', 'sell', 'بيع', 'هبوط'),
        }
        self.ASSET_BLACKLIST = {'ACTIVE', 'SIGNAL', 'PERFORMANCE', 'ENTRY', 'STOP', 'PLAN', 'EXIT', 'NOTES', 'LONG', 'SHORT'}

    # ---------------- Normalization & Numeric Helpers ----------------
    def _normalize_text(self, text: str) -> str:
        if not text: return ""
        s = unicodedata.normalize("NFKC", text)
        s = s.translate(self._AR_TO_EN_DIGITS)
        s = s.replace("،", ",")
        s = re.sub(r'[^\w\s\u0600-\u06FF@:.,\d\-+%$#/|]', ' ', s, flags=re.UNICODE)
        s = re.sub(r'(\r\n|\r|\n){2,}', '\n', s)
        s = re.sub(r'\s{2,}', ' ', s)
        return s.strip()

    def _normalize_for_key(self, text: str) -> str:
        return self._normalize_text(text).upper()

    def _compute_hint_hash(self, content: str) -> str:
        h = hashlib.sha256(self._normalize_for_key(content).encode('utf-8')).hexdigest()
        return h

    def _parse_one_number(self, token: str) -> Optional[Decimal]:
        if token is None: return None
        try:
            t = str(token).strip().replace(",", "").upper()
            if not t:
                return None
            multiplier = Decimal("1")
            num_part = t
            if t[-1].isalpha() and t[-1] in self._SUFFIXES:
                multiplier = self._SUFFIXES[t[-1]]
                num_part = t[:-1]
            if not re.fullmatch(r"[+\-]?\d*\.?\d+", num_part):
                return None
            val = Decimal(num_part) * multiplier
            return val if val.is_finite() and val > 0 else None
        except Exception:
            return None

    def _parse_targets_list(self, tokens: List[str]) -> List[Dict[str, Any]]:
        parsed_targets = []
        if not tokens:
            return parsed_targets
        for token in tokens:
            if not token: continue
            try:
                price_str = token
                pct_str = ""
                if '@' in token:
                    parts = token.split('@', 1)
                    if len(parts) != 2:
                        price_str = parts[0].strip()
                        pct_str = ""
                    else:
                        price_str, pct_str = parts[0].strip(), parts[1].strip().replace('%','')
                price = self._parse_one_number(price_str)
                pct = self._parse_one_number(pct_str) if pct_str else Decimal("0")
                pct_f = float(pct) if pct is not None and 0 <= pct <= 100 else 0.0
                if price is not None:
                    parsed_targets.append({"price": price, "close_percent": pct_f})
            except Exception:
                continue
        if parsed_targets and all(t["close_percent"] == 0.0 for t in parsed_targets):
            parsed_targets[-1]["close_percent"] = 100.0
        return parsed_targets

    def _find_asset_and_side(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        asset, side = None, None
        txt = text.upper()
        for s, keywords in self._side_maps.items():
            if any(re.search(r'\b' + re.escape(kw.upper()) + r'\b', txt) for kw in keywords):
                side = s
                break
        hashtag_match = re.search(r'#([A-Z0-9]{3,12})', txt)
        if hashtag_match and hashtag_match.group(1).upper() not in self.ASSET_BLACKLIST:
            asset = hashtag_match.group(1).upper()
        else:
            pair_match = re.search(r'\b([A-Z]{2,8}[/-]?(?:USDT|PERP|BTC|ETH))\b', txt)
            if pair_match and pair_match.group(1).upper() not in self.ASSET_BLACKLIST:
                asset = pair_match.group(1).upper().replace('/', '').replace('-', '')
            else:
                fallback = re.search(r'\b([A-Z]{3,8})\b', txt)
                if fallback and fallback.group(1).upper() not in self.ASSET_BLACKLIST:
                    if fallback.group(1).upper() not in ['ENTRY', 'STOP', 'LONG', 'SHORT', 'TARGET']:
                        asset = fallback.group(1).upper()
        return asset, side

    def _apply_regex_template(self, text: str, template_snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Apply regex using a snapshot dict {id, pattern} to avoid ORM lazy-loading.
        Returns parsed dict with Decimal values or None.
        """
        try:
            pattern = template_snapshot.get("pattern")
            if not pattern:
                return None
            match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE | re.DOTALL)
            if not match:
                return None
            data = match.groupdict()
            parsed = {}
            asset_cand = (data.get('asset') or '').strip().upper()
            side_cand = (data.get('side') or '').strip().upper()

            parsed['asset'], parsed['side'] = self._find_asset_and_side(text)
            if not parsed['asset'] and asset_cand:
                parsed['asset'] = asset_cand
            if not parsed['side'] and side_cand:
                parsed['side'] = 'LONG' if any(s.upper() in side_cand for s in self._side_maps['LONG']) else ('SHORT' if any(s.upper() in side_cand for s in self._side_maps['SHORT']) else None)

            if not parsed['asset'] or not parsed['side']:
                return None

            parsed['entry'] = self._parse_one_number(data.get('entry',''))
            parsed['stop_loss'] = self._parse_one_number(data.get('sl', data.get('stop_loss','')))
            target_str = (data.get('targets') or data.get('targets_str') or '').strip()
            tokens = [t for t in re.split(r'[\s,\n,]+', target_str) if t]
            parsed['targets'] = self._parse_targets_list(tokens)

            required = ['asset','side','entry','stop_loss','targets']
            if not all(parsed.get(k) for k in required):
                return None
            return parsed
        except Exception as e:
            log.warning(f"Error applying regex template snapshot {template_snapshot.get('id')}: {e}")
            return None

    def _apply_ner_fallback(self, text: str) -> Optional[Dict[str, Any]]:
        if not _NLP_MODEL:
            return None
        try:
            parsed = {}
            parsed['asset'], parsed['side'] = self._find_asset_and_side(text)
            if not parsed['asset'] or not parsed['side']:
                return None
            em = re.search(r'(?:ENTRY|BUY|شراء|الدخول)\s*[:=>]?\s*([\d.,]+[KMB]?)', text, re.IGNORECASE)
            if em: parsed['entry'] = self._parse_one_number(em.group(1))
            sm = re.search(r'(?:STOP|SL|STOPLOSS|وقف)\s*[:=>]?\s*([\d.,]+[KMB]?)', text, re.IGNORECASE)
            if sm: parsed['stop_loss'] = self._parse_one_number(sm.group(1))
            tpat = r'(?:TARGETS?|TPS?|هدف|اهداف)\s*\d*\s*[:=>]?\s*((?:[\d.,]+[KMB]?\s*(?:@\d+%?)?\s*[\s,\n]*)+)'
            tm = re.search(tpat, text, re.IGNORECASE)
            if tm:
                tokens = [t for t in re.split(r'[\s,\n,]+', tm.group(1)) if t]
                parsed['targets'] = self._parse_targets_list(tokens)
            else:
                parsed['targets'] = []
            req = ['asset','side','entry','stop_loss','targets']
            if any(not parsed.get(k) for k in req):
                return None
            return parsed
        except Exception as e:
            log.debug(f"NER fallback error: {e}")
            return None

    # ---------------- Repo / DB helpers ----------------
    def _repo_instance(self, session: Session) -> ParsingRepository:
        try:
            return self.parsing_repo_class(session)
        except Exception as e:
            raise DatabaseError(f"Could not instantiate ParsingRepository: {e}")

    def _find_recent_same_content_attempt(self, session: Session, user_id: int, raw_content: str) -> Optional[ParsingAttempt]:
        """
        Find attempts with identical raw_content within idempotency window for user.
        """
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=self.idempotency_window_seconds)
            stmt = select(ParsingAttempt).where(
                and_(
                    ParsingAttempt.user_id == user_id,
                    ParsingAttempt.raw_content == raw_content,
                    ParsingAttempt.created_at >= cutoff
                )
            ).order_by(ParsingAttempt.created_at.desc())
            res = session.execute(stmt).scalars().first()
            return res
        except Exception:
            log.debug("Direct recent-attempt query failed.")
            return None

    # ---------------- Public API ----------------
    async def extract_trade_data(self, content: str, user_db_id: int) -> ParsingResult:
        start = time.monotonic()
        cleaned = self._normalize_text(content)
        hint_hash = self._compute_hint_hash(cleaned)
        attempt_id = None
        parser_path_used = "failed"
        template_id_used = None
        parsed_result: Optional[Dict[str,Any]] = None  # contains Decimals
        success = False
        error_message = None

        # Step 1: create attempt record safely and avoid duplicate processing
        try:
            with session_scope() as session:
                existing = self._find_recent_same_content_attempt(session, user_db_id, content)
                if existing:
                    attempt_id = existing.id
                    if existing.was_successful and existing.result_data:
                        # Rehydrate result_data into DECIMALS for caller compatibility
                        try:
                            rehydrated = {
                                "asset": existing.result_data.get("asset"),
                                "side": existing.result_data.get("side"),
                                "entry": self._parse_one_number(existing.result_data.get("entry")),
                                "stop_loss": self._parse_one_number(existing.result_data.get("stop_loss")),
                                "targets": self._parse_targets_list([f"{t.get('price')}@{t.get('close_percent')}" for t in existing.result_data.get("targets", [])])
                            }
                        except Exception:
                            rehydrated = None
                        latency_ms = int((time.monotonic() - start) * 1000)
                        return ParsingResult(
                            success=True if rehydrated else False,
                            data=rehydrated,
                            parser_path_used=existing.parser_path_used,
                            template_id_used=getattr(existing, "used_template_id", None),
                            attempt_id=attempt_id,
                            error_message=None if rehydrated else "Failed to rehydrate cached data",
                            latency_ms=latency_ms,
                            idempotency_hint=hint_hash
                        )
                # create attempt record
                repo = self._repo_instance(session)
                if hasattr(repo, "add_attempt"):
                    try:
                        attempt_rec = repo.add_attempt(user_id=user_db_id, raw_content=content)
                    except TypeError:
                        attempt_rec = repo.add_attempt(user_id=user_db_id, raw_content=content)
                    attempt_id = attempt_rec.id
                else:
                    pa = ParsingAttempt(user_id=user_db_id, raw_content=content)
                    session.add(pa)
                    session.flush()
                    attempt_id = pa.id
        except Exception as e:
            log.error("DB error creating attempt record: %s", e, exc_info=True)
            return ParsingResult(success=False, error_message="Database error creating attempt record.")

        # Step 2: load templates and apply them INSIDE a session (snapshot to avoid DetachedInstance)
        try:
            with session_scope() as session:
                repo = self._repo_instance(session)
                templates: List[ParsingTemplate] = []
                if hasattr(repo, "get_active_templates"):
                    templates = repo.get_active_templates(user_id=user_db_id) or []
                else:
                    stmt = select(ParsingTemplate).where(getattr(ParsingTemplate, "is_public", True) == True)
                    templates = session.execute(stmt).scalars().all()

                # Build safe snapshots (id + pattern) while session is active
                template_snapshots = [
                    {"id": getattr(t, "id", None), "pattern": getattr(t, "pattern_value", None)}
                    for t in templates
                ]

                if template_snapshots:
                    # iterate snapshots to find a match; apply regex on normalized (upper) cleaned text
                    normalized_upper = self._normalize_for_key(cleaned)
                    for t_snap in template_snapshots:
                        parsed = self._apply_regex_template(normalized_upper, t_snap)
                        if parsed:
                            success = True
                            parsed_result = parsed
                            parser_path_used = "regex"
                            template_id_used = t_snap.get("id")
                            break

            # Step 3: NER fallback outside DB session (no ORM access required)
            if not success and _NLP_MODEL:
                parsed = self._apply_ner_fallback(cleaned)
                if parsed:
                    success = True
                    parsed_result = parsed
                    parser_path_used = "ner"

            # Step 4: prepare result JSON for DB storage (serialize Decimal -> str)
            result_json = None
            if success and parsed_result:
                try:
                    result_json = {
                        "asset": parsed_result["asset"],
                        "side": parsed_result["side"],
                        "entry": str(parsed_result["entry"]) if parsed_result.get("entry") is not None else None,
                        "stop_loss": str(parsed_result["stop_loss"]) if parsed_result.get("stop_loss") is not None else None,
                        "targets": [
                            {"price": str(t["price"]), "close_percent": t.get("close_percent", 0.0)}
                            for t in parsed_result.get("targets", [])
                        ]
                    }
                except Exception as e:
                    log.error("Result serialization error: %s", e, exc_info=True)
                    success = False
                    result_json = None
                    error_message = "Internal serialization error."
                    parser_path_used = "error"
            else:
                if not error_message:
                    error_message = "Could not recognize a valid trade signal."

            # Step 5: update attempt final state (safe)
            latency_ms = int((time.monotonic() - start) * 1000)
            update_kwargs = {
                "was_successful": success,
                "result_data": result_json,
                "used_template_id": template_id_used,
                "latency_ms": latency_ms,
                "parser_path_used": parser_path_used
            }
            try:
                with session_scope() as session:
                    repo = self._repo_instance(session)
                    if hasattr(repo, "update_attempt"):
                        repo.update_attempt(attempt_id=attempt_id, **update_kwargs)
                    else:
                        stmt = select(ParsingAttempt).where(ParsingAttempt.id == attempt_id)
                        pa = session.execute(stmt).scalar_one_or_none()
                        if pa:
                            for k, v in update_kwargs.items():
                                setattr(pa, k, v)
                            session.flush()
            except Exception as e:
                log.error("Failed to update attempt record: %s", e, exc_info=True)

            return ParsingResult(
                success=success,
                data=parsed_result,  # Caller receives Decimal values
                parser_path_used=parser_path_used,
                template_id_used=template_id_used,
                attempt_id=attempt_id,
                error_message=None if success else error_message,
                latency_ms=latency_ms,
                idempotency_hint=hint_hash
            )

        except Exception as e:
            log.exception("Unexpected parsing error: %s", e)
            latency_ms = int((time.monotonic() - start) * 1000)
            try:
                with session_scope() as session:
                    repo = self._repo_instance(session)
                    if hasattr(repo, "update_attempt"):
                        repo.update_attempt(attempt_id=attempt_id, was_successful=False, parser_path_used="error", latency_ms=latency_ms)
            except Exception:
                log.debug("Failed to mark attempt as errored.")
            return ParsingResult(success=False, error_message=str(e), latency_ms=latency_ms, attempt_id=attempt_id, idempotency_hint=hint_hash)

    # ---------------- Corrections / Template suggestion ----------------
    async def record_correction(self, attempt_id: int, corrected_data: Dict[str, Any], original_data: Optional[Dict[str, Any]]):
        if not attempt_id or original_data is None:
            log.warning("record_correction skipped: missing data.")
            return
        diff = {}
        keys = set(original_data.keys()) | set(corrected_data.keys())

        def norm(v):
            if isinstance(v, Decimal): return float(v)
            if isinstance(v, list):
                try:
                    return sorted([(float(t['price']), t.get('close_percent', 0.0)) for t in v])
                except Exception:
                    return v
            return v

        def ser(v):
            if isinstance(v, Decimal): return str(v)
            if isinstance(v, list):
                try:
                    return [[str(t['price']), t.get('close_percent', 0.0)] for t in v]
                except Exception:
                    return v
            return v

        for k in keys:
            norm_old = norm(original_data.get(k))
            norm_new = norm(corrected_data.get(k))
            if norm_old != norm_new:
                diff[k] = {"old": ser(original_data.get(k)), "new": ser(corrected_data.get(k))}

        if not diff:
            log.info("No differences to record for correction on attempt %s.", attempt_id)
            return

        try:
            with session_scope() as session:
                repo = self._repo_instance(session)
                if hasattr(repo, "update_attempt"):
                    repo.update_attempt(attempt_id=attempt_id, was_corrected=True, corrections_diff=diff)
                else:
                    stmt = select(ParsingAttempt).where(ParsingAttempt.id == attempt_id)
                    pa = session.execute(stmt).scalar_one_or_none()
                    if pa:
                        pa.was_corrected = True
                        pa.corrections_diff = diff
                        session.flush()
            log.info("Recorded correction for attempt %s", attempt_id)
        except Exception as e:
            log.error("Failed to record correction: %s", e, exc_info=True)

    async def suggest_template_save(self, attempt_id: int) -> Optional[Dict[str, Any]]:
        if not attempt_id:
            return None
        try:
            with session_scope() as session:
                repo = self._repo_instance(session)
                if hasattr(repo, "get_attempt"):
                    attempt = repo.get_attempt(attempt_id)
                else:
                    stmt = select(ParsingAttempt).where(ParsingAttempt.id == attempt_id)
                    attempt = session.execute(stmt).scalar_one_or_none()

                if not attempt:
                    return None

                was_corrected = getattr(attempt, "was_corrected", False)
                used_template_id = getattr(attempt, "used_template_id", None)
                raw_content = getattr(attempt, "raw_content", "")
                corrections_diff = getattr(attempt, "corrections_diff", None)

            if was_corrected and used_template_id is None:
                return {
                    "attempt_id": attempt_id,
                    "raw_content": raw_content,
                    "corrections_diff": corrections_diff
                }
        except Exception as e:
            log.error("Error suggesting template: %s", e, exc_info=True)
        return None

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/parsing_service.py ---