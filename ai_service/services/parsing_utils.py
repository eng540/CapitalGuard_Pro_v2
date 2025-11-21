# File: ai_service/services/parsing_utils.py
# Version: 2.3.0 (Financial-Grade Stability Hotfix)
# ✅ THE FIX: معالجة شاملة للبيانات الناقصة والتحقق المالي

import os
import re
import json
import logging
import asyncio
import httpx
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, List, Optional, Tuple

log = logging.getLogger(__name__)

# --- Retry/backoff config ---
try:
    IMAGE_PARSE_MAX_RETRIES = int(os.getenv("IMAGE_PARSE_MAX_RETRIES", "3"))
except Exception:
    IMAGE_PARSE_MAX_RETRIES = 3

try:
    IMAGE_PARSE_BACKOFF_BASE = float(os.getenv("IMAGE_PARSE_BACKOFF_BASE", "1.0"))
except Exception:
    IMAGE_PARSE_BACKOFF_BASE = 1.0

# --- 1. Core Parsers (Original) ---
_AR_TO_EN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_SUFFIXES = {"K": Decimal("1000"), "M": Decimal("1000000"), "B": Decimal("1000000000")}

def _normalize_arabic_numerals(s: str) -> str:
    if not s: return ""
    return s.translate(_AR_TO_EN_DIGITS)

def parse_decimal_token(token: str) -> Optional[Decimal]:
    """(Source of Truth) Parses a single numeric token (supports K/M/B) to Decimal."""
    if token is None: return None
    try:
        s = _normalize_arabic_numerals(str(token)).strip().lower().replace(',', '')
        if not s: return None
        multiplier = Decimal("1")
        num_part = s
        if s.endswith('k'):
            multiplier = _SUFFIXES["K"]
            num_part = s[:-1]
        elif s.endswith('m'):
            multiplier = _SUFFIXES["M"]
            num_part = s[:-1]
        elif s.endswith('b'):
            multiplier = _SUFFIXES["B"]
            num_part = s[:-1]
        if not num_part: return None
        if not re.fullmatch(r"[+\-]?\d*\.?\d+", num_part): return None
        val = Decimal(num_part) * multiplier
        return val if val.is_finite() and val >= 0 else None
    except (InvalidOperation, TypeError, ValueError) as e:
        log.debug(f"Failed to parse Decimal token: '{token}', error: {e}")
        return None

def _parse_token_price_and_pct(token: str) -> Dict[str, Optional[Decimal]]:
    """Parses a single target token (e.g., "6k@25%") to Decimal."""
    if not token or not str(token).strip():
        raise ValueError("Empty target token")
    token = str(token).strip()
    price_part, pct_part = token, "0"
    if '@' in token:
        parts = token.split('@', 1)
        if len(parts) == 2:
            price_part, pct_part = parts[0], parts[1].strip().rstrip('%')
        else:
            price_part = parts[0]
    price = parse_decimal_token(price_part)
    pct = parse_decimal_token(pct_part)
    return {"price": price, "pct": pct}

def _extract_each_percentage_from_text(source_text: str) -> Optional[Decimal]:
    """(v1.3.0) Searches for global percentage patterns."""
    if not source_text: return None
    normalized_text = _normalize_arabic_numerals(source_text)
    patterns = [
        r'\(?\s*(\d{1,3}(?:\.\d+)?)\s*%\s*(?:each|per target|لكل هدف|كل هدف|كل منها)\)?',
        r'(?:(?:close|اغلاق|إغلاق)\s*)(\d{1,3}(?:\.\d+)?)\s*%?(?:\s*(?:each|TP|هدف|targets|عند كل هدف))?',
        r'(?:(?:لكل|بنسبة|النسبة)\s*)(\d{1,3}(?:\.\d+)?)\s*%?',
        r'(\d{1,3}(?:\.\d+)?)\s*(?:each|لكل)\s*(?:هدف|TP|target)'
    ]
    for pattern in patterns:
        m = re.search(pattern, normalized_text, re.IGNORECASE)
        if m:
            try:
                val = Decimal(m.group(1))
                if 0 <= val <= 100:
                    log.debug(f"Found global percentage: {val}% using pattern: {pattern}")
                    return val
            except Exception:
                continue
    return None

