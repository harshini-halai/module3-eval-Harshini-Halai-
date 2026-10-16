import time
import json
import asyncio
from typing import AsyncGenerator
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

try:
    from q27.cache import SemanticCache
    from q27.metrics import GatewayMetrics
    from q27.providers import get_provider
except ModuleNotFoundError:
    from cache import SemanticCache
    from metrics import GatewayMetrics
    try:
        from providers import get_provider
    except ImportError:
        get_provider = None

app = FastAPI(title="SSE Streaming Gateway")
cache = SemanticCache(similarity_threshold=0.85)
metrics = GatewayMetrics() if 'GatewayMetrics' in globals() and GatewayMetrics else None


async def stream_generator(prompt: str, start_time: float) -> AsyncGenerator[str, None]:
    first_token_time = None

    # ---- 1. Cache Hit Path ----
    cached_chunks = cache.lookup(prompt)
    if cached_chunks is not None:
        total_tokens = len(cached_chunks)
        if metrics:
            metrics.record_cost(
                prompt_tokens=len(prompt.split()),
                completion_tokens=total_tokens,
                is_cache_hit=True,
            )

        for chunk in cached_chunks:
            if first_token_time is None:
                first_token_time = time.perf_counter()
            payload = {
                "token": chunk,
                "cached": True,
                "ttft_ms": round((first_token_time - start_time) * 1000.0, 2),
            }
            yield f"data: {json.dumps(payload)}\n\n"
            await asyncio.sleep(0.005)

        if metrics and first_token_time:
            metrics.record_request_timings(
                first_token_time - start_time,
                time.perf_counter() - start_time,
            )
        yield "event: done\ndata: [DONE]\n\n"
        return

    # ---- 2. Cache Miss Path ----
    stored_chunks = []
    provider = get_provider() if callable(get_provider) else None

    try:
        if provider and hasattr(provider, "stream"):
            async for token in provider.stream(prompt):
                if first_token_time is None:
                    first_token_time = time.perf_counter()
                stored_chunks.append(token)
                payload = {
                    "token": token,
                    "cached": False,
                    "ttft_ms": round((first_token_time - start_time) * 1000.0, 2),
                }
                yield f"data: {json.dumps(payload)}\n\n"
        else:
            # Fallback if provider interface is not available
            await asyncio.sleep(0.25)
            first_token_time = time.perf_counter()
            fallback_tokens = [f"{w} " for w in f"AI response for: {prompt}".split()]
            for token in fallback_tokens:
                stored_chunks.append(token)
                payload = {
                    "token": token,
                    "cached": False,
                    "ttft_ms": round((first_token_time - start_time) * 1000.0, 2),
                }
                yield f"data: {json.dumps(payload)}\n\n"
                await asyncio.sleep(0.04)

    except Exception as exc:
        yield f"event: error\ndata: {json.dumps({'error': type(exc).__name__, 'detail': str(exc)})}\n\n"
        return

    if metrics and first_token_time:
        metrics.record_request_timings(
            first_token_time - start_time,
            time.perf_counter() - start_time,
        )
        metrics.record_cost(
            prompt_tokens=len(prompt.split()),
            completion_tokens=len(stored_chunks),
            is_cache_hit=False,
        )

    cache.store(prompt, stored_chunks)
    yield "event: done\ndata: [DONE]\n\n"


@app.get("/v1/stream")
async def stream_get(prompt: str = ""):
    start_time = time.perf_counter()
    headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no"
    }
    return StreamingResponse(stream_generator(prompt, start_time), headers=headers, media_type="text/event-stream")


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    start_time = time.perf_counter()
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
    return StreamingResponse(stream_generator(prompt, start_time), headers=headers, media_type="text/event-stream")


@app.get("/metrics")
@app.get("/v1/metrics")
async def get_metrics():
    if metrics:
        hit_rate = cache.hit_rate() if hasattr(cache, "hit_rate") else 0.0
        return metrics.get_summary(cache_hit_rate=hit_rate)
    return {
        "status": "active",
        "cache": {"hit_rate": cache.hit_rate() if hasattr(cache, "hit_rate") else 0.0}
    }