# src/capitalguard/application/services/parsing_service.py

"""
ParsingService (v4.1.0 - NameError Hotfix)
✅ HOTFIX: Added missing 'from dataclasses import dataclass' to fix startup crash.

  - Handles multi-path text parsing and attempt logging.
  - Implements Regex -> NER fallback for text. Records all attempts.
"""
import logging
import re
import unicodedata
import time
from typing import Dict, Any, Optional, List, Tuple
from decimal import Decimal, InvalidOperation
import spacy  # ✅ NEW import
from dataclasses import dataclass  # ✅ HOTFIX: Added missing import

from sqlalchemy.orm import Session
from capitalguard.infrastructure.db.uow import session_scope
from capitalguard.infrastructure.db.repository import ParsingRepository
from capitalguard.infrastructure.db.models import ParsingTemplate, ParsingAttempt
from capitalguard.domain.value_objects import Price, Target, Targets

log = logging.getLogger(__name__)

# --- Load spaCy Model ---
_NLP_MODEL = None
try:
    _NLP_MODEL = spacy.load("en_core_web_sm")
    log.info("spaCy model 'en_core_web_sm' loaded successfully.")
except OSError:
    log.error(
        "spaCy model 'en_core_web_sm' not found.\n"
        "Please run 'python -m spacy download en_core_web_sm'.\n"
        "NER fallback will be disabled."
    )
except ImportError:
    log.error("spaCy library not installed. NER fallback will be disabled.")
    _NLP_MODEL = None


@dataclass
class ParsingResult:
    """Structured result from parsing attempt."""
    success: bool
    data: Optional[Dict[str, Any]] = None
    parser_path_used: Optional[str] = None
    template_id_used: Optional[int] = None
    attempt_id: Optional[int] = None
    error_message: Optional[str] = None


class ParsingService:
    """
    Multi-path parsing engine with attempt logging and basic NER fallback.
    Injects ParsingRepository class for database interactions.
    """
    def __init__(self, parsing_repo_class: type[ParsingRepository]):
        self.parsing_repo_class = parsing_repo_class
        self._AR_TO_EN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
        self._SUFFIXES = {"K": Decimal("1000"), "M": Decimal("1000000"), "B": Decimal("1000000000")}
        self._side_maps = {
            'LONG': ('long', 'buy', 'شراء', 'صعود'),
            'SHORT': ('short', 'sell', 'بيع', 'هبوط'),
        }
        self.ASSET_BLACKLIST = {
            'ACTIVE', 'SIGNAL', 'PERFORMANCE', 'ENTRY', 'STOP',
            'PLAN', 'EXIT', 'NOTES', 'LONG', 'SHORT'
        }

    def _clean_text(self, text: str) -> str:
        if not text:
            return ""
        s = unicodedata.normalize("NFKC", text)
        s = s.translate(self._AR_TO_EN_DIGITS)
        s = s.replace("،", ",")
        s = re.sub(r'[^\w\s\u0600-\u06FF@:.,\d\-+%$#/|]', ' ', s, flags=re.UNICODE)
        s = re.sub(r'(\r\n|\r|\n){2,}', '\n', s)
        s = re.sub(r'\s{2,}', ' ', s)
        return s.strip().upper()

    def _parse_one_number(self, token: str) -> Optional[Decimal]:
        if not token:
            return None
        try:
            t = token.strip().replace(",", "").lower()
            num_part = t
            suffix_char = None
            if t and t[-1].isalpha() and t[-1].upper() in self._SUFFIXES:
                suffix_char = t[-1].upper()
                num_part = t[:-1]
            if not re.fullmatch(r"[+\-]?\d*\.?\d+", num_part):
                return None
            value = Decimal(num_part)
            if suffix_char:
                value *= self._SUFFIXES[suffix_char]
            return value if value.is_finite() and value > 0 else None
        except Exception as e:
            log.debug(f"Failed to parse number: '{token}', error: {e}")
            return None

    def _parse_targets_list(self, tokens: List[str]) -> List[Dict[str, Any]]:
        parsed_targets = []
        if not tokens:
            return parsed_targets
        for token in tokens:
            if not token.strip():
                continue
            try:
                price_str, close_pct_str = token, "0"
                if '@' in token:
                    parts = token.split('@', 1)
                    if len(parts) != 2:
                        continue
                    price_str, close_pct_str = parts[0].strip(), parts[1].strip().replace('%', '')
                price = self._parse_one_number(price_str)
                close_pct_dec = self._parse_one_number(close_pct_str) if close_pct_str else Decimal("0")
                close_pct = float(close_pct_dec) if close_pct_dec and 0 <= close_pct_dec <= 100 else 0.0
                if price is not None:
                    parsed_targets.append({"price": price, "close_percent": close_pct})
            except Exception as e:
                log.warning(f"Failed to parse target token: '{token}', error: {e}")
        if parsed_targets and all(t["close_percent"] == 0.0 for t in parsed_targets):
            parsed_targets[-1]["close_percent"] = 100.0
        return parsed_targets

    def _find_asset_and_side(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        asset, side = None, None
        for s, keywords in self._side_maps.items():
            if any(re.search(r'\b' + keyword.upper() + r'\b', text, re.IGNORECASE) for keyword in keywords):
                side = s
                break
        hashtag_match = re.search(r'#([A-Z0-9]{3,12})', text)
        if hashtag_match and hashtag_match.group(1).upper() not in self.ASSET_BLACKLIST:
            asset = hashtag_match.group(1).upper()
        return asset, side

    async def extract_trade_data(self, content: str, user_db_id: int) -> ParsingResult:
        start_time = time.monotonic()
        cleaned_text_upper = self._clean_text(content)
        attempt_id = None
        parser_path_used = "failed"
        result_data_dict = None
        result_data_json = None
        success = False
        error_message = None

        try:
            with session_scope() as session:
                repo = self.parsing_repo_class(session)
                attempt_record = repo.add_attempt(user_id=user_db_id, raw_content=content)
                attempt_id = attempt_record.id
        except Exception as db_err:
            log.critical(f"Failed to create parsing attempt: {db_err}")
            return ParsingResult(success=False, error_message="Database error")

        try:
            result_data_dict = None
            success = False
            if not success and _NLP_MODEL:
                result_data_dict = self._apply_ner_fallback(cleaned_text_upper)
                if result_data_dict:
                    success = True
                    parser_path_used = "ner"

            if success and result_data_dict:
                result_data_json = {
                    'entry': str(result_data_dict['entry']),
                    'stop_loss': str(result_data_dict['stop_loss']),
                    'targets': [{'price': str(t['price']), 'close_percent': t['close_percent']} for t in result_data_dict.get('targets', [])]
                }
            else:
                error_message = "Parsing failed."
                parser_path_used = "failed"
        except Exception as e:
            log.error(f"Unexpected parsing error: {e}")
            error_message = str(e)

        latency_ms = int((time.monotonic() - start_time) * 1000)
        try:
            with session_scope() as session:
                repo = self.parsing_repo_class(session)
                repo.update_attempt(attempt_id=attempt_id,
                                    was_successful=success,
                                    result_data=result_data_json,
                                    parser_path_used=parser_path_used,
                                    latency_ms=latency_ms)
        except Exception as db_err:
            log.error(f"Failed to update parsing attempt: {db_err}")

        return ParsingResult(
            success=success,
            data=result_data_dict,
            parser_path_used=parser_path_used,
            attempt_id=attempt_id,
            error_message=error_message
        )