def normalize_targets(
    targets_raw: Any, 
    source_text: str = ""
) -> List[Dict[str, Any]]:
    """(Source of Truth - v2.0) Normalizes any target format into a clean list."""
    normalized: List[Dict[str, Any]] = []
    if not targets_raw:
        return normalized

    each_pct = _extract_each_percentage_from_text(source_text)

    # Case 1: List of dicts (correct format)
    if isinstance(targets_raw, list) and targets_raw and isinstance(targets_raw[0], dict):
        for t in targets_raw:
            try:
                raw_price = t.get("price") if isinstance(t, dict) else t
                price_val = parse_decimal_token(str(raw_price))
                if price_val is None or price_val <= 0: continue
                
                close_pct_raw = t.get("close_percent", None)
                close_pct = Decimal(str(close_pct_raw)) if close_pct_raw is not None else None
                
                if close_pct is None and each_pct is not None:
                    close_pct = each_pct
                elif close_pct is None:
                    close_pct = Decimal("0")

                normalized.append({"price": price_val, "close_percent": float(close_pct)})
            except Exception as e:
                log.debug(f"Skipping malformed target dict entry: {t} ({e})")

    # Case 2: List of raw values (numbers, strings)
    elif isinstance(targets_raw, list):
        tokens_flat: List[str] = []
        for item in targets_raw:
            if item is None: continue
            s = _normalize_arabic_numerals(str(item)).strip()
            parts = re.split(r'[\s\n,/\-→]+', s)
            tokens_flat.extend([p.strip() for p in parts if p.strip()])

        for tok in tokens_flat:
            try:
                parsed = _parse_token_price_and_pct(tok)
                price = parsed["price"]
                pct = parsed["pct"]
                
                if price is None or price <= 0: continue
                if pct is None and each_pct is not None:
                    pct = each_pct
                elif pct is None:
                    pct = Decimal("0")

                normalized.append({"price": price, "close_percent": float(pct)})
            except Exception as e:
                log.debug(f"Skipped token while normalizing targets: '{tok}' ({e})")
    
    # Case 3: Single string with multiple numbers
    elif isinstance(targets_raw, str):
        s = _normalize_arabic_numerals(targets_raw).strip()
        tokens = re.split(r'[\s\n,/\-→]+', s)
        
        for tok in tokens:
            tok = tok.strip()
            if not tok: continue
            try:
                parsed = _parse_token_price_and_pct(tok)
                price = parsed["price"]
                pct = parsed["pct"]
                if price is None or price <= 0: continue
                if pct is None and each_pct is not None:
                    pct = each_pct
                elif pct is None:
                    pct = Decimal("0")
                normalized.append({"price": price, "close_percent": float(pct)})
            except Exception:
                continue

    # Apply 100% last target rule
    if normalized and all(t["close_percent"] == 0.0 for t in normalized):
        normalized[-1]["close_percent"] = 100.0
        
    return normalized

