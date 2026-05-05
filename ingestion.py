"""
ingestion.py
------------
Phase 1: the ingestion module.

`JobSource` is an abstract base so we can swap or combine providers without
touching the rest of the pipeline. Four implementations are provided:

  - AdzunaSource (default): free public REST API, simple keys, aggregates
    many AU job boards. https://developer.adzuna.com/
  - GreenhouseSource (default): public, no-auth REST API for any company on
    the Greenhouse ATS. Pulls full boards and filters by title keyword.
    https://developers.greenhouse.io/job-board.html
  - LeverSource (default): public, no-auth REST API for any company on Lever.
    https://github.com/lever/postings-api
  - SerpApiSource (optional): hits Google Jobs via SerpApi. Flaky in 2025-2026.

`collect_jobs()` accepts a list of sources and merges their results with
deduplication by content hash, so the same role surfacing on multiple sources
collapses to a single Job.

Source roles:
  - Adzuna and SerpApi take a query and a location (one HTTP call per query).
  - Greenhouse and Lever take a company slug and pull the *whole* job board
    (one HTTP call per company), then filter client-side by title keywords
    and location. This is more efficient because their boards are usually
    small and there's no per-call cost.
"""

import asyncio
import hashlib
import logging
import re
from html import unescape
from typing import Iterable, List, Optional, Sequence

import aiohttp

from models import Job

log = logging.getLogger(__name__)


# ---- Helpers -------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    """Crude but adequate HTML-to-text. Greenhouse and Lever return HTML descriptions."""
    if not s:
        return ""
    return unescape(_TAG_RE.sub(" ", s)).strip()


def _hash_id(*parts: str) -> str:
    seed = "|".join(p.lower().strip() for p in parts if p)
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def _matches_any(text: str, needles: Sequence[str]) -> bool:
    """Case-insensitive substring match. Empty needle list = match everything."""
    if not needles:
        return True
    if not text:
        return False
    low = text.lower()
    return any(n.lower() in low for n in needles)


# ---- Abstract base -------------------------------------------------------

class JobSource:
    """Abstract base for job providers."""

    name: str = "abstract"

    async def fetch(self) -> List[Job]:
        """Return all jobs this source produces. Each implementation handles
        its own iteration (queries, company slugs, etc.) internally."""
        raise NotImplementedError


# ============================================================================
# Adzuna - aggregator, query-driven
# ============================================================================

class AdzunaSource(JobSource):
    name = "adzuna"
    BASE_URL = "https://api.adzuna.com/v1/api/jobs"

    def __init__(
        self,
        app_id: str,
        app_key: str,
        session: aiohttp.ClientSession,
        queries: Sequence[str],
        location: str,
        country: str = "au",
        results_per_page: int = 50,
        max_days_old: int = 14,
    ):
        self.app_id = app_id
        self.app_key = app_key
        self.session = session
        self.queries = list(queries)
        self.location = location
        self.country = country
        self.results_per_page = min(max(results_per_page, 1), 50)
        self.max_days_old = max_days_old

    async def fetch(self) -> List[Job]:
        out: List[Job] = []
        for q in self.queries:
            out.extend(await self._search(q))
        return out

    async def _search(self, query: str) -> List[Job]:
        jobs: List[Job] = []
        max_pages = 3  # Prevent infinite loops, grabs up to 150 jobs per query
        
        for page in range(1, max_pages + 1):
            url = f"{self.BASE_URL}/{self.country}/search/{page}"
            params = {
                "app_id": self.app_id,
                "app_key": self.app_key,
                "what": query,
                "where": self.location,
                "results_per_page": self.results_per_page,
                "max_days_old": self.max_days_old,
                "sort_by": "date",
                "content-type": "application/json",
            }
            try:
                async with self.session.get(url, params=params, timeout=30) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        log.error("[adzuna] HTTP %d for '%s': %s", resp.status, query, body[:200])
                        break # Stop paginating on error
                    data = await resp.json()
            except Exception as e:
                log.error("[adzuna] request failed for '%s': %s", query, e)
                break

            items = data.get("results") or []
            if not items:
                break  # Reached the end of the results

            page_jobs = 0
            for item in items:
                j = self._to_job(item)
                if j:
                    jobs.append(j)
                    page_jobs += 1
            
            # If a page returns fewer items than the max, we've hit the end
            if len(items) < self.results_per_page:
                break
                
        log.info("  [adzuna] query='%s' -> %d jobs across pages", query, len(jobs))
        return jobs

    @staticmethod
    def _to_job(item: dict) -> Optional[Job]:
        title = (item.get("title") or "").strip()
        company = ((item.get("company") or {}).get("display_name") or "").strip()
        if not title or not company:
            return None

        location = ((item.get("location") or {}).get("display_name") or "").strip()
        return Job(
            job_id=_hash_id(title, company, location),
            title=title,
            company=company,
            description=item.get("description") or "",
            requirements="",
            location=location,
            posted_at=item.get("created") or "",
            apply_url=item.get("redirect_url") or "",
            source="adzuna",
        )


