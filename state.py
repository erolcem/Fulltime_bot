"""
state.py
--------
Persistent set of job_ids we've already evaluated. Saves after every job so
that a Ctrl-C mid-run doesn't cause the next run to re-spend Gemini quota
on the same postings.
"""

import json
import logging
import os
from typing import Set

log = logging.getLogger(__name__)


class ProcessedState:
    def __init__(self, path: str):
        self.path = path
        self.ids: Set[str] = set()
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.ids = set(data.get("processed_ids", []))
        except Exception as e:
            log.warning("Could not load state file %s: %s. Starting fresh.", self.path, e)
            self.ids = set()

    def save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"processed_ids": sorted(self.ids)}, f, indent=2)
        os.replace(tmp, self.path)  # atomic on POSIX

    def has(self, job_id: str) -> bool:
        return job_id in self.ids

    def add(self, job_id: str) -> None:
        self.ids.add(job_id)
