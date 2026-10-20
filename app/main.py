"""HTTP layer. Thin on purpose: validation and routing here, rules in ledger.py."""
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import ledger
from .db import connect, init_db


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Mini Ledger API", version="0.1.0", lifespan=lifespan)


def get_db():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def _error(status: int, code: str, message: str, headers: Optional[dict] = None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status, headers=headers)


@app.exception_handler(ledger.LedgerError)
async def ledger_error_handler(_request: Request, exc: ledger.LedgerError):
    return _error(exc.status, exc.code, exc.message)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError):
    details = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
    return _error(422, "invalid_request", "; ".join(details))


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    currency: str = Field(pattern=r"^[A-Z]{3}$", description="ISO 4217 code, e.g. INR")
    allow_negative: bool = Field(default=False, description="True for funding/system accounts")


class TransferCreate(BaseModel):
    source_id: str
    destination_id: str
    amount: int = Field(gt=0, description="Minor units, e.g. paise")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/accounts", status_code=201)
def create_account(body: AccountCreate, conn=Depends(get_db)):
    return ledger.create_account(conn, body.name, body.currency, body.allow_negative)


@app.get("/accounts/{account_id}")
def get_account(account_id: str, conn=Depends(get_db)):
    return ledger.get_account(conn, account_id)


@app.get("/accounts/{account_id}/entries")
def list_entries(account_id: str, conn=Depends(get_db)):
    return {"data": ledger.list_entries(conn, account_id)}


@app.post("/transfers", status_code=201)
def create_transfer(
    body: TransferCreate,
    idempotency_key: Optional[str] = Header(default=None),
    conn=Depends(get_db),
):
    transfer, replayed = ledger.create_transfer(
        conn, body.source_id, body.destination_id, body.amount, idempotency_key or ""
    )
    headers = {"Idempotent-Replayed": "true"} if replayed else None
    return JSONResponse(transfer, status_code=201, headers=headers)


@app.get("/transfers/{transfer_id}")
def get_transfer(transfer_id: str, conn=Depends(get_db)):
    return ledger.get_transfer(conn, transfer_id)
