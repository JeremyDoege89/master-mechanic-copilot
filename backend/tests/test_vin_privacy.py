"""VIN confidentiality (M4 correction 2).

The full VIN is stored encrypted at rest (key from MMC_VIN_KEY, or a dev key
auto-generated into DATA_DIR), indexed by sha256, and only the last 6
characters may ever be displayed or logged. These tests fail if the full VIN
appears in the event log file, captured server logs, GET state, or report JSON.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi.testclient import TestClient

from mmc import vin as vin_mod
from mmc.api import routes as api_routes
from mmc.main import app

VIN = "1FAHP3F28CL123456"
LAST6 = "123456"
KEY_HEX = "ab" * 32


def _client(tmp_path, monkeypatch, key_hex: str | None = KEY_HEX) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    if key_hex is None:
        monkeypatch.delenv("MMC_VIN_KEY", raising=False)
    else:
        monkeypatch.setenv("MMC_VIN_KEY", key_hex)
    api_routes._listeners.clear()
    return TestClient(app)


def _create_with_vin(client: TestClient) -> str:
    r = client.post(
        "/sessions",
        json={
            "vehicle": {"make": "Ford", "model": "Focus", "year": 2012, "vin": VIN},
            "user_level": "L1",
            "tools": [],
            "mode": "sim",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def test_full_vin_never_persisted_or_served(tmp_path, monkeypatch, caplog):
    client = _client(tmp_path, monkeypatch)
    with caplog.at_level(logging.DEBUG):
        session_id = _create_with_vin(client)
        state = client.get(f"/sessions/{session_id}/state").json()
        assert LAST6 in json.dumps(state)
        assert VIN not in json.dumps(state)

        r = client.post(f"/sessions/{session_id}/report")
        assert r.status_code == 200
        report = client.get(f"/reports/{r.json()['report_id']}.json").json()
        assert VIN not in json.dumps(report)

    # Event log file: encrypted token present, plaintext absent.
    log_text = (tmp_path / "sessions" / session_id / "events.jsonl").read_text()
    assert VIN not in log_text
    assert "vin_encrypted" in log_text
    # Server logs captured during the requests: no full VIN either.
    assert VIN not in caplog.text


def test_vin_encrypted_at_rest_and_indexed(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    session_id = _create_with_vin(client)

    log_text = (tmp_path / "sessions" / session_id / "events.jsonl").read_text()
    token = json.loads(log_text.splitlines()[0])["payload"]["vehicle"]["vin_encrypted"]
    assert token.startswith("enc:v1:")
    assert vin_mod.decrypt_vin(token) == VIN

    index = json.loads((tmp_path / "vin_index.json").read_text())
    assert index[vin_mod.sha256_vin(VIN)] == [session_id]


def test_wrong_key_cannot_decrypt(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    session_id = _create_with_vin(client)
    log_text = (tmp_path / "sessions" / session_id / "events.jsonl").read_text()
    token = json.loads(log_text.splitlines()[0])["payload"]["vehicle"]["vin_encrypted"]
    try:
        vin_mod.decrypt_vin(token, bytes.fromhex("cd" * 32))
        raise AssertionError("decryption with the wrong key must fail")
    except ValueError:
        pass


def test_dev_key_generated_when_env_unset(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, key_hex=None)
    assert not (tmp_path / "vin.key").exists()
    session_id = _create_with_vin(client)
    key_path = tmp_path / "vin.key"
    assert key_path.exists()
    assert len(bytes.fromhex(key_path.read_text().strip())) == 32
    log_text = (tmp_path / "sessions" / session_id / "events.jsonl").read_text()
    assert VIN not in log_text
