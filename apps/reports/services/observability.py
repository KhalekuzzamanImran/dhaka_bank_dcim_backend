from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import is_dataclass, asdict
from time import perf_counter

from django.utils import timezone


REPORT_LOG_FIELDS = (
    "job_id",
    "schedule_id",
    "template_id",
    "definition_code",
    "organization_id",
    "data_center_id",
    "trigger_source",
    "request_id",
    "correlation_id",
    "execution_time_ms",
    "artifact_count",
    "artifact_size_bytes",
    "delivery_count",
    "retry_count",
    "download_count",
    "queue_latency_ms",
)


def _stringify(value):
    if value is None:
        return "-"
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    if is_dataclass(value):
        return _stringify(asdict(value))
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, sort_keys=True, default=str)
    return str(value)


def build_report_context(
    *,
    job_id=None,
    schedule_id=None,
    template_id=None,
    definition_code=None,
    organization_id=None,
    data_center_id=None,
    job=None,
    schedule=None,
    template=None,
    definition=None,
    organization=None,
    data_center=None,
    trigger_source=None,
    request=None,
    request_id=None,
    correlation_id=None,
    execution_time_ms=None,
    artifact_count=None,
    artifact_size_bytes=None,
    delivery_count=None,
    retry_count=None,
    download_count=None,
    queue_latency_ms=None,
    **extra,
):
    context = {field: "-" for field in REPORT_LOG_FIELDS}

    if job_id is not None:
        context["job_id"] = job_id
    if schedule_id is not None:
        context["schedule_id"] = schedule_id
    if template_id is not None:
        context["template_id"] = template_id
    if definition_code is not None:
        context["definition_code"] = definition_code
    if organization_id is not None:
        context["organization_id"] = organization_id
    if data_center_id is not None:
        context["data_center_id"] = data_center_id

    if job is not None:
        context["job_id"] = getattr(job, "pk", None) or getattr(job, "id", None) or "-"
        schedule = schedule or getattr(job, "schedule", None)
        template = template or getattr(job, "template", None)
        definition = definition or getattr(job, "definition", None)
        organization = organization or getattr(job, "organization", None)
        data_center = data_center or getattr(job, "data_center", None)
        trigger_source = trigger_source or getattr(job, "trigger_source", None)
    if schedule is not None:
        context["schedule_id"] = getattr(schedule, "pk", None) or getattr(schedule, "id", None) or "-"
        template = template or getattr(schedule, "template", None)
        organization = organization or getattr(schedule, "organization", None)
        data_center = data_center or getattr(schedule, "data_center", None)
    if template is not None:
        context["template_id"] = getattr(template, "pk", None) or getattr(template, "id", None) or "-"
        definition = definition or getattr(template, "definition", None)
        organization = organization or getattr(template, "organization", None)
    if definition is not None:
        context["definition_code"] = getattr(definition, "code", None) or getattr(definition, "pk", None) or "-"
    if organization is not None:
        context["organization_id"] = getattr(organization, "pk", None) or getattr(organization, "id", None) or "-"
    if data_center is not None:
        context["data_center_id"] = getattr(data_center, "pk", None) or getattr(data_center, "id", None) or "-"
    if trigger_source is not None:
        context["trigger_source"] = trigger_source
    if request is not None:
        request_id = request_id or getattr(request, "headers", {}).get("X-Request-ID") or getattr(request, "META", {}).get("HTTP_X_REQUEST_ID")
        correlation_id = correlation_id or getattr(request, "headers", {}).get("X-Correlation-ID") or getattr(request, "META", {}).get("HTTP_X_CORRELATION_ID")
    if request_id is not None:
        context["request_id"] = request_id
    if correlation_id is not None:
        context["correlation_id"] = correlation_id
    if execution_time_ms is not None:
        context["execution_time_ms"] = int(execution_time_ms)
    if artifact_count is not None:
        context["artifact_count"] = int(artifact_count)
    if artifact_size_bytes is not None:
        context["artifact_size_bytes"] = int(artifact_size_bytes)
    if delivery_count is not None:
        context["delivery_count"] = int(delivery_count)
    if retry_count is not None:
        context["retry_count"] = int(retry_count)
    if download_count is not None:
        context["download_count"] = int(download_count)
    if queue_latency_ms is not None:
        context["queue_latency_ms"] = int(queue_latency_ms)

    for key, value in extra.items():
        context[key] = value
    return context


def format_report_context(**kwargs) -> str:
    context = build_report_context(**kwargs)
    return " ".join(f"{field}={_stringify(context.get(field))}" for field in REPORT_LOG_FIELDS)


def log_report_event(logger, message: str, **kwargs):
    logger.info("%s %s", message, format_report_context(**kwargs))


def log_report_metric(logger, metric_name: str, value=1, **kwargs):
    context = build_report_context(**kwargs)
    context["metric_name"] = metric_name
    context["metric_value"] = value
    logger.info("report_metric %s", " ".join(f"{key}={_stringify(value)}" for key, value in context.items()))


@contextmanager
def track_report_duration(logger, message: str, **kwargs):
    started = perf_counter()
    log_report_event(logger, f"{message} started", **kwargs)
    try:
        yield
    except Exception:
        elapsed_ms = round((perf_counter() - started) * 1000)
        log_report_event(logger, f"{message} failed", execution_time_ms=elapsed_ms, **kwargs)
        raise
    else:
        elapsed_ms = round((perf_counter() - started) * 1000)
        log_report_event(logger, f"{message} completed", execution_time_ms=elapsed_ms, **kwargs)