# --- 2. Validation (FIXED VERSION) ---
def _financial_consistency_check(data: Dict[str, Any]) -> bool:
    """
    ✅ FIXED: Strict numeric checks with None protection
    Expects Decimals but handles None gracefully
    """
    try:
        # ✅ FIRST: Check for missing required fields
        required_fields = ["entry", "stop_loss", "side", "targets"]
        missing_fields = [field for field in required_fields if data.get(field) is None]
        
        if missing_fields:
            log.warning(f"Financial check failed: Missing required fields {missing_fields}")
            return False

        entry = data["entry"]
        sl = data["stop_loss"]
        side = str(data["side"]).strip().upper()
        targets_raw = data.get("targets", [])
        
        # ✅ SECOND: Safe type conversion
        if not isinstance(entry, Decimal):
            entry = parse_decimal_token(str(entry))
        if not isinstance(sl, Decimal):
            sl = parse_decimal_token(str(sl))

        if entry is None or sl is None:
            log.warning("Financial check failed: Entry or SL could not be parsed to Decimal")
            return False

        # ✅ THIRD: Validate targets structure
        if not isinstance(targets_raw, list) or len(targets_raw) == 0:
            log.warning("Financial check failed: Targets missing or empty")
            return False

        prices: List[Decimal] = []
        for t in targets_raw:
            if not isinstance(t, dict):
                continue
            price_val = t["price"] if isinstance(t.get("price"), Decimal) else parse_decimal_token(str(t.get("price")))
            if price_val and price_val > 0:
                prices.append(price_val)

        if not prices:
            log.warning("Financial check failed: No valid target prices found")
            return False
            
        if entry <= 0 or sl <= 0:
            log.warning("Financial check failed: Entry or SL non-positive")
            return False
            
        if len(set(prices)) != len(prices):
            log.warning("Financial check failed: Duplicate targets detected")
            return False

        # ✅ FOURTH: Business logic validation
        if side == "LONG":
            if not (sl < entry):
                log.warning(f"LONG check failed: SL {sl} >= Entry {entry}")
                return False
            if any(p <= entry for p in prices):
                log.warning("Financial check failed: At least one LONG target <= entry")
                return False
                
        elif side == "SHORT":
            if not (sl > entry):
                log.warning(f"SHORT check failed: SL {sl} <= Entry {entry}")
                return False
            if any(p >= entry for p in prices):
                log.warning("Financial check failed: At least one SHORT target >= entry")
                return False
        else:
            log.warning(f"Financial check failed: Invalid side value: {side}")
            return False

        log.debug(f"✅ Financial check passed: {data.get('asset')} {side} Entry:{entry} SL:{sl}")
        return True
        
    except (InvalidOperation, TypeError, KeyError, AttributeError) as e:
        log.warning(f"Financial check exception: {e}. Data: {data}")
        return False
    except Exception as e:
        log.error(f"Unexpected error in financial check: {e}. Data: {data}")
        return False

