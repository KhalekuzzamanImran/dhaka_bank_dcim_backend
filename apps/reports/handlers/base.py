from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ReportData:
    headers: list[str]
    rows: Any
    report_type: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReportHandlerContext:
    job: Any
    parameters: dict[str, Any]
    template_config: dict[str, Any]


class BaseReportHandler(ABC):
    code: str

    def validate_parameters(self, context: ReportHandlerContext) -> dict[str, Any]:
        return self.collect_data(context).metadata

    @abstractmethod
    def collect_data(self, context: ReportHandlerContext) -> ReportData:
        raise NotImplementedError

    def build_report_data(self, context: ReportHandlerContext, collected_data: ReportData | None = None) -> ReportData:
        return collected_data or self.collect_data(context)
