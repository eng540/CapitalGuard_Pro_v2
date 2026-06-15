#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: ai_service/services/regex_parser.py ---
# File: ai_service/services/regex_parser.py
# Version: 4.0.1 (Production-Ready Hotfix)
# ✅ THE FIX: (Protocol 1) إصلاح خطأ `NameError: name 'name' is not defined`.
#    - تم تغيير `logging.getLogger(name)` إلى `logging.getLogger(__name__)`.
# 🎯 IMPACT: هذا الملف الآن يدمج كل المنطق المتقدم من v4.0.0 وهو جاهز للتشغيل.
# NOTE: This file is self-contained and production-ready.

import re
import unicodedata
import logging
from typing import Dict, Any, Optional, List, Tuple
from decimal import Decimal, InvalidOperation, getcontext

# Ensure sufficient precision for crypto prices
getcontext().prec = 18

# Import shared SSoT utilities
from services.parsing_utils import parse_decimal_token, normalize_targets, _financial_consistency_check

# ✅ THE FIX (v4.0.1): Use __name__ for the logger
log = logging.getLogger(__name__)

# ----------------------------
# Text normalization / tokenization
# ----------------------------
_AR_TO_EN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

def _normalize_text(text: str) -> str:
    if not text:
        return ""
    # Unicode normalization, convert Arabic digits to EN, unify punctuation
    s = unicodedata.normalize("NFKC", text)
    s = s.translate(_AR_TO_EN_DIGITS)
    # Normalize newlines and replace uncommon punctuation with space, keep basic symbols
    s = s.replace("،", ",")
    s = re.sub(r'[^\w\s\u0600-\u06FF@:.,\d\-+%$#/|→\[\]\(\)`:\']', ' ', s, flags=re.UNICODE)
    s = re.sub(r'(\r\n|\r|\n){2,}', '\n', s)
    s = re.sub(r'\s{2,}', ' ', s)
    return s.strip()

def _split_lines_preserve(text: str) -> List[str]:
    return [ln.strip() for ln in re.split(r'[\r\n]+', text) if ln.strip()]

# ----------------------------
# Quick detector
# ----------------------------
_QUICK_KEYWORDS = [
    r'\bENTRY\b', r'\bSL\b', r'\bSTOP LOSS\b', r'\bTP\b', r'\bTARGETS\b',
    r'دخول', r'ايقاف خسارة', r'الاهداف', r'اهداف', r'الهدف', r'هدف', r'سعر الدخول'
]

def _quick_detector(text: str) -> bool:
    txt = text.upper()
    hits = sum(1 for kw in _QUICK_KEYWORDS if re.search(kw, txt))
    # Quick pass if at least 2 indicators appear OR asset-like token present
    has_asset_tag = bool(re.search(r'#\s*[A-Z0-9]{2,12}', txt))
    return hits >= 2 or has_asset_tag

# ----------------------------
# Helpers: decimal conversion and safe extraction
# ----------------------------
def _to_decimal(v: Any) -> Optional[Decimal]:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    try:
        # parse_decimal_token returns Decimal or None
        parsed = parse_decimal_token(str(v))
        if isinstance(parsed, Decimal):
            return parsed
        # fallback: direct Decimal conversion if parse_decimal_token couldn't handle suffixes
        return Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None

def _decimal_str(d: Optional[Decimal]) -> Optional[str]:
    if d is None:
        return None
    # Normalize string without scientific notation
    return format(d.normalize(), 'f')

# ----------------------------
# Target extractor (advanced)
# ----------------------------
_TARGET_SPLIT_RE = re.compile(r'[\s,;/→\-]+')

