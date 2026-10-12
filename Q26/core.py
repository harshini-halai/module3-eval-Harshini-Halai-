import asyncio
import hmac
import hashlib
import json
import os
import random
import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


class EventStatus(str, Enum):
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class SignatureVerificationError(Exception):
    def __init__(self, message: str, status_code: int = 401):
        super().__init__(message)
        self.status_code = status_code


class PermanentError(Exception):
    """Failures that must not be retried (e.g. malformed domain payload)."""
    pass


class TransientError(Exception):
    """Temporary network or server errors that should be retried."""
    pass


# ---------------------------------------------------------------------------
# Part B: Signature Verification
# ---------------------------------------------------------------------------

def verify_signature(
    raw_body: bytes,
    signature_header: Optional[str],
    timestamp_header: Optional[str],
    secret: Optional[str],
    current_time_fn: Callable[[], float] = time.time,
    max_drift_seconds: float = 300.0,
) -> None:
    """
    Verifies HMAC-SHA256 signature using raw bytes and guards against replay attacks.
    """
    if not secret:
        raise SignatureVerificationError("Webhook secret is not configured on receiver", status_code=500)

    if not signature_header or not timestamp_header:
        raise SignatureVerificationError("Missing required webhook authentication headers", status_code=401)

    # 1. Parse and validate timestamp drift
    try:
        timestamp_val = float(timestamp_header)
    except ValueError:
        raise SignatureVerificationError("Malformed X-Webhook-Timestamp header", status_code=401)

    now = current_time_fn()
    if abs(now - timestamp_val) > max_drift_seconds:
        raise SignatureVerificationError("Webhook timestamp outside tolerance window", status_code=401)

    # 2. Extract digest prefix
    prefix = "sha256="
    if not signature_header.startswith(prefix):
        raise SignatureVerificationError("Invalid signature scheme prefix", status_code=401)
    received_digest = signature_header[len(prefix):]

    # 3. Compute expected digest over <timestamp>.<raw request body>
    signed_payload = f"{timestamp_header}.".encode("utf-8") + raw_body
    computed_digest = hmac.new(
        secret.encode("utf-8"),
        signed_payload,
        hashlib.sha256
    ).hexdigest()

    # 4. Constant-time comparison
    if not hmac.compare_digest(computed_digest, received_digest):
        raise SignatureVerificationError("Signature digest mismatch", status_code=401)


# ---------------------------------------------------------------------------
# Part C: Thread-safe In-Process Idempotency Store
# ---------------------------------------------------------------------------

class IdempotencyStore:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._store: Dict[str, Dict[str, Any]] = {}

    async def acquire_or_check(self, event_id: str) -> Tuple[bool, Optional[EventStatus]]:
        """
        Atomically inspects the event record.
        Returns:
            (is_new, current_status)
            - If is_new is True, the caller owns processing and status is set to PROCESSING.
            - If is_new is False, event was previously registered.
        """
        async with self._lock:
            record = self._store.get(event_id)
            if record is not None:
                return False, record["status"]

            self._store[event_id] = {
                "status": EventStatus.PROCESSING,
                "first_seen": time.time(),
                "updated_at": time.time(),
            }
            return True, EventStatus.PROCESSING

    async def update_status(self, event_id: str, status: EventStatus, details: Optional[Dict] = None):
        async with self._lock:
            if event_id in self._store:
                self._store[event_id]["status"] = status
                self._store[event_id]["updated_at"] = time.time()
                if details:
                    self._store[event_id]["details"] = details

    async def get(self, event_id: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            return self._store.get(event_id)


# ---------------------------------------------------------------------------
# Part D: Retries, Exponential Backoff with Full Jitter, and DLQ
# ---------------------------------------------------------------------------

def calculate_backoff_delay(
    attempt: int,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    rng: Callable[[float, float], float] = random.uniform,
) -> float:
    """
    Computes exponential backoff with full jitter:
        Sleep ~ Uniform(0, min(max_delay, base_delay * 2 ** attempt))
    """
    exp_limit = min(max_delay, base_delay * (2 ** attempt))
    return rng(0.0, exp_limit)


class DeadLetterQueue:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._entries: List[Dict[str, Any]] = []

    async def push(self, event_id: str, payload: Any, failure_reason: str, attempts: int):
        async with self._lock:
            self._entries.append({
                "event_id": event_id,
                "payload": payload,
                "failure_reason": failure_reason,
                "attempts": attempts,
                "quarantined_at": time.time(),
            })

    async def list_all(self) -> List[Dict[str, Any]]:
        async with self._lock:
            return list(self._entries)


# ---------------------------------------------------------------------------
# Worker Pipeline
# ---------------------------------------------------------------------------

async def process_event_with_resilience(
    event_id: str,
    payload: Dict[str, Any],
    idempotency_store: IdempotencyStore,
    dlq: DeadLetterQueue,
    downstream_call: Callable[[Dict[str, Any]], Any],
    sleep_fn: Callable[[float], Any] = asyncio.sleep,
    max_attempts: int = 3,
):
    """
    Background handler executing downstream call with retries and DLQ dispatch.
    Never raises unhandled exceptions to background tasks.
    """
    attempts = 0
    while attempts < max_attempts:
        attempts += 1
        try:
            # Simulate or call external downstream dependency
            downstream_call(payload)
            await idempotency_store.update_status(event_id, EventStatus.COMPLETED)
            return

        except PermanentError as exc:
            # Skip retries directly to DLQ
            await idempotency_store.update_status(event_id, EventStatus.FAILED, {"error": str(exc)})
            await dlq.push(event_id, payload, f"Permanent error: {str(exc)}", attempts)
            return

        except TransientError as exc:
            if attempts >= max_attempts:
                await idempotency_store.update_status(event_id, EventStatus.FAILED, {"error": str(exc)})
                await dlq.push(event_id, payload, f"Exhausted {max_attempts} retries: {str(exc)}", attempts)
                return

            delay = calculate_backoff_delay(attempts - 1)
            await sleep_fn(delay)

        except Exception as exc:
            # Catch-all unexpected crashes as permanent to protect queue
            await idempotency_store.update_status(event_id, EventStatus.FAILED, {"error": str(exc)})
            await dlq.push(event_id, payload, f"Unexpected error: {str(exc)}", attempts)
            return