from __future__ import annotations

from .base import BaseReportGenerator, GeneratorContext, ReportDataset, ReportTable
from .datasets import parse_date_range
from .registry import register_generator


@register_generator("audit_export")
class AuditExportGenerator(BaseReportGenerator):
    definition_code = "AUDIT_EXPORT"
    generator_key = "audit_export"
    supported_formats = ("CSV", "XLSX", "PDF")

    def build_dataset(self, context: GeneratorContext) -> ReportDataset:
        from apps.audit.models import AuditAction, AuditLog

        parameters = context.parameters or {}
        qs = AuditLog.objects.select_related("organization", "actor").filter(organization_id=context.organization.id)
        start_dt, end_dt = parse_date_range(parameters)
        if start_dt:
            qs = qs.filter(created_at__gte=start_dt)
        if end_dt:
            qs = qs.filter(created_at__lte=end_dt)
        if parameters.get("actor_id"):
            qs = qs.filter(actor_id=parameters["actor_id"])
        if parameters.get("actions"):
            qs = qs.filter(action__in=parameters["actions"])
        if parameters.get("resource_type"):
            qs = qs.filter(resource_type=str(parameters["resource_type"]).strip())
        if parameters.get("resource_id"):
            qs = qs.filter(resource_id=str(parameters["resource_id"]).strip())

        rows = (
            {
                "created_at": log.created_at,
                "actor": getattr(log.actor, "username", None),
                "action": log.action,
                "resource_type": log.resource_type,
                "resource_id": log.resource_id,
                "organization": getattr(log.organization, "name", None),
                "message": log.message,
                "ip_address": log.ip_address,
                "user_agent": log.user_agent,
            }
            for log in qs.order_by("created_at", "id").iterator(chunk_size=1000)
        )
        return ReportDataset(
            title="Audit Export",
            subtitle="Audit log history",
            metadata={"report_type": context.definition.code},
            tables=[
                ReportTable(
                    name="Audit",
                    columns=["created_at", "actor", "action", "resource_type", "resource_id", "organization", "message", "ip_address", "user_agent"],
                    rows=rows,
                    primary=True,
                )
            ],
        )