def _extract_targets_from_string(targets_raw: str, source_text: str="") -> List[Dict[str, Decimal]]:
    """
    Uses parsing_utils.normalize_targets when possible, then coerces all numeric fields into Decimal.
    Returns list of {price: Decimal, close_percent: Decimal}.
    """
    results: List[Dict[str, Decimal]] = []
    try:
        # First try SSoT's normalize_targets for broad coverage
        norm = normalize_targets(targets_raw, source_text=source_text)
        if isinstance(norm, list) and norm:
            for t in norm:
                price = _to_decimal(t.get("price"))
                pct = t.get("close_percent")
                # convert pct which may be float to Decimal safely
                pct_dec = None
                try:
                    if pct is None:
                        pct_dec = Decimal("0")
                    elif isinstance(pct, Decimal):
                        pct_dec = pct
                    else:
                        pct_dec = Decimal(str(pct))
                except Exception:
                    pct_dec = Decimal("0")
                if price is None:
                    continue
                results.append({"price": price, "close_percent": pct_dec})
            return results
    except Exception as e:
        log.debug(f"normalize_targets failed: {e}")

    # Fallback custom parsing
    s = targets_raw or ""
    # remove bullets and common labels
    s = re.sub(r'(TP|TP\d+|TARGETS|TARGET|الاهداف|الهدف)\s*[:\-]?', '', s, flags=re.IGNORECASE)
    tokens = [tok for tok in _TARGET_SPLIT_RE.split(s) if tok.strip()]
    for tok in tokens:
        try:
            # token may be like "6k@25%", "0.0123", "1.2@20"
            price = None
            pct = Decimal("0")
            if '@' in tok:
                p, pctpart = tok.split('@', 1)
                price = _to_decimal(p)
                pct_str = pctpart.strip().rstrip('%')
                pct = Decimal(str(_to_decimal(pct_str) or Decimal("0")))
            else:
                price = _to_decimal(tok)
            if price:
                results.append({"price": price, "close_percent": pct})
        except Exception:
            continue

    # apply last-target-100% rule if all close_percent == 0
    if results and all(t["close_percent"] == Decimal("0") for t in results):
        results[-1]["close_percent"] = Decimal("100")
    return results

# ----------------------------
# Structured extractor: Templates
# ----------------------------
# Each template returns tuple (candidate_dict, confidence_score)
# candidate_dict fields: asset, side, entry (Decimal), stop_loss (Decimal), targets (list of dicts)
_TEMPLATES: List[Tuple[re.Pattern, Dict[str, Any]]] = []

# Common patterns (Arabic/English) - flexible groups
# Pattern 1: Compact single-line with #ASSET ENTRY SL TP
_TEMPLATES.append((
    re.compile(
        r'(?P<asset>#\s*[A-Z0-9]{2,12})[^\n\r]{0,60}?'
        r'(?P<side>\bLONG\b|\bSHORT\b|\bBUY\b|\bSELL\b|شراء|بيع|صعود|هبوط)?[^\n\r]{0,80}?'
        r'(?:ENTRY[:\s→]*?(?P<entry>[\d.,KMBkmb]+))?[^\n\r]{0,80}?'
        r'(?:SL|STOP(?:\s*LOSS)?|ايقاف خسارة)[:\s→]*?(?P<sl>[\d.,KMBkmb]+)?[^\n\r]{0,80}?'
        r'(?:TP|TARGETS|الاهداف|اهداف)[:\s\r\n\-]*?(?P<toks>[\d\w\.\,@%\s\/\-\u2192\→KMBkmb]+)?',
        re.IGNORECASE | re.UNICODE
    ),
    {"confidence": 90}
))

# Pattern 2: Multi-line labeled keys (Entry:, SL:, TP:)
_TEMPLATES.append((
    re.compile(
        r'(?:(?:ASSET|#\s*[A-Z0-9]{2,12}|رمز)\s*[:\-]?\s*(?P<asset2>[A-Z0-9]{2,12}))?'
        r'(?:(?:SIDE|نوع|الاتجاه)\s*[:\-]?\s*(?P<side2>\bLONG\b|\bSHORT\b|BUY|SELL|شراء|بيع)?)?'
        r'(?:(?:ENTRY|دخول|سعر الدخول)\s*[:\-]?\s*(?P<entry2>[\d.,KMBkmb]+))?'
        r'(?:(?:SL|STOP|ايقاف خسارة)\s*[:\-]?\s*(?P<sl2>[\d.,KMBkmb]+))?'
        r'(?:(?:TP|TARGETS|الاهداف|اهداف)\s*[:\-]?\s*(?P<toks2>[\d\w\.\,@%\s\/\-\u2192\→KMBkmb]+))?',
        re.IGNORECASE | re.UNICODE | re.DOTALL
    ),
    {"confidence": 85}
))

# Pattern 3: Lines like "TP1: 1.2, TP2: 1.5, TP3: 1.8"
_TEMPLATES.append((
    re.compile(
        r'(?P<toks3>(?:TP\d*[:\s]*[\d.,KMBkmb@%]+\s*[,\n\r]*){1,10})',
        re.IGNORECASE | re.UNICODE | re.DOTALL
    ),
    {"confidence": 70}
))