# --- 3. Data Quality Monitor (NEW) ---
class DataQualityMonitor:
    """✅ NEW: Comprehensive data validation before processing"""
    
    @staticmethod
    def validate_llm_output(data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Validates LLM output comprehensively
        Returns (is_valid, reason)
        """
        try:
            # Check 1: Required fields existence
            required_fields = ["asset", "side", "entry", "stop_loss", "targets"]
            missing_fields = [field for field in required_fields if field not in data or data[field] is None]
            if missing_fields:
                return False, f"Missing fields: {missing_fields}"

            # Check 2: Data types
            if not isinstance(data["asset"], str) or not data["asset"].strip():
                return False, "Invalid asset format"
                
            if data["side"] not in ["LONG", "SHORT"]:
                return False, f"Invalid side: {data['side']}"
                
            if not isinstance(data["targets"], list) or len(data["targets"]) == 0:
                return False, "Invalid or empty targets"

            # Check 3: Numeric values
            try:
                entry = Decimal(str(data["entry"]))
                sl = Decimal(str(data["stop_loss"]))
                if entry <= 0 or sl <= 0:
                    return False, "Non-positive entry or stop_loss"
            except (InvalidOperation, TypeError):
                return False, "Invalid numeric format in entry/stop_loss"

            # Check 4: Targets structure
            for i, target in enumerate(data["targets"]):
                if not isinstance(target, dict):
                    return False, f"Target {i} is not a dictionary"
                if "price" not in target:
                    return False, f"Target {i} missing price"
                try:
                    price = Decimal(str(target["price"]))
                    if price <= 0:
                        return False, f"Target {i} has non-positive price"
                except (InvalidOperation, TypeError):
                    return False, f"Target {i} has invalid price format"

            return True, "All checks passed"
            
        except Exception as e:
            return False, f"Validation error: {str(e)}"

# --- 4. v5.0 Engine Helpers (Existing) ---
def _extract_google_response(response_json: Dict[str, Any]) -> str:
    """Extracts text content from a Google Gemini response."""
    try:
        return response_json["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as e:
        log.warning(f"Failed to extract Google response: {e}")
        return json.dumps(response_json)

def _extract_openai_response(response_json: Dict[str, Any]) -> str:
    """Extracts text content from an OpenAI/OpenRouter response."""
    try:
        return response_json["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        log.warning(f"Failed to extract OpenAI response: {e}")
        return json.dumps(response_json)

def _build_google_headers(api_key: str) -> Dict[str, str]:
    return {"Content-Type": "application/json", "X-goog-api-key": api_key}

def _build_openai_headers(api_key: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

def _model_family(model_name: str) -> str:
    """Detects the model family from its name."""
    mn = (model_name or "").lower()
    if not mn: return "unknown"
    if "gemini" in mn or mn.startswith("google/"): return "google"
    if mn.startswith("gpt-") or mn.startswith("openai/") or "gpt-4o" in mn: return "openai"
    if "claude" in mn or mn.startswith("anthropic/"): return "anthropic"
    if "qwen" in mn or "alibaba" in mn: return "qwen"
    return "other"

def _headers_for_call(call_style: str, api_key: str) -> Dict[str, str]:
    """Builds the correct headers based on the provider type."""
    if call_style == "google_direct":
        return _build_google_headers(api_key)
    if call_style == "openai_direct":
        return _build_openai_headers(api_key)
    if call_style == "openrouter_bearer":
        headers = _build_openai_headers(api_key)
        headers["HTTP-Referer"] = "http://localhost"
        headers["X-Title"] = "CapitalGuard"
        return headers
    if call_style == "anthropic_direct":
        return {
            "x-api-key": api_key, 
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01"
        }
    return _build_openai_headers(api_key)

async def _post_with_retries(url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> Tuple[bool, Optional[Dict[str, Any]], int, str]:
    """(Source of Truth) POSTs data with exponential backoff on transient errors."""
    attempt = 0
    last_text = ""
    while attempt <= IMAGE_PARSE_MAX_RETRIES:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, headers=headers, json=payload, timeout=30.0)
                status = resp.status_code
                text_snip = resp.text[:4000]
                last_text = text_snip

                if status == 200:
                    try:
                        return True, resp.json(), status, text_snip
                    except Exception as json_e:
                        log.error(f"HTTP 200 OK, but JSON decode failed: {json_e}", exc_info=True)
                        return False, None, status, text_snip
                
                if status in (429, 500, 502, 503, 504):
                    backoff = IMAGE_PARSE_BACKOFF_BASE * (2 ** attempt)
                    log.warning(f"Transient HTTP {status}. Backing off {backoff}s (attempt {attempt+1}/{IMAGE_PARSE_MAX_RETRIES}).", extra={"status": status})
                    await asyncio.sleep(backoff)
                    attempt += 1
                    continue
                
                log.warning(f"Fatal HTTP status {status}. No retry.", extra={"status": status, "resp_snip": text_snip[:400]})
                return False, None, status, text_snip
                
        except httpx.RequestError as e:
            backoff = IMAGE_PARSE_BACKOFF_BASE * (2 ** attempt)
            log.warning(f"HTTP request error: {e}. Backoff {backoff}s (attempt {attempt+1}/{IMAGE_PARSE_MAX_RETRIES}).")
            await asyncio.sleep(backoff)
            attempt += 1
            last_text = str(e)
            continue
        except Exception as e:
            log.exception(f"Unexpected POST error: {e}")
            return False, None, 0, str(e)
            
    log.error(f"All retries failed. Last error snippet: {last_text}")
    return False, None, 0, last_text

def _safe_outer_json_extract(text: str) -> Optional[str]:
    """Extract outermost JSON object using fenced blocks or non-greedy regex."""
    if not text:
        return None
    
    json_block = None

    # 1. Try to find ```json ... ``` (Most reliable)
    m_fence = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL | re.IGNORECASE)
    if m_fence:
        json_block = m_fence.group(1)
    else:
        # 2. Try to find non-greedy { ... } or [ ... ]
        m_nongreedy = re.search(r'(\[.*?\]|\{.*?\})', text, re.DOTALL)
        if m_nongreedy:
            json_block = m_nongreedy.group(1)
        
    if not json_block:
        return None

    # ✅ FIXED: Comprehensive JSON sanitization
    try:
        # 1. Remove commas from numbers (107,787.79 → 107787.79)
        sanitized_block = json_block
        sanitized_block = re.sub(r'(\d),(\d{3})', r'\1\2', sanitized_block)
        sanitized_block = re.sub(r'(\d),(\d{3})', r'\1\2', sanitized_block)
        
        # 2. Fix missing braces
        sanitized_block = sanitized_block.strip()
        
        open_braces = sanitized_block.count('{')
        close_braces = sanitized_block.count('}')
        open_brackets = sanitized_block.count('[')
        close_brackets = sanitized_block.count(']')
        
        # Add missing closing braces
        if open_braces > close_braces:
            missing_braces = open_braces - close_braces
            sanitized_block += '}' * missing_braces
        
        # Add missing closing brackets
        if open_brackets > close_brackets:
            missing_brackets = open_brackets - close_brackets
            sanitized_block += ']' * missing_brackets
        
        # Common pattern fixes
        if sanitized_block.endswith('}]'):
            sanitized_block += '}'
        elif sanitized_block.endswith('"}'):
            pass  # Complete
        elif not sanitized_block.endswith('}') and not sanitized_block.endswith(']'):
            if 'targets' in sanitized_block and '[' in sanitized_block:
                if sanitized_block.count('[') > sanitized_block.count(']'):
                    sanitized_block += ']'
                if sanitized_block.count('{') > sanitized_block.count('}'):
                    sanitized_block += '}'
        
        return sanitized_block
    except Exception as e:
        log.warning(f"JSON sanitization failed: {e}, returning original")
        return json_block

def _extract_claude_response(response_json: Dict[str, Any]) -> str:
    """Handle multiple Claude response shapes."""
    try:
        if "content" in response_json and isinstance(response_json["content"], list):
            for block in response_json["content"]:
                if block.get("type") == "text":
                    return block.get("text", "")
        if "completion" in response_json:
            return response_json["completion"]
        if "choices" in response_json:
            return response_json["choices"][0].get("message", {}).get("content", "")
        return json.dumps(response_json)
    except Exception:
        return json.dumps(response_json)

def _extract_qwen_response(response_json: Dict[str, Any]) -> str:
    """Handle multiple Qwen response shapes."""
    try:
        if "output" in response_json and "text" in response_json["output"]:
            return response_json["output"]["text"]
        if "choices" in response_json:
            return response_json["choices"][0].get("message", {}).get("content", "")
        if "result" in response_json:
            return response_json["result"]
        return json.dumps(response_json)
    except Exception:
        return json.dumps(response_json)

def _has_obvious_errors(signal: Dict) -> bool:  
    """Detect obvious data extraction errors"""  
    try:  
        entry = float(signal.get("entry", 0))  
        sl = float(signal.get("stop_loss", 0)) if signal.get("stop_loss") else 0  
        if sl > 0 and entry > 0 and abs(sl - entry) / entry > 5:
            log.warning(f"Signal {signal.get('asset')} has obvious error: Entry {entry}, SL {sl}")
            return True  
        return False  
    except (ValueError, TypeError):  
        return True

def _smart_signal_selector(signals: List[Dict]) -> Optional[Dict]:  
    """Select best trade signal based on completeness and quality"""  
    if not signals:  
        return None  
    scored = []  
    for signal in signals:  
        if not isinstance(signal, dict):  
            continue  
        score = 0  
        required = ["asset", "side", "entry", "stop_loss", "targets"]  
        present_fields = sum(1 for k in required if k in signal and signal[k] is not None)  
        score += present_fields * 10  
        if present_fields == len(required):  
            score += 20  
        targets = signal.get("targets", [])  
        if isinstance(targets, list) and targets:  
            score += min(len(targets), 5)  
        if _has_obvious_errors(signal):  
            score -= 15  
        scored.append((score, signal))  
    return max(scored, key=lambda x: x[0])[1] if scored else None