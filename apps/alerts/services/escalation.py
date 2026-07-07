from __future__ import annotations

from django.utils import timezone

from apps.alerts.models import AlertEscalationPolicy, AlertEvent, AlertEventLogAction, AlertStatus
from .engine import create_alert_log
from .notifications import create_notifications_for_alert_opened


def _policy_matches(alert: AlertEvent, policy: AlertEscalationPolicy) -> bool:
    if not policy.is_active or policy.severity != alert.severity:
        return False
    if policy.organization_id and policy.organization_id != alert.organization_id:
        return False
    if policy.data_center_id and policy.data_center_id != alert.data_center_id:
        return False
    return True


def escalate_alert(alert: AlertEvent, policy: AlertEscalationPolicy):
    now = timezone.now()
    metadata = dict(alert.metadata or {})
    last_escalated_at = metadata.get("last_escalated_at")
    if last_escalated_at and policy.pk and metadata.get("last_escalation_policy_id") == str(policy.pk):
        return None

    metadata["last_escalated_at"] = now.isoformat()
    metadata["last_escalation_policy_id"] = str(policy.pk)
    alert.metadata = metadata
    alert.save(update_fields=["metadata", "updated_at"])
    create_alert_log(
        alert,
        AlertEventLogAction.ESCALATED,
        old_status=alert.status,
        new_status=alert.status,
        message="Alert escalation triggered",
        metadata={"policy_id": str(policy.pk)},
    )
    create_notifications_for_alert_opened(alert)
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
            elapsed_minutes = (now - (alert.acknowledged_at or alert.triggered_at)).total_seconds() / 60.0
            if alert.status == AlertStatus.OPEN and policy.if_not_acknowledged_minutes is not None and elapsed_minutes >= policy.if_not_acknowledged_minutes:
                result = escalate_alert(alert, policy)
                if result:
                    escalated.append(result)
                break
            if policy.if_not_resolved_minutes is not None and elapsed_minutes >= policy.if_not_resolved_minutes:
                result = escalate_alert(alert, policy)
                if result:
                    escalated.append(result)
                break
    return escalated
