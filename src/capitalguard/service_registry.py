# src/capitalguard/service_registry.py (SIMPLIFIED - NO LONGER A GLOBAL REGISTRY)

import logging
from typing import Dict, Any, Optional, TypeVar, Type

log = logging.getLogger(__name__)
T = TypeVar('T')

# This file no longer manages a global state. It only provides type hints and structure if needed elsewhere.
# The primary functions register_global_services and get_global_service are now REMOVED.