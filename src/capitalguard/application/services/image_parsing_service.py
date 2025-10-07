# src/capitalguard/application/services/image_parsing_service.py (v2.1 - Robust & Flexible Parser)
"""
ImageParsingService - خدمة تحليل الصور والنص لاستخراج بيانات التداول
"""

import logging
import re
import asyncio
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
        # إزالة الأيقونات والرموز غير الضرورية ولكن الحفاظ على # . : / |
        text = re.sub(r'[^\w\s\u0600-\u06FF@:.,\d\-+%$#/|]', ' ', text)
        # استبدال فواصل الأسطر المتعددة بواحد
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

    def _parse_key_value_format(self, text: str) -> ParsingResult:
        """المحلل الذكي الجديد الذي يعتمد على الكلمات المفتاحية."""
        
        cleaned_text = self._clean_text(text)
        lines = cleaned_text.split('\n')
        
        data = {
            "asset": None,
            "side": None,
            "entry": None,
            "stop_loss": None,
            "targets": []
        }

        # --- Extraction Phase ---
        for line in lines:
            # Asset and Side (usually in the header)
            if not data["asset"] or not data["side"]:
                asset_match = re.search(r'#?([A-Z0-9/USDT]+)', line)
                if asset_match:
                    data["asset"] = asset_match.group(1).replace('/', '')
                
                if "LONG" in line or "شراء" in line:
                    data["side"] = "LONG"
                elif "SHORT" in line or "بيع" in line:
                    data["side"] = "SHORT"

            # Entry Price
            entry_match = re.search(r'(?:ENTRY|الدخول)\s*[:]?\s*([\d.,]+[KMB]?)', line, re.IGNORECASE)
            if entry_match:
                data["entry"] = self._parse_number(entry_match.group(1))

            # Stop Loss
            sl_match = re.search(r'(?:STOP|SL|وقف)\s*[:]?\s*([\d.,]+[KMB]?)', line, re.IGNORECASE)
            if sl_match:
                data["stop_loss"] = self._parse_number(sl_match.group(1))

            # Targets
            target_match = re.search(r'(?:TP|TARGET|هدف)\s*(\d+)\s*[:]?\s*([\d.,]+[KMB]?)', line, re.IGNORECASE)
            if target_match:
                price = self._parse_number(target_match.group(2))
                if price:
                    close_pct = 0.0
                    close_match = re.search(r'CLOSE\s*(\d+)', line, re.IGNORECASE)
                    if close_match:
                        close_pct = float(close_match.group(1))
                    data["targets"].append({"price": price, "close_percent": close_pct})

        # --- Validation Phase ---
        if not all([data["asset"], data["side"], data["entry"], data["stop_loss"], data["targets"]]):
            missing = [k for k, v in data.items() if v is None or v == []]
            return ParsingResult(success=False, error_message=f"Missing required fields: {missing}")

        # Auto-assign close percentage if none were found
        if data["targets"] and all(t['close_percent'] == 0 for t in data["targets"]):
            data["targets"][-1]['close_percent'] = 100.0

        return ParsingResult(
            success=True,
            asset=data["asset"],
            side=data["side"],
            entry=data["entry"],
            stop_loss=data["stop_loss"],
            targets=data["targets"],
            confidence='high',
            parser='keyword_extractor'
        )