# Mini Ledger API

A small double-entry ledger service built with FastAPI and SQLite. It moves money between accounts safely: no overdrafts, no double charges on retries, and balances that always add up.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Interactive docs: http://127.0.0.1:8000/docs

## Try it

```bash
# A funding account may go negative; customer accounts may not.
curl -X POST localhost:8000/accounts -H 'Content-Type: application/json' \
  -d '{"name":"Funding","currency":"INR","allow_negative":true}'
curl -X POST localhost:8000/accounts -H 'Content-Type: application/json' \
  -d '{"name":"Alice","currency":"INR"}'

# Amounts are integers in minor units (10000 = 100.00 INR).
curl -X POST localhost:8000/transfers -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: fund-alice-1' \
  -d '{"source_id":"<funding id>","destination_id":"<alice id>","amount":10000}'
```

Send the same request with the same `Idempotency-Key` again and you get the original transfer back with an `Idempotent-Replayed: true` header, and no money moves.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/accounts` | Create an account |
| GET | `/accounts/{id}` | Account with current balance |
| GET | `/accounts/{id}/entries` | Ledger entries for the account |
| POST | `/transfers` | Move money (requires `Idempotency-Key`) |
| GET | `/transfers/{id}` | Look up a transfer |

Errors always look like `{"error": {"code": "...", "message": "..."}}`. Codes: `insufficient_funds` (409), `currency_mismatch`, `invalid_transfer`, `invalid_request`, `idempotency_key_reuse` (422), `missing_idempotency_key` (400), `account_not_found`, `transfer_not_found` (404).

## Design decisions

- **Integer minor units.** Floats cannot represent money exactly, so amounts are integers.
- **Double-entry.** Every transfer writes a debit and a credit. Balances are never stored, only summed from entries, so the ledger always sums to zero and has a full audit trail.
- **Idempotency.** The key and a hash of the request are stored with the response. Same key and same request replays the result. Same key with a different request is rejected. Only successful results are stored, so a failed request can be retried with the same key.
- **Concurrency.** Transfers run inside `BEGIN IMMEDIATE`, which serializes writers. Two simultaneous requests with one key move money once, and parallel transfers cannot overdraw an account. Both cases are tested.
- **Layering.** Business rules live in `app/ledger.py` with no web framework, so they are tested directly. `app/main.py` is only routing and validation.

## Tests

```bash
python -m unittest discover -s tests -p "test_ledger.py"   # core logic, stdlib only
pytest                                                       # includes HTTP tests
```

## Known limits and next steps

- SQLite serializes all writes. For real throughput, move to PostgreSQL with row-level locks (`SELECT ... FOR UPDATE`) or serializable transactions.
- No authentication or per-customer scoping of keys.
- Idempotency keys never expire. Add a TTL (for example 24 hours) and cleanup.
- No pagination on entries, no reversals or refunds, no outbound webhooks.
