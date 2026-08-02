from __future__ import annotations

from typing import Any

__all__ = ["generate_report_job"]


def __getattr__(name: str) -> Any:
    if name == "generate_report_job":
        from .generator import generate_report_job

        return generate_report_job
    raise AttributeError(name)
