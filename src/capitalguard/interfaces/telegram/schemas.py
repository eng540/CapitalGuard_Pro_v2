# --- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/schemas.py ---
from enum import Enum
from typing import Optional, List
from dataclasses import dataclass

class ManagementNamespace(Enum):
    MGMT = "mgmt"
    POSITION = "pos"
    RECOMMENDATION = "rec"
    EXIT_STRATEGY = "exit"

class ManagementAction(Enum):
    HUB = "hub"
    SHOW_LIST = "show_list"
    SHOW = "sh"
    CLOSE = "cl"
    ACTIVATE_TRADE = "activate_trade"
    
    # Menus
    EDIT_MENU = "edit_menu"
    PARTIAL_CLOSE_MENU = "partial_close_menu"
    SHOW_MENU = "show_menu"  # ✅ ADDED THIS MISSING ACTION
    
    # Edit Actions
    EDIT_ENTRY = "edit_entry"
    EDIT_SL = "edit_sl"
    EDIT_TP = "edit_tp"
    EDIT_NOTES = "edit_notes"
    
    # Close Actions
    CLOSE_MANUAL = "close_manual"
    CLOSE_MARKET = "close_market"
    PARTIAL_CLOSE_CUSTOM = "partial_close_custom"
    PARTIAL = "pt"
    
    # General
    CANCEL_INPUT = "cancel_input"
    MOVE_TO_BE = "move_to_be"
    CANCEL_STRATEGY = "cancel"
    CONFIRM_CHANGE = "confirm_change"

@dataclass
class TypedCallback:
    namespace: str
    action: str
    params: List[str]

    @classmethod
    def parse(cls, data: str) -> 'TypedCallback':
        parts = data.split(':')
        if len(parts) < 2:
            return cls("unknown", "unknown", [])
        return cls(
            namespace=parts[0],
            action=parts[1],
            params=parts[2:]
        )

    def get_int(self, index: int) -> Optional[int]:
        try:
            return int(self.params[index])
        except (IndexError, ValueError):
            return None

    def get_str(self, index: int) -> Optional[str]:
        try:
            return self.params[index]
        except IndexError:
            return None
# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/interfaces/telegram/schemas.py ---