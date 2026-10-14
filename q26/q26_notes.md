# Question 26 — Production Webhook Receiver: Architectural & Technical Notes

## 1. Core Endpoints & HTTP Contracts
- **POST /webhooks/events**:
  - 202 Accepted: New valid event accepted for background processing.
  - 200 OK: Duplicate event already registered. Must acknowledge to stop upstream provider retries without re-running actions.
  - 401 Unauthorized: Bad/missing HMAC signature or stale timestamp (> 300s drift).
  - 400 Bad Request: Non-JSON payload or missing/empty \id\ field.
- **GET /healthz**: Liveness probe confirming the event loop and application process are running.
- **GET /readyz**: Readiness probe validating secret presence and operational readiness.
- **GET /dlq**: Exposes quarantined events that permanently failed or exceeded retry budgets.

## 2. Signature Verification & Cryptographic Pitfalls
- **Raw Body vs. Parsed JSON**: HMAC must be computed exclusively over the incoming raw byte stream. Decoding or re-serializing JSON via \json.dumps()\ alters whitespace, dictionary key order, and unicode escapes, causing spurious signature failures.
- **Timing Attack Mitigation**: Uses \hmac.compare_digest()\ for constant-time evaluation to avoid side-channel timing leaks.
- **Replay Attack Defense**: Rejects requests where \bs(now - timestamp) > 300\, ensuring intercepted packets cannot be replayed arbitrarily.

## 3. Idempotency & Concurrency Under Scale
- **In-Memory Concurrency**: Controlled via an atomic \syncio.Lock\ wrapper over an internal store to avoid race conditions when simultaneous duplicate webhooks arrive.
- **Multi-Replica Bottleneck**: An in-process memory store is scoped to a single process. Across 3 replicas behind a load balancer, parallel redeliveries route to different instances without shared state, leading to duplicate execution.
- **Production Solution**: Centralized distributed lock/store using Redis (\SET event_id ... NX EX 86400\) or relational persistence via PostgreSQL (\INSERT INTO ... ON CONFLICT (id) DO NOTHING\).

## 4. Retries, Jitter & Dead-Letter Queue (DLQ)
- **Transient vs. Permanent Errors**:
  - Transient (timeouts, 503s): Retried up to a bounded attempt threshold.
  - Permanent (malformed domain fields, 4xx responses): Dispatched straight to DLQ to avoid wasted cycles.
- **Why Jitter is Mandatory**: Standard exponential backoff causes retries to cluster into synchronized waves. Full jitter (\Uniform(0, min(max_delay, base * 2^attempt))\) spreads retry traffic evenly, eliminating the Thundering Herd problem against recovering downstream services.
