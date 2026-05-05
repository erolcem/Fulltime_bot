"""
evaluator.py
------------
Phase 2: binary filter using Gemini. Returns True (MATCH) or False (DISCARD).

The prompt is intentionally strict and demands a single-token response so we
can parse robustly. We still defensively split-on-whitespace and uppercase
in case the model adds punctuation despite instructions.
"""

import logging

from llm_client import GeminiClient
from models import Job

log = logging.getLogger(__name__)

EVAL_PROMPT = """You are a strict binary classifier for engineering job postings.

Evaluate whether the role meets BOTH criteria below.

CRITERION 1 - Experience level: requires LESS than 3 years of commercial / professional experience.
  Acceptable: graduate, junior, entry-level, early-career, "0-2 years", new grad, internship-to-hire.
  Reject: senior, principal, lead, staff, "3+ years", "5+ years", management roles requiring extensive experience.

CRITERION 2 - Engineering depth: involves real engineering work, not generic CRUD or pure data analysis.
  Acceptable: hardware integration, embedded systems, robotics, control systems, kinematics, ROS / ROS 2,
  automated physical infrastructure, signal processing, FPGA, computer vision on hardware, real-time systems,
  firmware, electrical systems, instrumentation, AI/ML applied to physical systems.
  Reject: pure web development, marketing analytics, business analyst, IT support, sales engineering,
  pure cloud DevOps with no hardware/automation tie-in.

Respond with EXACTLY ONE of these two words and NOTHING else:
  MATCH
  DISCARD

No punctuation. No explanation. No quotes.

---
JOB TITLE: {title}
COMPANY: {company}
LOCATION: {location}

REQUIREMENTS:
{requirements}

DESCRIPTION:
{description}
---"""


async def evaluate(client: GeminiClient, job: Job) -> bool:
    prompt = EVAL_PROMPT.format(
        title=job.title,
        company=job.company,
        location=job.location or "(not specified)",
        requirements=job.requirements or "(not specified)",
        # Cap description so we don't waste prompt tokens on boilerplate
        description=(job.description or "")[:6000],
    )
    raw = await client.generate(prompt)

    # Defensive parse: take the first whitespace-separated token, strip
    # punctuation, uppercase. Tolerates "MATCH.", " match", "MATCH\n", etc.
    decision = raw.strip().split()[0].strip(".,'\"`*").upper() if raw.strip() else ""

    if decision == "MATCH":
        log.info("  MATCH    | %s @ %s", job.title, job.company)
        return True
    if decision == "DISCARD":
        log.info("  DISCARD  | %s @ %s", job.title, job.company)
        return False
    log.warning(
        "  UNCLEAR  | %s @ %s -> raw=%r (treating as DISCARD)",
        job.title, job.company, raw[:80],
    )
    return False
