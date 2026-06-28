from apps.alerts.models import AlertEvent, AlertOperator


def evaluate_rule(rule, device, metric, value, ts):
    threshold = rule.threshold_float or rule.threshold_integer or rule.threshold_boolean or rule.threshold_text
    if rule.operator == AlertOperator.GT:
        matched = value > threshold
    elif rule.operator == AlertOperator.GTE:
        matched = value >= threshold
    elif rule.operator == AlertOperator.LT:
        matched = value < threshold
    elif rule.operator == AlertOperator.LTE:
        matched = value <= threshold
    elif rule.operator == AlertOperator.EQ:
        matched = value == threshold
    elif rule.operator == AlertOperator.NEQ:
        matched = value != threshold
    else:
        matched = False
    if matched:
        return AlertEvent.objects.create(
            organization=device.organization,
            data_center=device.data_center,
            device=device,
            metric=metric,
            alert_rule=rule,
            severity=rule.severity,
            message=(rule.message_template or f"{metric.code} alert triggered"),
            triggered_at=ts,
        )
    return None
