"""Thin wrapper around the Anthropic SDK: structured output, cost accounting,
prompt caching and typed failures. The SDK already retries 429/5xx with backoff
(max_retries), so we don't layer our own retry loop on top."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Generic, TypeVar

import anthropic
from pydantic import BaseModel

from .config import PRICES

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """Any failure that should send the ticket to a human instead of crashing."""


@dataclass
class LLMResult(Generic[T]):
    parsed: T
    model: str
    latency_ms: int
    cost_usd: float
    request_id: str | None


def cost_of(model: str, usage: anthropic.types.Usage) -> float:
    price_in, price_out, price_cache_read = PRICES.get(model, (0.0, 0.0, 0.0))
    cache_write = (usage.cache_creation_input_tokens or 0) * price_in * 1.25
    cache_read = (usage.cache_read_input_tokens or 0) * price_cache_read
    return (usage.input_tokens * price_in + usage.output_tokens * price_out + cache_write + cache_read) / 1e6


class LLM:
    def __init__(self, client: anthropic.Anthropic | None = None) -> None:
        self.client = client or anthropic.Anthropic(max_retries=3, timeout=120)

    def structured(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: type[T],
        effort: str = "medium",
        max_tokens: int = 8000,
    ) -> LLMResult[T]:
        start = time.perf_counter()
        try:
            resp = self.client.messages.parse(
                model=model,
                max_tokens=max_tokens,
                # The system prompt is identical across tickets -> cache it.
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": user}],
                output_config={"effort": effort},
                output_format=schema,
            )
        except anthropic.APIConnectionError as e:
            raise LLMError(f"connection error: {e}") from e
        except anthropic.RateLimitError as e:
            raise LLMError("rate limited after retries") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"API error {e.status_code}: {e.message}") from e

        latency_ms = int((time.perf_counter() - start) * 1000)
        if resp.stop_reason == "refusal":
            category = resp.stop_details.category if resp.stop_details else None
            raise LLMError(f"model refused (category={category})")
        if resp.stop_reason == "max_tokens" or resp.parsed_output is None:
            raise LLMError(f"no structured output (stop_reason={resp.stop_reason})")

        return LLMResult(
            parsed=resp.parsed_output,
            model=model,
            latency_ms=latency_ms,
            cost_usd=cost_of(model, resp.usage),
            request_id=getattr(resp, "_request_id", None),
        )
