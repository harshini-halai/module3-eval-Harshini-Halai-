import asyncio
import json
import time
from typing import AsyncGenerator
from fastapi import FastAPI, Query, status
from fastapi.responses import JSONResponse, StreamingResponse

from providers import get_provider, ProviderError
from cache import SemanticCache
from metrics import GatewayMetrics

app = FastAPI(title="SSE Streaming Inference Gateway", version="1.0.0")

# Shared state instances
cache = SemanticCache(similarity_threshold=0.85, max_entries=500, ttl_seconds=3600.0)
metrics = GatewayMetrics()


async def token_streamer(prompt: str) -> AsyncGenerator[str, None]:
    start_time = time.perf_counter()
    first_token_received = False
    ttft = 0.0
    accumulated_tokens = []
    
    # 1. Check Semantic Cache
    cached_completion, sim_score = cache.get(prompt)
    is_cache_hit = cached_completion is not None

    try:
        if is_cache_hit:
            # Yield cached tokens simulating an ultra-fast local stream
            cached_words = [w + " " for w in cached_completion.split(" ")]
            if cached_words:
                cached_words[-1] = cached_words[-1].rstrip(" ")

            for word in cached_words:
                if not first_token_received:
                    first_token_received = True
                    ttft = time.perf_counter() - start_time
                accumulated_tokens.append(word)
                yield f"data: {json.dumps({'token': word})}\n\n"
                await asyncio.sleep(0.001)  # Minimal yield tick
        else:
            # Miss: Call configured provider
            provider = get_provider()
            async for token in provider.stream(prompt):
                if not first_token_received:
                    first_token_received = True
                    ttft = time.perf_counter() - start_time
                accumulated_tokens.append(token)
                yield f"data: {json.dumps({'token': token})}\n\n"

        # Record metrics & cache on successful completion
        total_duration = time.perf_counter() - start_time
        metrics.record_request_timings(ttft, total_duration)

        prompt_token_count = max(1, len(prompt.split()))
        output_token_count = max(1, len(accumulated_tokens))
        metrics.record_cost(prompt_token_count, output_token_count, is_cache_hit)

        if not is_cache_hit:
            full_text = "".join(accumulated_tokens)
            cache.put(prompt, full_text)

        # Explicit terminal event as required by Part A
        yield "event: done\ndata: [DONE]\n\n"

    except ProviderError as pe:
        # Part A requirement: Mid-stream failure cannot change HTTP status (headers sent)
        # We must emit an explicit error SSE event and terminate
        err_payload = json.dumps({"error": str(pe), "status": "provider_failure_mid_stream"})
        yield f"event: error\ndata: {err_payload}\n\n"
    except Exception as exc:
        err_payload = json.dumps({"error": f"Internal gateway failure: {str(exc)}"})
        yield f"event: error\ndata: {err_payload}\n\n"


@app.get("/v1/stream")
async def stream_inference(prompt: str = Query(..., min_length=1)):
    headers = {
        "Content-Type": "text/event-stream; charset=utf-8",
        # Part A: Anti-buffering proxy headers
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",  # Disables Nginx buffering
    }
    return StreamingResponse(token_streamer(prompt), media_type="text/event-stream", headers=headers)


@app.get("/v1/metrics")
async def get_metrics():
    return metrics.get_summary(cache_hit_rate=cache.hit_rate)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
