# src/capitalguard/application/services/image_parsing_service.py (v2.2 - Intelligent Contextual Parser)
"""
ImageParsingService - خدمة تحليل الصور والنص لاستخراج بيانات التداول
"""

import logging
import re
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

log = logging.getLogger(__name__)

@dataclass
class ParsingResult:
    """نتيجة تحليل بيانات التداول"""
    success: bool
    asset: str = ""
    side: str = ""
    entry: float = 0.0
    stop_loss: float = 0.0
    targets: List[Dict[str, float]] = None
    confidence: str = "low"
    parser: str = "unknown"
    error_message: str = ""

    def __post_init__(self):
        if self.targets is None:
            self.targets = []

class ImageParsingService:
    """
    خدمة متخصصة في استخراج بيانات التداول من النصوص المعقدة والصور.
    """
    
    def __init__(self):
        self._initialized = False
        # Keywords that should never be considered an asset
        self.ASSET_BLACKLIST = {'ACTIVE', 'SIGNAL', 'PERFORMANCE', 'ENTRY', 'STOP', 'PLAN', 'EXIT', 'NOTES', 'LONG', 'SHORT'}
        
    async def initialize(self):
        """تهيئة المحركات بشكل غير متزامن"""
        self._initialized = True
        log.info("✅ ImageParsingService initialized successfully")
            
    async def extract_trade_data(self, content: str, is_image: bool = False) -> Optional[Dict[str, Any]]:
        """
        الاستخراج الرئيسي لبيانات التداول من المحتوى
        """
        if not self._initialized:
            await self.initialize()
            
        try:
            raw_text = content
            if not raw_text or len(raw_text.strip()) < 10:
                return None
                
            result = self._parse_key_value_format(raw_text)
            
            if result.success:
                return {
                    'asset': result.asset,
                    'side': result.side,
                    'entry': result.entry,
                    'stop_loss': result.stop_loss,
                    'targets': result.targets,
                    'confidence': result.confidence,
                    'parser': result.parser
                }
            log.warning(f"Parsing failed for text. Reason: {result.error_message}")
            return None
        except Exception as e:
            log.error(f"❌ Error extracting trade data: {e}", exc_info=True)
            return None
        
    def _clean_text(self, text: str) -> str:
        """تنظيف النص مع الحفاظ على بنية الأسطر"""
        if not text:
            return ""
        text = re.sub(r'[^\w\s\u0600-\u06FF@:.,\d\-+%$#/|]', ' ', text)
        text = re.sub(r'(\r\n|\r|\n){2,}', '\n', text)
        return text.strip().upper()

    def _parse_number(self, num_str: str) -> Optional[float]:
        if not num_str: return None
        num_str = str(num_str).strip().upper().replace(',', '')
        multipliers = {'K': 1000, 'M': 1000000, 'B': 1000000000}
        try:
            if num_str and num_str[-1] in multipliers:
                return float(num_str[:-1]) * multipliers[num_str[-1]]
            return float(num_str)
        except (ValueError, TypeError):
            log.warning(f"Cannot parse number: '{num_str}'")
            return None

    def _find_asset_and_side(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        """Finds asset and side with priority."""
        asset, side = None, None
        
        # Find Side first
        if "LONG" in text or "شراء" in text:
            side = "LONG"
        elif "SHORT" in text or "بيع" in text:
            side = "SHORT"

        # Find Asset with priority
        # Priority 1: Hashtagged symbol (e.g., #SOLUSDT)
        hashtag_match = re.search(r'#([A-Z0-9]{4,12})', text)
        if hashtag_match:
            candidate = hashtag_match.group(1)
            if candidate not in self.ASSET_BLACKLIST:
                asset = candidate
                return asset, side

        # Priority 2: Symbol ending with USDT
        usdt_match = re.search(r'\b([A-Z]{3,8}USDT)\b', text)
        if usdt_match:
            candidate = usdt_match.group(1)
            if candidate not in self.ASSET_BLACKLIST:
                asset = candidate
                return asset, side
        
        return asset, side

    def _parse_key_value_format(self, text: str) -> ParsingResult:
        """المحلل الذكي الجديد الذي يعتمد على الكلمات المفتاحية."""
        
        cleaned_text = self._clean_text(text)
        lines = cleaned_text.split('\n')
        
        asset, side = self._find_asset_and_side(cleaned_text)
        
        data = {
            "entry": None,
            "stop_loss": None,
            "targets": []
        }

        for line in lines:
            # Entry Price
            entry_match = re.search(r'(?:ENTRY|الدخول)\s*[:]?\s*([\d.,]+[KMB]?)', line)
            if entry_match and data["entry"] is None:
                data["entry"] = self._parse_number(entry_match.group(1))

            # Stop Loss
            sl_match = re.search(r'(?:STOP|SL|وقف)\s*[:]?\s*([\d.,]+[KMB]?)', line)
            if sl_match and data["stop_loss"] is None:
                data["stop_loss"] = self._parse_number(sl_match.group(1))

            # Targets
            target_match = re.search(r'(?:TP|TARGET|هدف)\s*\d+\s*[:]?\s*([\d.,]+[KMB]?)', line)
            if target_match:
                price = self._parse_number(target_match.group(1))
                if price:
                    close_pct = 0.0
                    close_match = re.search(r'CLOSE\s*(\d+)', line)
                    if close_match:
                        close_pct = float(close_match.group(1))
                    data["targets"].append({"price": price, "close_percent": close_pct})

        # --- Validation Phase ---
        if not all([asset, side, data["entry"], data["stop_loss"], data["targets"]]):
            missing = [k for k, v in {**{"asset": asset, "side": side}, **data}.items() if v is None or v == []]
            return ParsingResult(success=False, error_message=f"Missing required fields: {missing}")

        # Auto-assign close percentage if none were found
        if data["targets"] and all(t['close_percent'] == 0 for t in data["targets"]):
            data["targets"][-1]['close_percent'] = 100.0

        return ParsingResult(
            success=True,
            asset=asset,
            side=side,
            entry=data["entry"],
            stop_loss=data["stop_loss"],
            targets=data["targets"],
            confidence='high',
            parser='keyword_extractor_v2'
        )