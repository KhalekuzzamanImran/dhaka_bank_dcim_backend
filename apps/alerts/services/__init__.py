"""Alert service facade.

Exports alert evaluation functions from the package root so imports such as
``from apps.alerts.services import evaluate_latest`` work correctly.
"""
from django.utils import timezone

from apps.alerts.models import AlertEvent, AlertRule, AlertStatus
from .evaluator import evaluate_rule


def _value_from_latest(latest):
    if latest.value_float is not None:
        return latest.value_float
    if latest.value_integer is not None:
        return latest.value_integer
    if latest.value_boolean is not None:
        return latest.value_boolean
    return latest.value_text


def _threshold(rule):
    if rule.threshold_float is not None:
        return rule.threshold_float
    if rule.threshold_integer is not None:
        return rule.threshold_integer
    if rule.threshold_boolean is not None:
        return rule.threshold_boolean
    return rule.threshold_text


def _match(operator, value, threshold):
    if operator == "GT":
        return value > threshold
    if operator == "GTE":
        return value >= threshold
    if operator == "LT":
        return value < threshold
    if operator == "LTE":
        return value <= threshold
    if operator == "EQ":
        return value == threshold
    if operator == "NEQ":
        return value != threshold
    return False


def evaluate_latest(latest):
    """Evaluate active alert rules against a LatestTelemetry row."""
    org_rules = AlertRule.objects.filter(
        is_active=True,
        metric=latest.metric,
        organization=latest.organization,
        device__isnull=True,
    )
    device_rules = AlertRule.objects.filter(
        is_active=True,
        metric=latest.metric,
        device=latest.device,
    )
    rules = (org_rules | device_rules).distinct()

    created = []
    value = _value_from_latest(latest)

    for rule in rules:
        threshold = _threshold(rule)
        try:
            active = _match(rule.operator, value, threshold)
        except Exception:
            active = False

        if active:
            event, was_created = AlertEvent.objects.get_or_create(
                organization=latest.organization,
                data_center=latest.data_center,
                device=latest.device,
                metric=latest.metric,
                alert_rule=rule,
                status=AlertStatus.OPEN,
                defaults={
                    "severity": rule.severity,
                    "message": rule.message_template or f"{latest.device.name} {latest.metric.code} alarm",
                    "triggered_at": timezone.now(),
                    "value_float": latest.value_float,
                    "value_integer": latest.value_integer,
                    "value_boolean": latest.value_boolean,
                    "value_text": latest.value_text,
                },
            )
            if was_created:
                created.append(event)
    return created


__all__ = ["evaluate_latest", "evaluate_rule"]