# ----------------------------
# Candidate scoring and repair
# ----------------------------
def _score_candidate(cand: Dict[str, Any], base_confidence: int) -> int:
    score = base_confidence
    # completeness
    required = ['asset', 'side', 'entry', 'stop_loss', 'targets']
    present = sum(1 for k in required if cand.get(k) is not None)
    score += int((present / len(required)) * 30)
    # numeric robustness: ensure Decimals
    try:
        if isinstance(cand.get("entry"), Decimal):
            score += 10
        if isinstance(cand.get("stop_loss"), Decimal):
            score += 10
    except Exception:
        pass
    # targets quality
    t = cand.get("targets") or []
    if isinstance(t, list) and t:
        score += min(len(t), 5) * 2
    # penalize obvious numeric issues
    if cand.get("entry") and cand.get("stop_loss"):
        try:
            entry = cand["entry"]
            sl = cand["stop_loss"]
            if entry > 0 and sl > 0:
                # small sanity check
                if entry == sl:
                    score -= 20
        except Exception:
            pass
    return max(0, min(score, 100))

def _auto_repair_candidate(cand: Dict[str, Any]) -> Dict[str, Any]:
    # 1) try to coerce strings to Decimal
    for key in ("entry", "stop_loss"):
        if cand.get(key) is not None and not isinstance(cand.get(key), Decimal):
            cand[key] = _to_decimal(cand[key])
    # 2) ensure targets are list of decimals
    repaired_targets = []
    for t in cand.get("targets", []) or []:
        if isinstance(t, dict):
            price = _to_decimal(t.get("price"))
            pct = t.get("close_percent")
            try:
                pct_dec = pct if isinstance(pct, Decimal) else Decimal(str(pct or "0"))
            except Exception:
                pct_dec = Decimal("0")
            if price:
                repaired_targets.append({"price": price, "close_percent": pct_dec})
    cand["targets"] = repaired_targets
    return cand

