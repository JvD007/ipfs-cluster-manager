"""Integration-tests voor de FastAPI app met een gemockte Cluster API.

Run met:
  pytest -q
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app  # noqa: E402

FAKE_PINS = [
    {
        "cid": "QmAAA",
        "name": "backup-staging-1",
        "timestamp": "2025-05-01T10:00:00Z",
        "metadata": {"env": "staging", "tag": "ephemeral"},
        "allocations": ["peer-1", "peer-2"],
        "replication_factor_min": 2,
        "replication_factor_max": 2,
    },
    {
        "cid": "QmBBB",
        "name": "model-prod-llama",
        "timestamp": "2025-05-10T10:00:00Z",
        "metadata": {"env": "prod", "tag": "model"},
        "allocations": ["peer-1"],
        "replication_factor_min": 1,
        "replication_factor_max": 1,
    },
    {
        "cid": "QmCCC",
        "name": "backup-staging-2",
        "timestamp": "2025-06-01T10:00:00Z",
        "metadata": {"env": "staging", "tag": "ephemeral"},
        "allocations": ["peer-2"],
        "replication_factor_min": 1,
        "replication_factor_max": 1,
    },
]


@pytest.fixture
def client_mock():
    with patch("app.main.ClusterClient") as MockClient:
        instance = MockClient.return_value
        instance.health = AsyncMock(return_value=True)
        instance.id = AsyncMock(return_value={"id": "peer-1", "peername": "node-1"})
        instance.version = AsyncMock(return_value={"version": "1.0.8"})
        instance.peers = AsyncMock(return_value=[
            {"id": "peer-1", "peername": "node-1", "version": "1.0.8", "ipfs": {"version": "0.27.0"}},
            {"id": "peer-2", "peername": "node-2", "version": "1.0.8", "ipfs": {"version": "0.27.0"}},
        ])
        instance.alerts = AsyncMock(return_value=[])
        instance.allocations = AsyncMock(return_value=FAKE_PINS)
        instance.unpin = AsyncMock(return_value={"cid": "ok"})
        instance.gc = AsyncMock(return_value={"gc": "done"})
        yield instance


def test_health(client_mock):
    with TestClient(app) as c:
        r = c.get("/api/health")
        assert r.status_code == 200
        assert r.json() == {"ok": True}


def test_status(client_mock):
    with TestClient(app) as c:
        r = c.get("/api/status")
        assert r.status_code == 200
        data = r.json()
        assert data["total_pins"] == 3
        assert data["peer_count"] == 2
        # peer-1 heeft 2 allocations, peer-2 heeft 2 allocations
        counts = {p["id"]: p["pins"] for p in data["peers"]}
        assert counts == {"peer-1": 2, "peer-2": 2}


def test_pins_endpoint(client_mock):
    with TestClient(app) as c:
        r = c.get("/api/pins")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 3
        assert data["pins"][0]["cid"] == "QmAAA"


def test_preview_filters_by_metadata(client_mock):
    with TestClient(app) as c:
        r = c.post("/api/preview-unpin", json={"metadata": {"env": "staging"}})
        assert r.status_code == 200
        data = r.json()
        assert data["matched_count"] == 2
        assert set(data["cids"]) == {"QmAAA", "QmCCC"}
        assert "confirm_token" in data and data["confirm_token"]


def test_preview_filters_by_timestamp(client_mock):
    with TestClient(app) as c:
        r = c.post("/api/preview-unpin", json={"before": "2025-05-15T00:00:00Z"})
        assert r.status_code == 200
        data = r.json()
        assert set(data["cids"]) == {"QmAAA", "QmBBB"}


def test_bulk_unpin_requires_valid_token(client_mock):
    with TestClient(app) as c:
        # eerst preview, dan unpin
        prev = c.post("/api/preview-unpin", json={"metadata": {"env": "staging"}}).json()
        r = c.post(
            "/api/bulk-unpin",
            json={"confirm_token": prev["confirm_token"], "cids": prev["cids"]},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["succeeded"] == 2
        assert data["failed"] == 0
        # client.unpin werd 2x aangeroepen
        assert client_mock.unpin.await_count == 2


def test_bulk_unpin_rejects_invalid_token(client_mock):
    with TestClient(app) as c:
        r = c.post(
            "/api/bulk-unpin",
            json={"confirm_token": "wrong-token", "cids": ["QmAAA"]},
        )
        assert r.status_code == 403


def test_bulk_unpin_rejects_token_with_different_cids(client_mock):
    with TestClient(app) as c:
        prev = c.post("/api/preview-unpin", json={"metadata": {"env": "staging"}}).json()
        # Iemand probeert dezelfde token te gebruiken voor een ándere CID-set
        r = c.post(
            "/api/bulk-unpin",
            json={"confirm_token": prev["confirm_token"], "cids": ["QmBBB"]},
        )
        assert r.status_code == 403


def test_bulk_unpin_token_is_single_use(client_mock):
    with TestClient(app) as c:
        prev = c.post("/api/preview-unpin", json={"metadata": {"env": "staging"}}).json()
        ok = c.post(
            "/api/bulk-unpin",
            json={"confirm_token": prev["confirm_token"], "cids": prev["cids"]},
        )
        assert ok.status_code == 200
        # Een tweede call met hetzelfde token moet falen
        again = c.post(
            "/api/bulk-unpin",
            json={"confirm_token": prev["confirm_token"], "cids": prev["cids"]},
        )
        assert again.status_code == 403


def test_bulk_unpin_with_gc(client_mock):
    with TestClient(app) as c:
        prev = c.post("/api/preview-unpin", json={"cids": ["QmBBB"]}).json()
        r = c.post(
            "/api/bulk-unpin",
            json={"confirm_token": prev["confirm_token"], "cids": prev["cids"], "run_gc": True},
        )
        assert r.status_code == 200
        assert r.json()["gc"] == {"gc": "done"}
        client_mock.gc.assert_awaited_once()
