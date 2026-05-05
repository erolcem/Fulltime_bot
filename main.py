"""
main.py
-------
Pipeline orchestrator. Run with: `python main.py`

Flow:
  1. Load config (env keys, models, queries, company lists).
  2. Load processed-jobs state from disk.
  3. Open a single aiohttp session shared by all sources.
  4. Phase 1: ingest jobs from all enabled sources in parallel, dedup by content hash.
  5. For each new job:
       Phase 2 (evaluator)   - flash-lite, 15 RPM, 1000 RPD
       Phase 3 (synthesizer) - flash, 10 RPM, 250 RPD (only on MATCH)
       Phase 4 (write to ledger)
       Save state after every job so progress is durable.

Rate limiting: each Gemini model has its own RPM quota on the free tier, so
we use TWO GeminiClient instances - one per task. Each owns its own
SlidingWindowRateLimiter and they don't interfere with each other.
"""

import asyncio
import logging
import sys

import aiohttp

from config import load_config
from evaluator import evaluate
from ingestion import (
    AdzunaSource,
    GreenhouseSource,
    JobSource,
    LeverSource,
    SerpApiSource,
    collect_jobs,
)
from ledger import Ledger
from llm_client import GeminiClient
from state import ProcessedState
from synthesizer import synthesize

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pipeline")


async def run() -> None:
    cfg = load_config()
    log.info(
        "Pipeline starting | evaluator=%s (%d RPM) | synthesizer=%s (%d RPM) | location=%s",
        cfg.evaluator_model, cfg.evaluator_rpm,
        cfg.synthesizer_model, cfg.synthesizer_rpm,
        cfg.search_location,
    )

    state = ProcessedState(cfg.state_path)
    log.info("State: %d jobs already processed in prior runs", len(state.ids))

    # Two clients: each owns its own rate limiter, gating its own RPM bucket.
    eval_llm = GeminiClient(cfg.gemini_api_key, cfg.evaluator_model, cfg.evaluator_rpm)
    synth_llm = GeminiClient(cfg.gemini_api_key, cfg.synthesizer_model, cfg.synthesizer_rpm)

    ledger = Ledger(cfg.output_path)

    async with aiohttp.ClientSession() as session:
        sources: list[JobSource] = []
        if cfg.use_adzuna:
            sources.append(AdzunaSource(
                app_id=cfg.adzuna_app_id,
                app_key=cfg.adzuna_app_key,
                session=session,
                queries=cfg.queries,
                location=cfg.search_location,
                country=cfg.adzuna_country,
                max_days_old=cfg.adzuna_max_days_old,
            ))
        if cfg.use_greenhouse:
            sources.append(GreenhouseSource(
                session=session,
                companies=cfg.greenhouse_companies,
                title_keywords=cfg.title_keywords,
                location_filters=cfg.location_filters,
            ))
        if cfg.use_lever:
            sources.append(LeverSource(
                session=session,
                companies=cfg.lever_companies,
                title_keywords=cfg.title_keywords,
                location_filters=cfg.location_filters,
            ))
        if cfg.use_serpapi:
            sources.append(SerpApiSource(
                api_key=cfg.serpapi_key,
                session=session,
                queries=cfg.queries,
                location=cfg.search_location,
                gl=cfg.serpapi_gl,
                google_domain=cfg.serpapi_google_domain,
            ))
        log.info("Sources enabled: %s", [s.name for s in sources])

        # --- Phase 1: ingestion (all sources in parallel) -----------------
        log.info("--- Phase 1: ingestion ---")
        jobs = await collect_jobs(sources)
        new_jobs = [j for j in jobs if not state.has(j.job_id)]
        log.info(
            "%d new jobs to evaluate (%d skipped as already processed)",
            len(new_jobs), len(jobs) - len(new_jobs),
        )
        if not new_jobs:
            log.info("Nothing new. Exiting.")
            return

        # --- Phases 2 -> 3 -> 4 ------------------------------------------
        log.info("--- Phases 2-4: evaluate, synthesise, write ---")
        matched = 0
        for i, job in enumerate(new_jobs, 1):
            log.info("[%d/%d] %s @ %s", i, len(new_jobs), job.title, job.company)
            try:
                is_match = await evaluate(eval_llm, job)
                if is_match:
                    summary, letter = await synthesize(synth_llm, job)
                    ledger.append(job, summary, letter)
                    matched += 1
                state.add(job.job_id)
                state.save()
            except Exception as e:
                log.error("  Failed processing %s: %s", job.title, e)
                continue

        log.info(
            "Pipeline complete | matched=%d/%d | ledger=%s",
            matched, len(new_jobs), cfg.output_path,
        )


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("Interrupted by user")
        sys.exit(130)


if __name__ == "__main__":
    main()