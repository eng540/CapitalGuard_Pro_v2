# src/capitalguard/application/services/image_parsing_service.py (v2.0 - Robust Parser)
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
                
            result = await self._parse_with_regex(raw_text)
            
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
            return None
        except Exception as e:
            log.error(f"❌ Error extracting trade data: {e}")
            return None
        
    async def _parse_with_regex(self, text: str) -> ParsingResult:
        """محاولة تحليل النص باستخدام regex patterns متعددة"""
        cleaned_text = self._clean_text(text)
        
        if not cleaned_text:
            return ParsingResult(success=False, error_message="Empty text after cleaning")
        
        # سلسلة من دوال التحليل، من الأكثر تحديدًا إلى الأكثر عمومية
        parsers = [
            self._parse_standard_format,
            self._parse_compact_format,
            self._parse_simple_format,
            self._parse_arabic_format
        ]
        
        for parser_func in parsers:
            try:
                result = parser_func(cleaned_text)
                if result.success:
                    log.info(f"✅ Successfully parsed with {result.parser}")
                    return result
            except Exception as e:
                log.warning(f"Parser {parser_func.__name__} failed: {e}")
                
        return ParsingResult(success=False, error_message="No suitable format recognized")
        
    def _clean_text(self, text: str) -> str:
        """تنظيف النص وتوحيد التنسيق"""
        if not text:
            return ""
        text = re.sub(r'[^\w\s\u0600-\u06FF@:.,\d\-+%$]', ' ', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip().upper()
        
    def _parse_number(self, num_str: str) -> float:
        if not num_str: return 0.0
        num_str = str(num_str).upper().replace(',', '').replace(' ', '')
        multipliers = {'K': 1000, 'M': 1000000, 'B': 1000000000}
        if num_str and num_str[-1] in multipliers:
            return float(num_str[:-1]) * multipliers[num_str[-1]]
        try:
            return float(num_str)
        except ValueError:
            log.warning(f"❌ Cannot parse number: {num_str}")
            return 0.0

    def _parse_targets(self, targets_text: str) -> List[Dict[str, float]]:
        targets = []
        if not targets_text: return targets
        
        # نمط مرن لالتقاط الأهداف
        pattern = r'(?:TP\d*[:]?|هدف\s*\d*[:]?\s*)?([\d.,]+[KMB]?)(?:[@]?(\d+))?'
        
        for match in re.finditer(pattern, targets_text, re.IGNORECASE):
            price_str, percent_str = match.groups()
            if price_str:
                price = self._parse_number(price_str)
                if price > 0:
                    percent = float(percent_str) if percent_str else 0.0
                    targets.append({'price': price, 'close_percent': percent})
        
        if targets and all(t['close_percent'] == 0 for t in targets):
            targets[-1]['close_percent'] = 100.0
                
        return targets

    def _parse_standard_format(self, text: str) -> ParsingResult:
        pattern = r'(\w+)\s+(LONG|SHORT)\s+ENTRY[:]?\s*([\d.,]+[KMB]?)\s+SL[:]?\s*([\d.,]+[KMB]?)\s+(.+)'
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            symbol, side, entry, sl, targets_part = match.groups()
            targets = self._parse_targets(targets_part)
            if targets:
                return ParsingResult(success=True, asset=symbol, side=side, entry=self._parse_number(entry), stop_loss=self._parse_number(sl), targets=targets, confidence='high', parser='regex_standard')
        return ParsingResult(success=False)
        
    def _parse_compact_format(self, text: str) -> ParsingResult:
        pattern = r'(\w+)\s+(LONG|SHORT)\s+([\d.,]+[KMB]?)\s+([\d.,]+[KMB]?)\s+([\d.,@% ]+)'
        match = re.search(pattern, text)
        if match:
            symbol, side, entry, sl, targets_part = match.groups()
            targets = self._parse_targets(targets_part)
            if targets:
                return ParsingResult(success=True, asset=symbol, side=side, entry=self._parse_number(entry), stop_loss=self._parse_number(sl), targets=targets, confidence='high', parser='regex_compact')
        return ParsingResult(success=False)
        
    def _parse_simple_format(self, text: str) -> ParsingResult:
        pattern = r'(\w+)\s+(LONG|SHORT)\s+([\d.,]+[KMB]?)\s+([\d.,]+[KMB]?)\s+([\d., ]+)'
        match = re.search(pattern, text)
        if match:
            symbol, side, first_num, second_num, rest = match.groups()
            entry = self._parse_number(first_num)
            sl = self._parse_number(second_num)
            if side == 'LONG' and entry <= sl: entry, sl = sl, entry
            elif side == 'SHORT' and entry >= sl: entry, sl = sl, entry
            targets = self._parse_targets(rest)
            if targets:
                return ParsingResult(success=True, asset=symbol, side=side, entry=entry, stop_loss=sl, targets=targets, confidence='medium', parser='regex_simple')
        return ParsingResult(success=False)
        
    def _parse_arabic_format(self, text: str) -> ParsingResult:
        patterns = [
            r'(\w+)\s+(لونج|لونغ|شراء)\s+دخول[:]?\s*([\d.,]+)\s+وقف[:]?\s*([\d.,]+)\s+(.+)',
            r'(\w+)\s+(شرت|شورت|بيع)\s+دخول[:]?\s*([\d.,]+)\s+وقف[:]?\s*([\d.,]+)\s+(.+)'
        ]
        for i, pattern in enumerate(patterns):
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                side = "LONG" if i == 0 else "SHORT"
                symbol, _, entry, sl, targets_part = match.groups()
                targets = self._parse_targets(targets_part)
                if targets:
                    return ParsingResult(success=True, asset=symbol, side=side, entry=self._parse_number(entry), stop_loss=self._parse_number(sl), targets=targets, confidence='medium', parser='regex_arabic')
        return ParsingResult(success=False)