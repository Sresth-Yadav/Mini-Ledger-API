"""HTTP-level tests. Needs: pip install -r requirements.txt, then run `pytest`."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LEDGER_DB", str(tmp_path / "api.db"))
    from app.main import app

    with TestClient(app) as c:
        yield c


def account(client, name, allow_negative=False, currency="INR"):
    r = client.post("/accounts", json={"name": name, "currency": currency, "allow_negative": allow_negative})
    assert r.status_code == 201
    return r.json()


def transfer(client, src, dst, amount, key=None):
    headers = {"Idempotency-Key": key} if key else {}
    return client.post(
        "/transfers",
        json={"source_id": src["id"], "destination_id": dst["id"], "amount": amount},
        headers=headers,
    )


def test_happy_path_and_balances(client):
    bank, alice = account(client, "Funding", True), account(client, "Alice")
    r = transfer(client, bank, alice, 5_000, "fund-1")
    assert r.status_code == 201
    assert client.get(f"/accounts/{alice['id']}").json()["balance"] == 5_000
    assert client.get(f"/accounts/{alice['id']}/entries").json()["data"][0]["amount"] == 5_000


def test_replay_returns_same_transfer_and_flags_it(client):
    bank, alice = account(client, "Funding", True), account(client, "Alice")
    first = transfer(client, bank, alice, 5_000, "fund-1")
    second = transfer(client, bank, alice, 5_000, "fund-1")
    assert second.json()["id"] == first.json()["id"]
    assert second.headers["Idempotent-Replayed"] == "true"
    assert client.get(f"/accounts/{alice['id']}").json()["balance"] == 5_000


def test_key_reuse_with_different_body_is_422(client):
    bank, alice = account(client, "Funding", True), account(client, "Alice")
    transfer(client, bank, alice, 5_000, "fund-1")
    r = transfer(client, bank, alice, 6_000, "fund-1")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "idempotency_key_reuse"


def test_missing_key_insufficient_funds_and_bad_input(client):
    alice, bob = account(client, "Alice"), account(client, "Bob")
    assert transfer(client, alice, bob, 100).status_code == 400
    r = transfer(client, alice, bob, 100, "k1")
    assert r.status_code == 409 and r.json()["error"]["code"] == "insufficient_funds"
    r = transfer(client, alice, bob, -5, "k2")
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
    assert client.get("/accounts/acct_nope").status_code == 404
