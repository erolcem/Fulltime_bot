"""
llm_client.py
-------------
Thin async wrapper around the Gemini SDK that:
  - serialises every call through a SlidingWindowRateLimiter
  - retries with exponential backoff on transient failures
  - optionally requests JSON-only responses
  - detects Daily Quota limits and fatally aborts to prevent endless retry loops.
"""

import asyncio
import logging
import sys
from typing import Optional

from google import genai
from google.genai import types

from rate_limiter import SlidingWindowRateLimiter

log = logging.getLogger(__name__)


class GeminiClient:
    def __init__(self, api_key: str, model_name: str, rpm: int):
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
                msg = str(e).lower()
                
                # --- The Daily Quota Killswitch ---
                if "quota" in msg and ("day" in msg or "daily" in msg):
                    log.critical(
                        "FATAL: Gemini Daily Quota exhausted for model %s. Aborting pipeline.", 
                        self.model_name
                    )
                    # We exit the entire program safely so the user isn't stuck 
                    # in an infinite loop of failing tasks.
                    sys.exit(1)
                # ----------------------------------

                is_quota = "quota" in msg or "rate" in msg or "429" in msg or "resource_exhausted" in msg
                wait = (5 if is_quota else 2) ** attempt
                
                log.warning(
                    "Gemini call failed for %s (attempt %d/%d): %s. Retrying in %ds",
                    self.model_name, attempt, max_retries, e, wait,
                )
                await asyncio.sleep(wait)

        raise RuntimeError(f"Gemini call failed for {self.model_name} after {max_retries} attempts: {last_err}")