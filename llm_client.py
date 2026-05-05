"""
llm_client.py
-------------
Thin async wrapper around the Gemini SDK that:
  - serialises every call through a SlidingWindowRateLimiter
  - retries with exponential backoff on transient failures
  - optionally requests JSON-only responses

Uses the modern `google-genai` SDK.

Each GeminiClient instance owns its own rate limiter, so you can construct
multiple clients (e.g. one per Gemini model) and have them gate independently.
That's important because each model has its own RPM bucket on the free tier.

Every Gemini call in the codebase MUST go through `GeminiClient.generate(...)`
so rate limiting stays authoritative.
"""

import asyncio
import logging
from typing import Optional

from google import genai
from google.genai import types

from rate_limiter import SlidingWindowRateLimiter

log = logging.getLogger(__name__)


class GeminiClient:
    def __init__(self, api_key: str, model_name: str, rpm: int):
        # New SDK: a single Client object owns auth and exposes both sync and
        # async surfaces. `client.aio.models.*` is the async surface.
        self._client = genai.Client(api_key=api_key)
        self.model_name = model_name
        self.limiter = SlidingWindowRateLimiter(max_calls=rpm, window_seconds=60.0)

    async def generate(
        self,
        prompt: str,
        *,
        expect_json: bool = False,
        max_retries: int = 3,
        timeout_s: float = 60.0,
    ) -> str:
        """
        Generate a completion. Always rate-limited.

        Args:
            prompt: full prompt text.
            expect_json: if True, asks Gemini to emit application/json.
            max_retries: total attempts (including the first).
            timeout_s: per-attempt timeout in seconds.

        Returns:
            The model's text response (stripped).
        """
        # Build the optional config once per call.
        config: Optional[types.GenerateContentConfig] = None
        if expect_json:
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
            )

        last_err: Optional[Exception] = None
        for attempt in range(1, max_retries + 1):
            await self.limiter.acquire()
            try:
                response = await asyncio.wait_for(
                    self._client.aio.models.generate_content(
                        model=self.model_name,
                        contents=prompt,
                        config=config,
                    ),
                    timeout=timeout_s,
                )
                text = (response.text or "").strip()
                if not text:
                    raise RuntimeError("Empty response from Gemini")
                return text
            except Exception as e:
                last_err = e
                # If we've been rate-limited despite the limiter, back off harder.
                msg = str(e).lower()
                is_quota = "quota" in msg or "rate" in msg or "429" in msg or "resource_exhausted" in msg
                wait = (5 if is_quota else 2) ** attempt
                log.warning(
                    "Gemini call failed (attempt %d/%d): %s. Retrying in %ds",
                    attempt, max_retries, e, wait,
                )
                await asyncio.sleep(wait)

        raise RuntimeError(f"Gemini call failed after {max_retries} attempts: {last_err}")