from __future__ import annotations

from django.utils import timezone

from apps.alerts.models import AlertEscalationPolicy, AlertEvent, AlertEventLogAction, AlertStatus
from .engine import create_alert_log
from .notifications import create_notifications_for_alert_escalated


def _policy_matches(alert: AlertEvent, policy: AlertEscalationPolicy) -> bool:
    if not policy.is_active or policy.severity != alert.severity:
        return False
    if policy.organization_id and policy.organization_id != alert.organization_id:
        return False
    if policy.data_center_id and policy.data_center_id != alert.data_center_id:
        return False
    return True


def _escalation_reason(alert: AlertEvent, policy: AlertEscalationPolicy) -> str:
    severity = (alert.severity or "UNKNOWN").upper()
    device_name = getattr(alert.device, "name", "unknown device")
    if alert.status == AlertStatus.OPEN and policy.if_not_acknowledged_minutes is not None:
        return (
            f"ESCALATION: {severity} alert {alert.message} on {device_name} "
            f"has not been acknowledged for {policy.if_not_acknowledged_minutes} minutes."
        )
    if policy.if_not_resolved_minutes is not None:
        return (
            f"ESCALATION: {severity} alert {alert.message} on {device_name} "
            f"has not been resolved for {policy.if_not_resolved_minutes} minutes."
        )
    return f"ESCALATION: {severity} alert {alert.message} on {device_name} requires attention."


def _policy_trigger_met(alert: AlertEvent, policy: AlertEscalationPolicy, now):
    open_elapsed = (now - alert.triggered_at).total_seconds() / 60.0
    resolved_elapsed = (now - (alert.acknowledged_at or alert.triggered_at)).total_seconds() / 60.0

    if alert.status == AlertStatus.OPEN and policy.if_not_acknowledged_minutes is not None:
        if open_elapsed >= policy.if_not_acknowledged_minutes:
            return True
    if policy.if_not_resolved_minutes is not None and resolved_elapsed >= policy.if_not_resolved_minutes:
        return True
    return False


def escalate_alert(alert: AlertEvent, policy: AlertEscalationPolicy):
    now = timezone.now()
    metadata = dict(alert.metadata or {})
    policy_key = str(policy.pk)
    escalated_policies = metadata.get("escalated_policy_ids", [])
    if policy_key in escalated_policies:
        return None

    escalated_policies.append(policy_key)
    metadata["last_escalated_at"] = now.isoformat()
    metadata["last_escalation_policy_id"] = policy_key
    metadata["escalated_policy_ids"] = escalated_policies
    alert.metadata = metadata
    alert.save(update_fields=["metadata", "updated_at"])
    create_alert_log(
        alert,
        AlertEventLogAction.ESCALATED,
        old_status=alert.status,
        new_status=alert.status,
        message=_escalation_reason(alert, policy),
        metadata={"policy_id": policy_key},
    )
    create_notifications_for_alert_escalated(alert, policy)
    return alert


def run_alert_escalation_check():
    now = timezone.now()
    alerts = AlertEvent.objects.filter(status__in=[AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED]).select_related("organization", "data_center", "device", "metric")
    policies = AlertEscalationPolicy.objects.filter(is_active=True).select_related("organization", "data_center", "target_role")
    escalated = []
    for alert in alerts:
        for policy in policies:
            if not _policy_matches(alert, policy):
                continue
            if _policy_trigger_met(alert, policy, now):
                result = escalate_alert(alert, policy)
                if result:
                    escalated.append(result)
                break
    return escalated
