"""
Intermediate caching system for idempotency and flexible reruns.
Saves and loads per-student machine-readable JSON results.
"""

from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Optional, Dict, Any

from grader.models import StudentGradingResult


class ResultsCache:
    """Manages intermediate JSON results per student to enable idempotent executions and --rerun-llm."""

    def __init__(self, results_dir: str | Path = "results"):
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)

    def get_result_file(self, student_id: str) -> Path:
        return self.results_dir / f"{student_id}.json"

    def has_cached_result(self, student_id: str) -> bool:
        return self.get_result_file(student_id).exists()

    def load_result(self, student_id: str) -> Optional[StudentGradingResult]:
        file_path = self.get_result_file(student_id)
        if not file_path.exists():
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return StudentGradingResult.model_validate(data)
        except Exception:
            return None

    def save_result(self, result: StudentGradingResult) -> Path:
        file_path = self.get_result_file(result.student_id)
        # Write-then-rename so the dashboard never reads a half-written file while grading runs in parallel
        tmp_path = file_path.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(result.model_dump(mode="json"), f, indent=2)
        os.replace(tmp_path, file_path)
        return file_path
