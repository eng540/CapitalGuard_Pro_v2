# --- src/capitalguard/application/services/parsing_service.py ---
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
import spacy # ✅ NEW import
from dataclasses import dataclass # ✅ HOTFIX: Added missing import

from sqlalchemy.orm import Session
from capitalguard.infrastructure.db.uow import session_scope
# ✅ NEW imports for repositories and models
from capitalguard.infrastructure.db.repository import ParsingRepository
from capitalguard.infrastructure.db.models import ParsingTemplate, ParsingAttempt
from capitalguard.domain.value_objects import Price, Target, Targets # Keep for potential internal use

log = logging.getLogger(__name__)

# --- Load spaCy Model ---
# Load model once when the service instance is created or module loaded
# Using a global variable for simplicity in this example
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


# --- Data Structures ---
@dataclass # ✅ HOTFIX: Decorator now recognized
class ParsingResult:
    """Structured result from parsing attempt."""
    success: bool
    data: Optional[Dict[str, Any]] = None # Includes asset, side, entry(Decimal), sl(Decimal), targets(List[Dict{price:Decimal, %:float}])
    parser_path_used: Optional[str] = None
    template_id_used: Optional[int] = None
    attempt_id: Optional[int] = None # Include attempt ID in result
    error_message: Optional[str] = None

