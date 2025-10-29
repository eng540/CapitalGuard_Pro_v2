# src/capitalguard/application/services/image_parsing_service.py
"""
Placeholder for ImageParsingService.
Used to maintain import compatibility until the actual service is implemented.
"""

import logging
log = logging.getLogger(__name__)

class ImageParsingService:
    """Stub implementation for image parsing service."""
    def __init__(self):
        log.warning("ImageParsingService is a placeholder. No functionality available.")

    async def parse(self, image_path: str) -> dict:
        """Temporary async parser mock."""
        return {"status": "not_implemented", "path": image_path}