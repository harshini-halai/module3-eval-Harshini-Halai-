import json
import os
from typing import Any, Dict, Optional
from fastapi import FastAPI, Request, Response, BackgroundTasks, status
from fastapi.responses import JSONResponse

from core import (
    IdempotencyStore,
    DeadLetterQueue,
    verify_signature,
    process_event_with_resilience,
    SignatureVerificationError,
    TransientError,
    PermanentError,
)

app = FastAPI(title="Production Webhook Ingestion Service")

# Sourced strictly from environment variable
WEBHOOK_SECRET = os.getenv("SECRET")

# In-process singleton stores
idempotency_store = IdempotencyStore()
dlq = DeadLetterQueue()

# Internal flag to demonstrate readiness probe semantics
is_ready = True


def mock_downstream_service(payload: Dict[str, Any]):
    """
    Simulated downstream consumer that triggers transient or permanent errors
    based on payload cues for testing.
    """
    data = payload.get("data", {})
    action = data.get("simulate_error")

    if action == "transient":
        raise TransientError("Downstream payment ledger timed out")
    elif action == "permanent":
        raise PermanentError("Downstream entity schema rejected")

# Part A: Observability Probes

@app.get("/healthz", status_code=status.HTTP_200_OK)
def healthz():
    """
    Liveness probe: returns 200 as long as the HTTP event loop and process are alive.
    """
    return {"status": "alive"}


@app.get("/readyz")
def readyz():
    """
    Readiness probe: validates internal state, dependencies, and configuration.
    Fails if the service cannot accept work.
    """
    if not is_ready:
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"status": "not ready"})
    
    if not WEBHOOK_SECRET:
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"status": "secret not configured"})

    return {"status": "ready"}

# Part A, B, C, E: Ingestion Endpoint

@app.post("/webhooks/events")
async def handle_webhook(request: Request, background_tasks: BackgroundTasks):
    # 1. Read raw binary body bytes BEFORE anything else parses it
    raw_body = await request.body()

    sig_header = request.headers.get("X-Webhook-Signature")
    ts_header = request.headers.get("X-Webhook-Timestamp")

    # 2. Part B: Verify Signature
    try:
        verify_signature(
            raw_body=raw_body,
            signature_header=sig_header,
            timestamp_header=ts_header,
            secret=WEBHOOK_SECRET,
        )
    except SignatureVerificationError as err:
        return JSONResponse(status_code=err.status_code, content={"error": str(err)})

    # 3. Part A: Validate JSON structure
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"error": "Body must be valid JSON"})

    if not isinstance(payload, dict):
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"error": "JSON root must be an object"})

    # 4. Part A: Validate existence and presence of 'id'
    event_id = payload.get("id")
    if not event_id or not isinstance(event_id, str) or not event_id.strip():
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"error": "Event payload missing valid 'id'"})

    # 5. Part C: Atomic Idempotency check
    is_new, current_status = await idempotency_store.acquire_or_check(event_id)

    if not is_new:
        # Acknowledge redelivery with 200 without re-enqueuing downstream execution
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "acknowledged", "note": "duplicate event ignored", "state": current_status}
        )

    # 6. Part E: Fast Acknowledgement - enqueue worker to background and return 202 immediately
    background_tasks.add_task(
        process_event_with_resilience,
        event_id=event_id,
        payload=payload,
        idempotency_store=idempotency_store,
        dlq=dlq,
        downstream_call=mock_downstream_service,
    )

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"status": "accepted", "event_id": event_id}
    )


# Part D: DLQ Inspection Endpoint

@app.get("/dlq", status_code=status.HTTP_200_OK)
async def get_dlq_records():
    records = await dlq.list_all()
    return {"count": len(records), "entries": records}