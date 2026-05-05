# Autonomous Employment Pipeline

Async, rate-limited Python pipeline that sources, filters, and drafts
applications for engineering jobs in Melbourne (or anywhere). Pulls from
multiple sources in parallel, uses one Gemini model for cheap classification
and a stronger one for cover-letter synthesis.

## Architecture

```
+-------------+     +-----------+      +-------------+     +----------+
| Sources     | --> | Evaluator | -->  | Synthesizer | --> | Markdown |
| (parallel)  |     | flash-lite|      |  flash      |     | Ledger   |
+-------------+     | 15 RPM    |      |  10 RPM     |     +----------+
| Adzuna      |     +-----------+      +-------------+
| Greenhouse  |
| Lever       |     Each Gemini client owns its own SlidingWindowRateLimiter,
| SerpApi     |     so the two model buckets are policed independently.
+-------------+
```

Phase 2 uses **gemini-2.5-flash-lite** (15 RPM / 1000 RPD) for fast binary
classification. Phase 3 uses **gemini-2.5-flash** (10 RPM / 250 RPD) for
higher-quality cover letter generation - only on the ~30% of jobs that pass
the evaluator.

A matched job costs 1 flash-lite call + 1 flash call. A discarded job costs
1 flash-lite call. Combining the executive summary and cover letter into a
single structured-JSON call halves the synth burn versus naive two-call
approach.

## Job sources

| Source     | Free? | What it gives you | Notes |
|------------|-------|-------------------|-------|
| **Adzuna** | Yes (signup) | Aggregator across many AU job boards | Volume; query-driven |
| **Greenhouse** | Yes (no auth) | Direct from company ATS | Curated AU tech list; full board pulls |
| **Lever** | Yes (no auth) | Direct from company ATS | Curated AU tech list; full board pulls |
| SerpApi (Google Jobs) | 100/mo | Variable, flaky in 2025-2026 | Optional secondary |

Greenhouse and Lever are the highest-quality signal: postings come straight
from the company's ATS with no aggregator middlemen, no ghost jobs, no
recruiter spam. The pipeline ships with curated lists of AU tech companies
on each platform - edit `DEFAULT_GREENHOUSE_COMPANIES` and
`DEFAULT_LEVER_COMPANIES` in `config.py` to grow the list.

### What's NOT included, and why

- **SEEK** - their official API is partner-only (recruitment-software vendors).
  ToS prohibits scraping. Adzuna covers many SEEK postings indirectly.
- **LinkedIn** - actively litigates against scrapers. Microsoft killed
  Proxycurl in 2025. Not worth the risk.
- **Indeed** - Publisher API is effectively closed. Indeed jobs leak into
  Adzuna's index anyway.

## File Layout

| File              | Role                                                   |
|-------------------|--------------------------------------------------------|
| `main.py`         | Orchestrator. Two GeminiClients, four sources.         |
| `config.py`       | Env loading, model choice, queries, company lists.     |
| `models.py`       | `Job` and `ProcessedJob` dataclasses.                  |
| `rate_limiter.py` | Sliding-window async rate limiter.                     |
| `llm_client.py`   | `GeminiClient` with retry + per-instance limiter.      |
| `ingestion.py`    | `JobSource` ABC + Adzuna, Greenhouse, Lever, SerpApi.  |
| `evaluator.py`    | flash-lite MATCH/DISCARD classifier. Phase 2.          |
| `synthesizer.py`  | flash summary + cover letter. Phase 3.                 |
| `ledger.py`       | Markdown appender. Phase 4.                            |
| `state.py`        | Persistent dedup across runs.                          |

## Setup (Ubuntu)

```bash
cd job_pipeline

# 1. Virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Dependencies
pip install -r requirements.txt

# 3. API keys
cp .env.example .env
```

Sign up for the keys:

- **Gemini** (required): <https://aistudio.google.com/app/apikey>
- **Adzuna** (recommended): <https://developer.adzuna.com/signup> -> 2 keys
  (`app_id` + `app_key`). Free, instant.
- **SerpApi** (optional only): <https://serpapi.com/>

Greenhouse and Lever require no signup at all.

```bash
python main.py
```

## Output

- **`applications_ledger.md`** - matched roles with summary, cover letter, apply link.
- **`.pipeline_state.json`** - job IDs already evaluated. Re-running won't re-spend quota. Delete to force re-evaluation.

## Tuning

### Search behaviour

- **Adzuna queries** (`Config.queries`): Each query = 1 Adzuna call. Phase 1 is
  broad on purpose; Phase 2 does strict filtering.
- **Greenhouse / Lever filters** (`Config.title_keywords`,
  `Config.location_filters`): These platforms return whole boards, so we
  filter client-side. Edit the keyword lists to widen or narrow.
- **Companies** (`DEFAULT_GREENHOUSE_COMPANIES`, `DEFAULT_LEVER_COMPANIES`):
  add slugs from `boards.greenhouse.io/<slug>` or `jobs.lever.co/<slug>` URLs.
- **Location** (`Config.search_location`): used by Adzuna and SerpApi.
- **Adzuna country** (`Config.adzuna_country`): `au`, `gb`, `us`, etc.

### Model choice

- `Config.evaluator_model` and `Config.synthesizer_model` - the defaults
  (`gemini-2.5-flash-lite` and `gemini-2.5-flash`) are the best free-tier
  trade-off as of May 2026. If you hit quota issues, the next step is to
  enable Cloud Billing on your Google Cloud project (no spend required) -
  this jumps you to Tier 1 with 30x higher RPM.

### Output content

- **Candidate background:** edit `CANDIDATE_PROFILE` in `synthesizer.py` when
  your resume changes.
- **Filter strictness:** edit `EVAL_PROMPT` in `evaluator.py`.

## Toggling sources

In `.env`:

```
USE_ADZUNA=true
USE_GREENHOUSE=true
USE_LEVER=true
USE_SERPAPI=false
```

## Adding a new source

The `JobSource` ABC in `ingestion.py` is the integration point:

```python
class MySource(JobSource):
    name = "mysource"

    def __init__(self, ..., session, queries_or_companies, ...):
        ...

    async def fetch(self) -> List[Job]:
        # call API, return list of Job(...)
        ...
```

Then construct it in `main.py` and append to `sources`. Dedup is automatic.

## Running it as a background job

Once a few runs look good:

```cron
# every weekday at 8am
0 8 * * 1-5 cd /home/erol/Projects/Fulltime_bot && /home/erol/Projects/Fulltime_bot/.venv/bin/python main.py >> pipeline.log 2>&1
```

## Notes

- The synthesizer prompt explicitly bans LLM-tells like em-dashes,
  "passionate", "thrilled". Read every cover letter before sending - the
  floor is higher than zero-shot, but it's still a draft.
- All Gemini rate limiting is enforced inside `GeminiClient.generate()`. If
  you add new Gemini call sites, route them through a `GeminiClient` instance.
- Greenhouse and Lever endpoints are public and rate-limit-tolerant; we make
  one call per company per run (~20 calls total), parallelised.