# ============================================================================
# Greenhouse - direct from company ATS, no auth, no per-query cost
# ============================================================================

class GreenhouseSource(JobSource):
    """
    Greenhouse public Job Board API.

    Endpoint: GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
    Docs: https://developers.greenhouse.io/job-board.html
    """

    name = "greenhouse"
    BASE_URL = "https://boards-api.greenhouse.io/v1/boards"

    def __init__(
        self,
        session: aiohttp.ClientSession,
        companies: Sequence[str],
        title_keywords: Sequence[str],
        location_filters: Sequence[str],
    ):
        self.session = session
        self.companies = list(companies)
        self.title_keywords = list(title_keywords)
        self.location_filters = list(location_filters)
        # The Governor: strictly limit to 5 concurrent connections to prevent 429s
        self.semaphore = asyncio.Semaphore(5) 

    async def fetch(self) -> List[Job]:
        tasks = [self._fetch_company_throttled(c) for c in self.companies]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: List[Job] = []
        for company, res in zip(self.companies, results):
            if isinstance(res, Exception):
                log.error("[greenhouse] %s failed: %s", company, res)
                continue
            out.extend(res)
        return out

    async def _fetch_company_throttled(self, slug: str) -> List[Job]:
        # This forces tasks to wait in line if 5 are already running
        async with self.semaphore:
            return await self._fetch_company(slug)

    async def _fetch_company(self, slug: str) -> List[Job]:
        url = f"{self.BASE_URL}/{slug}/jobs"
        params = {"content": "true"}  # include description in response
        try:
            async with self.session.get(url, params=params, timeout=30) as resp:
                if resp.status == 404:
                    log.warning("[greenhouse] %s: 404 (slug may be wrong or company removed board)", slug)
                    return []
                if resp.status != 200:
                    log.error("[greenhouse] %s: HTTP %d", slug, resp.status)
                    return []
                data = await resp.json()
        except Exception as e:
            log.error("[greenhouse] %s: %s", slug, e)
            return []

        all_items = data.get("jobs") or []
        kept: List[Job] = []
        for item in all_items:
            j = self._to_job(item, slug)
            if not j:
                continue
            # Filter by title keywords AND by location.
            if not _matches_any(j.title, self.title_keywords):
                continue
            if not _matches_any(j.location, self.location_filters):
                continue
            kept.append(j)

        log.info(
            "  [greenhouse] %s -> %d/%d jobs after filtering",
            slug, len(kept), len(all_items),
        )
        return kept

    @staticmethod
    def _to_job(item: dict, company_slug: str) -> Optional[Job]:
        title = (item.get("title") or "").strip()
        if not title:
            return None
        # Greenhouse doesn't return company name in board API; use the slug.
        # Title-case it as a reasonable display.
        company = company_slug.replace("-", " ").replace("_", " ").title()

        location = ((item.get("location") or {}).get("name") or "").strip()
        description = _strip_html(item.get("content") or "")
        apply_url = item.get("absolute_url") or ""
        posted_at = item.get("updated_at") or item.get("first_published") or ""

        return Job(
            job_id=_hash_id(title, company, location),
            title=title,
            company=company,
            description=description,
            requirements="",
            location=location,
            posted_at=posted_at,
            apply_url=apply_url,
            source="greenhouse",
        )


