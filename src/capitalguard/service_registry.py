# --- START OF FINAL, PRODUCTION-READY GLOBAL SERVICE REGISTRY (Version 1.0.0) ---
# src/capitalguard/service_registry.py

import logging
import threading
from typing import Dict, Any, Optional, TypeVar, Type

log = logging.getLogger(__name__)
T = TypeVar('T')

class ServiceRegistry:
    """A thread-safe, global service registry to ensure reliable service access."""
    
    def __init__(self):
        self._services: Dict[str, Any] = {}
        self._lock = threading.RLock()
        
    def register_services(self, services: Dict[str, Any]) -> None:
        """Registers a dictionary of services."""
        with self._lock:
            self._services.update(services)
            log.info(f"🚀 Global service registry populated with {len(services)} services: {list(services.keys())}")
    
    def get_service(self, name: str) -> Optional[Any]:
        """Retrieves a service by name from the global registry."""
        with self._lock:
            service = self._services.get(name)
            if not service:
                log.critical(f"❌ Service '{name}' not found in global registry! Available: {list(self._services.keys())}")
            return service
    
    def get_typed_service(self, name: str, service_type: Type[T]) -> Optional[T]:
        """Retrieves a service by name and validates its type."""
        service = self.get_service(name)
        if isinstance(service, service_type):
            return service
        return None

# The single, global instance of the registry.
_global_registry = ServiceRegistry()

def register_global_services(services: Dict[str, Any]) -> None:
    """Helper function to populate the global service registry."""
    _global_registry.register_services(services)

def get_global_service(name: str, service_type: Type[T]) -> Optional[T]:
    """Helper function to retrieve a typed service from the global registry."""
    return _global_registry.get_typed_service(name, service_type)

# --- END OF FINAL, PRODUCTION-READY GLOBAL SERVICE REGISTRY (Version 1.0.0) ---