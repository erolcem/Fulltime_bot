#!/usr/bin/env python3
import asyncio
import json
import os
import time
import aiohttp
import google.generativeai as genai
from dotenv import load_dotenv

# Load environment variables (Create a .env file with GEMINI_API_KEY and SERPAPI_KEY)
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SERPAPI_KEY = os.getenv("SERPAPI_KEY")

# Configure Gemini
genai.configure(api_key=GEMINI_API_KEY)

class AsyncRateLimiter:
    """
    Governor for API rate limits.
    For Gemini Free Tier (15 RPM), we limit to 14 requests per minute to be safe.
    This guarantees at least ~4.28 seconds between each AI API call.
    """
    def __init__(self, max_rpm=14):
        self.interval = 60.0 / max_rpm
        self.last_called = 0.0
        self.lock = asyncio.Lock()

    async def wait(self):
        async with self.lock:
            now = time.time()
            elapsed = now - self.last_called
            if elapsed < self.interval:
                await asyncio.sleep(self.interval - elapsed)
            self.last_called = time.time()


class JobIngestor:
    """Phase 1: Interfaces with Job API (SerpApi) to source roles."""
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://serpapi.com/search.json"

    async def fetch_jobs(self, session: aiohttp.ClientSession) -> list:
        # Search parameters for Melbourne-based robotics/engineering roles
        params = {
            "engine": "google_jobs",
            "q": "Graduate Engineer OR Robotics Engineer OR Control Systems",
            "location": "Melbourne, VIC, Australia",
            "gl": "au",
            "hl": "en",
            "api_key": self.api_key
        }
        
        print("[Phase 1] Sourcing jobs from SerpApi...")
        async with session.get(self.base_url, params=params) as response:
            if response.status != 200:
                print(f"Error fetching jobs: {response.status}")
                return []
            
            data = await response.json()
            raw_jobs = data.get("jobs_results", [])
            
            structured_jobs = []
            for job in raw_jobs:
                apply_links = job.get("apply_options", [])
                apply_url = apply_links[0].get("link") if apply_links else "No URL provided"
                
                structured_jobs.append({
                    "title": job.get("title", "Unknown Title"),
                    "company": job.get("company_name", "Unknown Company"),
                    "description": job.get("description", ""),
                    "due_date": "N/A",  # Google Jobs rarely provides a strict due date, defaulting to N/A
                    "apply_url": apply_url
                })
            
            print(f"[Phase 1] Found {len(structured_jobs)} jobs.")
            return structured_jobs


class GeminiFilter:
    """Phase 2: Evaluates job descriptions against strict engineering criteria."""
    def __init__(self, rate_limiter: AsyncRateLimiter):
        self.rate_limiter = rate_limiter
        # Using 1.5 Flash as it is fast and highly capable for logic gating
        self.model = genai.GenerativeModel('gemini-1.5-flash')
        self.prompt_template = """
        You are a strict technical recruiter filter.
        Evaluate the following job description against these baseline rules:
        1. Must require less than 3 years of commercial experience (graduate/junior to mid-level).
        2. Must involve actual engineering depth (e.g., hardware integration, control systems, kinematics, ROS 2, or automated physical infrastructure).

        Job Description:
        {description}

        If it meets BOTH rules, respond strictly with the word: MATCH
        If it fails either rule, respond strictly with the word: DISCARD
        Do not output any other text.
        """

    async def evaluate(self, job: dict) -> bool:
        await self.rate_limiter.wait() # Enforce RPM limits
        
        prompt = self.prompt_template.format(description=job['description'])
        
        try:
            response = await self.model.generate_content_async(prompt)
            decision = response.text.strip().upper()
            return "MATCH" in decision
        except Exception as e:
            print(f"Filter Error for {job['title']}: {e}")
            return False


