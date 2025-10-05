# src/capitalguard/application/services/image_parsing_service.py

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
    خدمة متخصصة في استخراج بيانات التداول من:
    1. الصور (باستخدام OCR)
    2. النص العادي (باستخدام parsers مخصصة)
    3. النص المعقد (باستخدام LLM APIs)
    """
    
    def __init__(self):
        self._initialized = False
        
    async def initialize(self):
        """تهيئة المحركات بشكل غير متزامن"""
        try:
            # TODO: تهيئة easyocr عند الحاجة
            # import easyocr
            # self.ocr_reader = easyocr.Reader(['en', 'ar'])
            self._initialized = True
            log.info("✅ ImageParsingService initialized successfully")
        except Exception as e:
            log.error(f"❌ Failed to initialize ImageParsingService: {e}")
            self._initialized = False
            
    async def extract_trade_data(self, content: str, is_image: bool = False) -> Optional[Dict[str, Any]]:
        """
        الاستخراج الرئيسي لبيانات التداول من المحتوى
        
        Args:
            content: إما نص أو مسار الصورة
            is_image: هل المحتوى صورة؟
            
        Returns:
            قاموس منظم ببيانات التداول أو None إذا فشل الاستخراج
        """
        if not self._initialized:
            await self.initialize()
            
        try:
            if is_image:
                raw_text = await self._extract_text_from_image(content)
            else:
                raw_text = content
                
            if not raw_text or len(raw_text.strip()) < 10:
                return None
                
            # محاولة التحليل بالطرق التقليدية أولاً
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
            
    async def _extract_text_from_image(self, image_path: str) -> str:
        """استخراج النص من الصورة باستخدام OCR"""
        try:
            log.info(f"📸 Extracting text from image: {image_path}")
            
            # TODO: تفعيل OCR الحقيقي
            # if hasattr(self, 'ocr_reader'):
            #     results = self.ocr_reader.readtext(image_path)
            #     text = ' '.join([result[1] for result in results])
            #     return text
            
            # نموذج نصي للاختبار
            sample_texts = [
                "BTCUSDT LONG Entry: 50000 SL: 49000 TP1: 52000@50 TP2: 54000@50",
                "ETHUSDT SHORT 3500 3400 3300@50 3200@50",
                "ADAUSDT LONG Entry 0.45 SL 0.42 TP 0.50@30 0.55@70"
            ]
            
            await asyncio.sleep(0.1)  # محاكاة معالجة غير متزامنة
            return sample_texts[0]  # إرجاع نموذج للاختبار
            
        except Exception as e:
            log.error(f"❌ OCR extraction failed: {e}")
            return ""
        
    async def _parse_with_regex(self, text: str) -> ParsingResult:
        """محاولة تحليل النص باستخدام regex patterns"""
        
        # تنظيف النص
        cleaned_text = self._clean_text(text)
        
        if not cleaned_text:
            return ParsingResult(success=False, error_message="نص فارغ بعد التنظيف")
        
        # patterns شائعة في قنوات التداول
        patterns = [
            self._parse_standard_format,
            self._parse_compact_format,
            self._parse_simple_format,
            self._parse_arabic_format
        ]
        
        for parser in patterns:
            result = parser(cleaned_text)
            if result.success:
                log.info(f"✅ Successfully parsed with {result.parser}")
                return result
                
        return ParsingResult(success=False, error_message="لم يتم التعرف على التنسيق")
        
    def _clean_text(self, text: str) -> str:
        """تنظيف النص وتوحيد التنسيق"""
        if not text:
            return ""
            
        # إزالة emojis ورموز خاصة ولكن الاحتفاظ بالأحرف العربية والإنجليزية
        text = re.sub(r'[^\w\s\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF@:.,\d\-+%$]', ' ', text)
        # توحيد المسافات
        text = re.sub(r'\s+', ' ', text)
        # تحويل إلى uppercase للتبسيط
        text = text.upper()
        return text.strip()
        
    def _parse_standard_format(self, text: str) -> ParsingResult:
        """تحليل التنسيق القياسي: SYMBOL SIDE Entry: X SL: Y TP: Z"""
        patterns = [
            r'(\w+)\s+(LONG|SHORT)\s+ENTRY:\s*([\d.,]+[KMB]?)\s+SL:\s*([\d.,]+[KMB]?)\s+(.+)',
            r'(\w+)\s+(LONG|SHORT)\s+ENTRY\s*([\d.,]+[KMB]?)\s+SL\s*([\d.,]+[KMB]?)\s+(.+)',
            r'(\w+)/(LONG|SHORT)\s+ENTRY:\s*([\d.,]+[KMB]?)\s+SL:\s*([\d.,]+[KMB]?)\s+(.+)'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                symbol, side, entry, sl, targets_part = match.groups()
                targets = self._parse_targets(targets_part)
                
                if targets:
                    return ParsingResult(
                        success=True,
                        asset=symbol.upper(),
                        side=side.upper(),
                        entry=self._parse_number(entry),
                        stop_loss=self._parse_number(sl),
                        targets=targets,
                        confidence='high',
                        parser='regex_standard'
                    )
        return ParsingResult(success=False)
        
    def _parse_compact_format(self, text: str) -> ParsingResult:
        """تحليل التنسيق المختصر: SYMBOL SIDE ENTRY SL TP1 TP2"""
        pattern = r'(\w+)\s+(LONG|SHORT)\s+([\d.,]+[KMB]?)\s+([\d.,]+[KMB]?)\s+([\d.,@% ]+)'
        match = re.search(pattern, text)
        
        if match:
            symbol, side, entry, sl, targets_part = match.groups()
            targets = self._parse_targets(targets_part)
            
            if targets:
                return ParsingResult(
                    success=True,
                    asset=symbol.upper(),
                    side=side.upper(),
                    entry=self._parse_number(entry),
                    stop_loss=self._parse_number(sl),
                    targets=targets,
                    confidence='high',
                    parser='regex_compact'
                )
        return ParsingResult(success=False)
        
    def _parse_simple_format(self, text: str) -> ParsingResult:
        """تحليل التنسيق البسيط: SYMBOL DIRECTION PRICES"""
        # نمط: BTCUSDT LONG 50000 49000 52000 54000
        pattern = r'(\w+)\s+(LONG|SHORT)\s+([\d.,]+[KMB]?)\s+([\d.,]+[KMB]?)\s+([\d., ]+)'
        match = re.search(pattern, text)
        
        if match:
            symbol, side, first_num, second_num, rest = match.groups()
            
            # نحدد أي رقم هو ENTRY وأي هو SL بناءً على الاتجاه
            entry = self._parse_number(first_num)
            sl = self._parse_number(second_num)
            
            # التحقق من المنطق
            if side.upper() == 'LONG' and entry <= sl:
                entry, sl = sl, entry  # مبادلة إذا كانت القيم معكوسة
            elif side.upper() == 'SHORT' and entry >= sl:
                entry, sl = sl, entry  # مبادلة إذا كانت القيم معكوسة
                
            targets = self._parse_targets(rest)
            
            if targets:
                return ParsingResult(
                    success=True,
                    asset=symbol.upper(),
                    side=side.upper(),
                    entry=entry,
                    stop_loss=sl,
                    targets=targets,
                    confidence='medium',
                    parser='regex_simple'
                )
        return ParsingResult(success=False)
        
    def _parse_arabic_format(self, text: str) -> ParsingResult:
        """تحليل التنسيق العربي"""
        # أنماط عربية شائعة
        arabic_patterns = [
            r'(\w+)\s+(لونج|لونغ|شرت|شورت)\s+دخول:\s*([\d.,]+)\s+وقف:\s*([\d.,]+)\s+(.+)',
            r'(\w+)\s+(شراء|بيع)\s+سعر الدخول:\s*([\d.,]+)\s+وقف الخسارة:\s*([\d.,]+)\s+(.+)'
        ]
        
        for pattern in arabic_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                symbol, side_ar, entry, sl, targets_part = match.groups()
                
                # تحويل الاتجاه من العربية إلى الإنجليزية
                side = "LONG" if side_ar.lower() in ['لونج', 'لونغ', 'شراء'] else "SHORT"
                targets = self._parse_targets(targets_part)
                
                if targets:
                    return ParsingResult(
                        success=True,
                        asset=symbol.upper(),
                        side=side,
                        entry=self._parse_number(entry),
                        stop_loss=self._parse_number(sl),
                        targets=targets,
                        confidence='medium',
                        parser='regex_arabic'
                    )
        return ParsingResult(success=False)
        
    def _parse_targets(self, targets_text: str) -> List[Dict[str, float]]:
        """تحليل نص الأهداف إلى قائمة منظمة"""
        targets = []
        if not targets_text:
            return targets
            
        # أنماط مختلفة للأهداف
        patterns = [
            r'TP\d*[:]?\s*([\d.,]+[KMB]?)(?:[@]?(\d+))?',  # TP1: 50000@50
            r'([\d.,]+[KMB]?)(?:[@](\d+))?',  # 50000@50 أو 50000
            r'هدف\s*\d*[:]?\s*([\d.,]+)(?:[@]?(\d+))?',  # هدف 1: 50000@50
        ]
        
        for pattern in patterns:
            for match in re.finditer(pattern, targets_text, re.IGNORECASE):
                price_str, percent_str = match.groups()
                if price_str:
                    price = self._parse_number(price_str)
                    percent = float(percent_str) if percent_str else 0.0
                    
                    targets.append({
                        'price': price,
                        'close_percent': percent
                    })
            
            if targets:  # إذا وجدنا أهدافاً، نتوقف
                break
        
        # إذا لم تكن هناك نسب، نقوم بتوزيعها تلقائياً
        if targets and all(t['close_percent'] == 0 for t in targets):
            equal_percent = 100.0 / len(targets)
            for target in targets:
                target['close_percent'] = equal_percent
                
        return targets
        
    def _parse_number(self, num_str: str) -> float:
        """تحويل السلسلة النصية إلى رقم مع دعم K,M,B"""
        if not num_str:
            return 0.0
            
        num_str = str(num_str).upper().replace(',', '').replace(' ', '')
        
        multipliers = {'K': 1000, 'M': 1000000, 'B': 1000000000}
        if num_str and num_str[-1] in multipliers:
            return float(num_str[:-1]) * multipliers[num_str[-1]]
            
        try:
            return float(num_str)
        except ValueError:
            log.warning(f"❌ Cannot parse number: {num_str}")
            return 0.0