--- START OF FILE: tests/test_api.py ---
import os
import pytest
from fastapi.testclient import TestClient

@pytest.fixture(autouse=True)
def _db_sqlite_tmp(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("API_KEY", "test_api_key")

from capitalguard.interfaces.api.main import app
client = TestClient(app)
HEADERS = {"X-API-Key": "test_api_key"}

def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}

def test_api_key_protection():
    assert client.get("/recommendations", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/recommendations").status_code == 401

def test_create_and_close_recommendation_flow():
    create_payload = {
        "asset": "ETHUSDT",
        "side": "SHORT",
        "entry": 3000,
        "stop_loss": 3100,
        "targets": [2900, 2800]
    }
    r1 = client.post("/recommendations", json=create_payload, headers=HEADERS)
    assert r1.status_code == 200
    rec = r1.json()
    rec_id = rec["id"]
    assert rec["status"] == "OPEN"

    r2 = client.post(f"/recommendations/{rec_id}/close", json={"exit_price": 2950.0}, headers=HEADERS)
    assert r2.status_code == 200
    rec2 = r2.json()
    assert rec2["status"] == "CLOSED"

    r3 = client.get("/recommendations", headers=HEADERS)
    found = any(item["id"] == rec_id and item["status"] == "CLOSED" for item in r3.json())
    assert found
--- END OF FILE ---