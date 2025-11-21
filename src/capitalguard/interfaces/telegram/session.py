# --- START OF NEW FILE: src/capitalguard/interfaces/telegram/session.py --- v1
import time
import logging
from typing import Dict, Any, Optional
from telegram.ext import ContextTypes

log = logging.getLogger(__name__)

# Keys used in user_data
KEY_LAST_ACTIVITY = "last_activity_management"
KEY_AWAITING_INPUT = "awaiting_management_input"
KEY_PENDING_CHANGE = "pending_management_change"
TIMEOUT_SECONDS = 3600  # 1 Hour Timeout

class SessionContext:
    """
    Encapsulates user session logic to ensure consistency and prevent timeouts.
    """
    def __init__(self, context: ContextTypes.DEFAULT_TYPE):
        self.context = context
        self.user_data = context.user_data

    def touch(self):
        """Updates the last activity timestamp to keep session alive."""
        self.user_data[KEY_LAST_ACTIVITY] = time.time()

    def is_expired(self) -> bool:
        """Checks if the session has expired."""
        last = self.user_data.get(KEY_LAST_ACTIVITY, 0)
        return (time.time() - last) > TIMEOUT_SECONDS

    def set_input_state(self, state_data: Dict[str, Any]):
        """Sets the user into a state waiting for text input."""
        self.touch()
        self.user_data[KEY_AWAITING_INPUT] = state_data

    def get_input_state(self) -> Optional[Dict[str, Any]]:
        """Retrieves the current input state."""
        return self.user_data.get(KEY_AWAITING_INPUT)

    def clear_input_state(self):
        """Clears the input state."""
        self.user_data.pop(KEY_AWAITING_INPUT, None)
        self.user_data.pop(KEY_PENDING_CHANGE, None)

    def clear_all(self):
        """Clears all management related session data."""
        self.clear_input_state()
        self.user_data.pop(KEY_LAST_ACTIVITY, None)
# --- END OF NEW FILE ---