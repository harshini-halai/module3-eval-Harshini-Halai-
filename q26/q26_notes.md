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
- **Why Jitter is Mandatory**: Standard exponential backoff causes retries to cluster into synchronized waves. Full jitter (\Uniform(0, min(max_delay, base \* 2^attempt))\) spreads retry traffic evenly, eliminating the Thundering Herd problem against recovering downstream services.

## Setup & Execution Guide (macOS & Windows)

This document provides step-by-step setup, configuration, testing, and execution instructions for **Question 26: Production Webhook Receiver** on both **macOS** and **Windows (PowerShell)**.

---

## 1. Prerequisites Check

### Step 1: Open the Terminal / Shell

- **macOS:**
  1. Press `Cmd + Space` to open **Spotlight Search**.
  2. Type `Terminal` and press `Enter`.
- **Windows:**
  1. Press `Win + S` to open the search bar.
  2. Type `PowerShell` and click **Windows PowerShell**.

### Step 2: Verify Python 3 & Git

- **macOS:**

  ```bash
  python3 --version
  git --version
  ```

  _(If Python is missing, macOS will prompt to install Xcode Command Line Tools. Click **Install**)._

- **Windows:**

  ```powershell
  python --version
  git --version
  ```

  _(If Python is not recognized, install Python 3.11+ from [python.org](https://www.python.org/) or Microsoft Store and ensure "Add Python to PATH" is checked)._

---

## 2. Clone Repository & Setup Virtual Environment

### Step 3: Clone the Repository

- **macOS:**

  ```bash
  git clone https://github.com/harshini-halai/module3-eval-Harshini-Halai-.git
  cd module3-eval-Harshini-Halai-
  ```

- **Windows (PowerShell):**

  ```powershell
  git clone https://github.com/harshini-halai/module3-eval-Harshini-Halai-.git
  cd module3-eval-Harshini-Halai-
  ```

### Step 4: Create and Activate Virtual Environment

- **macOS:**

  ```bash
  python3 -m venv venv
  source venv/bin/activate
  ```

  _(Terminal prompt will prefix with `(venv)`)._

- **Windows (PowerShell):**

  ```powershell
  python -m venv venv
  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
  .\venv\Scripts\Activate.ps1
  ```

  _(PowerShell prompt will prefix with `(venv)`)._

---

## 3. Install Dependencies & Configure `.env`

### Step 5: Install Requirements

- **macOS:**

  ```bash
  pip install --upgrade pip
  pip install -r requirements.txt
  ```

- **Windows (PowerShell):**

  ```powershell
  python -m pip install --upgrade pip
  pip install -r requirements.txt
  ```

### Step 6: Create Environment Configuration (`.env`)

- **macOS:**

  ```bash
  cat <<EOF > .env
  WEBHOOK_SECRET=prod_webhook_signing_secret_xyz123
  WEBHOOK_URL=http://127.0.0.1:8000/webhooks/events
  EOF
  ```

- **Windows (PowerShell):**

  ```powershell
  @"
  WEBHOOK_SECRET=prod_webhook_signing_secret_xyz123
  WEBHOOK_URL=http://127.0.0.1:8000/webhooks/events
  "@ | Out-File -FilePath .env -Encoding utf8
  ```

---

## 4. Run Automated Test Suite (Evaluation Step)

Run the full pytest suite verifying HMAC-SHA256 crypto, timestamp tolerance, idempotency deduplication, and exponential backoff retry with DLQ:

- **macOS:**

  ```bash
  export PYTHONPATH="."
  pytest q26/test_service.py -v
  ```

- **Windows (PowerShell):**

  ```powershell
  $env:PYTHONPATH="."
  python -m pytest q26/test_service.py -v
  


### Expected Test Results

q26/test_service.py::test_signature_preserves_raw_byte_invariance PASSED
q26/test_service.py::test_signature_replay_attack_rejected PASSED
q26/test_service.py::test_concurrent_duplicate_deliveries_race_condition PASSED
q26/test_service.py::test_exponential_backoff_and_jitter_bounds PASSED
q26/test_service.py::test_resilience_pipeline_transient_retry_and_dlq PASSED
q26/test_service.py::test_resilience_pipeline_permanent_error_bypasses_retry PASSED
q26/test_service.py::test_probes_distinction PASSED
============================== 7 passed in 0.XXs ==============================

## 5. Live Receiver & Mock Sender Execution

To demonstrate end-to-end HTTP deliveries across two terminals:

### Terminal 1: Launch Webhook Receiver Server

- **macOS:**

  ```bash
  export PYTHONPATH="."
  python3 -m uvicorn q26.app:app --port 8000 --reload
  ```

- **Windows (PowerShell):**

  ```powershell
  $env:PYTHONPATH="."
  python -m uvicorn q26.app:app --port 8000 --reload
  ```

  _(Status: `Uvicorn running on http://127.0.0.1:8000`)_

---

### Terminal 2: Run Mock Payment Gateway Sender

Open a second terminal window or tab (`Cmd + T` on Mac / New Tab in Windows Terminal):

- **macOS:**

  ```bash
  cd module3-eval-Harshini-Halai-
  source venv/bin/activate
  export PYTHONPATH="."
  python3 q26/mock_sender.py
  ```

- **Windows (PowerShell):**

  ```powershell
  cd module3-eval-Harshini-Halai-
  .\venv\Scripts\Activate.ps1
  $env:PYTHONPATH="."
  python q26/mock_sender.py
  ```

### Expected Output for All 7 Scenarios

```text
--- Scenario: 1. Valid, new event (Expect 2xx) ---
Status Code : 202
Response    : {"status":"accepted","id":"evt_prod_001"}

--- Scenario: 2. Valid, duplicate event redelivery (Expect 2xx) ---
Status Code : 200
Response    : {"status":"ok","message":"Duplicate event acknowledged with status PROCESSING"}

--- Scenario: 3. Tampered payload body (Expect 401) ---
Status Code : 401
Response    : {"error":"Signature digest mismatch"}

--- Scenario: 4. Stale timestamp / Replay attack (Expect 401) ---
Status Code : 401
Response    : {"error":"Webhook timestamp outside tolerance window"}

--- Scenario: 5. Signed with wrong secret (Expect 401) ---
Status Code : 401
Response    : {"error":"Signature digest mismatch"}

--- Scenario: 6. Body that isn't JSON (Expect 400) ---
Status Code : 400
Response    : {"error":"Malformed JSON body"}

--- Scenario: 7. Valid JSON missing 'id' (Expect 400) ---
Status Code : 400
Response    : {"error":"Missing event id"}
```