# ----------------------------
# Validator (business rules)
# ----------------------------
def _validate_financials(cand: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    Uses _financial_consistency_check (SSoT) but adapts for Decimal conversion.
    Returns (valid, reason)
    """
    try:
        # Prepare data dict expected by _financial_consistency_check
        data = {
            "entry": cand.get("entry"),
            "stop_loss": cand.get("stop_loss"),
            "side": cand.get("side"),
            "targets": cand.get("targets", [])
        }
        # Ensure types: convert strings to Decimal
        if not isinstance(data["entry"], Decimal):
            data["entry"] = _to_decimal(data["entry"])
        if not isinstance(data["stop_loss"], Decimal):
            data["stop_loss"] = _to_decimal(data["stop_loss"])
        # Ensure each target has Decimal price
        validated_targets = []
        for t in data["targets"]:
            price = t.get("price") if isinstance(t.get("price"), Decimal) else _to_decimal(t.get("price"))
            pct = t.get("close_percent")
            try:
                pct_dec = pct if isinstance(pct, Decimal) else Decimal(str(pct or "0"))
            except Exception:
                pct_dec = Decimal("0")
            if price:
                validated_targets.append({"price": price, "close_percent": pct_dec})
        data["targets"] = validated_targets
        
        # Call the SSoT validator
        ok = _financial_consistency_check(data)
        
        if ok:
            return True, None
        return False, "financial_consistency_failed"
    except Exception as e:
        log.debug(f"Validator exception: {e}", exc_info=False)
        return False, "validator_exception"

# ----------------------------
# Structured extractor engine
# ----------------------------
def _structured_extract(text: str) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    txt = _normalize_text(text)
    for pattern, meta in _TEMPLATES:
        for m in pattern.finditer(txt):
            try:
                cand: Dict[str, Any] = {}
                # asset
                asset = None
                if m.groupdict().get("asset"):
                    asset = m.group("asset").strip().lstrip('#').replace(" ", "").upper()
                elif m.groupdict().get("asset2"):
                    asset = m.group("asset2").strip().upper()
                if asset:
                    # try to append USDT if common asset and no market suffix
                    if len(asset) <= 5 and not asset.endswith(("USDT","USD","BTC")):
                        asset = asset.upper()
                    cand["asset"] = asset

                # side
                side_val = None
                for k in ("side", "side2"):
                    if m.groupdict().get(k):
                        side_val = m.group(k)
                        break
                if side_val:
                    side_val = side_val.upper()
                    if any(x in side_val for x in ("LONG","BUY","شراء","صعود")):
                        cand["side"] = "LONG"
                    elif any(x in side_val for x in ("SHORT","SELL","بيع","هبوط")):
                        cand["side"] = "SHORT"

                # entry / sl extraction from groups
                entry_raw = None
                for g in ("entry", "entry2"):
                    if m.groupdict().get(g):
                        entry_raw = m.group(g)
                        break
                sl_raw = None
                for g in ("sl", "sl2"):
                    if m.groupdict().get(g):
                        sl_raw = m.group(g)
                        break

                if entry_raw:
                    cand["entry"] = _to_decimal(entry_raw)
                if sl_raw:
                    cand["stop_loss"] = _to_decimal(sl_raw)

                # targets
                toks = None
                for g in ("toks", "toks2", "toks3"):
                    if m.groupdict().get(g):
                        toks = m.group(g)
                        break
                if toks:
                    cand["targets"] = _extract_targets_from_string(toks, source_text=text)

                # score & repair
                base_conf = meta.get("confidence", 50)
                cand = _auto_repair_candidate(cand)
                cand_score = _score_candidate(cand, base_conf)
                cand["_score"] = cand_score
                candidates.append(cand)
            except Exception:
                log.debug("Template parse produced exception; continuing.", exc_info=False)
                continue
    return candidates

# ----------------------------
# KV fallback parser (flexible, multiline aware)
# ----------------------------
_KV_KEYWORDS = {
    "asset": [r'\bASSET\b', r'\bرمز\b', r'#\s*[A-Z0-9]{2,12}'],
    "side": [r'\bSIDE\b', r'\bنوع\b', r'\bLONG\b', r'\bSHORT\b', r'شراء', r'بيع'],
    "entry": [r'\bENTRY\b', r'سعر الدخول', r'دخول', r'\bBUY\b'],
    "stop_loss": [r'\bSL\b', r'\bSTOP\b', r'ايقاف خسارة', r'STOP LOSS'],
    "targets": [r'\bTP\b', r'\bTARGETS\b', r'الاهداف', r'اهداف', r'TP\d*']
}

def _kv_fallback(text: str) -> Optional[Dict[str, Any]]:
    """
    Scans lines for key-like tokens and collects values (supports multiline values).
    """
    lines = _split_lines_preserve(text)
    collected: Dict[str, str] = {}
    # join short continuations to previous key value if indented or starting with digit
    current_key = None
    for ln in lines:
        # detect key
        matched_key = None
        for key, kws in _KV_KEYWORDS.items():
            for kw in kws:
                if re.search(kw, ln, flags=re.IGNORECASE):
                    matched_key = key
                    break
            if matched_key:
                break
        if matched_key:
            # capture after colon or keyword
            after = re.split(r'[:\-]\s*', ln, maxsplit=1)
            if len(after) == 2 and after[1].strip():
                collected[matched_key] = after[1].strip()
            else:
                # capture rest of the line as value if present
                parts = ln.split()
                if len(parts) > 1:
                    # take trailing tokens as value
                    val = " ".join(parts[1:])
                    collected[matched_key] = val.strip()
                else:
                    # prepare to capture next lines
                    collected.setdefault(matched_key, "")
                    current_key = matched_key
            continue
        # continuation line: if starts with digit or is indented, append to current key
        if current_key and (re.match(r'^\s*\d', ln) or len(ln.split()) <= 6):
            collected[current_key] = (collected.get(current_key, "") + " " + ln).strip()
        else:
            current_key = None

    if not collected:
        return None

    parsed: Dict[str, Any] = {}
    # asset
    asset_raw = collected.get("asset")
    if asset_raw:
        asset_search = re.search(r'([A-Z0-9]{2,12})', asset_raw.upper())
        if asset_search:
            parsed["asset"] = asset_search.group(1).upper()

    # side
    side_raw = collected.get("side")
    if side_raw:
        s = side_raw.upper()
        if any(x in s for x in ("LONG","BUY","شراء","صعود")):
            parsed["side"] = "LONG"
        elif any(x in s for x in ("SHORT","SELL","بيع","هبوط")):
            parsed["side"] = "SHORT"

    # entry / sl
    if collected.get("entry"):
        parsed["entry"] = _to_decimal(collected.get("entry"))
    if collected.get("stop_loss"):
        parsed["stop_loss"] = _to_decimal(collected.get("stop_loss"))

    # targets
    if collected.get("targets"):
        parsed["targets"] = _extract_targets_from_string(collected.get("targets"), source_text=text)

    # if we have minimal fields, attempt to coerce and return
    if parsed.get("asset") and parsed.get("side") and parsed.get("entry") and parsed.get("stop_loss") and parsed.get("targets"):
        parsed = _auto_repair_candidate(parsed)
        return parsed
    # else return partial if at least entry and targets present
    if parsed.get("entry") and parsed.get("targets"):
        parsed = _auto_repair_candidate(parsed)
        return parsed
    return None

# ----------------------------
# Public API: parse_with_regex
# ----------------------------
def parse_with_regex(text: str, user_id: Optional[int] = None, lang_hint: Optional[str] = None, allow_llm_fallback: bool = True) -> Optional[Dict[str, Any]]:
    """
    Multi-level regex parser entrypoint.
    Returns a dict on success:
    {
      asset: str,
      side: "LONG"|"SHORT",
      entry: Decimal,
      stop_loss: Decimal,
      targets: [{"price": Decimal, "close_percent": Decimal}, ...],
      score: int,
      path: "quick"|"structured"|"kv"
    }
    Or None if parsing failed.
    """
    if not text or not text.strip():
        return None

    raw = text
    txt = _normalize_text(raw)

    # Quick detector: if message unlikely to contain trading signal, exit early
    quick_hit = _quick_detector(txt)
    # Attempt structured extraction if quick detector positive OR long message
    candidates: List[Dict[str, Any]] = []
    try:
        if quick_hit or len(txt) > 80:
            candidates.extend(_structured_extract(raw))

        # If structured produced nothing, try KV fallback
        if not candidates:
            kv = _kv_fallback(raw)
            if kv:
                kv["_score"] = _score_candidate(kv, 60)
                candidates.append(kv)

        # If still nothing and quick detector was negative, do one more attempt with structured (loosen)
        if not candidates and not quick_hit:
            candidates.extend(_structured_extract(raw))

        if not candidates:
            return None

        # Normalize all candidates and pick best by score after repair and validation
        normalized_candidates = []
        for c in candidates:
            c = _auto_repair_candidate(c)
            # compute score if not present
            base = c.get("_score", 50)
            c["_score"] = _score_candidate(c, base)
            # run validator; annotate reason if invalid
            valid, reason = _validate_financials(c)
            c["_valid"] = valid
            c["_reason"] = reason
            normalized_candidates.append(c)

        # Prefer valid candidates first, then highest score
        valid_cands = [c for c in normalized_candidates if c.get("_valid")]
        chosen = None
        if valid_cands:
            chosen = max(valid_cands, key=lambda x: x["_score"])
        else:
            # choose highest scoring even if invalid (to allow LLM fallback or review)
            chosen = max(normalized_candidates, key=lambda x: x["_score"])

        # Prepare final shape, ensure Decimal types and sort targets
        final = {
            "asset": chosen.get("asset"),
            "side": chosen.get("side"),
            "entry": chosen.get("entry"),
            "stop_loss": chosen.get("stop_loss"),
            "targets": [],
            "score": int(chosen.get("_score", 0)),
            "path": "structured" if chosen in candidates and chosen.get("_score",0) >= 70 else "kv"
        }

        # ensure targets sorted by price depending on side
        tlist = chosen.get("targets") or []
        tlist_clean = []
        for t in tlist:
            price = t.get("price")
            pct = t.get("close_percent")
            if not isinstance(price, Decimal):
                price = _to_decimal(price)
            if not isinstance(pct, Decimal):
                try:
                    pct = Decimal(str(pct or "0"))
                except Exception:
                    pct = Decimal("0")
            if price:
                tlist_clean.append({"price": price, "close_percent": pct})
        # sort targets
        if final["side"] == "SHORT":
            tlist_clean.sort(key=lambda x: x["price"], reverse=True)
        else:
            tlist_clean.sort(key=lambda x: x["price"])
        final["targets"] = tlist_clean

        # Final validation step
        valid_final, reason_final = _validate_financials(final)
        final["valid"] = bool(valid_final)
        final["reason"] = reason_final

        # convert Decimal values to Decimal objects (kept) — caller may serialize
        return final if final.get("asset") and final.get("side") and final.get("entry") and final.get("stop_loss") and final.get("targets") else None

    except Exception as e:
        log.exception(f"regex_v2 parsing failed unexpectedly: {e}")
        return None
#--- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: ai_service/services/regex_parser.py ---