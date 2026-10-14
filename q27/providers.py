import abc
import asyncio
import os
from typing import AsyncGenerator, Optional


class ProviderError(Exception):
    """Raised when an LLM provider encounters an error mid-stream or during startup."""
    pass


class BaseLLMProvider(abc.ABC):
    @abc.abstractmethod
    async def stream(self, prompt: str) -> AsyncGenerator[str, None]:
        """Yields string token chunks one by one."""
        pass


class MockLLMProvider(BaseLLMProvider):
    def __init__(
        self,
        first_token_delay: float = 0.25,
        inter_token_delay: float = 0.05,
        simulated_response: Optional[str] = None,
        fail_mid_stream_after_tokens: Optional[int] = None,
    ):
        # Separated as per Part B: collapsing them makes TTFT vs Total Duration untestable
        self.first_token_delay = first_token_delay
        self.inter_token_delay = inter_token_delay
        self.simulated_response = simulated_response
        self.fail_mid_stream_after_tokens = fail_mid_stream_after_tokens

    async def stream(self, prompt: str) -> AsyncGenerator[str, None]:
        # Realistic TTFT delay before yielding the very first token
        if self.first_token_delay > 0:
            await asyncio.sleep(self.first_token_delay)

        if self.simulated_response is not None:
            text = self.simulated_response
        else:
            text = f"Simulated streaming completion for: '{prompt}'. Generating token-by-token sequence."

        # Tokenize by words with trailing spaces to simulate authentic LLM chunking
        tokens = [word + " " for word in text.split(" ")]
        if tokens:
            tokens[-1] = tokens[-1].rstrip(" ")

        for idx, token in enumerate(tokens):
            if self.fail_mid_stream_after_tokens is not None and idx == self.fail_mid_stream_after_tokens:
                raise ProviderError("Upstream provider socket terminated mid-generation.")

            yield token

            # Paced inter-token delay
            if self.inter_token_delay > 0:
                await asyncio.sleep(self.inter_token_delay)


class RealLLMProvider(BaseLLMProvider):
    """
    Stub for a real remote provider (e.g., OpenAI/Anthropic).
    Only used when API key is present in environment.
    """
    def __init__(self, api_key: str):
        self.api_key = api_key

    async def stream(self, prompt: str) -> AsyncGenerator[str, None]:
        # Offline safe placeholder if called without active network
        yield f"[RealProvider-Stub] Authenticated response for prompt: {prompt}"


def get_provider(
    first_token_delay: float = 0.25,
    inter_token_delay: float = 0.05,
    simulated_response: Optional[str] = None,
    fail_mid_stream_after_tokens: Optional[int] = None,
) -> BaseLLMProvider:
    """
    Factory selection based solely on configuration (no code edits).
    """
    api_key = os.getenv("LLM_API_KEY")
    if api_key and api_key.strip():
        return RealLLMProvider(api_key=api_key.strip())

    return MockLLMProvider(
        first_token_delay=first_token_delay,
        inter_token_delay=inter_token_delay,
        simulated_response=simulated_response,
        fail_mid_stream_after_tokens=fail_mid_stream_after_tokens,
    )
