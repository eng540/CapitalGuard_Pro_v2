"""Controlled Telegram export adapter.

This adapter intentionally accepts an export payload/file only. It does not
connect to Telegram and cannot fetch arbitrary channel history.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class TelegramExportAdapter:
    source_kind = "TELEGRAM_EXPORT"

    @staticmethod
    def _flatten_text(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "".join(parts)
        return ""

    def load_payload(self, path: str | Path) -> dict[str, Any]:
        with Path(path).open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError("Telegram export root must be a JSON object")
        return payload

    def to_manifest(
        self,
        payload: dict[str, Any],
        *,
        telegram_channel_id: int | None = None,
        source_uri: str | None = None,
    ) -> dict[str, Any]:
        messages = payload.get("messages")
        if not isinstance(messages, list):
            raise ValueError("Telegram export payload must contain a messages list")
        records: list[dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict) or message.get("type") not in (None, "message"):
                continue
            text = self._flatten_text(message.get("text"))
            timestamp = message.get("date")
            edited_date = message.get("edited_date")
            message_id = message.get("id")
            if not text.strip() or not isinstance(timestamp, str) or not isinstance(message_id, int):
                continue
            records.append(
                {
                    "telegram_channel_id": telegram_channel_id,
                    "telegram_message_id": message_id,
                    "message_revision": 1 if isinstance(edited_date, str) and edited_date else 0,
                    "message_timestamp": timestamp,
                    "raw_text": text,
                    "source_uri": source_uri,
                    "metadata": {
                        "telegram_export_type": message.get("type"),
                        "from": message.get("from"),
                        "reply_to_message_id": message.get("reply_to_message_id"),
                        "edited_date": edited_date,
                    },
                }
            )
        return {"source_kind": self.source_kind, "records": records}
