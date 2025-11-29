#--- START OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/image_parsing_service.py ---
# src/capitalguard/application/services/image_parsing_service.py (v1.1 - Timeout Increase)
"""
This service acts as a client for the AI microservice's image parsing endpoint.
✅ THE FIX: Increased HTTP timeout to 60 seconds to handle Railway 'cold starts'.
"""

import logging
import httpx
from typing import Dict, Any, Optional

from capitalguard.config import settings

log = logging.getLogger(__name__)

# Constants retrieved from settings
AI_SERVICE_URL = settings.AI_SERVICE_URL # e.g., http://ai-service:8001/ai/parse
BOT_TOKEN = settings.TELEGRAM_BOT_TOKEN
TELEGRAM_API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

class ImageParsingService:
    """
    Service responsible for orchestrating the parsing of image-based signals.
    """

    def __init__(self):
        if not AI_SERVICE_URL or not BOT_TOKEN:
            log.critical(
                "ImageParsingService FATAL: AI_SERVICE_URL or TELEGRAM_BOT_TOKEN is not set."
            )
            self.base_ai_url = None
        else:
            # Construct the URL for the new image parsing endpoint
            self.base_ai_url = AI_SERVICE_URL.rsplit('/', 1)[0]
            self.parse_image_url = f"{self.base_ai_url}/parse_image"
        
        # ✅ FIX: Increased timeout to 60s for cold starts
        self.http_client = httpx.AsyncClient(timeout=60.0)

    async def _get_telegram_file_url(self, file_id: str) -> Optional[str]:
        """Uses the Telegram Bot API to get a temporary download URL."""
        if not BOT_TOKEN: return None
        get_file_url = f"{TELEGRAM_API_BASE}/getFile"
        try:
            response = await self.http_client.post(get_file_url, params={'file_id': file_id})
            response.raise_for_status()
            data = response.json()
            if data.get("ok") and data.get("result", {}).get("file_path"):
                file_path = data["result"]["file_path"]
                return f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
            else:
                log.error(f"Failed to get file path: {data.get('description')}")
                return None
        except httpx.RequestError as e:
            log.error(f"HTTP error getting file from Telegram: {e}")
            return None

    async def parse_image_from_file_id(self, user_db_id: int, file_id: str) -> Dict[str, Any]:
        """Main method: Proxies request to AI service."""
        if not self.parse_image_url:
            return {"status": "error", "error": "Image parsing service is not configured."}

        file_download_url = await self._get_telegram_file_url(file_id)
        if not file_download_url:
            return {"status": "error", "error": "Failed to retrieve file from Telegram."}

        log.debug(f"Sending image to AI service (User: {user_db_id})")
        try:
            response = await self.http_client.post(
                self.parse_image_url,
                json={"user_id": user_db_id, "image_url": file_download_url}
            )
            
            if response.status_code >= 400:
                log.error(f"AI Service returned HTTP {response.status_code}")
                error_detail = response.json().get("detail", "Analysis failed.")
                return {"status": "error", "error": f"Error: {error_detail}"}

            return response.json()

        except httpx.RequestError as e:
            log.error(f"HTTP request to AI Service failed: {e}")
            return {"status": "error", "error": "Image analysis service is unreachable."}
        except Exception as e:
            log.error(f"Critical error in image proxy: {e}", exc_info=True)
            return {"status": "error", "error": "An unexpected error occurred."}

# --- END OF FULL, FINAL, AND CONFIRMED READY-TO-USE FILE: src/capitalguard/application/services/image_parsing_service.py ---