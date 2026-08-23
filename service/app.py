"""Toy payment-authorization API.

This service exists to be broken. It is the *subject* of the triage experiment, not a product:
a deliberately small FastAPI app over PostgreSQL with a handful of seeded defects that the
scenario runner can switch on one at a time. Each defect is off by default, produces a distinct
observable failure, and — importantly for the experiment — never announces itself in the logs.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
import re
import time
import uuid
from contextlib import asynccontextmanager, contextmanager

import psycopg
from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
from pydantic import BaseModel, Field, ValidationError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("payments")

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = int(os.environ.get("DB_PORT", "5432"))
DB_NAME = os.environ.get("DB_NAME", "payments")
DB_USER = os.environ.get("DB_USER", "payments")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "payments")

# Constant across every scenario — never vary these per run, or the timing itself becomes a label.
CONNECT_TIMEOUT_S = 5
STATEMENT_TIMEOUT_MS = 8000

DECLINE_OVER_CENTS = 500_000
SUPPORTED_CURRENCIES = ("USD", "EUR", "GBP")
IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


def _flag(name: str) -> bool:
    return os.environ.get(name, "") == "1"


# Seeded defects. Read once at import; never logged, never surfaced on any endpoint.
IGNORE_IDEMPOTENCY = _flag("BUG_IGNORE_IDEMPOTENCY")
FLOAT_MONEY = _flag("BUG_FLOAT_MONEY")
NO_ROW_LOCK = _flag("BUG_NO_ROW_LOCK")
ADD_JITTER = _flag("SVC_JITTER")
JITTER_MAX_S = 0.5

SCHEMA = """
CREATE TABLE IF NOT EXISTS authorizations (
    id                UUID PRIMARY KEY,
    idempotency_key   TEXT NOT NULL,
    request_digest    TEXT NOT NULL,
    card_last4        TEXT NOT NULL,
    amount_cents      BIGINT NOT NULL,
    currency          TEXT NOT NULL,
    status            TEXT NOT NULL,
    decline_reason    TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS authorizations_idempotency_key_idx
    ON authorizations (idempotency_key);
"""


def _connect() -> psycopg.Connection:
    return psycopg.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        connect_timeout=CONNECT_TIMEOUT_S,
        options=f"-c statement_timeout={STATEMENT_TIMEOUT_MS}",
        row_factory=dict_row,
    )


@contextmanager
def db():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    deadline = time.time() + 60
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with db() as conn:
                conn.execute(SCHEMA)
            log.info("schema ready, listening for authorization requests")
            break
        except Exception as exc:  # pragma: no cover - startup retry loop
            last_error = exc
            await asyncio.sleep(1)
    else:  # pragma: no cover - only on a genuinely dead database
        log.error("could not prepare schema before timeout: %s", last_error)
    yield


app = FastAPI(title="payments", lifespan=lifespan)


class AuthorizeRequest(BaseModel):
    card_last4: str = Field(pattern=r"^\d{4}$")
    amount_cents: int = Field(strict=True, gt=0)
    currency: str

    def digest(self) -> str:
        raw = f"{self.card_last4}|{self.amount_cents}|{self.currency}"
        return hashlib.sha256(raw.encode()).hexdigest()


@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    """Report request-shape problems as 400, not FastAPI's default 422."""
    return JSONResponse(
        status_code=400, content={"detail": "invalid request", "errors": _brief(exc)}
    )


def _brief(exc: RequestValidationError | ValidationError) -> list[str]:
    out = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", []) if p != "body")
        out.append(f"{loc or 'body'}: {err.get('msg', 'invalid')}")
    return out


def _row_to_response(row: dict, replayed: bool) -> dict:
    body = {
        "authorization_id": str(row["id"]),
        "status": row["status"],
        "card_last4": row["card_last4"],
        "amount_cents": row["amount_cents"],
        "currency": row["currency"],
        "replayed": replayed,
    }
    if row["status"] == "declined":
        body["decline_reason"] = row["decline_reason"]
    return body


def _status_for(row: dict, replayed: bool) -> int:
    if row["status"] == "declined":
        return 402
    return 200 if replayed else 201


def _settle_amount(amount_cents: int) -> int:
    """Normalize the amount before it is stored.

    The clean path is a no-op: cents in, cents out. The seeded money defect routes the value
    through floating-point dollars, which loses a cent on amounts such as 1999.
    """
    if FLOAT_MONEY:
        dollars = amount_cents / 100.0
        return int(dollars * 100)
    return amount_cents


def _find_existing(conn: psycopg.Connection, key: str) -> dict | None:
    cur = conn.execute(
        "SELECT id, idempotency_key, request_digest, card_last4, amount_cents, currency,"
        " status, decline_reason FROM authorizations WHERE idempotency_key = %s",
        (key,),
    )
    return cur.fetchone()


@app.post("/authorize")
def authorize(payload: AuthorizeRequest, idempotency_key: str = Header(alias="Idempotency-Key")):
    if ADD_JITTER:
        time.sleep(random.uniform(0, JITTER_MAX_S))

    if not IDEMPOTENCY_KEY_RE.match(idempotency_key):
        return JSONResponse(
            status_code=400,
            content={"detail": "Idempotency-Key must be 8-128 characters of [A-Za-z0-9_-]"},
        )
    if payload.currency not in SUPPORTED_CURRENCIES:
        return JSONResponse(
            status_code=400,
            content={"detail": f"currency must be one of {', '.join(SUPPORTED_CURRENCIES)}"},
        )

    digest = payload.digest()
    amount = _settle_amount(payload.amount_cents)

    try:
        with db() as conn:
            # The clean path serializes every request that shares an idempotency key, so the
            # replay check and the insert below cannot interleave with a concurrent twin.
            if not NO_ROW_LOCK:
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (idempotency_key,))

            if not IGNORE_IDEMPOTENCY:
                existing = _find_existing(conn, idempotency_key)
                if existing is not None:
                    if existing["request_digest"] != digest:
                        log.info("idempotency key reused with a different request body")
                        return JSONResponse(
                            status_code=409,
                            content={
                                "detail": "Idempotency-Key was already used with another request",
                                "authorization_id": str(existing["id"]),
                            },
                        )
                    return JSONResponse(
                        status_code=_status_for(existing, replayed=True),
                        content=_row_to_response(existing, replayed=True),
                    )

            if amount > DECLINE_OVER_CENTS:
                status, reason = "declined", "amount_exceeds_limit"
            else:
                status, reason = "approved", None

            row_id = uuid.uuid4()
            conn.execute(
                "INSERT INTO authorizations (id, idempotency_key, request_digest, card_last4,"
                " amount_cents, currency, status, decline_reason)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    row_id,
                    idempotency_key,
                    digest,
                    payload.card_last4,
                    amount,
                    payload.currency,
                    status,
                    reason,
                ),
            )
            row = {
                "id": row_id,
                "card_last4": payload.card_last4,
                "amount_cents": amount,
                "currency": payload.currency,
                "status": status,
                "decline_reason": reason,
            }
            log.info("authorization %s %s for %s cents", row_id, status, amount)
            return JSONResponse(
                status_code=_status_for(row, replayed=False),
                content=_row_to_response(row, replayed=False),
            )
    except psycopg.Error as exc:
        log.error("database error while authorizing: %s", _clean(exc))
        return JSONResponse(status_code=503, content={"detail": "database unavailable"})