# ============================================================================
# Lever - direct from company ATS, no auth
# ============================================================================

class LeverSource(JobSource):
    """
    Lever public Postings API.

    Endpoint: GET https://api.lever.co/v0/postings/{slug}?mode=json
    Docs: https://github.com/lever/postings-api
    """

    name = "lever"
    BASE_URL = "https://api.lever.co/v0/postings"

    def __init__(
        self,
        session: aiohttp.ClientSession,
        companies: Sequence[str],
        title_keywords: Sequence[str],
        location_filters: Sequence[str],
    ):
        self.session = session
        self.companies = list(companies)
        self.title_keywords = list(title_keywords)
        self.location_filters = list(location_filters)
        # The Governor: strictly limit to 5 concurrent connections to prevent 429s
        self.semaphore = asyncio.Semaphore(5) 

    async def fetch(self) -> List[Job]:
        tasks = [self._fetch_company_throttled(c) for c in self.companies]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out: List[Job] = []
        for company, res in zip(self.companies, results):
            if isinstance(res, Exception):
                log.error("[greenhouse] %s failed: %s", company, res)
                continue
            out.extend(res)
        return out

    async def _fetch_company_throttled(self, slug: str) -> List[Job]:
        # This forces tasks to wait in line if 5 are already running
        async with self.semaphore:
            return await self._fetch_company(slug)

    async def _fetch_company(self, slug: str) -> List[Job]:
        url = f"{self.BASE_URL}/{slug}"
        params = {"mode": "json"}
        try:
            async with self.session.get(url, params=params, timeout=30) as resp:
                if resp.status == 404:
                    log.warning("[lever] %s: 404 (slug may be wrong)", slug)
                    return []
                if resp.status != 200:
                    log.error("[lever] %s: HTTP %d", slug, resp.status)
                    return []
                data = await resp.json()
        except Exception as e:
            log.error("[lever] %s: %s", slug, e)
            return []

        all_items = data if isinstance(data, list) else []
        kept: List[Job] = []
        for item in all_items:
            j = self._to_job(item, slug)
            if not j:
                continue
            if not _matches_any(j.title, self.title_keywords):
                continue
            if not _matches_any(j.location, self.location_filters):
                continue
            kept.append(j)

        log.info(
            "  [lever] %s -> %d/%d jobs after filtering",
            slug, len(kept), len(all_items),
        )
        return kept

    @staticmethod
    def _to_job(item: dict, company_slug: str) -> Optional[Job]:
        title = (item.get("text") or "").strip()
        if not title:
            return None
        company = company_slug.replace("-", " ").replace("_", " ").title()

        cats = item.get("categories") or {}
        location = (cats.get("location") or "").strip()

        # Lever description splits across descriptionPlain + lists[].content.
        # Concatenate for the evaluator's benefit.
        desc_parts: List[str] = []
        if item.get("descriptionPlain"):
            desc_parts.append(item["descriptionPlain"])
        elif item.get("description"):
            desc_parts.append(_strip_html(item["description"]))
        for section in (item.get("lists") or []):
            text_html = section.get("content") or ""
            desc_parts.append(_strip_html(text_html))
        if item.get("additionalPlain"):
            desc_parts.append(item["additionalPlain"])

        description = "\n\n".join(p for p in desc_parts if p)
        apply_url = item.get("hostedUrl") or item.get("applyUrl") or ""
        # Lever returns createdAt as a millisecond epoch
        created = item.get("createdAt")
        posted_at = ""
        if isinstance(created, (int, float)):
            from datetime import datetime, timezone
            try:
                posted_at = datetime.fromtimestamp(created / 1000, tz=timezone.utc).isoformat()
            except (ValueError, OSError):
                pass

        return Job(
            job_id=_hash_id(title, company, location),
            title=title,
            company=company,
            description=description,
            requirements="",
            location=location,
            posted_at=posted_at,
            apply_url=apply_url,
            source="lever",
        )


# ============================================================================
# SerpApi (Google Jobs) - optional, query-driven
# ============================================================================

