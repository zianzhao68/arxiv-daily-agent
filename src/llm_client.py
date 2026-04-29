from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_BASE = "https://llm.thundersoft.com/v1/chat/completions"

MAX_RETRIES = 3
BACKOFF_SECONDS = [2, 4, 8]


async def call_llm(
    model: str,
    messages: list[dict],
    temperature: float = 0.1,
    max_tokens: int | None = None,
    api_key: str = "",
    plugins: list[dict] | None = None,
) -> str:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/arxiv-daily-agent",
        "X-Title": "arXiv Daily Papers Agent",
    }
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if plugins is not None:
        payload["plugins"] = plugins

    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=480) as client:
                resp = await client.post(
                    OPENROUTER_BASE, headers=headers, json=payload
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    body = resp.text[:500]
                    raise httpx.HTTPStatusError(
                        f"status {resp.status_code}: {body}",
                        request=resp.request,
                        response=resp,
                    )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                if content is None:
                    raise ValueError(f"Empty content in response: {str(data)[:300]}")
                return content
        except Exception as exc:
            last_exc = exc
            retryable = isinstance(exc, (httpx.HTTPStatusError, httpx.TimeoutException, httpx.ConnectError))
            if retryable and attempt < MAX_RETRIES - 1:
                wait = BACKOFF_SECONDS[attempt]
                logger.warning(
                    "LLM call failed (attempt %d/%d), retrying in %ds: %s: %s",
                    attempt + 1, MAX_RETRIES, wait,
                    type(exc).__name__, exc,
                )
                await asyncio.sleep(wait)
            elif attempt < MAX_RETRIES - 1 and not retryable:
                logger.error("LLM call non-retryable error: %s: %s", type(exc).__name__, exc)
                break
            else:
                logger.error("LLM call failed after %d attempts: %s: %s", MAX_RETRIES, type(exc).__name__, exc)

    raise RuntimeError(f"LLM call failed after {MAX_RETRIES} retries") from last_exc
