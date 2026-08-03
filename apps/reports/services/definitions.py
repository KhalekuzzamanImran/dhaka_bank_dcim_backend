from __future__ import annotations

from copy import deepcopy

from ..definition_seeds import REPORT_DEFINITION_SEEDS


REPORT_TYPE_TO_DEFINITION_CODE = {
    "device_inventory": "DEVICE_INVENTORY",
    "telemetry_export": "TELEMETRY_EXPORT",
    "alert_summary": "ALERT_SUMMARY",
    "alert_export": "ALERT_DETAIL",
    "notification_delivery": "NOTIFICATION_DELIVERY",
    "audit_export": "AUDIT_EXPORT",
    "room_environment": "ENVIRONMENTAL_TREND",
}


def get_definition_code_for_report_type(report_type: str | None) -> str | None:
    if not report_type:
        return None
    return REPORT_TYPE_TO_DEFINITION_CODE.get(str(report_type).strip())


def seed_report_definitions(ReportDefinitionModel=None, *, seeds=None):
    """Seed canonical report definitions idempotently.

    The model class may be supplied explicitly from a migration's historical app
    registry. When omitted, the live model from ``apps.reports.models`` is used.
    """

    if ReportDefinitionModel is None:
        from ..models import ReportDefinition as ReportDefinitionModel  # local import for runtime use

    definition_seeds = seeds or REPORT_DEFINITION_SEEDS
    created = 0
    updated = 0

    for seed in definition_seeds:
        defaults = deepcopy(seed)
        code = defaults.pop("code")
        defaults.setdefault("version", 1)
        obj, was_created = ReportDefinitionModel.objects.update_or_create(code=code, defaults=defaults)
        if was_created:
            created += 1
        else:
            updated += 1
            changed = False
            for field_name, value in defaults.items():
                if getattr(obj, field_name) != value:
                    setattr(obj, field_name, value)
                    changed = True
            if changed:
                obj.save()

    return {"created": created, "updated": updated}
