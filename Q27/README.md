# Question 27 — SSE Inference Gateway & Cost Accounting

A resilient Server-Sent Events (SSE) inference gateway with anti-buffering headers, semantic caching, TTFT tracking, and asymmetric token billing.

---

## Architecture Flow

Client -> GET /v1/stream?prompt=... -> FastAPI Gateway
  |-- Cache Check (Cosine >= 0.70): HIT -> instant completion stream
  \-- Provider Stream (MISS): Token-by-token chunks -> 'event: done'

---

## Key Design Decisions

- Anti-Buffering Headers:
  - Content-Type: text/event-stream; charset=utf-8
  - Cache-Control: no-cache, no-transform
  - Connection: keep-alive
  - X-Accel-Buffering: no (disables reverse proxy buffering)
- Terminal Event: Emits explicit 'event: done' with data '[DONE]' to separate completed streams from severed sockets.
- Mid-Stream Failures: HTTP 200 commits before streaming; mid-stream errors emit 'event: error' chunk instead of rewriting status codes.
- Latency Decoupling: Separates first_token_delay and inter_token_delay to strictly verify TTFT != duration.
- Semantic Cache: Threshold 0.70 empirically separates paraphrases (>0.76) from distinct queries (<=0.42).
- Cost Accounting: Split billing (.0015 input / .0020 output per 1k tokens) for accurate ROI tracking.

---

## Local Run & Verification

1. Run Tests:
   pytest Q27/test_gateway.py -v

2. Start Server:
   uvicorn app:app --host 127.0.0.1 --port 8000

3. Run Client:
   python mock_client.py

4. Fetch Metrics:
   curl http://127.0.0.1:8000/v1/metrics
