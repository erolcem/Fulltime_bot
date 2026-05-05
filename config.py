"""
config.py
---------
Centralised configuration. All API keys are loaded from environment variables
(via .env file) so nothing secret lives in source.

To inject keys: edit `.env` (created from `.env.example`) - that's the only place.

Source selection: set USE_ADZUNA, USE_GREENHOUSE, USE_LEVER, USE_SERPAPI in .env.
Defaults: Adzuna + Greenhouse + Lever enabled, SerpApi disabled.
"""

import os
from dataclasses import dataclass, field
from typing import Tuple

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


# ---- Curated AU tech-company boards -------------------------------------
# These are companies known to host substantial Australian engineering teams
# AND to publish job listings via Greenhouse or Lever (both have free public
# APIs). Edit these lists to broaden or narrow coverage.
#
# To find a company's Greenhouse slug: visit their careers page. If the URL
# contains "boards.greenhouse.io/<slug>" or "job-boards.greenhouse.io/<slug>",
# that's the slug.
#
# To find a Lever slug: same drill, look for "jobs.lever.co/<slug>".

# ---- Curated AU tech & deep-tech company boards ---------------------------

DEFAULT_GREENHOUSE_COMPANIES: Tuple[str, ...] = (
    # --- Software & Fintech Unicorns ---
    "cultureamp",        # Culture Amp - Melbourne HQ
    "safetyculture",     # SafetyCulture - Sydney/Townsville
    "canva",             # Canva - Sydney
    "linktree",          # Linktree - Melbourne
    "airwallex",         # Airwallex - Melbourne HQ
    "immutable",         # Immutable - Sydney (web3/gaming infra)
    "octopusdeploy",     # Octopus Deploy - Brisbane
    "employmenthero",    # Employment Hero - Sydney
    "deputy",            # Deputy - Sydney
    "myob",              # MYOB - Melbourne
    "redbubble",         # Redbubble - Melbourne
    "tyro",              # Tyro Payments - Sydney
    "zeller",            # Zeller - Melbourne (fintech)
    "buildkite",         # Buildkite - Melbourne (CI/CD)
    "go1",               # Go1 - Brisbane (edtech)
    "xero",              # Xero - Melbourne/NZ
    "afterpay",          # Afterpay / Block - Melbourne
    "squareup",          # Square (Block) - Melbourne
    "zendesk",           # Zendesk - Huge Melbourne engineering hub
    "siteminder",        # SiteMinder - Sydney
    "eucalyptus",        # Eucalyptus - Sydney/Melbourne (HealthTech)
    "harrisonai",        # Harrison.ai - Sydney (Health AI)
    "mr-yum",            # Mr Yum / me&u - Melbourne
    "airtasker",         # Airtasker - Sydney
    
    # --- Hardware, Robotics & Deep Tech (Highly Relevant) ---
    "advancednavigation", # Advanced Navigation - Syd/Melb/Perth (Robotics, AI, Sensors)
    "baraja",            # Baraja - Sydney (LiDAR for autonomous vehicles)
    "morsemicro",        # Morse Micro - Sydney (Wi-Fi HaLow Silicon/Hardware)
    "qctrl",             # Q-CTRL - Sydney (Quantum computing hardware/control systems)
    "droneshield",       # DroneShield - Sydney (Hardware/Software for drone defense)
    "fleetspace",        # Fleet Space - Adelaide (Satellites/Hardware)
    "gilmourspace",      # Gilmour Space - Queensland (Aerospace/Rockets)
    "fastbrickrobotics", # FBR - Perth (Construction Robotics - EXTREMELY relevant to your startup goal)
)

DEFAULT_LEVER_COMPANIES: Tuple[str, ...] = (
    # --- Software & Platforms ---
    "rokt",              # Rokt - Sydney
    "envato",            # Envato - Melbourne
    "atlassian",         # Atlassian - Sydney/Remote (Uses Lever for some departments)
    "hipages",           # HiPages - Sydney
    "whogivesacrap",     # Who Gives A Crap - Melbourne
    "petcircle",         # Pet Circle - Sydney/Remote
    "brighte",           # Brighte - Sydney (Green energy fintech)
    "skedsocial",        # Sked Social - Melbourne
    "mable",             # Mable - Sydney
    "easygopost",
    
    # --- Hardware & Robotics ---
    "atomos",            # Atomos - Melbourne (Broadcast hardware)
    "swoopaero",         # Swoop Aero - Melbourne (Autonomous drone logistics / Hardware)
    "vow",               # Vow - Sydney (Lab-grown meat, requires complex bio-reactor hardware/controls)
)