@app.get("/authorizations/{authorization_id}")
def get_authorization(authorization_id: str):
    try:
        uuid.UUID(authorization_id)
    except ValueError:
        return JSONResponse(status_code=400, content={"detail": "authorization id must be a UUID"})
    try:
        with db() as conn:
            cur = conn.execute(
                "SELECT id, idempotency_key, request_digest, card_last4, amount_cents, currency,"
                " status, decline_reason FROM authorizations WHERE id = %s",
                (authorization_id,),
            )
            row = cur.fetchone()
    except psycopg.Error as exc:
        log.error("database error while reading authorization: %s", _clean(exc))
        return JSONResponse(status_code=503, content={"detail": "database unavailable"})
    if row is None:
        return JSONResponse(status_code=404, content={"detail": "authorization not found"})
    return JSONResponse(status_code=200, content=_row_to_response(row, replayed=False))


@app.get("/health")
def health():
    """Shallow: says the process is up. Deliberately does not touch the database."""
    return {"status": "ok"}


@app.get("/health/deep")
def health_deep():
    try:
        with db() as conn:
            conn.execute("SELECT 1")
    except psycopg.Error as exc:
        log.error("deep health check could not reach the database: %s", _clean(exc))
        return JSONResponse(
            status_code=503, content={"status": "degraded", "database": "unreachable"}
        )
    return {"status": "ok", "database": "reachable"}


def _clean(exc: Exception) -> str:
    return " ".join(str(exc).split())
