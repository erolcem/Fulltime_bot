"""
evaluator.py
------------
Phase 2: binary filter using Gemini. Returns True (MATCH) or False (DISCARD).

Utilizes Chain-of-Thought (CoT) prompting to maximize the logical accuracy of 
the flash-lite model, asking it to reason for one sentence before declaring 
its binary decision.
"""

import logging

from llm_client import GeminiClient
from models import Job

log = logging.getLogger(__name__)

EVAL_PROMPT = """You are a strict, elite technical recruiter filtering roles for a candidate graduating with a Master's degree in Electrical and Computer Systems Engineering.

Evaluate whether the role meets BOTH criteria below.

CRITERION 1 - Experience Level: 
  The candidate is a recent Master's graduate. 
  Acceptable: Graduate, junior, entry-level, early-career, "0-3 years" (a Master's degree often substitutes for 1-2 years of experience).
  Reject: Roles strictly demanding "Senior", "Principal", "Lead", or "5+ years" of commercial industry experience.

CRITERION 2 - Engineering Depth: 
  The role must involve rigorous physical or deep-tech engineering.
  Acceptable: Hardware integration, low level software, embedded systems, robotics, control systems, kinematics, ROS / ROS 2, automated physical infrastructure, signal processing, FPGA, computer vision on hardware, real-time systems, firmware, electrical systems, or applied AI/ML.
  Reject: Pure web development (CRUD), marketing analytics, IT support, sales engineering, or pure cloud DevOps with no physical/hardware tie-in.

TASK:
First, provide a ONE SENTENCE rationale evaluating the criteria.
Then, on a new line, output exactly: "DECISION: MATCH" or "DECISION: DISCARD".

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

    # Defensive parse updated for Chain-of-Thought.
    # We split by "DECISION:" and take the last part, then clean it.
    if "DECISION:" in raw.upper():
        decision_string = raw.upper().split("DECISION:")[-1]
        decision = decision_string.strip().split()[0].strip(".,'\"`*")
    else:
        # Fallback if the model disobeys formatting
        decision = raw.strip().split()[0].strip(".,'\"`*").upper()

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