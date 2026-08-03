from __future__ import annotations

from copy import deepcopy

from django.core.exceptions import ValidationError
from django.db import transaction

from .configuration import validate_report_template_config
from .definitions import (
    build_definition_capabilities,
    get_active_definition_by_code,
    get_definition_code_for_report_type,
    validate_definition_request,
)
from .permissions import ensure_organization_access, ensure_data_center_access


def build_report_template_snapshot(template) -> dict:
    return {
        "id": str(template.pk),
        "code": template.code,
        "name": template.name,
        "version": template.version,
        "definition_code": getattr(template.definition, "code", None),
        "configuration": deepcopy(template.config if isinstance(template.config, dict) else {}),
        "default_parameters": deepcopy(template.default_parameters if isinstance(template.default_parameters, dict) else {}),
        "primary_format": template.primary_format,
        "attachment_formats": deepcopy(template.attachment_formats if isinstance(template.attachment_formats, list) else []),
        "include_charts": template.include_charts,
        "include_raw_data": template.include_raw_data,
    }


def _template_mutation_signature(template, *, name, description, is_active, config, default_parameters, primary_format, attachment_formats, include_charts, include_raw_data, definition):
    return {
        "name": name,
        "description": description,
        "is_active": is_active,
        "config": deepcopy(config if isinstance(config, dict) else {}),
        "default_parameters": deepcopy(default_parameters if isinstance(default_parameters, dict) else {}),
        "primary_format": primary_format,
        "attachment_formats": list(attachment_formats or []),
        "include_charts": bool(include_charts),
        "include_raw_data": bool(include_raw_data),
        "definition_id": getattr(definition, "id", None),
    }


def _template_signature(template):
    return _template_mutation_signature(
        template,
        name=template.name,
        description=template.description,
        is_active=template.is_active,
        config=template.config,
        default_parameters=template.default_parameters,
        primary_format=template.primary_format,
        attachment_formats=template.attachment_formats,
        include_charts=template.include_charts,
        include_raw_data=template.include_raw_data,
        definition=template.definition,
    )


@transaction.atomic
def create_report_template(*, actor=None, organization, data_center=None, definition_code=None, config=None, name=None, code=None, description=None, default_parameters=None, primary_format=None, attachment_formats=None, include_charts=False, include_raw_data=False, is_active=True, updated_by=None):
    ensure_organization_access(actor, organization)
    if data_center is not None:
        ensure_data_center_access(actor, data_center)
        if data_center.organization_id != organization.id:
            raise ValidationError({"data_center": "Data center must belong to the selected organization."})

    config = validate_report_template_config(config or {}, existing_config={})
    report_type = config.get("report_type")
    expected_definition_code = get_definition_code_for_report_type(report_type)
    if definition_code and expected_definition_code and definition_code != expected_definition_code:
        raise ValidationError({"definition": "Definition must match the report type."})
    definition_code = definition_code or expected_definition_code
    definition = get_active_definition_by_code(definition_code) if definition_code else None

    if report_type and not definition:
        raise ValidationError({"definition": "A matching active report definition is required."})
    if definition_code and not definition:
        raise ValidationError({"definition": "Selected report definition is inactive or unknown."})

    if definition:
        validate_definition_request(
            definition,
            organization,
            data_center,
            default_parameters or config.get("default_parameters") or {},
            primary_format or config.get("output_format"),
            attachment_formats or config.get("attachment_formats") or [],
            config.get("delivery_channels") or [],
        )

    from ..models import ReportTemplate

    template = ReportTemplate.objects.create(
        organization=organization,
        definition=definition,
        name=name or config.get("name") or code,
        code=code,
        description=description or config.get("description", ""),
        config=config,
        default_parameters=default_parameters or config.get("default_parameters") or {},
        primary_format=primary_format or config.get("output_format"),
        attachment_formats=attachment_formats or config.get("attachment_formats") or [],
        include_charts=include_charts,
        include_raw_data=include_raw_data,
        is_active=is_active,
        updated_by=updated_by,
    )
    return template


@transaction.atomic
def update_report_template(template, *, actor=None, organization=None, data_center=None, definition_code=None, config=None, name=None, description=None, default_parameters=None, primary_format=None, attachment_formats=None, include_charts=None, include_raw_data=None, is_active=None, updated_by=None):
    if organization is None:
        organization = template.organization
    ensure_organization_access(actor, organization)
    if data_center is not None:
        ensure_data_center_access(actor, data_center)
        if data_center.organization_id != organization.id:
            raise ValidationError({"data_center": "Data center must belong to the selected organization."})

    existing_signature = _template_signature(template)

    config = validate_report_template_config(config if config is not None else template.config, existing_config=template.config or {})
    report_type = config.get("report_type")
    expected_definition_code = get_definition_code_for_report_type(report_type)
    if definition_code and expected_definition_code and definition_code != expected_definition_code:
        raise ValidationError({"definition": "Definition must match the report type."})
    definition_code = definition_code or expected_definition_code
    definition = get_active_definition_by_code(definition_code) if definition_code else template.definition

    if report_type and not definition:
        raise ValidationError({"definition": "A matching active report definition is required."})
    if definition_code and not definition:
        raise ValidationError({"definition": "Selected report definition is inactive or unknown."})
    if definition:
        validate_definition_request(
            definition,
            organization,
            data_center,
            default_parameters if default_parameters is not None else template.default_parameters,
            primary_format if primary_format is not None else template.primary_format,
            attachment_formats if attachment_formats is not None else template.attachment_formats,
            config.get("delivery_channels") or [],
        )

    template.organization = organization
    template.definition = definition
    template.name = name if name is not None else template.name
    template.description = description if description is not None else template.description
    template.config = config
    if default_parameters is not None:
        template.default_parameters = default_parameters
    if primary_format is not None:
        template.primary_format = primary_format
    if attachment_formats is not None:
        template.attachment_formats = attachment_formats
    if include_charts is not None:
        template.include_charts = include_charts
    if include_raw_data is not None:
        template.include_raw_data = include_raw_data
    if is_active is not None:
        template.is_active = is_active
    if updated_by is not None:
        template.updated_by = updated_by

    updated_signature = _template_signature(template)
    if updated_signature != existing_signature:
        template.version = (template.version or 1) + 1

    template.save()
    return template