class SerpApiSource(JobSource):
    name = "serpapi"
    BASE_URL = "https://serpapi.com/search.json"

    def __init__(
        self,
        api_key: str,
        session: aiohttp.ClientSession,
        queries: Sequence[str],
        location: str,
        gl: str = "au",
        google_domain: str = "google.com.au",
    ):
        self.api_key = api_key
        self.session = session
        self.queries = list(queries)
        self.location = location
        self.gl = gl
        self.google_domain = google_domain

    async def fetch(self) -> List[Job]:
        out: List[Job] = []
        for q in self.queries:
            out.extend(await self._search(q))
        return out

    async def _search(self, query: str) -> List[Job]:
        params = {
            "engine": "google_jobs",
            "q": query,
            "location": self.location,
            "hl": "en",
            "gl": self.gl,
            "google_domain": self.google_domain,
            "api_key": self.api_key,
        }
        try:
            async with self.session.get(self.BASE_URL, params=params, timeout=30) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.error("[serpapi] HTTP %d for '%s': %s", resp.status, query, body[:200])
                    return []
                data = await resp.json()
        except Exception as e:
            log.error("[serpapi] request failed for '%s': %s", query, e)
            return []

        items = data.get("jobs_results") or []
        if not items:
            meta = data.get("search_metadata", {}) or {}
            err = data.get("error")
            log.warning(
                "  [serpapi] query='%s' -> 0 jobs | status=%s%s",
                query,
                meta.get("status"),
                f" | error={err!r}" if err else "",
            )
            return []

        jobs: List[Job] = []
        for item in items:
            j = self._to_job(item)
            if j:
                jobs.append(j)
        log.info("  [serpapi] query='%s' -> %d jobs", query, len(jobs))
        return jobs

    @staticmethod
    def _to_job(item: dict) -> Optional[Job]:
        title = (item.get("title") or "").strip()
        company = (item.get("company_name") or "").strip()
        if not title or not company:
            return None
        location = (item.get("location") or "").strip()
        description = item.get("description") or ""

        apply_url = ""
        for opt in (item.get("apply_options") or []):
            if opt.get("link"):
                apply_url = opt["link"]
                break
        if not apply_url:
            apply_url = item.get("share_link", "") or ""

        req_parts: List[str] = []
        for h in (item.get("job_highlights") or []):
            htitle = (h.get("title") or "").lower()
            if htitle in ("qualifications", "requirements", "responsibilities"):
                req_parts.append(f"[{h.get('title')}]")
                req_parts.extend(f"  - {x}" for x in (h.get("items") or []))
        requirements = "\n".join(req_parts)

        ext = item.get("detected_extensions") or {}
        return Job(
            job_id=_hash_id(title, company, location),
            title=title,
            company=company,
            description=description,
            requirements=requirements,
            location=location,
            posted_at=ext.get("posted_at", "") or "",
            apply_url=apply_url,
            source="serpapi",
        )


# ============================================================================
# Orchestration
# ============================================================================

async def collect_jobs(sources: Sequence[JobSource], previously_seen_ids: set[str] = None) -> List[Job]:
    """
    Fetch from every source, merge, and heavily deduplicate.
    Checks against both the current run AND historical runs (previously_seen_ids).
    """
    if not sources:
        return []

    if previously_seen_ids is None:
        previously_seen_ids = set()

    results = await asyncio.gather(
        *[s.fetch() for s in sources],
        return_exceptions=True,
    )

    seen: set[str] = set(previously_seen_ids) # Initialize with historical data
    out: List[Job] = []
    per_source: dict[str, int] = {}

    for source, res in zip(sources, results):
        if isinstance(res, Exception):
            log.error("Source %s failed: %s", source.name, res)
            per_source[source.name] = 0
            continue
        
        count = 0
        for j in res:
            if j.job_id in seen:
                continue # Skip if seen in THIS run OR historical runs
            seen.add(j.job_id)
            out.append(j)
            count += 1
        per_source[source.name] = count

    breakdown = ", ".join(f"{k}={v}" for k, v in per_source.items())
    log.info("Collected %d brand new unique jobs (%s)", len(out), breakdown)
    return out