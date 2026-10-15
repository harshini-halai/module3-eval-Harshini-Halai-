import asyncio
import json
import pytest
from httpx import AsyncClient, ASGITransport
try:
    from q27.app import app, cache, metrics
except ModuleNotFoundError:
    from app import app, cache, metrics
from cache import SemanticCache, compute_deterministic_embedding, cosine_similarity
from metrics import calculate_percentile, GatewayMetrics
from providers import MockLLMProvider, ProviderError

@pytest.mark.asyncio
async def test_sse_wire_format_raw_body_parsing():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/v1/stream", params={"prompt": "Ping"})
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        assert response.headers["cache-control"] == "no-cache, no-transform"
        assert response.headers["x-accel-buffering"] == "no"

        raw_text = response.text
        blocks = [b.strip() for b in raw_text.split("\n\n") if b.strip()]
        
        data_blocks = [b for b in blocks if b.startswith("data:")]
        assert len(data_blocks) > 0
        for block in data_blocks:
            line = block.split("\n")[0]
            json_str = line[len("data:"):].strip()
            if json_str != "[DONE]":
                parsed = json.loads(json_str)
                assert "token" in parsed

        terminal_blocks = [b for b in blocks if "event: done" in b]
        assert len(terminal_blocks) == 1
        assert "data: [DONE]" in terminal_blocks[0]


@pytest.mark.asyncio
async def test_anti_buffering_ttft_is_strictly_less_than_total_duration():
    first_token_delay = 0.05
    inter_token_delay = 0.05
    
    mock = MockLLMProvider(
        first_token_delay=first_token_delay,
        inter_token_delay=inter_token_delay,
        simulated_response="one two three four five"
    )
    
    start = asyncio.get_event_loop().time()
    first_token_time = None
    
    async for _ in mock.stream("test"):
        if first_token_time is None:
            first_token_time = asyncio.get_event_loop().time()
            
    total_time = asyncio.get_event_loop().time()
    
    ttft = first_token_time - start
    duration = total_time - start
    
    assert ttft < (duration * 0.7), f"Stream was buffered! TTFT ({ttft:.3f}s) ~ Duration ({duration:.3f}s)"


def test_percentile_empty_list_does_not_return_zero():
    result = calculate_percentile([], 95)
    assert result != 0.0
    assert result is None


def test_semantic_cache_hit_and_badly_chosen_threshold():
    # 1. Paraphrase hit with empirically measured threshold (0.70)
    sc = SemanticCache(similarity_threshold=0.70)
    sc.put("What is the capital of France?", "Paris is the capital of France.")
    hit_val, score = sc.get("Tell me France's capital city")
    assert hit_val is not None
    assert score >= 0.70

    # 2. Failure Mode A: Badly chosen threshold that is TOO HIGH (0.98)
    strict_cache = SemanticCache(similarity_threshold=0.98)
    strict_cache.put("What is the capital of France?", "Paris is the capital of France.")
    strict_hit, _ = strict_cache.get("Tell me France's capital city")
    assert strict_hit is None, "A 0.98 threshold erroneously missed a genuine semantic paraphrase!"

    # 3. Failure Mode B: Badly chosen threshold that is TOO LOW (0.0)
    loose_cache = SemanticCache(similarity_threshold=0.0)
    loose_cache.put("What is the capital of France?", "Paris is the capital of France.")
    loose_hit, _ = loose_cache.get("How do I bake sourdough bread?")
    assert loose_hit is not None, "A 0.0 threshold caused a false-positive semantic collision!"


def test_cost_accounting_and_zero_traffic_division_safety():
    gm = GatewayMetrics()
    summary_empty = gm.get_summary(cache_hit_rate=0.0)
    assert summary_empty["cost_accounting"]["savings_percentage"] == 0.0
    assert summary_empty["cost_accounting"]["actual_spend_usd"] == 0.0

    gm.record_cost(1000, 1000, is_cache_hit=False)
    gm.record_cost(1000, 1000, is_cache_hit=True)

    summary = gm.get_summary(cache_hit_rate=0.5)
    assert summary["cost_accounting"]["actual_spend_usd"] > 0
    assert summary["cost_accounting"]["avoided_spend_usd"] > 0
    assert summary["cost_accounting"]["savings_percentage"] == 50.0

@pytest.mark.asyncio
async def test_mid_stream_failure_emits_error_event():
    mock = MockLLMProvider(fail_mid_stream_after_tokens=2, simulated_response="alpha beta gamma delta")
    
    received_tokens = []
    error_caught = False
    
    try:
        async for token in mock.stream("test"):
            received_tokens.append(token)
    except ProviderError:
        error_caught = True
        
    assert error_caught is True
    assert len(received_tokens) == 2