import asyncio
import json
import time
from typing import AsyncGenerator, Optional
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

# Robust imports
try:
    from q27.cache import SemanticCache
except ModuleNotFoundError:
    from cache import SemanticCache

try:
    from q27.metrics import GatewayMetrics
except (ImportError, ModuleNotFoundError):
    try:
        from metrics import GatewayMetrics
    except (ImportError, ModuleNotFoundError):
        GatewayMetrics = None

try:
    from q27.providers import get_provider, ProviderError
except (ImportError, ModuleNotFoundError):
    try:
        from providers import get_provider, ProviderError
    except (ImportError, ModuleNotFoundError):
        get_provider = None
        ProviderError = Exception

# Initialize FastAPI application
app = FastAPI(
    title="Question 27 - SSE Streaming Gateway with Semantic Cache",
    version="1.0.0"
)

# Global in-memory cache and metrics
try:
    cache = SemanticCache(similarity_threshold=0.85, max_entries=100)
except TypeError:
    cache = SemanticCache()

metrics = GatewayMetrics() if GatewayMetrics else None


async def stream_generator(prompt: str) -> AsyncGenerator[str, None]:
    start_time = time.perf_counter()
    first_token_time = None

    # Step 1: Semantic Cache Lookup (Cosine Similarity >= 0.85)
    cached_chunks = cache.lookup(prompt)
    if cached_chunks is not None:
        ttft = (time.perf_counter() - start_time) * 1000.0
        total_tokens = len(cached_chunks)

        if metrics:
            metrics.record_request_timings(ttft / 1000.0, ttft / 1000.0)
            metrics.record_cost(
                prompt_tokens=len(prompt.split()),
                completion_tokens=total_tokens,
                is_cache_hit=True
            )

        for chunk in cached_chunks:
            payload = {
                "token": chunk,
                "cached": True,
                "ttft_ms": round(ttft, 2)
            }
            yield f"data: {json.dumps(payload)}\n\n"
            await asyncio.sleep(0.005)

        yield "event: done\ndata: [DONE]\n\n"
        return

    # Step 2: Cache Miss -> LLM Inference Simulation
    stored_chunks = []
    first_token = True

    if get_provider:
        try:
            provider = get_provider("mock")
            token_stream = provider.generate_stream(prompt)
        except Exception:
            token_stream = [f"{w} " for w in f"AI generated complete response for: {prompt}".split()]
    else:
        token_stream = [f"{w} " for w in f"AI generated complete response for: {prompt}".split()]

    for token in token_stream:
        if first_token:
            first_token_time = time.perf_counter()
            first_token = False

        stored_chunks.append(token)
        current_ttft = ((first_token_time - start_time) * 1000.0) if first_token_time else 0.0

        payload = {
            "token": token,
            "cached": False,
            "ttft_ms": round(current_ttft, 2)
        }
        yield f"data: {json.dumps(payload)}\n\n"
        await asyncio.sleep(0.04)

    end_time = time.perf_counter()
    if metrics and first_token_time:
        metrics.record_request_timings(first_token_time - start_time, end_time - start_time)
        metrics.record_cost(
            prompt_tokens=len(prompt.split()),
            completion_tokens=len(stored_chunks),
            is_cache_hit=False
        )

    # Step 3: Store in Semantic Cache
    cache.store(prompt, stored_chunks)
    yield "event: done\ndata: [DONE]\n\n"


# Endpoint 1: GET /v1/stream (Browser / Query Param testing)
@app.get("/v1/stream")
async def stream_get(prompt: str = ""):
    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no"
    }
    return StreamingResponse(stream_generator(prompt), headers=headers, media_type="text/event-stream")


# Endpoint 2: POST /v1/chat/completions (Standard OpenAI API format)
@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    try:
        body = await request.json()
        prompt = body.get("prompt", "")
    except Exception:
        prompt = ""

    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no"
    }
    return StreamingResponse(stream_generator(prompt), headers=headers, media_type="text/event-stream")


# Endpoint 3: GET /metrics (Live Dashboard)
@app.get("/metrics")
async def get_metrics():
    if metrics:
        total = metrics.actual_spend + metrics.avoided_spend
        hit_rate = (metrics.avoided_spend / total) if total > 0 else 0.0
        return metrics.get_summary(cache_hit_rate=hit_rate)
    return {
        "status": "active",
        "cached_entries": len(getattr(cache, "entries", []))
    }

# Keep alias
gateway = app