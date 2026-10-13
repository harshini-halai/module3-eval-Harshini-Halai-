import asyncio
import json
import time
import httpx


async def consume_sse_stream(base_url: str, prompt: str):
    print(f"\n[CLIENT] Requesting prompt: '{prompt}'")
    url = f"{base_url}/v1/stream"
    
    start_time = time.perf_counter()
    first_token_time = None
    tokens = []
    terminal_event_received = False
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        async with client.stream("GET", url, params={"prompt": prompt}) as response:
            assert response.status_code == 200, f"Expected 200, got {response.status_code}"
            assert "text/event-stream" in response.headers.get("content-type", "")
            
            buffer = ""
            async for chunk in response.aiter_text():
                buffer += chunk
                while "\n\n" in buffer:
                    event_block, buffer = buffer.split("\n\n", 1)
                    if not event_block.strip():
                        continue
                    
                    lines = event_block.strip().split("\n")
                    event_type = "message"
                    data_str = ""
                    for line in lines:
                        if line.startswith("event:"):
                            event_type = line[len("event:"):].strip()
                        elif line.startswith("data:"):
                            data_str = line[len("data:"):].strip()
                    
                    if event_type == "message" and data_str:
                        if first_token_time is None:
                            first_token_time = time.perf_counter()
                        try:
                            payload = json.loads(data_str)
                            token = payload.get("token", "")
                            tokens.append(token)
                        except Exception:
                            tokens.append(data_str)
                    elif event_type == "done":
                        terminal_event_received = True

    end_time = time.perf_counter()
    client_ttft = (first_token_time - start_time) if first_token_time else 0.0
    total_duration = end_time - start_time
    
    print(f" -> Client-Side Measured TTFT : {client_ttft * 1000:.2f} ms")
    print(f" -> Total Stream Duration    : {total_duration * 1000:.2f} ms")
    print(f" -> Total Tokens Received    : {len(tokens)}")
    print(f" -> Terminal Event Verified  : {terminal_event_received}")
    print(f" -> Full Text Reconstructed  : {''.join(tokens)}")
    
    return {
        "ttft": client_ttft,
        "total_duration": total_duration,
        "token_count": len(tokens),
        "completed": terminal_event_received,
    }


async def main():
    base_url = "http://127.0.0.1:8000"
    print("==================================================")
    print("   Starting SSE Gateway Mock Client Harness       ")
    print("==================================================")

    # 1. Cold Request (Cache Miss -> invokes mock provider with TTFT delay)
    p1 = "What is the capital of France?"
    r1 = await consume_sse_stream(base_url, p1)

    # 2. Semantic Paraphrase (Cache Hit -> fast streaming from cache)
    p2 = "Tell me France's capital city"
    r2 = await consume_sse_stream(base_url, p2)

    # 3. Fetch Gateway Metrics
    async with httpx.AsyncClient() as client:
        res = await client.get(f"{base_url}/v1/metrics")
        print("\n--- Current Gateway Metrics (/v1/metrics) ---")
        print(json.dumps(res.json(), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
