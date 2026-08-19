#!/usr/bin/env python3
"""Run a read-only historical Telegram dry-run from an owner workstation.

The first run may prompt for phone/code/2FA through Telethon. The resulting
session file is sensitive and must stay outside Git and ordinary logs.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict

from capitalguard.application.services.authorized_history_connector import AuthorizedHistoryConnector, ReaderAccountPolicy
from capitalguard.application.services.historical_import_service import HistoricalImportService
from capitalguard.application.services.telethon_history_backend import TelethonHistoryBackend


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


async def run(channel_id: int, max_pages: int, page_size: int) -> None:
    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise SystemExit(
            "Telethon is optional. Install requirements-history-connector.txt before running this script."
        ) from exc

    api_id = int(_required_env("TELEGRAM_HISTORY_API_ID"))
    api_hash = _required_env("TELEGRAM_HISTORY_API_HASH")
    session_path = _required_env("TELEGRAM_HISTORY_SESSION_PATH")
    account_alias = _required_env("HISTORY_READER_ACCOUNT_ALIAS")

    client = TelegramClient(session_path, api_id, api_hash, receive_updates=False)
    try:
        await client.start()
        connector = AuthorizedHistoryConnector(
            policy=ReaderAccountPolicy(
                account_alias=account_alias,
                session_secret_ref=f"local-file:{session_path}",
                allowed_channel_ids=frozenset({channel_id}),
            ),
            backend=TelethonHistoryBackend(client),
            page_size=page_size,
        )
        report, checkpoint, _payload = await HistoricalImportService().dry_run_authorized_page_async(
            connector,
            channel_id=channel_id,
            max_pages=max_pages,
        )
        output = {
            "report": {
                "source_kind": report.source_kind,
                "total_records": report.total_records,
                "accepted_records": report.accepted_records,
                "rejected_records": report.rejected_records,
                "manifest_hash": report.manifest_hash,
                "issues": [asdict(issue) for issue in report.issues],
            },
            "checkpoint": asdict(checkpoint),
            "note": "Dry-run only; no evidence or live recommendation was persisted.",
        }
        print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    finally:
        await client.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Telegram historical dry-run")
    parser.add_argument("--channel-id", type=int, required=True)
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument("--page-size", type=int, default=100)
    args = parser.parse_args()
    asyncio.run(run(args.channel_id, args.max_pages, args.page_size))


if __name__ == "__main__":
    main()
