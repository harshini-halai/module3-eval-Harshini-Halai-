# Question 27: SSE Streaming Gateway & Semantic Cache Execution Guide

This document outlines the step-by-step instructions for setting up, running, testing, and demonstrating **Question 27: Server-Sent Events (SSE) Streaming Gateway with Semantic Caching** across both **macOS** and **Windows**.

---

## 1. Environment & Path Setup

Ensure that the project's virtual environment is activated and the root project directory is exposed to the Python runtime path.

### macOS (bash / zsh)

```bash
# Navigate to the repository root
cd /path/to/module3-eval-Harshini-Halai-

# Activate virtual environment
source venv/bin/activate

# Set python path to current root directory
export PYTHONPATH="."
```

### Windows (PowerShell) — Environment Setup

```powershell
# Navigate to the repository root
cd C:\path\to\module3-eval-Harshini-Halai-

# Activate virtual environment
.\venv\Scripts\Activate.ps1

# Set python path to current root directory
$env:PYTHONPATH="."
```

---

## 2. Automated Test Suite (Evaluation Step)

Run the full pytest suite verifying SSE stream framing, cosine similarity calculations, semantic cache hits, TTFT optimization, and cold LLM fallbacks:

### macOS

```bash
pytest q27/test_gateway.py -v
```

### Windows (PowerShell) — Automated Tests

```powershell
python -m pytest q27/test_gateway.py -v
```

### Expected Output

```text
q27/test_gateway.py::test_sse_wire_format_raw_body_parsing PASSED
q27/test_gateway.py::test_anti_buffering_ttft_is_strictly_less_than_total_duration PASSED
q27/test_gateway.py::test_percentile_empty_list_does_not_return_zero PASSED
q27/test_gateway.py::test_semantic_cache_hit_and_badly_chosen_threshold PASSED
q27/test_gateway.py::test_cost_accounting_and_zero_traffic_division_safety PASSED
q27/test_gateway.py::test_mid_stream_failure_emits_error_event PASSED
============================== 6 passed in 0.XXs ==============================
```

---

## 3. Live Server & Interactive Client Verification

To evaluate real-time token streaming and dynamic semantic caching across two terminal instances:

### Terminal 1: Start the SSE Streaming Gateway

#### Start the Gateway on macOS

```bash
export PYTHONPATH="."
python3 -m uvicorn q27.app:app --port 8000 --reload
````

### Windows (PowerShell) — Start the SSE Gateway

#### Windows (PowerShell)

```powershell
$env:PYTHONPATH="."
python -m uvicorn q27.app:app --port 8000 --reload
```

_Expected Server Status:_
`INFO: Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)`

---

### Terminal 2: Stream Requests & Observe Semantic Cache Hits

#### Windows (PowerShell) — Start Gateway

#### Step 3A: Cold Query (Simulated Inference & Cache Warm-Up)

- **macOS (curl with unbuffered stream `-N`):**

  ```bash
       -H "Content-Type: application/json" \
       -d '{"prompt": "What is Python programming"}'
  ```

- **Windows (PowerShell):**

  ```powershell
    -Method Post `
    -ContentType "application/json" `
    -Body '{"prompt": "What is Python programming"}'
  ```

- **Expected Behavior:**
  - Tokens stream sequentially with simulated generation latency (~40ms per token).
  - Stream payloads display `"cached": false`.

---

#### Step 3B: Semantically Equivalent Query (Cache Hit)

Send a rephrased query with identical intent:

- **macOS (curl):**

  ```bash
       -H "Content-Type: application/json" \
       -d '{"prompt": "Explain Python programming language"}'
  ```

- **Windows (PowerShell):**

  ```powershell
    -Method Post `
    -ContentType "application/json" `
    -Body '{"prompt": "Explain Python programming language"}'
  ```

- **Expected Behavior:**
  - Cosine similarity algorithm matches the vector with score $\ge 0.85$.
  - Downstream LLM inference is completely bypassed.
  - TTFT drops to near-zero ($< 5\text{ms}$).
  - Stream concludes cleanly with `event: done` and data `[DONE]`.
