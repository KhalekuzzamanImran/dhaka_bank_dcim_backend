from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RenderedArtifact:
    file_path: str
    file_name: str
    content_type: str
    format: str
    artifact_type: str = "PRIMARY"
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseReportRenderer(ABC):
    format_code: str

    @abstractmethod
    def render(self, *, job, report_data, output_config) -> RenderedArtifact:
        raise NotImplementedError
