# --- START OF FILE: src/capitalguard/interfaces/telegram/parsers.py ---
import re
from typing import Dict, Any, List, Optional

def parse_quick_command(text: str) -> Optional[Dict[str, Any]]:
    """
    Parses a full recommendation string from the /rec command.
    Format: /rec <ASSET> <SIDE> <ENTRY[,ENTRY2]> <SL> <TP1> [TP2...] [--notes "notes"] [--market market] [--risk X%]
    Returns a dictionary with the parsed data or None if parsing fails.
    """
    try:
        # Regex to capture main parts and optional flags
        pattern = re.compile(
            r'^\/rec\s+'
            r'([A-Z0-9\/]+)\s+'  # Asset (e.g., BTCUSDT)
            r'(LONG|SHORT)\s+'   # Side
            r'([\d.,]+)\s+'      # Entry price(s)
            r'([\d.]+)\s+'       # Stop Loss
            r'([\d.\s,kK]+?)'    # Targets (non-greedy)
            r'(?:\s*--notes\s*\"(.*?)\")?'  # Optional notes
            r'(?:\s*--market\s*(\w+))?'    # Optional market
            r'(?:\s*--risk\s*([\d.]+%?))?' # Optional risk
            r'\s*$', re.IGNORECASE
        )
        match = pattern.match(text)
        if not match:
            return None

        asset, side, entries_str, sl_str, targets_str, notes, market, risk = match.groups()

        # Process entries
        entries = [float(e.strip()) for e in entries_str.replace(',', ' ').split()]
        
        # Process targets (handle 'k' suffix)
        targets = []
        for t in targets_str.replace(',', ' ').split():
            t = t.strip().lower()
            if not t: continue
            if 'k' in t:
                targets.append(float(t.replace('k', '')) * 1000)
            else:
                targets.append(float(t))
        
        data = {
            "asset": asset.upper(),
            "side": side.upper(),
            "entry": entries[0] if len(entries) == 1 else entries, # Store as list if multiple
            "stop_loss": float(sl_str),
            "targets": targets,
            "notes": notes if notes else None,
            "market": market.capitalize() if market else "Futures",
            "risk": risk if risk else None,
        }
        return data
    except (ValueError, IndexError) as e:
        print(f"Error parsing quick command: {e}")
        return None

def parse_text_editor(text: str) -> Optional[Dict[str, Any]]:
    """
    Parses a multi-line recommendation string from the text editor mode.
    Uses keywords to identify fields.
    """
    data = {}
    lines = text.strip().split('\n')
    
    key_map = {
        'asset': ['asset', 'symbol', 'الأصل'],
        'side': ['side', 'type', 'direction', 'الاتجاه'],
        'entry': ['entry', 'entries', 'دخول'],
        'stop_loss': ['stop', 'sl', 'stoploss', 'إيقاف', 'وقف'],
        'targets': ['targets', 'tps', 'goals', 'أهداف'],
        'notes': ['notes', 'note', 'ملاحظات'],
        'market': ['market', 'سوق'],
        'risk': ['risk', 'مخاطرة']
    }
    
    for line in lines:
        try:
            key_str, value_str = line.split(':', 1)
            key_str = key_str.strip().lower()
            value_str = value_str.strip()
            
            for key, aliases in key_map.items():
                if key_str in aliases:
                    # Special handling for numeric fields
                    if key in ['entry', 'targets']:
                        # Handle 'k' and multiple values
                        values = []
                        for v in value_str.replace(',', ' ').split():
                            v = v.strip().lower()
                            if not v: continue
                            if 'k' in v:
                                values.append(float(v.replace('k', '')) * 1000)
                            else:
                                values.append(float(v))
                        # Store single entry as float, multiple as list
                        data[key] = values[0] if key == 'entry' and len(values) == 1 else values
                    elif key == 'stop_loss':
                        data[key] = float(value_str)
                    else:
                        data[key] = value_str
                    break
        except ValueError:
            # Line doesn't contain ':', skip
            continue
            
    # Basic validation to ensure core fields are present
    if not all(k in data for k in ['asset', 'side', 'entry', 'stop_loss', 'targets']):
        return None
        
    return data
# --- END OF FILE ---