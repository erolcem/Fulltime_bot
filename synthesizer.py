"""
synthesizer.py
--------------
Phase 3: for matched roles, generate an executive summary AND a tailored
cover letter in a SINGLE Gemini call returning JSON. 

The candidate background is grounded in empirical evidence (project names, 
technologies, papers) to force the LLM to generate highly specific, technically 
dominant applications, completely devoid of standard recruiter fluff.
"""

import json
import logging
from typing import Tuple

from llm_client import GeminiClient
from models import Job

log = logging.getLogger(__name__)

# ---- Candidate background -----------------------------------------------
# Ground truth for the synthesizer. 

CANDIDATE_PROFILE = """\
Erol Cemiloglu - Systems & Robotics Engineer, M.Sc. candidate at Monash University.

EDUCATION
- M.Sc. Electrical & Computer Systems Engineering, Monash University. Expected Oct 2026.
- B.E. (Honours) Electrical & Computer Systems Engineering, Monash University. Completed Dec 2025.
  Minor: Chemical Engineering, Micro & Nano Technologies. Accelerated Master's pathway.

CURRENT & RECENT EXPERIENCE
- Research Assistant, Monash Faculty of Civil Engineering (Oct 2025 - present):
  Designed and ran human-robot collaboration experiments in simulated construction environments
  using Universal Robots arms, Xsens IMUs, and Optitrack motion capture to quantify trust and
  performance metrics. Built Python data-analysis pipelines (NumPy, Matplotlib, statistical
  modelling). Co-authored ISARC 2026 paper on trust and performance in HRC for construction tasks.

- Roboticist Engineer, MindPerfect Technologies (Nov 2025 - present):
  Prototyping construction robotics solutions. Manufactured and programmed an Annin Robotics AR4
  arm using ROS 2, C++, and OpenCV for real-world site deployment.

- Technical Lead & Head Lecturer, Monash Deep Neuron (Feb 2025 - present):
  Delivered a 6-workshop HPC series to 100+ undergraduates covering modern C++, computer
  architecture, CMake, CUDA, and accelerated computing - co-run with NVIDIA, NCI, and Pawsey
  Supercomputing Centre. Spearheaded an EEG-controlled robotic arm BCI project funded by a
  Motorola Solutions grant: real-time pipeline streaming EEG via LSL and Apache Kafka into a
  3D CNN + Transformer model that maps motor imagery to Universal Robots arm commands. Directed
  the technical branch including recruitment, team management, and project governance.

- Software Engineering Intern, MindPerfect Technologies (Oct 2024 - Feb 2025):
  Profiled and optimised proprietary AI swarm algorithms in C++ using Google Benchmark, Abseil,
  GoogleTest, and Valgrind, eliminating critical performance bottlenecks before production.
  Integrated Eigen, C++ threading primitives, UML statecharts, and Llama 3 LLM inference into
  swarm coordination logic. Extended the SQLite-based inter-agent communication database.

PUBLICATION
Cemiloglu, E. et al. "Trust and Performance in Human-Robot Collaboration for Construction
Tasks." Accepted, ISARC 2026.

CORE SKILLS
- Languages: C, C++, C#, Python, ARM Assembly, MATLAB, Verilog, PyBind
- Robotics & Hardware: ROS 2, Universal Robots, Annin AR4, MIR100, Arduino, STM32, Raspberry Pi, FPGA
- Systems & Performance: OpenMP, CUDA, Abseil, gRPC, CMake, GoogleTest, Google Benchmark, Valgrind, Linux tracing
- AI / ML: PyTorch, TensorFlow, Keras, OpenCV, Ollama, Qwen, Llama, AutoGen
- Math & Signal: Eigen, Wolfram, FFT/signal processing, ODE/PDE solvers, Xsens, Optitrack
- Fabrication & Circuits: PCB design, Altium, LTspice, electromagnetics, RF, photolithography, 3D printing
- Infra & Data: Docker, Apache Kafka, Linux, WSL, Git, Google Colab, SQL/SQLite, Supabase

CHARACTER
Disciplined and technically rigorous. Drawn to building complex automated systems end-to-end -
from electrical assembly through firmware and control to high-level software. Comfortable
operating across the hardware/software boundary.
"""


SYNTH_PROMPT = """You are drafting application material for an elite engineering candidate.
Cross-reference the candidate's background with the job posting and produce a JSON object
matching EXACTLY this schema:

{{
  "executive_summary": "...",
  "cover_letter": "..."
}}

FIELD: executive_summary
  Exactly 3 sentences for the candidate's internal review.
  Sentence 1: The company's core product/mission in plain terms.
  Sentence 2: The specific technical scope of this role.
  Sentence 3: The strongest two concrete reasons this candidate is a lethal match, naming actual technologies.

FIELD: cover_letter
  A tailored cover letter, 250-300 words. Open with "Dear Hiring Team," unless the posting names a recruiter.

  HARD RULES for the Cover Letter:
  - THE HOOK: The very first sentence MUST immediately state a technical intersection between the candidate's physical engineering expertise and the company's core product. (Example: "Building robust autonomous systems requires tight integration across hardware, ROS 2, and embedded C++—challenges I am currently solving with Universal Robots and aim to bring to [Company].") Do NOT use cliches like "I am writing to express my interest".
  - EVIDENCE: Reference at least TWO concrete items from the candidate's background by name (e.g., AR4 arm, ISARC 2026 paper, Valgrind/Google Benchmark optimization, BCI Kafka pipeline). Tie each item to a specific requirement in the posting.
  - TONE: Assertive, highly technical, and precise. Avoid em-dashes.
  - BANNED WORDS: "passionate", "exciting opportunity", "thrilled", "team player", "dynamic", "synergy", "leverage", "delighted". 
  - CLOSING: Close with exactly one sentence offering to discuss further, followed by:
        Kind regards,
        Erol Cemiloglu

Output ONLY the JSON object.

CANDIDATE BACKGROUND:
{profile}

JOB POSTING:
Title:        {title}
Company:      {company}
Location:     {location}
Requirements: {requirements}
Description:  {description}
"""


async def synthesize(client: GeminiClient, job: Job) -> Tuple[str, str]:
    """For a matched job, return (executive_summary, cover_letter) in a single LLM call."""
    prompt = SYNTH_PROMPT.format(
        profile=CANDIDATE_PROFILE,
        title=job.title,
        company=job.company,
        location=job.location or "(not specified)",
        requirements=job.requirements or "(not specified in posting)",
        description=(job.description or "")[:6000],
    )
    
    # expect_json=True triggers the application/json MIME type in the client
    raw = await client.generate(prompt, expect_json=True)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        # Fallback defensive parsing
        cleaned = raw.strip().strip("`").lstrip("json").strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            log.error("Synthesizer JSON parse failed for %s: %s", job.title, e)
            log.debug("Raw output: %s", raw[:500])
            return (
                "(synthesis JSON parse failed - see cover letter for raw output)",
                raw,
            )

    summary = (data.get("executive_summary") or "").strip()
    letter = (data.get("cover_letter") or "").strip()
    
    if not summary or not letter:
        log.warning("Synthesizer returned incomplete fields for %s", job.title)
        
    return summary, letter