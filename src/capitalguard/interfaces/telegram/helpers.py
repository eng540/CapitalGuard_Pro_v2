# --- START OF FINAL, ROBUST FILE USING SERVICE REGISTRY (Version 9.3.0) ---
# src/capitalguard/interfaces/telegram/helpers.py

from typing import TypeVar
from telegram.ext import ContextTypes

# ✅ Import the new global service getter
from capitalguard.service_registry import get_global_service
from capitalguard.application.services.trade_service import TradeService
from capitalguard.application.services.price_service import PriceService
from capitalguard.application.services.analytics_service import AnalyticsService
from capitalguard.application.services.market_data_service import MarketDataService

T = TypeVar('T')

def get_service(context: ContextTypes.DEFAULT_TYPE, service_name: str, service_type: type[T]) -> T:
    """
    A robust service getter that retrieves a service from the global registry.
    This completely replaces the old, unreliable context-based method.
    """
    service = get_global_service(service_name, service_type)
    
    if service is None:
        # This error should now be virtually impossible to hit if startup was successful.
        raise RuntimeError(
            f"Service '{service_name}' of type '{service_type.__name__}' could not be found. "
            "This indicates a critical failure during application bootstrap."
        )
        
    return service

# --- END OF FINAL, ROBUST FILE USING SERVICE REGISTRY (Version 9.3.0) ---