# --- ParsingService Class ---
class ParsingService:
    """
    Multi-path parsing engine with attempt logging and basic NER fallback.
    Injects ParsingRepository class for database interactions.
    """
    def __init__(self, parsing_repo_class: type[ParsingRepository]):
        self.parsing_repo_class = parsing_repo_class
        # --- Normalization Helpers (from v3.3) ---
        self._AR_TO_EN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
        self._SUFFIXES = {"K": Decimal("1000"), "M": Decimal("1000000"), "B": Decimal("1000000000")}
        # --- Keyword Maps (from v3.3) ---
        self._key_maps = {
            'entry': ('entry', 'buy', 'شراء', 'الدخول'),
            'stop_loss': ('stop', 'sl', 'stoploss', 'وقف'),
            'targets': ('target', 'tp', 'targets', 'tps', 'take profit', 'هدف', 'اهداف'),
        }
        self._side_maps = {
            'LONG': ('long', 'buy', 'شراء', 'صعود'),
            'SHORT': ('short', 'sell', 'بيع', 'هبوط'),
        }
        self.ASSET_BLACKLIST = {'ACTIVE', 'SIGNAL', 'PERFORMANCE', 'ENTRY', 'STOP', 'PLAN', 'EXIT', 'NOTES', 'LONG', 'SHORT'}

    # --- Normalization and Basic Parsing Helpers (Mostly from v3.3) ---
    def _clean_text(self, text: str) -> str:
        """Normalizes Arabic numerals, symbols, whitespace."""
        if not text: return ""
        s = unicodedata.normalize("NFKC", text)
        s = s.translate(self._AR_TO_EN_DIGITS)
        s = s.replace("،", ",")
        # Keep essential chars for parsing + Arabic letters
        s = re.sub(r'[^\w\s\u0600-\u06FF@:.,\d\-+%$#/|]', ' ', s, flags=re.UNICODE)
        s = re.sub(r'(\r\n|\r|\n){2,}', '\n', s) # Normalize newlines
        s = re.sub(r'\s{2,}', ' ', s) # Normalize spaces
        return s.strip().upper()

    def _parse_one_number(self, token: str) -> Optional[Decimal]:
        """Parses a single numeric token into Decimal, supporting suffixes."""
        if not token: return None
        try:
            # Use lowercase for suffix matching after cleaning
            t = token.strip().replace(",", "").lower()
            num_part = t
            multiplier = Decimal("1")
            suffix_char = None

            # Find potential suffix
            if t and t[-1].isalpha() and t[-1].upper() in self._SUFFIXES:
                 suffix_char = t[-1].upper()
                 num_part = t[:-1]

            # Allow leading +/- for flexibility, but result must be > 0
            if not re.fullmatch(r"[+\-]?\d*\.?\d+", num_part):
                return None

            value = Decimal(num_part)
            if suffix_char:
                 value *= self._SUFFIXES[suffix_char]

            # Return None for non-finite or non-positive results
            return value if value.is_finite() and value > 0 else None
        except Exception as e:
            log.debug(f"Failed to parse number: '{token}', error: {e}")
            return None

    def _parse_targets_list(self, tokens: List[str]) -> List[Dict[str, Any]]:
        """Parses target tokens like ['60k@50', '62k'].
        Returns list of dicts with Decimals."""
        parsed_targets = []
        if not tokens: return parsed_targets

        for token in tokens:
            if not token or not token.strip(): continue
            try:
                price_str, close_pct_str = token, "0"
                if '@' in token:
                    parts = token.split('@', 1)
                    if len(parts) != 2:
                        log.warning(f"Skipping malformed target token (invalid '@' format): {token}")
                        continue
                    price_str, close_pct_str = parts[0].strip(), parts[1].strip().replace('%','')

                price = self._parse_one_number(price_str)
                # Parse close percent, default to 0
                close_pct_dec = self._parse_one_number(close_pct_str) if close_pct_str else Decimal("0")
                # Ensure close percent is valid (0-100)
                close_pct = float(close_pct_dec) if close_pct_dec is not None and 0 <= close_pct_dec <= 100 else 0.0

                if price is not None:
                    parsed_targets.append({"price": price, "close_percent": close_pct})
                else:
                    log.warning(f"Skipping target token with invalid price: {token}")

            except Exception as e:
                log.warning(f"Failed to parse target token: '{token}', error: {e}")
                continue # Skip malformed token

        # Assign 100% to last target if none specified
        if parsed_targets and all(t["close_percent"] == 0.0 for t in parsed_targets):
            parsed_targets[-1]["close_percent"] = 100.0

        return parsed_targets

    def _find_asset_and_side(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        """Smarter asset and side detection using regex (from v3.3)."""
        asset, side = None, None
        # Side detection
        for s, keywords in self._side_maps.items():
            # Use word boundaries for more accuracy
            if any(re.search(r'\b' + keyword.upper() + r'\b', text, re.IGNORECASE) for keyword in keywords):
                side = s
                break
        # Asset detection (prioritized)
        hashtag_match = re.search(r'#([A-Z0-9]{3,12})', text) # Relaxed length slightly
        if hashtag_match and hashtag_match.group(1).upper() not in self.ASSET_BLACKLIST:
            asset = hashtag_match.group(1).upper()
        else:
            # Common patterns like BTCUSDT, ETH/USDT, BTC-PERP
            pair_match = re.search(r'\b([A-Z]{2,8}[/-]?(?:USDT|PERP|BTC|ETH))\b', text)
            if pair_match and pair_match.group(1).upper() not in self.ASSET_BLACKLIST:
                # Normalize separators
                asset = pair_match.group(1).upper().replace('/', '').replace('-', '')
            else:
                # Fallback: Look for 3-8 uppercase letters alone (less reliable)
                fallback_match = re.search(r'\b([A-Z]{3,8})\b', text)
                if fallback_match and fallback_match.group(1).upper() not in self.ASSET_BLACKLIST:
                     # Check context to avoid capturing keywords like 'ENTRY' if missed by blacklist
                     if fallback_match.group(1).upper() not in ['ENTRY', 'STOP', 'LONG', 'SHORT', 'TARGET']:
                          asset = fallback_match.group(1).upper()

        return asset, side

    # --- Multi-Path Parsing Logic ---

    def _apply_regex_template(self, text: str, template: ParsingTemplate) -> Optional[Dict[str, Any]]:
        """Attempts to parse text using a single regex template.
        Returns dict with Decimals."""
        try:
            # Assume pattern_value contains regex with named groups: asset, side, entry, sl, targets_str
            # Use re.DOTALL to allow '.' to match newlines within the targets section
            match = re.search(template.pattern_value, text, re.IGNORECASE | re.MULTILINE | re.DOTALL)
            if not match: return None

            data = match.groupdict()
            parsed = {}
            # Refine asset/side using dedicated function even if captured
            asset_cand = data.get('asset','').strip().upper()
            side_cand = data.get('side','').strip().upper()
            parsed['asset'], parsed['side'] = self._find_asset_and_side(text) # Try finding in whole text first
            if not parsed['asset'] and asset_cand: parsed['asset'] = asset_cand # Fallback to captured asset
            if not parsed['side'] and side_cand: # Fallback to captured side
                parsed['side'] = 'LONG' if any(s.upper() in side_cand for s in self._side_maps['LONG']) else ('SHORT' if any(s.upper() in side_cand for s in self._side_maps['SHORT']) else None)

            if not parsed['asset'] or not parsed['side']: return None # Asset and Side are mandatory

            parsed['entry'] = self._parse_one_number(data.get('entry',''))
            parsed['stop_loss'] = self._parse_one_number(data.get('sl', data.get('stop_loss', ''))) # Allow 'sl' alias

            # Parse targets string captured by regex
            target_str = data.get('targets', data.get('targets_str', '')).strip()
            # Split targets robustly: handle spaces, commas, newlines as separators
            target_tokens = [t for t in re.split(r'[\s,\n]+', target_str) if t]
            parsed['targets'] = self._parse_targets_list(target_tokens)

            # --- Final Validation ---
            required_fields = ['asset', 'side', 'entry', 'stop_loss', 'targets']
            if not all(parsed.get(key) for key in required_fields):
                log.debug(f"Regex template {template.id} matched but missed required fields: { {k:v for k,v in parsed.items() if not v} }")
                return None

            log.debug(f"Regex template {template.id} successfully parsed data.")
            return parsed # Return dict with Decimals
        except Exception as e:
            log.error(f"Error applying regex template {template.id}: {e}", exc_info=False) # Log less verbosely
            return None

    def _apply_ner_fallback(self, text: str) -> Optional[Dict[str, Any]]:
        """(MVP Stub) Attempts to parse text using spaCy NER as a fallback."""
        if not _NLP_MODEL:
            log.debug("NER fallback skipped: spaCy model not loaded.")
            return None
        log.debug("Applying NER fallback.")
        try:
            # --- Basic Heuristic Extraction (MVP - Needs Improvement) ---
            # This relies heavily on regex around keywords as spaCy base model
            # is not trained for specific trading signal formats.
            parsed = {}
            # 1. Get Asset and Side using robust regex method first
            parsed['asset'], parsed['side'] = self._find_asset_and_side(text)
            if not parsed['asset'] or not parsed['side']:
                 log.debug("NER fallback failed: Could not reliably detect asset or side via regex.")
                 return None # Asset and Side are critical

            # 2. Extract Numbers near Keywords using Regex (more reliable than generic NER for prices)
            entry_match = re.search(r'(?:ENTRY|BUY|شراء|الدخول)\s*[:=>]?\s*([\d.,]+[KMB]?)', text, re.IGNORECASE)
            if entry_match: parsed['entry'] = self._parse_one_number(entry_match.group(1))

            sl_match = re.search(r'(?:STOP|SL|STOPLOSS|وقف)\s*[:=>]?\s*([\d.,]+[KMB]?)', text, re.IGNORECASE)
            if sl_match: parsed['stop_loss'] = self._parse_one_number(sl_match.group(1))

            # Try to capture targets more robustly
            targets_pattern = r'(?:TARGETS?|TPS?|هدف|اهداف)\s*\d*\s*[:=>]?\s*((?:[\d.,]+[KMB]?\s*(?:@\d+%?)?\s*[\s,\n]*)+)'
            targets_match = re.search(targets_pattern, text, re.IGNORECASE)
            if targets_match:
                target_str = targets_match.group(1)
                # Split carefully, considering potential newlines between targets
                target_tokens = [t for t in re.split(r'[\s,\n]+', target_str) if t]
                parsed['targets'] = self._parse_targets_list(target_tokens)
            else:
                 parsed['targets'] = [] # Ensure key exists

            # --- Final Validation ---
            required_fields = ['asset', 'side', 'entry', 'stop_loss', 'targets']
            missing_fields = [key for key in required_fields if not parsed.get(key)]
            if missing_fields:
                log.debug(f"NER fallback missed required fields: {missing_fields}")
                return None

            log.debug("NER fallback successfully parsed data (using keyword regex).")
            return parsed # Return dict with Decimals
        except Exception as e:
            log.error(f"Error during NER fallback parsing: {e}", exc_info=True)
            return None

    # --- Main Public Method ---

    async def extract_trade_data(self, content: str, user_db_id: int) -> ParsingResult:
        """
        Main entry point for extracting trade data from text, implementing multi-path logic.
        Logs the attempt to the database using session_scope internally.
        """
        start_time = time.monotonic()
        # Clean text initially for all paths
        # Use lower() for case-insensitive regex in templates later if needed,
        # but keep UPPER for asset/side consistency in result.
        cleaned_text_upper = self._clean_text(content) # Keep UPPER for consistency
        attempt_id: Optional[int] = None
        parser_path_used = "failed" # Default
        template_id_used = None
        result_data_dict: Optional[Dict[str, Any]] = None # Store parsed data (with Decimals)
        result_data_json: Optional[Dict[str, Any]] = None # Store JSON-serializable version for DB
        success = False
        error_message = None

        # 1. Create initial attempt record in DB
        try:
            with session_scope() as session:
                repo = self.parsing_repo_class(session)
                # Ensure user_id is passed correctly
                attempt_record = repo.add_attempt(user_id=user_db_id, raw_content=content)
                attempt_id = attempt_record.id
                log.info(f"Parsing Attempt {attempt_id} created for user DB ID {user_db_id}.")
        except Exception as db_err:
            log.critical(f"Failed to create initial parsing attempt record for user {user_db_id}: {db_err}", exc_info=True)
            # Cannot proceed without attempt_id for updates
            return ParsingResult(success=False, error_message="Database error creating attempt record.")

        try:
            # 2. Path 1: Try Regex Templates
            templates = []
            try:
                with session_scope() as session:
                    repo = self.parsing_repo_class(session)
                    templates = repo.get_active_templates(user_id=user_db_id)
            except Exception as db_err:
                log.error(f"Attempt {attempt_id}: Failed to fetch parsing templates: {db_err}")

            if templates:
                log.debug(f"Attempt {attempt_id}: Trying {len(templates)} regex templates...")
                for template in templates:
                    # Pass the *original cleaned* text to regex
                    result_data_dict = self._apply_regex_template(cleaned_text_upper, template)
                    if result_data_dict:
                        success = True
                        parser_path_used = "regex"
                        template_id_used = template.id
                        log.info(f"Attempt {attempt_id}: Success via Template ID {template.id}")
                        break # Stop on first successful template match

            # 3. Path 2: Try NER Fallback (if Regex failed and model loaded)
            if not success and _NLP_MODEL:
                log.debug(f"Attempt {attempt_id}: Regex failed, trying NER fallback.")
                # Pass cleaned text to NER
                result_data_dict = self._apply_ner_fallback(cleaned_text_upper)
                if result_data_dict:
                    success = True
                    parser_path_used = "ner"
                    log.info(f"Attempt {attempt_id}: Success via NER fallback.")

            # 4. Handle Final Result & Prepare JSON for DB
            if success and result_data_dict:
                # Convert Decimals to strings for JSONB storage before updating DB record
                result_data_json = result_data_dict.copy()
                try:
                    result_data_json['entry'] = str(result_data_dict['entry'])
                    result_data_json['stop_loss'] = str(result_data_dict['stop_loss'])
                    # Ensure targets conversion is safe
                    result_data_json['targets'] = [
                        {'price': str(t['price']), 'close_percent': t.get('close_percent', 0.0)}
                        for t in result_data_dict.get('targets', []) if 'price' in t
                    ]
                except Exception as json_conv_err:
                     log.error(f"Attempt {attempt_id}: Error converting result data to JSON format: {json_conv_err}")
                     success = False # Mark as failed if conversion breaks
                     error_message = "Internal error formatting result data."
                     result_data_json = None # Don't save corrupt data
                     parser_path_used = "error"

            elif not success: # Explicitly handle the 'failed' case
                error_message = "Could not recognize a valid trade signal."
                log.warning(f"Attempt {attempt_id}: Parsing failed using all available methods.")
                parser_path_used = "failed"

        except Exception as parse_err:
            success = False
            error_message = f"Unexpected parsing error: {str(parse_err)}"
            log.error(f"Attempt {attempt_id}: Unexpected error during parsing logic: {parse_err}", exc_info=True)
            parser_path_used = "error"
            result_data_json = None # Ensure no data saved on error

        # 5. Update Attempt Record in DB (Final) - Always update latency and path
        latency_ms = int((time.monotonic() - start_time) * 1000)
        update_payload = {
            "was_successful": success,
            "result_data": result_data_json, # Store JSON serializable version or None
            "used_template_id": template_id_used,
            "latency_ms": latency_ms,
            "parser_path_used": parser_path_used
        }
        try:
            with session_scope() as session:
                repo = self.parsing_repo_class(session)
                repo.update_attempt(attempt_id=attempt_id, **update_payload)
        except Exception as db_err:
            # Log error but still return result from memory
            log.error(f"Attempt {attempt_id}: Failed to update final parsing attempt record: {db_err}", exc_info=True)

        return ParsingResult(
            success=success,
            data=result_data_dict, # Return dict with Decimals
            parser_path_used=parser_path_used,
            template_id_used=template_id_used,
            attempt_id=attempt_id,
            error_message=error_message
        )

    # --- Correction Recording ---
    # Made async to allow potential async DB operations if needed later
    async def record_correction(self, attempt_id: int, corrected_data: Dict[str, Any], original_data: Optional[Dict[str, Any]]):
        """Records user corrections to a parsing attempt in the database."""
        if not attempt_id or original_data is None:
             log.warning(f"Skipping correction recording: Missing attempt_id ({attempt_id}) or original_data.")
             return
        diff = {}
        # Simple diff implementation comparing normalized values
        all_keys = set(original_data.keys()) | set(corrected_data.keys())

        def normalize_for_diff(v):
             """Convert complex types to comparable primitives for diff."""
             if isinstance(v, Decimal): return float(v) # Compare as float
             if isinstance(v, list) and v and isinstance(v[0], dict) and 'price' in v[0]:
                 try: # Sort targets by price for consistent comparison
                     return sorted([(float(t['price']), t.get('close_percent', 0.0)) for t in v])
                 except Exception: return v # Fallback
             return v

        for key in all_keys:
            old_val = original_data.get(key)
            new_val = corrected_data.get(key)
            old_norm = normalize_for_diff(old_val)
            new_norm = normalize_for_diff(new_val)

            if old_norm != new_norm:
                # Store original (serializable) representation in diff
                def serialize_for_diff(v):
                     if isinstance(v, Decimal): return str(v)
                     if isinstance(v, list) and v and isinstance(v[0], dict):
                          # Store targets as list of [price_str, percent]
                          return [[str(t['price']), t.get('close_percent', 0.0)] for t in v]
                     return v
                diff[key] = {"old": serialize_for_diff(old_val), "new": serialize_for_diff(new_val)}

        if diff:
            try:
                with session_scope() as session:
                    repo = self.parsing_repo_class(session)
                    repo.update_attempt(
                        attempt_id=attempt_id,
                        was_corrected=True,
                        corrections_diff=diff # Store the calculated diff
                    )
                log.info(f"Correction recorded successfully for ParsingAttempt ID {attempt_id}")
            except Exception as db_err:
                log.error(f"Failed to record correction for Attempt ID {attempt_id}: {db_err}", exc_info=True)
        else:
            log.info(f"No difference detected for correction recording on Attempt ID {attempt_id}.")


    # --- Template Suggestion ---
    # Made async for consistency
    async def suggest_template_save(self, attempt_id: int) -> Optional[Dict[str, Any]]:
        """
        Analyzes a corrected attempt.
        MVP: Returns raw content if corrected and not parsed by a template.
        """
        if not attempt_id: return None
        try:
            with session_scope() as session:
                repo = self.parsing_repo_class(session)
                # Query attempt ensuring necessary fields are loaded
                attempt = repo.session.query(ParsingAttempt).filter(ParsingAttempt.id == attempt_id).first()
                # Suggest only if: corrected by user AND originally failed or used NER (not a template)
                if attempt and attempt.was_corrected and attempt.used_template_id is None:
                    log.info(f"Suggesting template save based on corrected Attempt ID {attempt_id}")
                    # Return data needed for the suggestion message/handler
                    return {
                        "attempt_id": attempt_id,
                        "raw_content": attempt.raw_content,
                        "corrections_diff": attempt.corrections_diff # Send diff for context
                    }
                else:
                     log.debug(f"Not suggesting template for attempt {attempt_id}: Corrected={attempt.was_corrected if attempt else 'N/A'}, TemplateUsed={attempt.used_template_id if attempt else 'N/A'}")

        except Exception as e:
             log.error(f"Error suggesting template for attempt {attempt_id}: {e}", exc_info=True)
        return None

# --- END of ParsingService ---