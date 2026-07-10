"""Central alert engine for telemetry, polling, and trap-confirmed alarms.

This module owns the full alert lifecycle:
open, update, acknowledge, resolve, suppress, log, and duration tracking.
The collectors still only produce telemetry; the engine decides whether an
alert should exist and keeps duplicate prevention centralized.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.alerts.models import (
    AlertComment,
    AlertConditionState,
    AlertEvent,
    AlertEventLog,
    AlertEventLogAction,
    AlertOperator,
    AlertRule,
    AlertSeverity,
    AlertStatus,
    AlertSuppressionWindow,
)
from apps.telemetry.models import LatestTelemetry
from .cache import ALERT_RULE_MATCH_CACHE_TIMEOUT_SECONDS, get_alert_rule_match_cache_version

logger = logging.getLogger(__name__)


ACTIVE_ALERT_STATUSES = {AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED}


def _value_from_latest(latest: LatestTelemetry):
    if latest.value_float is not None:
        return latest.value_float
    if latest.value_integer is not None:
        return latest.value_integer
    if latest.value_boolean is not None:
        return latest.value_boolean
    return latest.value_text


def _threshold_from_rule(rule: AlertRule):
    if rule.threshold_float is not None:
        return rule.threshold_float
    if rule.threshold_integer is not None:
        return rule.threshold_integer
    if rule.threshold_boolean is not None:
        return rule.threshold_boolean
    if rule.threshold_text is not None:
        return rule.threshold_text
    return None


def _value_snapshot(latest: LatestTelemetry | None = None, value=None, threshold=None):
    snapshot = {}
    if latest is not None:
        snapshot.update(
            {
                "organization_id": str(latest.organization_id) if latest.organization_id else None,
                "data_center_id": str(latest.data_center_id) if latest.data_center_id else None,
                "device_id": str(latest.device_id) if latest.device_id else None,
                "metric_id": str(latest.metric_id) if latest.metric_id else None,
                "metric_code": getattr(latest.metric, "code", None),
                "value_float": latest.value_float,
                "value_integer": latest.value_integer,
                "value_boolean": latest.value_boolean,
                "value_text": latest.value_text,
                "last_seen_at": latest.last_seen_at.isoformat() if latest.last_seen_at else None,
                "source": latest.source,
            }
        )
    if value is not None:
        snapshot["value"] = value
    if threshold is not None:
        snapshot["threshold"] = threshold
    return snapshot


def _severity_rank(severity: str) -> int:
    order = {
        AlertSeverity.INFO: 0,
        AlertSeverity.WARNING: 1,
        AlertSeverity.CRITICAL: 2,
        getattr(AlertSeverity, "EMERGENCY", "EMERGENCY"): 3,
    }
    return order.get(severity, 0)


def _rule_specificity(rule: AlertRule) -> tuple:
    """Return a deterministic specificity ordering for overlapping rules."""

    if rule.device_id:
        rank = 4
    elif rule.data_center_id and rule.device_type_id:
        rank = 3
    elif rule.data_center_id:
        rank = 2
    elif rule.device_type_id:
        rank = 1
    else:
        rank = 0
    return (
        rank,
        rule.created_at or timezone.now(),
        str(rule.pk),
    )


def _matching_rule_cache_key(latest: LatestTelemetry) -> str:
    version = get_alert_rule_match_cache_version()
    return ":".join(
        [
            "alert",
            "matching-rules",
            version,
            str(latest.organization_id),
            str(latest.data_center_id),
            str(latest.device_id),
            str(getattr(latest.device, "device_type_id", None)),
            str(latest.metric_id),
        ]
    )


def _matching_rule_queryset(latest: LatestTelemetry):
    device_type_id = getattr(latest.device, "device_type_id", None)
    conditions = Q(device_id=latest.device_id)
    if latest.data_center_id and device_type_id:
        conditions |= Q(
            device_id__isnull=True,
            data_center_id=latest.data_center_id,
            device_type_id=device_type_id,
        )
    if latest.data_center_id:
        conditions |= Q(
            device_id__isnull=True,
            data_center_id=latest.data_center_id,
            device_type_id__isnull=True,
        )
    if device_type_id:
        conditions |= Q(
            device_id__isnull=True,
            data_center_id__isnull=True,
            device_type_id=device_type_id,
        )
    conditions |= Q(
        device_id__isnull=True,
        data_center_id__isnull=True,
        device_type_id__isnull=True,
    )
    return (
        AlertRule.objects.filter(
            is_active=True,
            organization_id=latest.organization_id,
            metric_id=latest.metric_id,
        )
        .filter(conditions)
        .select_related("organization", "data_center", "device_type", "device", "metric")
    )


def get_matching_rules(latest: LatestTelemetry):
    cache_key = _matching_rule_cache_key(latest)
    cached_ids = cache.get(cache_key)
    if cached_ids is not None:
        rule_map = {
            rule.pk: rule
            for rule in _matching_rule_queryset(latest).filter(pk__in=cached_ids)
        }
        return [rule_map[rule_id] for rule_id in cached_ids if rule_id in rule_map]

    matching = list(_matching_rule_queryset(latest))
    matching.sort(key=_rule_specificity, reverse=True)
    cache.set(cache_key, [rule.pk for rule in matching], ALERT_RULE_MATCH_CACHE_TIMEOUT_SECONDS)
    return matching


def _compare(operator: str, value, threshold) -> bool:
    if operator == AlertOperator.GT:
        return value > threshold
    if operator == AlertOperator.GTE:
        return value >= threshold
    if operator == AlertOperator.LT:
        return value < threshold
    if operator == AlertOperator.LTE:
        return value <= threshold
    if operator == AlertOperator.EQ:
        return value == threshold
    if operator == AlertOperator.NEQ:
        return value != threshold
    return False


def evaluate_rule_condition(rule: AlertRule, latest: LatestTelemetry):
    value = _value_from_latest(latest)
    threshold = _threshold_from_rule(rule)
    if threshold is None:
        return False, value, threshold
    try:
        return _compare(rule.operator, value, threshold), value, threshold
    except Exception:
        logger.exception("Alert rule comparison failed rule=%s latest=%s", rule.pk, latest.pk)
        return False, value, threshold


def _alert_scope_q(latest: LatestTelemetry, rule: AlertRule):
    return Q(
        organization=latest.organization,
        data_center=latest.data_center,
        device=latest.device,
        metric=latest.metric,
        alert_rule=rule,
        status__in=ACTIVE_ALERT_STATUSES,
    )


def _condition_state_key(latest: LatestTelemetry, rule: AlertRule):
    return {
        "organization": latest.organization,
        "data_center": latest.data_center,
        "device": latest.device,
        "metric": latest.metric,
        "alert_rule": rule,
    }


def _has_suppression(latest: LatestTelemetry, rule: AlertRule) -> bool:
    now = timezone.now()
    qs = (
        AlertSuppressionWindow.objects.filter(
            organization=latest.organization,
            is_active=True,
            starts_at__lte=now,
            ends_at__gte=now,
        )
        .filter(Q(data_center__isnull=True) | Q(data_center=latest.data_center))
        .filter(Q(device__isnull=True) | Q(device=latest.device))
        .filter(Q(metric__isnull=True) | Q(metric=latest.metric))
    )
    return qs.exists()


def create_alert_log(
    alert: AlertEvent,
    action: str,
    old_status: str | None = None,
    new_status: str | None = None,
    actor=None,
    message: str | None = None,
    value_snapshot: dict | None = None,
    metadata: dict | None = None,
):
    return AlertEventLog.objects.create(
        alert_event=alert,
        action=action,
        old_status=old_status,
        new_status=new_status,
        actor=actor,
        message=message,
        value_snapshot=value_snapshot or {},
        metadata=metadata or {},
    )


def _alert_payload(latest: LatestTelemetry, rule: AlertRule, value, threshold, state: str):
    base_message = rule.message_template or f"{latest.device.name} {latest.metric.code} alert"
    if threshold is not None:
        return f"{base_message} ({value} {rule.operator} {threshold})"
    return base_message


def _upsert_condition_state(latest: LatestTelemetry, rule: AlertRule, value, now):
    defaults = {
        "first_seen_at": now,
        "last_seen_at": now,
        "last_value_float": latest.value_float,
        "last_value_integer": latest.value_integer,
        "last_value_boolean": latest.value_boolean,
        "last_value_text": latest.value_text,
        "condition_is_active": True,
    }
    state, created = AlertConditionState.objects.get_or_create(
        **_condition_state_key(latest, rule),
        defaults=defaults,
    )
    if not created:
        state.last_seen_at = now
        state.last_value_float = latest.value_float
        state.last_value_integer = latest.value_integer
        state.last_value_boolean = latest.value_boolean
        state.last_value_text = latest.value_text
        state.condition_is_active = True
        state.save(
            update_fields=[
                "last_seen_at",
                "last_value_float",
                "last_value_integer",
                "last_value_boolean",
                "last_value_text",
                "condition_is_active",
                "updated_at",
            ]
        )
    return state


def _clear_condition_state(latest: LatestTelemetry, rule: AlertRule):
    AlertConditionState.objects.filter(**_condition_state_key(latest, rule)).delete()


def open_alert(rule: AlertRule, latest: LatestTelemetry, value, threshold):
    now = timezone.now()
    payload = _value_snapshot(latest, value=value, threshold=threshold)
    alert = AlertEvent.objects.create(
        organization=latest.organization,
        data_center=latest.data_center,
        device=latest.device,
        metric=latest.metric,
        alert_rule=rule,
        severity=rule.severity,
        status=AlertStatus.OPEN,
        message=_alert_payload(latest, rule, value, threshold, "OPEN"),
        triggered_at=now,
        last_seen_at=now,
        value_float=latest.value_float,
        value_integer=latest.value_integer,
        value_boolean=latest.value_boolean,
        value_text=latest.value_text,
        occurrence_count=1,
        metadata={"first_value": payload, "engine": "central"},
    )
    create_alert_log(
        alert,
        AlertEventLogAction.OPENED,
        new_status=AlertStatus.OPEN,
        message=alert.message,
        value_snapshot=payload,
        metadata={"rule_id": str(rule.pk)},
    )
    try:
        from .notifications import create_notifications_for_alert_opened

        create_notifications_for_alert_opened(alert)
    except Exception:
        logger.exception("Alert notification creation failed for alert=%s", alert.pk)
    return alert


def update_active_alert(alert: AlertEvent, latest: LatestTelemetry, value, threshold):
    now = timezone.now()
    old_status = alert.status
    alert.last_seen_at = now
    alert.message = _alert_payload(latest, alert.alert_rule, value, threshold, alert.status)
    alert.value_float = latest.value_float
    alert.value_integer = latest.value_integer
    alert.value_boolean = latest.value_boolean
    alert.value_text = latest.value_text
    alert.occurrence_count = (alert.occurrence_count or 1) + 1
    alert.metadata = {**(alert.metadata or {}), "last_snapshot": _value_snapshot(latest, value=value, threshold=threshold)}
    alert.save(
        update_fields=[
            "last_seen_at",
            "message",
            "value_float",
            "value_integer",
            "value_boolean",
            "value_text",
            "occurrence_count",
            "metadata",
            "updated_at",
        ]
    )
    create_alert_log(
        alert,
        AlertEventLogAction.UPDATED,
        old_status=old_status,
        new_status=alert.status,
        message=alert.message,
        value_snapshot=_value_snapshot(latest, value=value, threshold=threshold),
    )
    return alert


def resolve_alert(alert: AlertEvent, latest: LatestTelemetry | None, resolution_type: str = "AUTO", actor=None, comment: str | None = None):
    old_status = alert.status
    now = timezone.now()
    alert.status = AlertStatus.RESOLVED
    alert.resolved_at = now
    alert.resolved_by = actor
    alert.resolution_type = resolution_type
    alert.resolve_comment = comment or alert.resolve_comment
    alert.last_seen_at = now
    alert.save(
        update_fields=[
            "status",
            "resolved_at",
            "resolved_by",
            "resolution_type",
            "resolve_comment",
            "last_seen_at",
            "updated_at",
        ]
    )
    create_alert_log(
        alert,
        AlertEventLogAction.RESOLVED,
        old_status=old_status,
        new_status=alert.status,
        actor=actor,
        message=comment or "Alert resolved",
        value_snapshot=_value_snapshot(
            latest,
            value=_value_from_latest(latest) if latest is not None else None,
            threshold=_threshold_from_rule(alert.alert_rule) if alert.alert_rule_id else None,
        ),
        metadata={"resolution_type": resolution_type},
    )
    try:
        from .notifications import create_notifications_for_alert_resolved

        create_notifications_for_alert_resolved(alert)
    except Exception:
        logger.exception("Alert resolved notification creation failed for alert=%s", alert.pk)
    return alert


def acknowledge_alert(alert: AlertEvent, user, comment: str | None = None):
    if alert.status != AlertStatus.OPEN:
        return alert
    alert.status = AlertStatus.ACKNOWLEDGED
    alert.acknowledged_by = user
    alert.acknowledged_at = timezone.now()
    if comment:
        alert.acknowledge_comment = comment
    alert.save(
        update_fields=[
            "status",
            "acknowledged_by",
            "acknowledged_at",
            "acknowledge_comment",
            "updated_at",
        ]
    )
    if comment:
        AlertComment.objects.create(alert_event=alert, user=user, comment=comment)
        create_alert_log(
            alert,
            AlertEventLogAction.COMMENTED,
            old_status=AlertStatus.ACKNOWLEDGED,
            new_status=AlertStatus.ACKNOWLEDGED,
            actor=user,
            message=comment,
        )
    create_alert_log(
        alert,
        AlertEventLogAction.ACKNOWLEDGED,
        old_status=AlertStatus.OPEN,
        new_status=AlertStatus.ACKNOWLEDGED,
        actor=user,
        message=comment or "Alert acknowledged",
    )
    return alert


def manually_resolve_alert(alert: AlertEvent, user, comment: str | None = None):
    alert = resolve_alert(alert, latest=None, resolution_type="MANUAL", actor=user, comment=comment)
    if comment:
        AlertComment.objects.create(alert_event=alert, user=user, comment=comment)
        create_alert_log(
            alert,
            AlertEventLogAction.COMMENTED,
            old_status=AlertStatus.RESOLVED,
            new_status=AlertStatus.RESOLVED,
            actor=user,
            message=comment,
        )
    return alert


def suppress_alert(alert: AlertEvent, user=None, reason: str | None = None):
    old_status = alert.status
    alert.status = AlertStatus.SUPPRESSED
    alert.resolved_at = timezone.now()
    alert.resolved_by = user
    alert.resolution_type = "SUPPRESSED"
    alert.resolve_comment = reason or alert.resolve_comment
    alert.save(
        update_fields=[
            "status",
            "resolved_at",
            "resolved_by",
            "resolution_type",
            "resolve_comment",
            "updated_at",
        ]
    )
    create_alert_log(
        alert,
        AlertEventLogAction.SUPPRESSED,
        old_status=old_status,
        new_status=AlertStatus.SUPPRESSED,
        actor=user,
        message=reason or "Alert suppressed",
    )
    return alert


def create_or_update_trap_alert(*, device, event_code: str | None, event_name: str | None, severity: str, message: str, triggered_at, trap_oid: str, raw_payload: dict | None = None):
    """Create one durable alert for a trap event and prevent duplicate open alerts."""

    metadata = {
        "trap_oid": trap_oid,
        "trap_event_code": event_code,
        "trap_event_name": event_name,
        "source": "SNMP_TRAP",
        "raw_payload": raw_payload or {},
    }
    with transaction.atomic():
        existing = (
            AlertEvent.objects.select_for_update()
            .filter(
                organization=device.organization,
                data_center=device.data_center,
                device=device,
                metric__isnull=True,
                alert_rule__isnull=True,
                status__in=ACTIVE_ALERT_STATUSES,
                metadata__trap_oid=trap_oid,
            )
            .order_by("-triggered_at", "-created_at")
            .first()
        )
        if existing and (event_code is None or existing.metadata.get("trap_event_code") == event_code):
            existing.last_seen_at = triggered_at
            existing.message = message
            existing.severity = severity
            existing.occurrence_count = (existing.occurrence_count or 1) + 1
            existing.metadata = {**(existing.metadata or {}), **metadata}
            existing.save(
                update_fields=["last_seen_at", "message", "severity", "occurrence_count", "metadata", "updated_at"]
            )
            create_alert_log(
                existing,
                AlertEventLogAction.UPDATED,
                old_status=existing.status,
                new_status=existing.status,
                message=message,
                metadata=metadata,
            )
            return existing

        alert = AlertEvent.objects.create(
            organization=device.organization,
            data_center=device.data_center,
            device=device,
            metric=None,
            alert_rule=None,
            severity=severity,
            status=AlertStatus.OPEN,
            message=message,
            triggered_at=triggered_at,
            last_seen_at=triggered_at,
            occurrence_count=1,
            metadata=metadata,
        )
        create_alert_log(
            alert,
            AlertEventLogAction.OPENED,
            new_status=AlertStatus.OPEN,
            message=message,
            metadata=metadata,
        )
        try:
            from .notifications import create_notifications_for_alert_opened

            create_notifications_for_alert_opened(alert)
        except Exception:
            logger.exception("Trap alert notification creation failed for alert=%s", alert.pk)
        return alert


def _maybe_create_duration_alert(latest: LatestTelemetry, rule: AlertRule, value, threshold):
    now = timezone.now()
    state = _upsert_condition_state(latest, rule, value, now)
    duration = int(rule.duration_seconds or 0)
    if duration <= 0 or (now - state.first_seen_at).total_seconds() >= duration:
        return True
    return False


def _active_alert_for_rule(latest: LatestTelemetry, rule: AlertRule):
    return (
        AlertEvent.objects.select_for_update()
        .filter(_alert_scope_q(latest, rule))
        .order_by("-triggered_at", "-created_at")
        .first()
    )


def _ensure_alert_for_true_condition(latest: LatestTelemetry, rule: AlertRule, value, threshold):
    now = timezone.now()
    alert = _active_alert_for_rule(latest, rule)
    if _has_suppression(latest, rule):
        if alert and alert.status != AlertStatus.SUPPRESSED:
            suppress_alert(alert, reason="Suppression window active")
        elif not alert:
            alert = AlertEvent.objects.create(
                organization=latest.organization,
                data_center=latest.data_center,
                device=latest.device,
                metric=latest.metric,
                alert_rule=rule,
                severity=rule.severity,
                status=AlertStatus.SUPPRESSED,
                message=_alert_payload(latest, rule, value, threshold, "SUPPRESSED"),
                triggered_at=now,
                last_seen_at=now,
                value_float=latest.value_float,
                value_integer=latest.value_integer,
                value_boolean=latest.value_boolean,
                value_text=latest.value_text,
                occurrence_count=1,
                resolution_type="SUPPRESSED",
                resolve_comment="Suppression window active",
            )
            create_alert_log(alert, AlertEventLogAction.SUPPRESSED, new_status=AlertStatus.SUPPRESSED, message="Suppressed by maintenance window")
        return alert

    if alert:
        if alert.status in ACTIVE_ALERT_STATUSES:
            return update_active_alert(alert, latest, value, threshold)
        return alert

    if not _maybe_create_duration_alert(latest, rule, value, threshold):
        return None

    alert = open_alert(rule, latest, value, threshold)
    return alert


def _resolve_if_needed(latest: LatestTelemetry, rule: AlertRule):
    alert = _active_alert_for_rule(latest, rule)
    if not alert:
        _clear_condition_state(latest, rule)
        return None
    if alert.status == AlertStatus.SUPPRESSED:
        _clear_condition_state(latest, rule)
        return alert
    if alert.status in ACTIVE_ALERT_STATUSES:
        return resolve_alert(alert, latest, resolution_type="AUTO")
    return alert


def evaluate_latest(latest: LatestTelemetry):
    """Evaluate a single LatestTelemetry row and update the alert lifecycle."""

    created_or_updated = []
    for rule in get_matching_rules(latest):
        condition_is_true, value, threshold = evaluate_rule_condition(rule, latest)
        if condition_is_true:
            with transaction.atomic():
                alert = _ensure_alert_for_true_condition(latest, rule, value, threshold)
                if alert:
                    created_or_updated.append(alert)
        else:
            with transaction.atomic():
                alert = _resolve_if_needed(latest, rule)
                if alert:
                    created_or_updated.append(alert)
    return created_or_updated


def evaluate_many(latest_rows: Iterable[LatestTelemetry]):
    results = []
    for latest in latest_rows:
        results.extend(evaluate_latest(latest))
    return results
