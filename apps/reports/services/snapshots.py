from __future__ import annotations

def build_execution_snapshots(*, organization, data_center, definition, template, schedule=None, parameters=None, recipients=None, requested_by=None, primary_format=None, attachment_formats=None):
    schedule_recipients = []
    if recipients:
        for recipient in recipients:
            if isinstance(recipient, dict):
                schedule_recipients.append(recipient)
            else:
                schedule_recipients.append({"destination": str(recipient), "channel": "EMAIL", "recipient_type": "TO"})
    return {
        "definition_code_snapshot": getattr(definition, "code", None),
        "definition_version_snapshot": getattr(definition, "version", None),
        "template_name_snapshot": getattr(template, "name", None),
        "template_version_snapshot": getattr(template, "version", None),
        "template_config_snapshot": getattr(template, "config", {}) if isinstance(getattr(template, "config", {}), dict) else {},
        "parameters_snapshot": parameters if isinstance(parameters, dict) else {},
        "scope_snapshot": {
            "organization_id": str(getattr(organization, "id", organization)) if organization else None,
            "organization_name": getattr(organization, "name", None),
            "data_center_id": str(getattr(data_center, "id", data_center)) if data_center else None,
            "data_center_name": getattr(data_center, "name", None),
            "schedule_id": str(schedule.pk) if schedule else None,
        },
        "recipient_snapshot": schedule_recipients,
        "output_config_snapshot": {
            "primary_format": primary_format,
            "attachment_formats": list(attachment_formats or []),
        },
        "requested_by_snapshot": {
            "user_id": str(requested_by.pk) if requested_by else None,
            "username": getattr(requested_by, "username", None),
            "email": getattr(requested_by, "email", None),
            "full_name": getattr(requested_by, "full_name", None),
        },
    }