@dataclass(frozen=True)
class Config:
    # --- Required secret ---
    gemini_api_key: str

    # --- Source secrets (only required if their toggle is on) ---
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    serpapi_key: str = ""

    # --- Source toggles ---
    use_adzuna: bool = True
    use_greenhouse: bool = True
    use_lever: bool = True
    use_serpapi: bool = False

    # --- Gemini models (per task) ---
    # Free tier as of May 2026:
    #   gemini-2.5-flash-lite  -> 15 RPM, 1000 RPD  (cheapest, best for classification)
    #   gemini-2.5-flash       -> 10 RPM, 250 RPD   (balanced, best for cover letters)
    #   gemini-2.5-pro         ->  5 RPM, 50 RPD    (most capable, very restrictive)
    # We use lite for the evaluator (1 call per job, easy task) and flash for the
    # synthesizer (1 call per matched job, needs better quality).
    evaluator_model: str = "gemini-2.5-flash-lite"
    evaluator_rpm: int = 15

    synthesizer_model: str = "gemini-2.5-flash"
    synthesizer_rpm: int = 10

    # --- File outputs ---
    output_path: str = "applications_ledger.md"
    state_path: str = ".pipeline_state.json"

    # --- Search parameters ---
    search_location: str = "Melbourne, Victoria, Australia"

    # Adzuna
    adzuna_country: str = "au"
    adzuna_max_days_old: int = 40

    # SerpApi-specific routing
    serpapi_gl: str = "au"
    serpapi_google_domain: str = "google.com.au"

    # Greenhouse / Lever company slugs to poll
    greenhouse_companies: Tuple[str, ...] = field(
        default_factory=lambda: DEFAULT_GREENHOUSE_COMPANIES
    )
    lever_companies: Tuple[str, ...] = field(
        default_factory=lambda: DEFAULT_LEVER_COMPANIES
    )
    # When polling company boards, accept jobs whose location text contains
    # any of these substrings (case-insensitive). Empty tuple = accept all.
    # This keeps Sydney/Brisbane/Melbourne roles and drops US/EU offices.
    location_filters: Tuple[str, ...] = field(default_factory=lambda: (
        "australia", "melbourne", "sydney", "brisbane", "perth", "adelaide",
        "remote", "anywhere",
    ))

    # Phase 1 query list - used by Adzuna + SerpApi.
    # We use explicit bigraphs to prevent pulling tradie/technician roles.
    queries: Tuple[str, ...] = field(default_factory=lambda: (
        "robotics engineer",
        "mechatronics engineer",
        "software engineer",
        "embedded software",
        "firmware engineer",
        "electrical engineer",
        "computer engineer",
        "systems engineer",
        "control systems engineer",
        "automation engineer",
        "hardware engineer",
        "machine learning engineer",
        "AI engineer",
    ))

    # Keywords used to filter Greenhouse / Lever job titles. 
    # Single words are safe here because we are already inside a tech company's domain.
    title_keywords: Tuple[str, ...] = field(default_factory=lambda: (
        "engineer", "engineering", "developer", "robotics", "embedded",
        "firmware", "systems", "controls", "automation", "hardware",
        "graduate", "junior", "software", "computer", "ai", "electrical", 
        "mechatronics", "machine learning", "ml", "algorithms", "perception"
    ))


def load_config() -> Config:
    gemini = os.environ.get("GEMINI_API_KEY")
    if not gemini:
        raise RuntimeError(
            "GEMINI_API_KEY not set. Copy .env.example to .env and add your key."
        )

    use_adzuna = _bool("USE_ADZUNA", True)
    use_greenhouse = _bool("USE_GREENHOUSE", True)
    use_lever = _bool("USE_LEVER", True)
    use_serpapi = _bool("USE_SERPAPI", True)

    adzuna_id = os.environ.get("ADZUNA_APP_ID", "")
    adzuna_key = os.environ.get("ADZUNA_APP_KEY", "")
    serpapi_key = os.environ.get("SERPAPI_KEY", "")

    if use_adzuna and not (adzuna_id and adzuna_key):
        raise RuntimeError(
            "USE_ADZUNA=true but ADZUNA_APP_ID and/or ADZUNA_APP_KEY not set. "
            "Sign up at https://developer.adzuna.com/ and add both to .env, "
            "or set USE_ADZUNA=false."
        )
    if use_serpapi and not serpapi_key:
        raise RuntimeError(
            "USE_SERPAPI=true but SERPAPI_KEY not set. "
            "Sign up at https://serpapi.com/ and add the key to .env, "
            "or set USE_SERPAPI=false."
        )
    if not (use_adzuna or use_greenhouse or use_lever or use_serpapi):
        raise RuntimeError(
            "No job sources enabled. Enable at least one in .env "
            "(USE_ADZUNA, USE_GREENHOUSE, USE_LEVER, or USE_SERPAPI)."
        )

    return Config(
        gemini_api_key=gemini,
        adzuna_app_id=adzuna_id,
        adzuna_app_key=adzuna_key,
        serpapi_key=serpapi_key,
        use_adzuna=use_adzuna,
        use_greenhouse=use_greenhouse,
        use_lever=use_lever,
        use_serpapi=use_serpapi,
    )