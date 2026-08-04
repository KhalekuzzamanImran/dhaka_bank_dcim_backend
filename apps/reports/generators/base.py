from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping


@dataclass(slots=True)
class GeneratorContext:
    job: Any
    definition: Any
    organization: Any
    data_center: Any | None
    parameters: dict[str, Any]
    template_snapshot: dict[str, Any]
    scope_snapshot: dict[str, Any]
    output_config_snapshot: dict[str, Any]
    timezone: Any
    generated_at: datetime


@dataclass(slots=True)
class ReportTable:
    name: str
    columns: list[str]
    rows: Iterable[Mapping[str, Any]]
    title: str | None = None
    description: str | None = None
    primary: bool = False
    include_in_csv: bool = True


@dataclass(slots=True)
class ReportDataset:
    title: str
    subtitle: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    summary_rows: list[dict[str, Any]] = field(default_factory=list)
    tables: list[ReportTable] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def primary_table(self) -> ReportTable | None:
        for table in self.tables:
            if table.primary:
                return table
        return self.tables[0] if self.tables else None


@dataclass(slots=True)
class RenderedArtifact:
    format: str
    path: str
    filename: str
    content_type: str
    size_bytes: int
    checksum_sha256: str = ""


class BaseReportGenerator:
    definition_code: str = ""
    generator_key: str = ""
    supported_formats: tuple[str, ...] = ("CSV",)
    row_limit_for_pdf: int = 1000

    def validate_parameters(self, context: GeneratorContext) -> None:  # pragma: no cover - default hook
        return None

    def build_dataset(self, context: GeneratorContext) -> ReportDataset:
        raise NotImplementedError

    def build_summary(self, context: GeneratorContext, dataset: ReportDataset) -> dict[str, Any]:  # pragma: no cover - optional hook
        return {}

    def get_filename(self, context: GeneratorContext, output_format: str) -> str:
        from django.utils.text import slugify

        prefix = slugify(context.definition.code or context.output_config_snapshot.get("definition_code") or "report") or "report"
        timestamp = context.generated_at.strftime("%Y%m%d_%H%M%S")
        return f"{prefix}_{context.job.pk}_{timestamp}.{output_format.lower()}"

    def render(self, context: GeneratorContext, dataset: ReportDataset, output_format: str, output_path: str) -> RenderedArtifact:
        from .csv_renderer import render_csv
        from .pdf_renderer import render_pdf
        from .xlsx_renderer import render_xlsx

        normalized = str(output_format or "").strip().upper()
        if normalized == "CSV":
            return render_csv(dataset, context, output_path, self.get_filename(context, normalized))
        if normalized == "XLSX":
            return render_xlsx(dataset, context, output_path, self.get_filename(context, normalized))
        if normalized == "PDF":
            return render_pdf(dataset, context, output_path, self.get_filename(context, normalized), row_limit=self.row_limit_for_pdf)
        raise ValueError(f"Unsupported output format: {output_format}")
