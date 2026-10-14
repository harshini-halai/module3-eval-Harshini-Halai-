import asyncio
import hmac
import hashlib
import json
import pytest
import time
from httpx import AsyncClient, ASGITransport

import app as app_module
from core import (
    IdempotencyStore,
    DeadLetterQueue,
    verify_signature,
    calculate_backoff_delay,
    process_event_with_resilience,
    SignatureVerificationError,
    PermanentError,
    TransientError,
)

TEST_SECRET = "test_signing_secret_998877"


@pytest.fixture(autouse=True)
def setup_env(monkeypatch):
    monkeypatch.setattr(app_module, "WEBHOOK_SECRET", TEST_SECRET)
    monkeypatch.setattr(app_module, "idempotency_store", IdempotencyStore())
    monkeypatch.setattr(app_module, "dlq", DeadLetterQueue())


def create_signed_headers(body_bytes: bytes, secret: str = TEST_SECRET, timestamp: int = None):
    ts = str(int(time.time()) if timestamp is None else timestamp)
    signed_payload = f"{ts}.".encode("utf-8") + body_bytes
    sig = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Webhook-Timestamp": ts,
        "X-Webhook-Signature": f"sha256={sig}",
    }


# ---------------------------------------------------------------------------
# Part B Tests: Cryptography & Whitespace Bug Prevention
# ---------------------------------------------------------------------------

def test_signature_preserves_raw_byte_invariance():
    """
    Ensures that whitespace formatting differences in JSON do not break
    verification when raw bytes are used.
    """
    raw_body_with_extra_spaces = b'{\n  "id": "evt_123"  , \n "type": "ping"   \n}'
    ts = str(int(time.time()))
    signed = f"{ts}.".encode("utf-8") + raw_body_with_extra_spaces
    correct_sig = f"sha256={hmac.new(TEST_SECRET.encode(), signed, hashlib.sha256).hexdigest()}"

    # Must succeed with exact raw bytes
    verify_signature(raw_body_with_extra_spaces, correct_sig, ts, TEST_SECRET)

    # Must fail if someone reformatted/re-serialized JSON prior to verification
    reformatted_bytes = json.dumps(json.loads(raw_body_with_extra_spaces.decode())).encode()
    with pytest.raises(SignatureVerificationError):
        verify_signature(reformatted_bytes, correct_sig, ts, TEST_SECRET)


def test_signature_replay_attack_rejected():
    raw_body = b'{"id":"evt_1"}'
    stale_ts = time.time() - 301.0  # Outside 5 min tolerance
    headers = create_signed_headers(raw_body, timestamp=int(stale_ts))

    with pytest.raises(SignatureVerificationError, match="tolerance window"):
        verify_signature(
            raw_body,
            headers["X-Webhook-Signature"],
            headers["X-Webhook-Timestamp"],
            TEST_SECRET,
            current_time_fn=lambda: stale_ts + 301.0
        )


# ---------------------------------------------------------------------------
# Part C Tests: Concurrency Verification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_duplicate_deliveries_race_condition():
    """
    Fires 10 simultaneous requests carrying the same event ID concurrently.
    Exactly one request must initiate processing; all other 9 must receive 2xx.
    """
    transport = ASGITransport(app=app_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        event = {"id": "evt_concurrent_99", "type": "charge.success", "data": {}}
        body = json.dumps(event).encode()
        headers = create_signed_headers(body)

        async def send_req():
            return await client.post("/webhooks/events", content=body, headers=headers)

        responses = await asyncio.gather(*[send_req() for _ in range(10)])

        status_codes = [r.status_code for r in responses]
        assert all(code in (200, 202) for code in status_codes)
        # Exactly one initiates background work (202 Accepted)
        assert status_codes.count(202) == 1
        # The remaining 9 were atomically flagged as duplicates (200 OK)
        assert status_codes.count(200) == 9


# ---------------------------------------------------------------------------
# Part D Tests: Exponential Backoff, Jitter, Injectable Time, DLQ
# ---------------------------------------------------------------------------

def test_exponential_backoff_and_jitter_bounds():
    # Deterministic mock rng that picks the upper bound
    mock_rng_max = lambda low, high: high
    assert calculate_backoff_delay(0, base_delay=1.0, rng=mock_rng_max) == 1.0
    assert calculate_backoff_delay(1, base_delay=1.0, rng=mock_rng_max) == 2.0
    assert calculate_backoff_delay(2, base_delay=1.0, rng=mock_rng_max) == 4.0

    # Ensure delay stays bounded by max_delay
    assert calculate_backoff_delay(10, base_delay=1.0, max_delay=30.0, rng=mock_rng_max) == 30.0


@pytest.mark.asyncio
async def test_resilience_pipeline_transient_retry_and_dlq():
    store = IdempotencyStore()
    dlq = DeadLetterQueue()
    sleep_calls = []

    async def mock_sleep(seconds: float):
        sleep_calls.append(seconds)

    call_count = 0
    def failing_downstream(_):
        nonlocal call_count
        call_count += 1
        raise TransientError("Connection reset by peer")

    await store.acquire_or_check("evt_transient")

    await process_event_with_resilience(
        event_id="evt_transient",
        payload={"id": "evt_transient"},
        idempotency_store=store,
        dlq=dlq,
        downstream_call=failing_downstream,
        sleep_fn=mock_sleep,
        max_attempts=3,
    )

    # Executed 3 times (initial + 2 retries)
    assert call_count == 3
    # Slept twice without real wall-clock latency
    assert len(sleep_calls) == 2
    # Landed in DLQ
    entries = await dlq.list_all()
    assert len(entries) == 1
    assert entries[0]["event_id"] == "evt_transient"
    assert "Exhausted 3 retries" in entries[0]["failure_reason"]


@pytest.mark.asyncio
async def test_resilience_pipeline_permanent_error_bypasses_retry():
    store = IdempotencyStore()
    dlq = DeadLetterQueue()
    sleep_calls = []

    def permanent_failure(_):
        raise PermanentError("Malformed customer currency")

    await store.acquire_or_check("evt_permanent")

    await process_event_with_resilience(
        event_id="evt_permanent",
        payload={"id": "evt_permanent"},
        idempotency_store=store,
        dlq=dlq,
        downstream_call=permanent_failure,
        sleep_fn=lambda s: sleep_calls.append(s),
        max_attempts=5,
    )

    # Never slept or retried
    assert len(sleep_calls) == 0
    entries = await dlq.list_all()
    assert len(entries) == 1
    assert entries[0]["event_id"] == "evt_permanent"
    assert entries[0]["attempts"] == 1


# ---------------------------------------------------------------------------
# Part A: Health and Readiness Probes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_probes_distinction(monkeypatch):
    transport = ASGITransport(app=app_module.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Normal state
        h = await client.get("/healthz")
        r = await client.get("/readyz")
        assert h.status_code == 200
        assert r.status_code == 200

        # Simulate broken dependency / unready state
        monkeypatch.setattr(app_module, "is_ready", False)
        h_after = await client.get("/healthz")
        r_after = await client.get("/readyz")
        assert h_after.status_code == 200      # Process still running
        assert r_after.status_code == 503      # Ingestion stopped