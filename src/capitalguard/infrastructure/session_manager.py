# ✅ THE FIX: Added centralized session management system to prevent session timeout issues and ensure proper state initialization
# ✅ THE FIX: Implemented proper session initialization for channel picker to fix channel selection board
# ✅ THE FIX: Added token shortening mechanism to comply with Telegram's 64-byte callback data limit

"""
src/capitalguard/infrastructure/session_manager.py (v1.0.0)
Centralized session management for CapitalGuard Telegram bot
Ensures consistent session state across all handlers and prevents timeout issues

Key features:
- Unified session initialization
- Automatic activity tracking
- Session validation
- Secure token handling
"""

import time
import hashlib
import logging
from typing import Dict, Any, Set, Optional

from telegram.ext import ContextTypes
from capitalguard.domain.entities import RecommendationStatus
from capitalguard.interfaces.telegram.ui_texts import LAST_ACTIVITY_KEY, SESSION_TIMEOUT
from capitalguard.interfaces.telegram.keyboards import CHANNEL_PICKER_KEY, DRAFT_KEY, SESSION_ID_KEY

log = logging.getLogger(__name__)

class SessionManager:
    """Centralized session management for CapitalGuard Telegram bot"""
    
    @staticmethod
    def init_session(context: ContextTypes.DEFAULT_TYPE):
        """Initialize all required session variables for a new session"""
        context.user_data[LAST_ACTIVITY_KEY] = time.time()
        context.user_data[CHANNEL_PICKER_KEY] = set()
        context.user_data[DRAFT_KEY] = {}
        context.user_data[SESSION_ID_KEY] = SessionManager._generate_session_id()
        
        log.debug(f"Session initialized for user {context._user_id}")
    
    @staticmethod
    def update_activity(context: ContextTypes.DEFAULT_TYPE):
        """Update activity timestamp and ensure session is properly initialized"""
        if LAST_ACTIVITY_KEY not in context.user_data:
            SessionManager.init_session(context)
        context.user_data[LAST_ACTIVITY_KEY] = time.time()
    
    @staticmethod
    def is_session_valid(context: ContextTypes.DEFAULT_TYPE) -> bool:
        """Check if session is still valid based on activity timestamp"""
        if LAST_ACTIVITY_KEY not in context.user_data:
            return False
            
        elapsed = time.time() - context.user_data[LAST_ACTIVITY_KEY]
        return elapsed <= SESSION_TIMEOUT
    
    @staticmethod
    def get_session_id(context: ContextTypes.DEFAULT_TYPE) -> str:
        """Get session ID, initializing session if needed"""
        if SESSION_ID_KEY not in context.user_data:
            SessionManager.init_session(context)
        return context.user_data[SESSION_ID_KEY]
    
    @staticmethod
    def clean_session(context: ContextTypes.DEFAULT_TYPE):
        """Clean up session state while preserving activity timestamp"""
        current_time = context.user_data.get(LAST_ACTIVITY_KEY, time.time())
        
        # Preserve activity timestamp but clear other session data
        context.user_data.clear()
        context.user_data[LAST_ACTIVITY_KEY] = current_time
        context.user_data[SESSION_ID_KEY] = SessionManager._generate_session_id()
        
        log.debug(f"Session cleaned for user {context._user_id}")
    
    @staticmethod
    def _generate_session_id() -> str:
        """Generate a unique session ID"""
        return hashlib.md5(f"{time.time()}{id(object())}".encode()).hexdigest()[:16]
    
    @staticmethod
    def get_channel_picker_state(context: ContextTypes.DEFAULT_TYPE) -> Set[int]:
        """Get channel picker state, initializing if needed"""
        if CHANNEL_PICKER_KEY not in context.user_data:
            context.user_data[CHANNEL_PICKER_KEY] = set()
        return context.user_data[CHANNEL_PICKER_KEY]
    
    @staticmethod
    def set_channel_picker_state(context: ContextTypes.DEFAULT_TYPE, selected_ids: Set[int]):
        """Set channel picker state with proper initialization"""
        SessionManager.update_activity(context)
        context.user_data[CHANNEL_PICKER_KEY] = selected_ids
    
    @staticmethod
    def get_draft(context: ContextTypes.DEFAULT_TYPE) -> Dict[str, Any]:
        """Get draft data, initializing if needed"""
        if DRAFT_KEY not in context.user_data:
            context.user_data[DRAFT_KEY] = {}
        return context.user_data[DRAFT_KEY]
    
    @staticmethod
    def set_draft(context: ContextTypes.DEFAULT_TYPE, draft_data: Dict[str, Any]):
        """Set draft data with proper initialization"""
        SessionManager.update_activity(context)
        context.user_data[DRAFT_KEY] = draft_data
    
    @staticmethod
    def _shorten_token(full_token: str, length: int = 8) -> str:
        """
        Create a shortened token using hashing to comply with Telegram's 64-byte callback data limit
        This is critical for channel picker and recommendation review flows
        """
        if len(full_token) <= length:
            return full_token
        return hashlib.md5(full_token.encode()).hexdigest()[:length]
    
    @staticmethod
    def get_safe_token(context: ContextTypes.DEFAULT_TYPE, base_token: str) -> str:
        """
        Get a safe token for callback data that won't exceed Telegram's limits
        Uses session ID to ensure uniqueness while keeping length minimal
        """
        session_id = SessionManager.get_session_id(context)
        combined = f"{session_id}:{base_token}"
        return SessionManager._shorten_token(combined)
    
    @staticmethod
    def validate_token(context: ContextTypes.DEFAULT_TYPE, callback_token: str, expected_base: str) -> bool:
        """
        Validate that a callback token matches the expected value
        Handles both full and shortened token formats for backward compatibility
        """
        session_id = SessionManager.get_session_id(context)
        expected_combined = f"{session_id}:{expected_base}"
        
        # Check against shortened version
        expected_short = SessionManager._shorten_token(expected_combined)
        if callback_token == expected_short:
            return True
            
        # Check against full version (for backward compatibility)
        return callback_token == expected_base