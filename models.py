"""
models.py
---------
Plain dataclasses representing the structured payloads that move through
the pipeline. Kept deliberately framework-free.
"""

from dataclasses import dataclass, asdict, field
from typing import Optional


@dataclass
class Job:
    job_id: str          # stable hash for cross-run dedup
    title: str
    company: str
    description: str
    requirements: str = ""
    location: str = ""
    posted_at: str = ""
    due_date: Optional[str] = None
    apply_url: str = ""
    source: str = "serpapi"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProcessedJob:
    """A job that passed the evaluator and was synthesised."""
    job: Job
    executive_summary: str
    cover_letter: str
