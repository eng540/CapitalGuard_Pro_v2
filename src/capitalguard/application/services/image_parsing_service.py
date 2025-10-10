# src/capitalguard/application/services/image_parsing_service.py (v3.1 - COMPLETE, FINAL & PRODUCTION-READY)
"""
ImageParsingService - A smarter, more flexible parsing engine for trade data.
This is a complete, final, and production-ready file.
"""
import logging
import re
from typing import Dict, Any, Optional, List, Tuple

from dataclasses import dataclass

log = logging.getLogger(__name__)

@dataclass
class ParsingResult:
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
        if self.targets is None: self.targets = []

class ImageParsingService:
    def __init__(self):
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

    async def extract_trade_data(self, content: str, is_image: bool = False) -> Optional[Dict[str, Any]]:
        if not content or len(content.strip()) < 10: return None
        try:
            result = self._parse_flexible_format(content)
            if result.success:
                return {'asset': result.asset, 'side': result.side, 'entry': result.entry, 'stop_loss': result.stop_loss, 'targets': result.targets, 'confidence': result.confidence, 'parser': result.parser}
            log.warning(f"Parsing failed. Reason: {result.error_message}")
            return None
        except Exception as e:
            log.error(f"Error extracting trade data: {e}", exc_info=True)
            return None

    def _clean_text(self, text: str) -> str:
        if not text: return ""
        text = re.sub(r'[^\w\s\u0600-\u06FF@:.,\d\-+%$#/|]', ' ', text, flags=re.UNICODE)
        text = re.sub(r'(\r\n|\r|\n){2,}', '\n', text)
        return text.strip().upper()

    def _parse_number(self, num_str: str) -> Optional[float]:
        if not num_str: return None
        num_str = str(num_str).strip().upper().replace(',', '')
        multipliers = {'K': 1000, 'M': 1000000}
        try:
            if num_str and num_str[-1] in multipliers: return float(num_str[:-1]) * multipliers[num_str[-1]]
            return float(num_str)
        except (ValueError, TypeError): return None

    def _find_asset_and_side(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        asset, side = None, None
        for s, keywords in self._side_maps.items():
            if any(re.search(r'\b' + keyword + r'\b', text, re.IGNORECASE) for keyword in keywords):
                side = s
                break
        
        hashtag_match = re.search(r'#([A-Z0-9]{4,12})', text)
        if hashtag_match and hashtag_match.group(1) not in self.ASSET_BLACKLIST:
            asset = hashtag_match.group(1)
        else:
            usdt_match = re.search(r'\b([A-Z]{3,8}(?:USDT|PERP))\b', text)
            if usdt_match and usdt_match.group(1) not in self.ASSET_BLACKLIST:
                asset = usdt_match.group(1)
        return asset, side

    def _parse_flexible_format(self, text: str) -> ParsingResult:
        cleaned_text = self._clean_text(text)
        
        asset, side = self._find_asset_and_side(cleaned_text)
        data = {"entry": None, "stop_loss": None, "targets": []}

        patterns = {
            'entry': re.compile(r'(?:' + '|'.join(self._key_maps['entry']) + r')\s*[:]?\s*([\d.,]+[KMB]?)', re.IGNORECASE),
            'stop_loss': re.compile(r'(?:' + '|'.join(self._key_maps['stop_loss']) + r')\s*[:]?\s*([\d.,]+[KMB]?)', re.IGNORECASE),
            'targets': re.compile(r'(?:' + '|'.join(self._key_maps['targets']) + r')\s*\d*\s*[:]?\s*((?:[\d.,]+[KMB]?\s*(?:@\d+)?\s*)+)', re.IGNORECASE)
        }

        full_text_for_targets = cleaned_text.replace('\n', ' ')
        
        for key, pattern in patterns.items():
            for line in cleaned_text.split('\n'):
                if not line.strip(): continue
                match = pattern.search(line)
                if not match: continue
                
                value_str = match.group(1)
                if key == 'entry' and data['entry'] is None: data['entry'] = self._parse_number(value_str)
                elif key == 'stop_loss' and data['stop_loss'] is None: data['stop_loss'] = self._parse_number(value_str)
            
            if key == 'targets' and not data['targets']:
                 match = pattern.search(full_text_for_targets)
                 if match:
                    value_str = match.group(1)
                    target_tokens = re.findall(r'([\d.,]+[KMB]?)(?:@(\d+))?', value_str)
                    for price_str, close_pct_str in target_tokens:
                        if price := self._parse_number(price_str):
                            data['targets'].append({"price": price, "close_percent": float(close_pct_str) if close_pct_str else 0.0})

        if not all([asset, side, data["entry"], data["stop_loss"], data["targets"]]):
            missing = [k for k, v in {**{"asset": asset, "side": side}, **data}.items() if v is None or v == []]
            return ParsingResult(success=False, error_message=f"Missing required fields: {missing}")

        if data["targets"] and all(t['close_percent'] == 0 for t in data["targets"]):
            data["targets"][-1]['close_percent'] = 100.0

        return ParsingResult(
            success=True, asset=asset, side=side, entry=data["entry"],
            stop_loss=data["stop_loss"], targets=data["targets"],
            confidence='high', parser='flexible_keyword_v3.1'
        )