class CoverLetterSynthesizer:
    """Phase 3: Drafts targeted cover letters and executive summaries for matched roles."""
    def __init__(self, rate_limiter: AsyncRateLimiter):
        self.rate_limiter = rate_limiter
        self.model = genai.GenerativeModel('gemini-1.5-flash')
        self.background = """
        Recent graduate with a Bachelor of Electrical and Computer Systems Engineering (Honours), 
        currently undertaking a Master of Engineering. Deeply focused on a 'rigid flight into greatness,' 
        prioritizing discipline, technical mastery, and the development of complex automated systems. 
        Possesses hands-on expertise in mechanical and electrical assembly (e.g., AR4 robotic arms), 
        kinematics, and software integration.
        """
        
    async def synthesize(self, job: dict) -> dict:
        await self.rate_limiter.wait() # Enforce RPM limits
        
        prompt = f"""
        Candidate Background: {self.background}
        
        Target Role: {job['title']} at {job['company']}
        Job Description: {job['description']}
        
        Task: 
        1. Write a 3-sentence executive summary detailing what the company does and why the candidate's skills are an exact match.
        2. Draft a formal, highly technically precise cover letter proving capability and alignment with the job description. Do not use generic fluff.
        
        Format your response as a JSON object with exactly two keys: "executive_summary" and "cover_letter".
        """
        
        try:
            # We enforce JSON output generation to make parsing highly predictable
            response = await self.model.generate_content_async(
                prompt,
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json"
                )
            )
            return json.loads(response.text)
        except Exception as e:
            print(f"Synthesis Error for {job['title']}: {e}")
            return {"executive_summary": "Error generating summary.", "cover_letter": "Error generating letter."}


class OutputLedger:
    """Phase 4: Appends processed data into an asynchronous review pipeline (Markdown)."""
    def __init__(self, filename="jobs_pipeline.md"):
        self.filename = filename

    async def write_entry(self, job: dict, synthesis: dict):
        # Using blocking file I/O here is fine as it's a fast append, 
        # but in a massive scale system, aiofiles could be used.
        entry = f"""
## {job['title']} & {job['company']}

**Executive Summary:**
{synthesis.get('executive_summary', '')}

**Actionable Data:**
*   **Due Date:** {job['due_date']}
*   **Apply Here:** [Application Link]({job['apply_url']})

**The Draft:**
{synthesis.get('cover_letter', '')}

---
"""
        with open(self.filename, 'a', encoding='utf-8') as f:
            f.write(entry)
        print(f"[Phase 4] Successfully logged {job['title']} to {self.filename}")


async def process_job(job: dict, filter_ai: GeminiFilter, synthesizer: CoverLetterSynthesizer, ledger: OutputLedger):
    """Orchestrates the evaluation and synthesis for a single job."""
    print(f"[Phase 2] Evaluating: {job['title']} at {job['company']}...")
    is_match = await filter_ai.evaluate(job)
    
    if not is_match:
        print(f"          -> DISCARDED: {job['title']}")
        return

    print(f"          -> MATCHED: {job['title']}. Triggering Phase 3 Synthesis...")
    synthesis = await synthesizer.synthesize(job)
    
    await ledger.write_entry(job, synthesis)


async def main():
    if not GEMINI_API_KEY or not SERPAPI_KEY:
        print("Error: Missing API Keys. Please check your .env file.")
        return

    rate_limiter = AsyncRateLimiter(max_rpm=14)
    ingestor = JobIngestor(api_key=SERPAPI_KEY)
    filter_ai = GeminiFilter(rate_limiter)
    synthesizer = CoverLetterSynthesizer(rate_limiter)
    ledger = OutputLedger()

    async with aiohttp.ClientSession() as session:
        # Phase 1: Ingest jobs
        jobs = await ingestor.fetch_jobs(session)
        
        # Phase 1 Test: Print jobs to terminal and exit early
        print(json.dumps(jobs, indent=2))
        return

        # Phases 2-4: Process jobs concurrently via asyncio.gather 
        # (The rate limiter ensures Gemini API doesn't get flooded)
        tasks = [process_job(job, filter_ai, synthesizer, ledger) for job in jobs]
        await asyncio.gather(*tasks)

    print("\nPipeline execution complete. Check jobs_pipeline.md for results.")

if __name__ == "__main__":
    # Run the asynchronous pipeline
    asyncio.run(main())
