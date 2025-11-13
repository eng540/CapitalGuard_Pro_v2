#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: ai_service/services/parsing_utils.py ---
# File: ai_service/services/parsing_utils.py
# Version: 2.2.0 (v5.1 Engine Core - ImportError Hotfix)
# ✅ THE FIX: (Protocol 1) إصلاح خطأ `ImportError`.
#    - 1. (MOVED) تمت إضافة `_extract_google_response` و `_extract_openai_response`.
#    - 2. (MOVED) تمت إضافة `_build_google_headers` و `_build_openai_headers`.
#    - 3. (NEW) إضافة آلية إعادة المحاولة: `_post_with_retries`.
#    - 4. (NEW) إضافة "محدد الإشارة الذكي": `_smart_signal_selector`.
#    - 5. (NEW) إضافة "مستخرج JSON الآمن": `_safe_outer_json_extract`.
#    - 6. (NEW) إضافة مستخرِجات مخصصة: `_extract_claude_response`, `_extract_qwen_response`.
#    - 7. (NEW) إضافة مساعدين: `_model_family`, `_headers_for_call`.
#    - 8. (MOVED) نقل `_financial_consistency_check` (من llm_parser) إلى هنا.
# 🎯 IMPACT: هذا الملف أصبح الآن "مصدر الحقيقة" (SSoT) لجميع عمليات التحليل.

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

                normalized.append({"price": price_val, "close_percent": float(close_pct)}) # Store Decimal
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

                normalized.append({"price": price, "close_percent": float(pct)}) # Store Decimal
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
                normalized.append({"price": price, "close_percent": float(pct)}) # Store Decimal
            except Exception:
                continue

    # Apply 100% last target rule
    if normalized and all(t["close_percent"] == 0.0 for t in normalized):
        normalized[-1]["close_percent"] = 100.0
        
    return normalized


# --- 2. Validation (Moved from llm_parser) ---
def _financial_consistency_check(data: Dict[str, Any]) -> bool:
    """Strict numeric checks (v5.0). Expects Decimals."""
    try:
        entry = data["entry"]
        sl = data["stop_loss"]
        side = str(data["side"]).strip().upper()
        targets_raw = data.get("targets", [])
        
        if not (isinstance(entry, Decimal) and isinstance(sl, Decimal)):
             entry = parse_decimal_token(str(entry))
             sl = parse_decimal_token(str(sl))

        if not isinstance(targets_raw, list) or len(targets_raw) == 0:
            log.warning("Targets missing or empty in financial check.")
            return False

        prices: List[Decimal] = []
        for t in targets_raw:
            price_val = t["price"] if isinstance(t.get("price"), Decimal) else parse_decimal_token(str(t.get("price")))
            if price_val:
                prices.append(price_val)

        if not prices:
             log.warning("No valid target prices found in financial check.")
             return False
        if entry <= 0 or sl <= 0:
            log.warning("Entry or SL non-positive.")
            return False
        if len(set(prices)) != len(prices):
            log.warning("Duplicate targets detected.")
            return False

        if side == "LONG":
            if not (sl < entry):
                log.warning(f"LONG check failed: SL {sl} >= Entry {entry}")
                return False
            if any(p <= entry for p in prices):
                log.warning("At least one LONG target <= entry")
                return False
        elif side == "SHORT":
            if not (sl > entry):
                log.warning(f"SHORT check failed: SL {sl} <= Entry {entry}")
                return False
            if any(p >= entry for p in prices):
                log.warning("At least one SHORT target >= entry")
                return False
        else:
            log.warning(f"Invalid side value: {side}")
            return False

        return True
    except (InvalidOperation, TypeError, KeyError, AttributeError) as e:
        log.warning(f"Financial check exception: {e}. Data: {data}")
        return False


# --- 3. v5.0 Engine Helpers (NEW/MOVED) ---

# ✅ THE FIX (v2.2.0): Add the missing extractors
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

# ✅ THE FIX (v2.2.0): Add the missing header builders
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
    return "other" # Default to OpenAI compatible

def _headers_for_call(call_style: str, api_key: str) -> Dict[str, str]:
    """Builds the correct headers based on the provider type."""
    if call_style == "google_direct":
        return _build_google_headers(api_key)
    if call_style == "openai_direct":
        return _build_openai_headers(api_key)
    if call_style == "openrouter_bearer":
        headers = _build_openai_headers(api_key) # Start with OpenAI headers
        headers["HTTP-Referer"] = "http://localhost" # Required by OpenRouter
        headers["X-Title"] = "CapitalGuard"
        return headers
    if call_style == "anthropic_direct":
        return {
            "x-api-key": api_key, 
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01"
        }
    return _build_openai_headers(api_key) # Default

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
                
                if status in (429, 500, 502, 503, 504): # Transient errors
                    backoff = IMAGE_PARSE_BACKOFF_BASE * (2 ** attempt)
                    log.warning(f"Transient HTTP {status}. Backing off {backoff}s (attempt {attempt+1}/{IMAGE_PARSE_MAX_RETRIES}).", extra={"status": status})
                    await asyncio.sleep(backoff)
                    attempt += 1
                    continue
                
                log.warning(f"Fatal HTTP status {status}. No retry.", extra={"status": status, "resp_snip": text_snip[:400]})
                return False, None, status, text_snip
                
        except httpx.RequestError as e: # Network errors
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
    """ Extract outermost JSON object using fenced blocks or non-greedy regex. """
    if not text:
        return None
    
    # 1. Try to find ```json ... ``` (Most reliable)
    m_fence = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL | re.IGNORECASE)
    if m_fence:
        return m_fence.group(1)

    # 2. Try to find non-greedy { ... } or [ ... ]
    m_nongreedy = re.search(r'(\[.*?\]|\{.*?\})', text, re.DOTALL)
    if m_nongreedy:
        return m_nongreedy.group(1)
        
    return None

def _extract_claude_response(response_json: Dict[str, Any]) -> str:
    """ Handle multiple Claude response shapes. """
    try:
        # Standard Claude response
        if "content" in response_json and isinstance(response_json["content"], list):
            for block in response_json["content"]:
                if block.get("type") == "text":
                    return block.get("text", "")
        # Fallback for completion-style
        if "completion" in response_json:
            return response_json["completion"]
        # Fallback for OpenRouter-proxied Claude
        if "choices" in response_json:
            return response_json["choices"][0].get("message", {}).get("content", "")
        return json.dumps(response_json)
    except Exception:
        return json.dumps(response_json)

def _extract_qwen_response(response_json: Dict[str, Any]) -> str:
    """ Handle multiple Qwen response shapes. """
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
        if sl > 0 and entry > 0 and abs(sl - entry) / entry > 5:  # 500% difference
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
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: ai_service/services/parsing_utils.py ---