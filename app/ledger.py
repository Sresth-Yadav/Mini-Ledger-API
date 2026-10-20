"""Ledger business logic. No web framework in here, so it is easy to test."""
import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone

from .db import transaction


class LedgerError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _balance(conn: sqlite3.Connection, account_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) AS b FROM entries WHERE account_id = ?", (account_id,)
    ).fetchone()
    return row["b"]


def _account_row(conn: sqlite3.Connection, account_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    if row is None:
        raise LedgerError(404, "account_not_found", f"No account with id {account_id}.")
    return row


def _account_view(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "currency": row["currency"],
        "allow_negative": bool(row["allow_negative"]),
        "balance": _balance(conn, row["id"]),
        "created_at": row["created_at"],
    }


def create_account(conn, name: str, currency: str, allow_negative: bool = False) -> dict:
    account_id = "acct_" + uuid.uuid4().hex[:16]
    conn.execute(
        "INSERT INTO accounts (id, name, currency, allow_negative, created_at) VALUES (?, ?, ?, ?, ?)",
        (account_id, name, currency, int(allow_negative), _now()),
    )
    return _account_view(conn, _account_row(conn, account_id))


def get_account(conn, account_id: str) -> dict:
    return _account_view(conn, _account_row(conn, account_id))


def list_entries(conn, account_id: str) -> list:
    _account_row(conn, account_id)
    rows = conn.execute(
        "SELECT id, transfer_id, amount FROM entries WHERE account_id = ? ORDER BY id", (account_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_transfer(conn, transfer_id: str) -> dict:
    row = conn.execute("SELECT * FROM transfers WHERE id = ?", (transfer_id,)).fetchone()
    if row is None:
        raise LedgerError(404, "transfer_not_found", f"No transfer with id {transfer_id}.")
    return dict(row)


def create_transfer(conn, source_id: str, destination_id: str, amount: int, idempotency_key: str):
    """Move `amount` minor units from source to destination.

    Returns (transfer, replayed). If the same Idempotency-Key was already used
    with the same request, the original result is returned and nothing changes.
    """
    if not idempotency_key or len(idempotency_key) > 255:
        raise LedgerError(400, "missing_idempotency_key", "Send an Idempotency-Key header (max 255 characters).")
    if amount <= 0:
        raise LedgerError(422, "invalid_amount", "Amount must be a positive integer in minor units.")

    payload = {"source_id": source_id, "destination_id": destination_id, "amount": amount}
    request_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    with transaction(conn):
        prior = conn.execute(
            "SELECT request_hash, response FROM idempotency_keys WHERE key = ?", (idempotency_key,)
        ).fetchone()
        if prior is not None:
            if prior["request_hash"] != request_hash:
                raise LedgerError(
                    422, "idempotency_key_reuse",
                    "This Idempotency-Key was already used with different parameters.",
                )
            return json.loads(prior["response"]), True

        if source_id == destination_id:
            raise LedgerError(422, "invalid_transfer", "Source and destination must be different accounts.")
        src = _account_row(conn, source_id)
        dst = _account_row(conn, destination_id)
        if src["currency"] != dst["currency"]:
            raise LedgerError(422, "currency_mismatch", "Both accounts must use the same currency.")
        if not src["allow_negative"] and _balance(conn, source_id) < amount:
            raise LedgerError(409, "insufficient_funds", "The source account does not have enough balance.")

        transfer = {
            "id": "txn_" + uuid.uuid4().hex[:16],
            "source_id": source_id,
            "destination_id": destination_id,
            "amount": amount,
            "currency": src["currency"],
            "created_at": _now(),
        }
        conn.execute(
            "INSERT INTO transfers (id, source_id, destination_id, amount, currency, created_at) "
            "VALUES (:id, :source_id, :destination_id, :amount, :currency, :created_at)",
            transfer,
        )
        conn.execute(
            "INSERT INTO entries (transfer_id, account_id, amount) VALUES (?, ?, ?)",
            (transfer["id"], source_id, -amount),
        )
        conn.execute(
            "INSERT INTO entries (transfer_id, account_id, amount) VALUES (?, ?, ?)",
            (transfer["id"], destination_id, amount),
        )
        # Only successful results are stored, so a failed request can be retried with the same key.
        conn.execute(
            "INSERT INTO idempotency_keys (key, request_hash, status_code, response, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (idempotency_key, request_hash, 201, json.dumps(transfer), _now()),
        )
    return transfer, False
