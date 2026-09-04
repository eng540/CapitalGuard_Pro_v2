from datetime import datetime, timedelta, timezone
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from capitalguard.infrastructure.market.binance_client import BinanceClient

UTC = timezone.utc


def _archive_bytes(rows):
    payload = "agg_trade_id,price,qty,first_trade_id,last_trade_id,transact_time,is_buyer_maker,is_best_match\n"
    payload += "\n".join(",".join(map(str, row)) for row in rows)
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("BTCUSDT-aggTrades-2025-12-03.csv", payload)
    return buffer.getvalue()


class FakeResponse:
    status_code = 404
    content = b""
    headers = {}

    def raise_for_status(self):
        return None


def test_archive_fallback_reads_only_requested_minute(monkeypatch):
    start = datetime(2025, 12, 3, 18, 12, tzinfo=UTC)
    end = start + timedelta(minutes=1)
    rows = [
        (1, "100", "1", 1, 1, int((start - timedelta(seconds=1)).timestamp() * 1000), False, True),
        (2, "93400", "1", 2, 2, int((start + timedelta(seconds=10)).timestamp() * 1000), False, True),
        (3, "93500", "1", 3, 3, int((end + timedelta(seconds=1)).timestamp() * 1000), False, True),
    ]

    def fake_get(url, **kwargs):
        return FakeArchiveResponse(_archive_bytes(rows))

    class FakeArchiveResponse(FakeResponse):
        def __init__(self, content):
            self.content = content
            self.status_code = 200

    monkeypatch.setattr("capitalguard.infrastructure.market.binance_client.requests.get", fake_get)
    client = BinanceClient()
    trades = client.fetch_agg_trades(symbol="BTCUSDT", start=start, end=end, market="FUTURES")
    assert [trade["trade_id"] for trade in trades] == [2]
    assert trades[0]["timestamp"] == start + timedelta(seconds=10)
