from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.test import APIClient

from apps.alerts.models import (
    AlertComment,
    AlertEvent,
    AlertEventLog,
    AlertEventLogAction,
    AlertEscalationPolicy,
    AlertRule,
    AlertSeverity,
    AlertStatus,
    AlertSuppressionWindow,
)
from apps.access_control.models import Permission, Role, RolePermission
from apps.alerts.services import acknowledge_alert, evaluate_latest, manually_resolve_alert
from apps.alerts.services import engine as alert_engine
from apps.alerts.services.escalation import run_alert_escalation_check
from apps.alerts.services.notifications import get_alert_recipients, get_escalation_recipients
from apps.alerts.services.summary import build_alert_summary
from apps.alerts.serializers import AlertEscalationPolicySerializer
from apps.access_control.models import Role, RoleScope, UserResourceAccess
from apps.accounts.models import User
from apps.datacenters.models import DataCenter
from apps.devices.models import Device, DeviceModel, DeviceType, Vendor
from apps.organizations.models import Organization
from apps.telemetry.models import LatestTelemetry, MetricCategory, MetricDataType, MetricDefinition
from apps.traps.models import SNMPTrapOIDMapping, SNMPTrapSource
from collectors.snmp_trap_receiver.services import process_snmp_trap
from apps.notifications.models import Notification, NotificationChannel, NotificationStatus
from apps.notifications.services import deliver_pending_notifications


def _device_setup():
    org = Organization.objects.create(name="Org", code="ORG")
    dc = DataCenter.objects.create(organization=org, name="DC", code="DC")
    device_type = DeviceType.objects.create(name="UPS", code="UPS", category="POWER")
    vendor = Vendor.objects.create(name="Vendor", code="VENDOR")
    model = DeviceModel.objects.create(vendor=vendor, device_type=device_type, name="Model", model_number="M1")
    device = Device.objects.create(organization=org, data_center=dc, device_type=device_type, device_model=model, name="UPS-01", code="UPS-01")
    metric = MetricDefinition.objects.create(code="UPS_ON_BATTERY", name="UPS On Battery", category=MetricCategory.STATUS, data_type=MetricDataType.INTEGER, is_active=True)
    return org, dc, device, metric


def _rule(device, metric, threshold=1, duration_seconds=0):
    return AlertRule.objects.create(
        organization=device.organization,
        data_center=None,
        device_type=None,
        device=device,
        metric=metric,
        name="UPS On Battery",
        operator="EQ",
        threshold_integer=threshold,
        severity=AlertSeverity.CRITICAL,
        duration_seconds=duration_seconds,
        is_active=True,
    )


def _rule_with_severity(device, metric, *, severity, threshold=1, duration_seconds=0):
    return AlertRule.objects.create(
        organization=device.organization,
        data_center=None,
        device_type=None,
        device=device,
        metric=metric,
        name=f"{metric.code} rule",
        operator="EQ",
        threshold_integer=threshold,
        severity=severity,
        duration_seconds=duration_seconds,
        is_active=True,
    )


def _latest(device, metric, value):
    return LatestTelemetry.objects.create(
        organization=device.organization,
        data_center=device.data_center,
        device=device,
        metric=metric,
        value_integer=value,
        quality="GOOD",
        last_seen_at=timezone.now(),
        source="test",
    )


def _escalation_policy(org, dc, severity="CRITICAL", trigger_field="ack", minutes=15, target_role=None):
    kwargs = {
        "organization": org,
        "data_center": dc,
        "severity": severity,
        "is_active": True,
        "channel": "EMAIL",
    }
    if trigger_field == "ack":
        kwargs["if_not_acknowledged_minutes"] = minutes
    else:
        kwargs["if_not_resolved_minutes"] = minutes
    if target_role is not None:
        kwargs["target_role"] = target_role
    return AlertEscalationPolicy(**kwargs)


def _perm(code):
    return Permission.objects.get_or_create(
        code=code,
        defaults={"module": code.split(".")[0], "description": code},
    )[0]


def _role(code, name, perm_codes):
    role, _ = Role.objects.update_or_create(
        code=code,
        defaults={"name": name, "scope": "ORGANIZATION", "status": "ACTIVE"},
    )
    for perm_code in perm_codes:
        RolePermission.objects.get_or_create(role=role, permission=_perm(perm_code))
    return role


def _make_access(user, role, *, organization=None, data_center=None, room=None, rack=None, device=None):
    from apps.access_control.models import UserResourceAccess

    return UserResourceAccess.objects.create(
        user=user,
        role=role,
        organization=organization,
        data_center=data_center,
        room=room,
        rack=rack,
        device=device,
        assigned_by=user,
        is_active=True,
    )


def _json_results(response):
    payload = response.json()
    return payload["results"] if isinstance(payload, dict) and "results" in payload else payload


@pytest.mark.django_db
def test_alert_opens_and_does_not_duplicate():
    _, _, device, metric = _device_setup()
    _rule(device, metric)
    latest = _latest(device, metric, 1)

    evaluate_latest(latest)
    evaluate_latest(latest)

    assert AlertEvent.objects.filter(device=device, metric=metric, status=AlertStatus.OPEN).count() == 1
    assert AlertEventLog.objects.filter(action=AlertEventLogAction.OPENED).count() == 1
    assert AlertEventLog.objects.filter(action=AlertEventLogAction.UPDATED).count() >= 1


@pytest.mark.django_db
def test_alert_auto_resolves_when_condition_clears():
    _, _, device, metric = _device_setup()
    _rule(device, metric)
    latest = _latest(device, metric, 1)
    evaluate_latest(latest)
    alert = AlertEvent.objects.get(device=device, metric=metric)
    original_message = alert.message
    latest.value_integer = 0
    latest.save(update_fields=["value_integer", "updated_at"])

    evaluate_latest(latest)

    alert.refresh_from_db()
    assert alert.status == AlertStatus.RESOLVED
    assert alert.resolved_at is not None
    assert alert.message == original_message
    assert AlertEventLog.objects.filter(action=AlertEventLogAction.RESOLVED).exists()


@pytest.mark.django_db
def test_threshold_zero_is_respected():
    _, _, device, metric = _device_setup()
    AlertRule.objects.create(
        organization=device.organization,
        device=device,
        metric=metric,
        name="Zero threshold",
        operator="GTE",
        threshold_integer=0,
        severity=AlertSeverity.WARNING,
        duration_seconds=0,
        is_active=True,
    )
    latest = _latest(device, metric, 0)
    evaluate_latest(latest)
    assert AlertEvent.objects.filter(device=device, metric=metric, status=AlertStatus.OPEN).count() == 1


@pytest.mark.django_db
def test_matching_rules_prefer_device_specific_and_cache_by_device():
    org, dc, device, metric = _device_setup()
    other_device = Device.objects.create(
        organization=org,
        data_center=dc,
        device_type=device.device_type,
        device_model=device.device_model,
        name="UPS-02",
        code="UPS-02",
    )

    broad_rule = AlertRule.objects.create(
        organization=org,
        data_center=None,
        device_type=None,
        device=None,
        metric=metric,
        name="Broad rule",
        operator="EQ",
        threshold_integer=1,
        severity=AlertSeverity.WARNING,
        duration_seconds=0,
        is_active=True,
    )
    device_rule_1 = AlertRule.objects.create(
        organization=org,
        data_center=None,
        device_type=None,
        device=device,
        metric=metric,
        name="Device 1 rule",
        operator="EQ",
        threshold_integer=1,
        severity=AlertSeverity.CRITICAL,
        duration_seconds=0,
        is_active=True,
    )
    device_rule_2 = AlertRule.objects.create(
        organization=org,
        data_center=None,
        device_type=None,
        device=other_device,
        metric=metric,
        name="Device 2 rule",
        operator="EQ",
        threshold_integer=1,
        severity=AlertSeverity.CRITICAL,
        duration_seconds=0,
        is_active=True,
    )

    latest_1 = _latest(device, metric, 1)
    latest_2 = _latest(other_device, metric, 1)

    rules_1 = alert_engine.get_matching_rules(latest_1)
    rules_2 = alert_engine.get_matching_rules(latest_2)
    rules_1_again = alert_engine.get_matching_rules(latest_1)

    assert [rule.pk for rule in rules_1][:2] == [device_rule_1.pk, broad_rule.pk]
    assert [rule.pk for rule in rules_2][:2] == [device_rule_2.pk, broad_rule.pk]
    assert [rule.pk for rule in rules_1_again][:2] == [device_rule_1.pk, broad_rule.pk]


@pytest.mark.django_db
def test_alert_rule_update_invalidates_matching_cache():
    org, dc, device, metric = _device_setup()
    broad_rule = AlertRule.objects.create(
        organization=org,
        data_center=None,
        device_type=None,
        device=None,
        metric=metric,
        name="Broad rule",
        operator="EQ",
        threshold_integer=1,
        severity=AlertSeverity.WARNING,
        duration_seconds=0,
        is_active=True,
    )
    device_rule = AlertRule.objects.create(
        organization=org,
        data_center=None,
        device_type=None,
        device=device,
        metric=metric,
        name="Device rule",
        operator="EQ",
        threshold_integer=1,
        severity=AlertSeverity.CRITICAL,
        duration_seconds=0,
        is_active=True,
    )
    latest = _latest(device, metric, 1)

    initial_rules = alert_engine.get_matching_rules(latest)
    assert [rule.pk for rule in initial_rules][:2] == [device_rule.pk, broad_rule.pk]

    device_rule.is_active = False
    device_rule.save(update_fields=["is_active", "updated_at"])

    updated_rules = alert_engine.get_matching_rules(latest)
    assert [rule.pk for rule in updated_rules] == [broad_rule.pk]


@pytest.mark.django_db
def test_alert_rule_delete_invalidates_matching_cache():
    org, dc, device, metric = _device_setup()
    broad_rule = AlertRule.objects.create(
        organization=org,
        data_center=None,
        device_type=None,
        device=None,
        metric=metric,
        name="Broad rule",
        operator="EQ",
        threshold_integer=1,
        severity=AlertSeverity.WARNING,
        duration_seconds=0,
        is_active=True,
    )
    device_rule = AlertRule.objects.create(
        organization=org,
        data_center=None,
        device_type=None,
        device=device,
        metric=metric,
        name="Device rule",
        operator="EQ",
        threshold_integer=1,
        severity=AlertSeverity.CRITICAL,
        duration_seconds=0,
        is_active=True,
    )
    latest = _latest(device, metric, 1)

    initial_rules = alert_engine.get_matching_rules(latest)
    assert [rule.pk for rule in initial_rules][:2] == [device_rule.pk, broad_rule.pk]

    device_rule.delete()

    updated_rules = alert_engine.get_matching_rules(latest)
    assert [rule.pk for rule in updated_rules] == [broad_rule.pk]


@pytest.mark.django_db
def test_alert_rule_cache_invalidation_failure_does_not_block_save(monkeypatch):
    org, _, device, metric = _device_setup()
    rule = AlertRule(
        organization=org,
        data_center=None,
        device_type=None,
        device=device,
        metric=metric,
        name="Cache invalidation failure",
        operator="EQ",
        threshold_integer=1,
        severity=AlertSeverity.WARNING,
        duration_seconds=0,
        is_active=True,
    )

    monkeypatch.setattr(
        "apps.alerts.models.invalidate_alert_rule_match_cache",
        lambda: (_ for _ in ()).throw(RuntimeError("cache unavailable")),
    )

    rule.save()
    assert rule.pk is not None


@pytest.mark.django_db
def test_acknowledge_and_resolve_adds_comments():
    _, _, device, metric = _device_setup()
    _rule(device, metric)
    latest = _latest(device, metric, 1)
    evaluate_latest(latest)
    alert = AlertEvent.objects.get(device=device, metric=metric)

    acknowledge_alert(alert, user=None, comment="Checking device")
    alert.refresh_from_db()
    assert alert.status == AlertStatus.ACKNOWLEDGED
    assert AlertComment.objects.filter(alert_event=alert).count() == 1

    manually_resolve_alert(alert, user=None, comment="Issue cleared")
    alert.refresh_from_db()
    assert alert.status == AlertStatus.RESOLVED
    assert AlertComment.objects.filter(alert_event=alert).count() == 2


@pytest.mark.django_db
def test_duration_prevents_early_open(monkeypatch):
    _, _, device, metric = _device_setup()
    _rule(device, metric, duration_seconds=60)
    latest = _latest(device, metric, 1)

    frozen = timezone.now()
    monkeypatch.setattr(alert_engine.timezone, "now", lambda: frozen)
    evaluate_latest(latest)
    assert AlertEvent.objects.filter(device=device, metric=metric).count() == 0

    monkeypatch.setattr(alert_engine.timezone, "now", lambda: frozen + timedelta(seconds=61))
    evaluate_latest(latest)
    assert AlertEvent.objects.filter(device=device, metric=metric, status=AlertStatus.OPEN).count() == 1


@pytest.mark.django_db
def test_alert_rule_requires_exactly_one_threshold_field():
    org, dc, device, metric = _device_setup()
    rule = AlertRule(
        organization=org,
        data_center=dc,
        device=device,
        metric=metric,
        name="Invalid threshold",
        operator="EQ",
        severity=AlertSeverity.WARNING,
        duration_seconds=0,
        is_active=True,
    )

    with pytest.raises(ValidationError):
        rule.full_clean()

    rule.threshold_integer = 1
    rule.threshold_float = 1.5
    with pytest.raises(ValidationError):
        rule.full_clean()


@pytest.mark.django_db
def test_alert_rule_scope_must_match_selected_device_and_datacenter():
    org, dc, device, metric = _device_setup()
    other_org = Organization.objects.create(name="Other Org", code="ORG-2")
    other_dc = DataCenter.objects.create(organization=other_org, name="Other DC", code="DC-2")
    other_type = DeviceType.objects.create(name="ATS", code="ATS", category="POWER")

    rule = AlertRule(
        organization=org,
        data_center=other_dc,
        device_type=other_type,
        device=device,
        metric=metric,
        name="Scope mismatch",
        operator="EQ",
        threshold_integer=1,
        severity=AlertSeverity.CRITICAL,
        duration_seconds=0,
        is_active=True,
    )

    with pytest.raises(ValidationError) as exc:
        rule.full_clean()

    assert "data_center" in exc.value.message_dict
    assert "device_type" in exc.value.message_dict


@pytest.mark.django_db
def test_alert_rule_duplicate_active_definition_is_rejected():
    org, dc, device, metric = _device_setup()
    AlertRule.objects.create(
        organization=org,
        data_center=dc,
        device_type=None,
        device=device,
        metric=metric,
        name="Original",
        operator="EQ",
        threshold_integer=1,
        severity=AlertSeverity.CRITICAL,
        duration_seconds=0,
        is_active=True,
    )

    duplicate = AlertRule(
        organization=org,
        data_center=dc,
        device_type=None,
        device=device,
        metric=metric,
        name="Duplicate",
        operator="EQ",
        threshold_integer=1,
        severity=AlertSeverity.CRITICAL,
        duration_seconds=0,
        is_active=True,
    )

    with pytest.raises(ValidationError) as exc:
        duplicate.full_clean()

    assert "__all__" in exc.value.message_dict


@pytest.mark.django_db
def test_suppression_window_invalid_time_range_is_rejected():
    org, dc, device, metric = _device_setup()
    starts_at = timezone.now()
    ends_at = starts_at - timedelta(minutes=5)
    window = AlertSuppressionWindow(
        organization=org,
        data_center=dc,
        device=device,
        metric=metric,
        starts_at=starts_at,
        ends_at=ends_at,
        is_active=True,
    )

    with pytest.raises(ValidationError) as exc:
        window.full_clean()

    assert "starts_at" in exc.value.message_dict
    assert "ends_at" in exc.value.message_dict


@pytest.mark.django_db
def test_suppression_window_empty_scope_is_rejected():
    window = AlertSuppressionWindow(
        organization=None,
        starts_at=timezone.now(),
        ends_at=timezone.now() + timedelta(minutes=10),
        is_active=True,
    )

    with pytest.raises(ValidationError):
        window.full_clean()


@pytest.mark.django_db
def test_suppression_window_datacenter_must_belong_to_organization():
    org, dc, device, metric = _device_setup()
    other_org = Organization.objects.create(name="Other Org", code="ORG-3")
    other_dc = DataCenter.objects.create(organization=other_org, name="Other DC", code="DC-3")
    window = AlertSuppressionWindow(
        organization=org,
        data_center=other_dc,
        device=device,
        metric=metric,
        starts_at=timezone.now(),
        ends_at=timezone.now() + timedelta(minutes=10),
        is_active=True,
    )

    with pytest.raises(ValidationError) as exc:
        window.full_clean()

    assert "data_center" in exc.value.message_dict


@pytest.mark.django_db
def test_suppression_window_device_scope_must_match_selected_parents():
    org, dc, device, metric = _device_setup()
    other_org = Organization.objects.create(name="Other Org", code="ORG-4")
    other_dc = DataCenter.objects.create(organization=other_org, name="Other DC", code="DC-4")
    window = AlertSuppressionWindow(
        organization=other_org,
        data_center=other_dc,
        device=device,
        metric=metric,
        starts_at=timezone.now(),
        ends_at=timezone.now() + timedelta(minutes=10),
        is_active=True,
    )

    with pytest.raises(ValidationError) as exc:
        window.full_clean()

    assert "organization" in exc.value.message_dict
    assert "data_center" in exc.value.message_dict


@pytest.mark.django_db
def test_overlapping_active_suppression_window_is_rejected():
    org, dc, device, metric = _device_setup()
    starts_at = timezone.now()
    ends_at = starts_at + timedelta(minutes=30)
    AlertSuppressionWindow.objects.create(
        organization=org,
        data_center=dc,
        device=device,
        metric=metric,
        starts_at=starts_at,
        ends_at=ends_at,
        is_active=True,
    )

    overlap = AlertSuppressionWindow(
        organization=org,
        data_center=dc,
        device=device,
        metric=metric,
        starts_at=starts_at + timedelta(minutes=10),
        ends_at=ends_at + timedelta(minutes=10),
        is_active=True,
    )

    with pytest.raises(ValidationError) as exc:
        overlap.full_clean()

    assert "__all__" in exc.value.message_dict


@pytest.mark.django_db
def test_non_overlapping_suppression_window_is_allowed():
    org, dc, device, metric = _device_setup()
    AlertSuppressionWindow.objects.create(
        organization=org,
        data_center=dc,
        device=device,
        metric=metric,
        starts_at=timezone.now(),
        ends_at=timezone.now() + timedelta(minutes=30),
        is_active=True,
    )
    ok = AlertSuppressionWindow(
        organization=org,
        data_center=dc,
        device=device,
        metric=metric,
        starts_at=timezone.now() + timedelta(minutes=31),
        ends_at=timezone.now() + timedelta(minutes=60),
        is_active=True,
    )

    ok.full_clean()


@pytest.mark.django_db
def test_inactive_overlapping_suppression_window_does_not_block_active_one():
    org, dc, device, metric = _device_setup()
    AlertSuppressionWindow.objects.create(
        organization=org,
        data_center=dc,
        device=device,
        metric=metric,
        starts_at=timezone.now(),
        ends_at=timezone.now() + timedelta(minutes=30),
        is_active=False,
    )
    active = AlertSuppressionWindow(
        organization=org,
        data_center=dc,
        device=device,
        metric=metric,
        starts_at=timezone.now() + timedelta(minutes=10),
        ends_at=timezone.now() + timedelta(minutes=20),
        is_active=True,
    )

    active.full_clean()


@pytest.mark.django_db
def test_escalation_policy_without_trigger_is_rejected():
    org, dc, _, _ = _device_setup()
    policy = AlertEscalationPolicy(
        organization=org,
        data_center=dc,
        severity=AlertSeverity.CRITICAL,
        channel="EMAIL",
    )

    with pytest.raises(ValidationError):
        policy.full_clean()


@pytest.mark.django_db
def test_escalation_policy_with_non_positive_minutes_is_rejected():
    org, dc, _, _ = _device_setup()
    policy = AlertEscalationPolicy(
        organization=org,
        data_center=dc,
        severity=AlertSeverity.CRITICAL,
        if_not_acknowledged_minutes=0,
        channel="EMAIL",
    )

    with pytest.raises(ValidationError):
        policy.full_clean()


@pytest.mark.django_db
def test_escalation_policy_serializer_allows_target_role_only():
    org, dc, _, _ = _device_setup()
    role = Role.objects.create(name="Ops", code="OPS-TARGET-ROLE", scope=RoleScope.ORGANIZATION)
    serializer = AlertEscalationPolicySerializer(
        data={
            "organization": org.pk,
            "data_center": dc.pk,
            "severity": AlertSeverity.CRITICAL,
            "if_not_acknowledged_minutes": 10,
            "target_role": role.pk,
            "channel": "EMAIL",
            "is_active": True,
        }
    )

    assert serializer.is_valid(), serializer.errors
    instance = serializer.save()
    assert instance.target_role_id == role.id


@pytest.mark.django_db
def test_escalation_policy_serializer_allows_target_users_only():
    org, dc, _, _ = _device_setup()
    user = User.objects.create_user(username="target-user", password="test12345", is_active=True)
    serializer = AlertEscalationPolicySerializer(
        data={
            "organization": org.pk,
            "data_center": dc.pk,
            "severity": AlertSeverity.CRITICAL,
            "if_not_acknowledged_minutes": 10,
            "target_users": [user.pk],
            "channel": "EMAIL",
            "is_active": True,
        }
    )

    assert serializer.is_valid(), serializer.errors
    instance = serializer.save()
    assert list(instance.target_users.values_list("pk", flat=True)) == [user.pk]


@pytest.mark.django_db
def test_escalation_policy_serializer_allows_both_targets():
    org, dc, _, _ = _device_setup()
    role = Role.objects.create(name="Ops", code="OPS-TARGET-BOTH", scope=RoleScope.ORGANIZATION)
    user = User.objects.create_user(username="target-user-both", password="test12345", is_active=True)
    serializer = AlertEscalationPolicySerializer(
        data={
            "organization": org.pk,
            "data_center": dc.pk,
            "severity": AlertSeverity.CRITICAL,
            "if_not_acknowledged_minutes": 10,
            "target_role": role.pk,
            "target_users": [user.pk],
            "channel": "EMAIL",
            "is_active": True,
        }
    )

    assert serializer.is_valid(), serializer.errors
    instance = serializer.save()
    assert instance.target_role_id == role.id
    assert list(instance.target_users.values_list("pk", flat=True)) == [user.pk]


@pytest.mark.django_db
def test_escalation_policy_serializer_rejects_missing_targets():
    org, dc, _, _ = _device_setup()
    serializer = AlertEscalationPolicySerializer(
        data={
            "organization": org.pk,
            "data_center": dc.pk,
            "severity": AlertSeverity.CRITICAL,
            "if_not_acknowledged_minutes": 10,
            "channel": "EMAIL",
            "is_active": True,
        }
    )

    assert not serializer.is_valid()
    assert "target_role" in serializer.errors


@pytest.mark.django_db
def test_escalation_policy_serializer_can_update_from_role_to_users_only():
    org, dc, _, _ = _device_setup()
    role = Role.objects.create(name="Ops", code="OPS-UPDATE-ROLE", scope=RoleScope.ORGANIZATION)
    existing_user = User.objects.create_user(username="existing-target", password="test12345", is_active=True)
    policy = AlertEscalationPolicy.objects.create(
        organization=org,
        data_center=dc,
        severity=AlertSeverity.CRITICAL,
        if_not_acknowledged_minutes=10,
        target_role=role,
        channel="EMAIL",
        is_active=True,
    )
    policy.target_users.add(existing_user)
    new_user = User.objects.create_user(username="new-target", password="test12345", is_active=True)

    serializer = AlertEscalationPolicySerializer(
        instance=policy,
        data={
            "organization": org.pk,
            "data_center": dc.pk,
            "severity": AlertSeverity.CRITICAL,
            "if_not_acknowledged_minutes": 10,
            "target_role": None,
            "target_users": [new_user.pk],
            "channel": "EMAIL",
            "is_active": True,
        },
        partial=True,
    )

    assert serializer.is_valid(), serializer.errors
    updated = serializer.save()
    updated.refresh_from_db()
    assert updated.target_role is None
    assert list(updated.target_users.values_list("pk", flat=True)) == [new_user.pk]


@pytest.mark.django_db
def test_escalation_recipients_allow_target_users_only_policy():
    org, dc, device, metric = _device_setup()
    user = User.objects.create_user(username="target-only", password="test12345", is_active=True)
    role = Role.objects.create(name="Ops", code="OPS-TARGET-ONLY", scope=RoleScope.ORGANIZATION)
    _make_access(user, role, organization=org)

    _rule(device, metric)
    evaluate_latest(_latest(device, metric, 1))
    alert = AlertEvent.objects.get(device=device, metric=metric)

    policy = AlertEscalationPolicy.objects.create(
        organization=org,
        data_center=dc,
        severity=AlertSeverity.CRITICAL,
        if_not_acknowledged_minutes=10,
        channel="EMAIL",
        is_active=True,
    )
    policy.target_users.add(user)

    recipients = get_escalation_recipients(alert, policy)
    assert [recipient.pk for recipient in recipients] == [user.pk]


@pytest.mark.django_db
def test_alert_recipients_are_db_scoped_and_deduplicated():
    org, dc, device, metric = _device_setup()
    role = _role("ALERT_RECIPIENT", "Alert Recipient", ["alert.view"])
    user = User.objects.create_user(username="recipient", password="test12345", is_active=True)
    _make_access(user, role, organization=org)
    # Duplicate access rows should not produce duplicate recipients.
    _make_access(user, role, organization=org)

    unauthorized = User.objects.create_user(username="unauthorized", password="test12345", is_active=True)
    inactive = User.objects.create_user(username="inactive", password="test12345", is_active=False)
    _make_access(inactive, role, organization=org)

    _rule(device, metric)
    evaluate_latest(_latest(device, metric, 1))
    alert = AlertEvent.objects.get(device=device, metric=metric)

    recipients = get_alert_recipients(alert)
    recipient_ids = [recipient.pk for recipient in recipients]

    assert user.pk in recipient_ids
    assert unauthorized.pk not in recipient_ids
    assert inactive.pk not in recipient_ids
    assert len(recipient_ids) == len(set(recipient_ids))


@pytest.mark.django_db
def test_escalation_policy_datacenter_must_belong_to_organization():
    org, dc, _, _ = _device_setup()
    other_org = Organization.objects.create(name="Other Org", code="ORG-5")
    other_dc = DataCenter.objects.create(organization=other_org, name="Other DC", code="DC-5")
    role = Role.objects.create(name="Ops", code="OPS-ROLE", scope=RoleScope.ORGANIZATION)
    policy = AlertEscalationPolicy(
        organization=org,
        data_center=other_dc,
        severity=AlertSeverity.CRITICAL,
        if_not_acknowledged_minutes=10,
        target_role=role,
        channel="EMAIL",
    )

    with pytest.raises(ValidationError) as exc:
        policy.full_clean()

    assert "data_center" in exc.value.message_dict


@pytest.mark.django_db
def test_duplicate_active_escalation_policy_is_rejected():
    org, dc, _, _ = _device_setup()
    role = Role.objects.create(name="Ops", code="OPS-ROLE-2", scope=RoleScope.ORGANIZATION)
    AlertEscalationPolicy.objects.create(
        organization=org,
        data_center=dc,
        severity=AlertSeverity.CRITICAL,
        if_not_acknowledged_minutes=10,
        target_role=role,
        channel="EMAIL",
        is_active=True,
    )
    duplicate = AlertEscalationPolicy(
        organization=org,
        data_center=dc,
        severity=AlertSeverity.CRITICAL,
        if_not_acknowledged_minutes=10,
        target_role=role,
        channel="EMAIL",
        is_active=True,
    )

    with pytest.raises(ValidationError) as exc:
        duplicate.full_clean()

    assert "__all__" in exc.value.message_dict


@pytest.mark.django_db
def test_inactive_duplicate_escalation_policy_does_not_block_active_one():
    org, dc, _, _ = _device_setup()
    role = Role.objects.create(name="Ops", code="OPS-ROLE-3", scope=RoleScope.ORGANIZATION)
    AlertEscalationPolicy.objects.create(
        organization=org,
        data_center=dc,
        severity=AlertSeverity.CRITICAL,
        if_not_acknowledged_minutes=10,
        target_role=role,
        channel="EMAIL",
        is_active=False,
    )
    active = AlertEscalationPolicy(
        organization=org,
        data_center=dc,
        severity=AlertSeverity.CRITICAL,
        if_not_acknowledged_minutes=10,
        target_role=role,
        channel="EMAIL",
        is_active=True,
    )

    active.full_clean()


@pytest.mark.django_db
def test_alert_list_response_is_frontend_friendly_and_scoped():
    org, dc, device, metric = _device_setup()
    role = _role("ALERT_VIEWER", "Alert Viewer", ["alert.view"])
    user = User.objects.create_user(username="alert-user", password="test12345", is_active=True)
    _make_access(user, role, organization=org)

    _rule(device, metric)
    evaluate_latest(_latest(device, metric, 1))

    other_org = Organization.objects.create(name="Other Org", code="ORG-LIST")
    other_dc = DataCenter.objects.create(organization=other_org, name="Other DC", code="DC-LIST")
    other_type = DeviceType.objects.create(name="PAC", code="PAC-LIST", category="COOLING")
    other_vendor = Vendor.objects.create(name="Other Vendor", code="OTHER-VENDOR-LIST")
    other_model = DeviceModel.objects.create(vendor=other_vendor, device_type=other_type, name="Other Model", model_number="OM-1")
    other_device = Device.objects.create(
        organization=other_org,
        data_center=other_dc,
        device_type=other_type,
        device_model=other_model,
        name="PAC-99",
        code="PAC-99",
    )
    other_metric = MetricDefinition.objects.create(
        code="OTHER_METRIC",
        name="Other Metric",
        category=MetricCategory.STATUS,
        data_type=MetricDataType.INTEGER,
        is_active=True,
    )
    AlertRule.objects.create(
        organization=other_org,
        device=other_device,
        metric=other_metric,
        name="Other rule",
        operator="EQ",
        threshold_integer=1,
        severity=AlertSeverity.CRITICAL,
        duration_seconds=0,
        is_active=True,
    )
    evaluate_latest(_latest(other_device, other_metric, 1))

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.get("/api/v1/alerts/alert-events/")

    assert response.status_code == 200
    results = _json_results(response)
    assert len(results) == 1
    item = results[0]
    assert item["device_name"] == "UPS-01"
    assert item["metric_code"] == "UPS_ON_BATTERY"
    assert isinstance(item["age_seconds"], int)
    assert item["is_active"] is True


@pytest.mark.django_db
def test_alert_detail_includes_comments_and_logs():
    org, dc, device, metric = _device_setup()
    role = _role("ALERT_MANAGER", "Alert Manager", ["alert.view", "alert.acknowledge", "alert.resolve"])
    user = User.objects.create_user(username="alert-manager", password="test12345", is_active=True)
    _make_access(user, role, organization=org)

    _rule(device, metric)
    evaluate_latest(_latest(device, metric, 1))
    alert = AlertEvent.objects.get(device=device, metric=metric)

    client = APIClient()
    client.force_authenticate(user=user)
    ack_response = client.post(
        f"/api/v1/alerts/alert-events/{alert.id}/acknowledge/",
        {"comment": "Checking alert"},
        format="json",
    )
    assert ack_response.status_code == 200

    detail = client.get(f"/api/v1/alerts/alert-events/{alert.id}/")
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["comments"]
    assert payload["comments"][0]["comment"] == "Checking alert"
    assert payload["logs"]
    assert len(payload["logs"]) >= 2
    assert isinstance(payload["metadata"], dict)


@pytest.mark.django_db
def test_acknowledge_and_resolve_endpoints_accept_comment_and_return_updated_event():
    org, dc, device, metric = _device_setup()
    role = _role("ALERT_OPERATOR", "Alert Operator", ["alert.view", "alert.acknowledge", "alert.resolve"])
    user = User.objects.create_user(username="alert-operator", password="test12345", is_active=True)
    _make_access(user, role, organization=org)

    _rule(device, metric)
    evaluate_latest(_latest(device, metric, 1))
    alert = AlertEvent.objects.get(device=device, metric=metric)

    client = APIClient()
    client.force_authenticate(user=user)

    ack_response = client.post(
        f"/api/v1/alerts/alert-events/{alert.id}/acknowledge/",
        {"comment": "Investigating"},
        format="json",
    )
    assert ack_response.status_code == 200
    ack_payload = ack_response.json()
    assert ack_payload["status"] == AlertStatus.ACKNOWLEDGED
    assert ack_payload["acknowledged_at"] is not None

    resolve_response = client.post(
        f"/api/v1/alerts/alert-events/{alert.id}/resolve/",
        {"comment": "Fixed"},
        format="json",
    )
    assert resolve_response.status_code == 200
    resolve_payload = resolve_response.json()
    assert resolve_payload["status"] == AlertStatus.RESOLVED
    assert resolve_payload["resolved_at"] is not None
    assert AlertComment.objects.filter(alert_event=alert).count() == 2


@pytest.mark.django_db
def test_alert_lifecycle_fields_are_not_directly_writable_through_update():
    org, dc, device, metric = _device_setup()
    role = _role("ALERT_EDITOR", "Alert Editor", ["alert.view", "alert.update"])
    user = User.objects.create_user(username="alert-editor", password="test12345", is_active=True)
    _make_access(user, role, organization=org)

    _rule(device, metric)
    evaluate_latest(_latest(device, metric, 1))
    alert = AlertEvent.objects.get(device=device, metric=metric)

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.patch(
        f"/api/v1/alerts/alert-events/{alert.id}/",
        {
            "status": "RESOLVED",
            "resolved_at": timezone.now().isoformat(),
            "acknowledged_at": timezone.now().isoformat(),
        },
        format="json",
    )

    assert response.status_code in (200, 202)
    alert.refresh_from_db()
    assert alert.status == AlertStatus.OPEN
    assert alert.resolved_at is None
    assert alert.acknowledged_at is None


@pytest.mark.django_db
def test_alert_access_filtering_still_limits_results_to_assigned_scope():
    org, dc, device, metric = _device_setup()
    role = _role("ALERT_SCOPE", "Alert Scope", ["alert.view"])
    user = User.objects.create_user(username="alert-scope", password="test12345", is_active=True)
    _make_access(user, role, organization=org)

    _rule(device, metric)
    evaluate_latest(_latest(device, metric, 1))

    other_org = Organization.objects.create(name="Hidden Org", code="ORG-HIDDEN")
    other_dc = DataCenter.objects.create(organization=other_org, name="Hidden DC", code="DC-HIDDEN")
    other_type = DeviceType.objects.create(name="ATS", code="ATS-HIDDEN", category="POWER")
    other_vendor = Vendor.objects.create(name="Hidden Vendor", code="HIDDEN-VENDOR")
    other_model = DeviceModel.objects.create(vendor=other_vendor, device_type=other_type, name="Hidden Model", model_number="HM-1")
    other_device = Device.objects.create(
        organization=other_org,
        data_center=other_dc,
        device_type=other_type,
        device_model=other_model,
        name="ATS-99",
        code="ATS-99",
    )
    other_metric = MetricDefinition.objects.create(
        code="HIDDEN_METRIC",
        name="Hidden Metric",
        category=MetricCategory.STATUS,
        data_type=MetricDataType.INTEGER,
        is_active=True,
    )
    AlertRule.objects.create(
        organization=other_org,
        device=other_device,
        metric=other_metric,
        name="Hidden rule",
        operator="EQ",
        threshold_integer=1,
        severity=AlertSeverity.CRITICAL,
        duration_seconds=0,
        is_active=True,
    )
    evaluate_latest(_latest(other_device, other_metric, 1))

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.get("/api/v1/alerts/alert-events/")

    assert response.status_code == 200
    results = _json_results(response)
    assert len(results) == 1
    assert results[0]["device_name"] == "UPS-01"


@pytest.mark.django_db
def test_alert_summary_endpoints_share_same_shape_and_respect_scope():
    org, dc, device, metric = _device_setup()
    role = _role("ALERT_SUMMARY", "Alert Summary", ["alert.view"])
    user = User.objects.create_user(username="alert-summary", password="test12345", is_active=True)
    _make_access(user, role, organization=org)

    _rule_with_severity(device, metric, severity=AlertSeverity.CRITICAL)
    evaluate_latest(_latest(device, metric, 1))

    warning_metric = MetricDefinition.objects.create(
        code="UPS_WARNING",
        name="UPS Warning",
        category=MetricCategory.STATUS,
        data_type=MetricDataType.INTEGER,
        is_active=True,
    )
    _rule_with_severity(device, warning_metric, severity=AlertSeverity.WARNING)
    evaluate_latest(_latest(device, warning_metric, 1))
    warning_alert = AlertEvent.objects.get(device=device, metric=warning_metric)
    acknowledge_alert(warning_alert, user=user, comment="ack")

    resolved_metric = MetricDefinition.objects.create(
        code="UPS_RESOLVED",
        name="UPS Resolved",
        category=MetricCategory.STATUS,
        data_type=MetricDataType.INTEGER,
        is_active=True,
    )
    _rule_with_severity(device, resolved_metric, severity=AlertSeverity.WARNING)
    evaluate_latest(_latest(device, resolved_metric, 1))
    resolved_alert = AlertEvent.objects.get(device=device, metric=resolved_metric)
    manually_resolve_alert(resolved_alert, user=user, comment="resolved")

    other_org = Organization.objects.create(name="Hidden Org", code="ORG-HIDDEN-SUM")
    other_dc = DataCenter.objects.create(organization=other_org, name="Hidden DC", code="DC-HIDDEN-SUM")
    other_type = DeviceType.objects.create(name="PAC", code="PAC-HIDDEN-SUM", category="COOLING")
    other_vendor = Vendor.objects.create(name="Hidden Vendor", code="HIDDEN-VENDOR-SUM")
    other_model = DeviceModel.objects.create(vendor=other_vendor, device_type=other_type, name="Hidden Model", model_number="HM-SUM")
    other_device = Device.objects.create(
        organization=other_org,
        data_center=other_dc,
        device_type=other_type,
        device_model=other_model,
        name="PAC-HIDDEN",
        code="PAC-HIDDEN",
    )
    other_metric = MetricDefinition.objects.create(
        code="HIDDEN_SUMMARY",
        name="Hidden Summary",
        category=MetricCategory.STATUS,
        data_type=MetricDataType.INTEGER,
        is_active=True,
    )
    _rule_with_severity(other_device, other_metric, severity=AlertSeverity.CRITICAL)
    evaluate_latest(_latest(other_device, other_metric, 1))

    client = APIClient()
    client.force_authenticate(user=user)
    summary_response = client.get("/api/v1/alerts/summary/")
    viewset_summary_response = client.get("/api/v1/alerts/alert-events/summary/")
    active_response = client.get("/api/v1/alerts/active-by-severity/")
    top_devices_response = client.get("/api/v1/alerts/top-devices/")
    recent_response = client.get("/api/v1/alerts/recent/")

    assert summary_response.status_code == 200
    assert viewset_summary_response.status_code == 200
    summary_payload = summary_response.json()
    viewset_summary_payload = viewset_summary_response.json()
    assert summary_payload == viewset_summary_payload

    assert summary_payload["open_total"] == 2
    assert summary_payload["critical_open"] == 1
    assert summary_payload["warning_open"] == 1
    assert summary_payload["acknowledged_total"] == 1
    assert summary_payload["resolved_today"] == 1
    assert summary_payload["unacknowledged_critical"] == 1
    assert summary_payload["by_severity"] == {"CRITICAL": 1, "WARNING": 1}
    assert summary_payload["by_status"] == {"ACKNOWLEDGED": 1, "OPEN": 1, "RESOLVED": 1}
    assert summary_payload["by_data_center"] == {"DC": 2}
    assert summary_payload["by_device_type"] == {"UPS": 2}

    active_payload = _json_results(active_response)
    assert active_payload == [
        {"severity": "CRITICAL", "total": 1},
        {"severity": "WARNING", "total": 1},
    ]

    top_devices_payload = _json_results(top_devices_response)
    assert top_devices_payload[0]["device_name"] == "UPS-01"
    assert top_devices_payload[0]["device_code"] == "UPS-01"
    assert top_devices_payload[0]["alert_count"] == 2
    assert top_devices_payload[0]["critical_count"] == 1

    recent_payload = _json_results(recent_response)
    assert recent_payload
    assert recent_payload[0]["device_name"] == "UPS-01"
    assert recent_payload[0]["metric_code"] in {"UPS_ON_BATTERY", "UPS_WARNING", "UPS_RESOLVED"}
    assert isinstance(recent_payload[0]["age_seconds"], int)
    assert isinstance(recent_payload[0]["is_active"], bool)


@pytest.mark.django_db
def test_top_devices_orders_by_active_alert_count():
    org, dc, device, metric = _device_setup()
    role = _role("ALERT_TOP", "Alert Top", ["alert.view"])
    user = User.objects.create_user(username="alert-top", password="test12345", is_active=True)
    _make_access(user, role, organization=org)

    _rule_with_severity(device, metric, severity=AlertSeverity.CRITICAL)
    evaluate_latest(_latest(device, metric, 1))

    metric_two = MetricDefinition.objects.create(
        code="UPS_OTHER_2",
        name="UPS Other 2",
        category=MetricCategory.STATUS,
        data_type=MetricDataType.INTEGER,
        is_active=True,
    )
    _rule_with_severity(device, metric_two, severity=AlertSeverity.WARNING)
    evaluate_latest(_latest(device, metric_two, 1))

    second_device = Device.objects.create(
        organization=org,
        data_center=dc,
        device_type=device.device_type,
        device_model=device.device_model,
        name="UPS-02",
        code="UPS-02",
    )
    metric_three = MetricDefinition.objects.create(
        code="UPS_OTHER_3",
        name="UPS Other 3",
        category=MetricCategory.STATUS,
        data_type=MetricDataType.INTEGER,
        is_active=True,
    )
    _rule_with_severity(second_device, metric_three, severity=AlertSeverity.WARNING)
    evaluate_latest(_latest(second_device, metric_three, 1))

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.get("/api/v1/alerts/top-devices/")
    assert response.status_code == 200
    payload = _json_results(response)
    assert payload[0]["device_name"] == "UPS-01"
    assert payload[0]["device__name"] == "UPS-01"
    assert payload[0]["alert_count"] == 2
    assert payload[0]["total"] == 2
    assert payload[1]["device_name"] == "UPS-02"
    assert payload[1]["alert_count"] == 1


@pytest.mark.django_db
def test_alert_summary_resolved_today_respects_business_timezone(monkeypatch):
    org, dc, device, metric = _device_setup()
    _rule(device, metric)
    evaluate_latest(_latest(device, metric, 1))
    alert = AlertEvent.objects.get(device=device, metric=metric)
    manually_resolve_alert(alert, user=None, comment="resolved")
    alert.refresh_from_db()

    fixed_now = datetime(2026, 7, 10, 0, 30, tzinfo=dt_timezone.utc)
    alert.resolved_at = datetime(2026, 7, 9, 18, 30, tzinfo=dt_timezone.utc)
    alert.save(update_fields=["resolved_at", "updated_at"])

    monkeypatch.setattr("apps.alerts.services.summary.timezone.now", lambda: fixed_now)
    payload_dhaka = build_alert_summary(AlertEvent.objects.filter(pk=alert.pk), business_timezone="Asia/Dhaka")
    payload_utc = build_alert_summary(AlertEvent.objects.filter(pk=alert.pk), business_timezone="UTC")
    payload_default = build_alert_summary(AlertEvent.objects.filter(pk=alert.pk))

    assert payload_dhaka["resolved_today"] == 1
    assert payload_utc["resolved_today"] == 0
    assert payload_default["resolved_today"] in {0, 1}
    assert set(payload_dhaka.keys()) == set(payload_utc.keys()) == set(payload_default.keys())


@pytest.mark.django_db
def test_matching_policy_escalates_old_unacknowledged_alert():
    org, dc, device, metric = _device_setup()
    role = Role.objects.create(name="Ops", code="OPS-ROLE-4", scope=RoleScope.ORGANIZATION)
    user = User.objects.create_user(username="ops1", password="test12345", is_active=True)
    UserResourceAccess.objects.create(user=user, organization=org, data_center=None, room=None, rack=None, device=None, role=role, is_active=True)

    AlertRule.objects.create(
        organization=org,
        device=device,
        metric=metric,
        name="Escalation rule",
        operator="EQ",
        threshold_integer=1,
        severity=AlertSeverity.CRITICAL,
        duration_seconds=0,
        is_active=True,
    )
    latest = _latest(device, metric, 1)
    evaluate_latest(latest)
    alert = AlertEvent.objects.get(device=device, metric=metric)
    alert.triggered_at = timezone.now() - timedelta(minutes=20)
    alert.last_seen_at = alert.triggered_at
    alert.save(update_fields=["triggered_at", "last_seen_at", "updated_at"])

    policy = AlertEscalationPolicy.objects.create(
        organization=org,
        data_center=dc,
        severity=AlertSeverity.CRITICAL,
        if_not_acknowledged_minutes=10,
        target_role=role,
        channel="EMAIL",
        is_active=True,
    )

    created = []
    with patch("apps.alerts.services.notifications._queue_delivery", side_effect=lambda n: created.append(str(n.id)) or n):
        escalated = run_alert_escalation_check()

    alert.refresh_from_db()
    assert escalated
    assert AlertEventLog.objects.filter(alert_event=alert, action=AlertEventLogAction.ESCALATED).exists()
    assert Notification.objects.filter(metadata__alert_event_id=str(alert.pk), metadata__action="ESCALATED").count() >= 1
    assert created


@pytest.mark.django_db
def test_matching_policy_escalates_old_acknowledged_unresolved_alert():
    org, dc, device, metric = _device_setup()
    role = Role.objects.create(name="Ops", code="OPS-ROLE-5", scope=RoleScope.ORGANIZATION)
    user = User.objects.create_user(username="ops2", password="test12345", is_active=True)
    UserResourceAccess.objects.create(user=user, organization=org, data_center=None, room=None, rack=None, device=None, role=role, is_active=True)

    AlertRule.objects.create(
        organization=org,
        device=device,
        metric=metric,
        name="Escalation rule",
        operator="EQ",
        threshold_integer=1,
        severity=AlertSeverity.CRITICAL,
        duration_seconds=0,
        is_active=True,
    )
    latest = _latest(device, metric, 1)
    evaluate_latest(latest)
    alert = AlertEvent.objects.get(device=device, metric=metric)
    acknowledge_alert(alert, user=user, comment="ack")
    alert.refresh_from_db()
    alert.acknowledged_at = timezone.now() - timedelta(minutes=20)
    alert.save(update_fields=["acknowledged_at", "updated_at"])

    policy = AlertEscalationPolicy.objects.create(
        organization=org,
        data_center=dc,
        severity=AlertSeverity.CRITICAL,
        if_not_resolved_minutes=10,
        target_role=role,
        channel="EMAIL",
        is_active=True,
    )

    created = []
    with patch("apps.alerts.services.notifications._queue_delivery", side_effect=lambda n: created.append(str(n.id)) or n):
        escalated = run_alert_escalation_check()

    alert.refresh_from_db()
    assert escalated
    assert AlertEventLog.objects.filter(alert_event=alert, action=AlertEventLogAction.ESCALATED).exists()
    assert Notification.objects.filter(metadata__alert_event_id=str(alert.pk), metadata__action="ESCALATED").count() >= 1
    assert created


@pytest.mark.django_db
def test_repeated_escalation_does_not_duplicate_notifications():
    org, dc, device, metric = _device_setup()
    role = Role.objects.create(name="Ops", code="OPS-ROLE-6", scope=RoleScope.ORGANIZATION)
    user = User.objects.create_user(username="ops3", password="test12345", is_active=True)
    UserResourceAccess.objects.create(user=user, organization=org, data_center=None, room=None, rack=None, device=None, role=role, is_active=True)

    AlertRule.objects.create(
        organization=org,
        device=device,
        metric=metric,
        name="Escalation rule",
        operator="EQ",
        threshold_integer=1,
        severity=AlertSeverity.CRITICAL,
        duration_seconds=0,
        is_active=True,
    )
    latest = _latest(device, metric, 1)
    evaluate_latest(latest)
    alert = AlertEvent.objects.get(device=device, metric=metric)
    alert.triggered_at = timezone.now() - timedelta(minutes=20)
    alert.last_seen_at = alert.triggered_at
    alert.save(update_fields=["triggered_at", "last_seen_at", "updated_at"])

    AlertEscalationPolicy.objects.create(
        organization=org,
        data_center=dc,
        severity=AlertSeverity.CRITICAL,
        if_not_acknowledged_minutes=10,
        target_role=role,
        channel="EMAIL",
        is_active=True,
    )

    with patch("apps.alerts.services.notifications._queue_delivery", side_effect=lambda n: n):
        run_alert_escalation_check()
        first_count = Notification.objects.filter(metadata__alert_event_id=str(alert.pk), metadata__action="ESCALATED").count()
        run_alert_escalation_check()

    second_count = Notification.objects.filter(metadata__alert_event_id=str(alert.pk), metadata__action="ESCALATED").count()
    assert first_count == second_count


@pytest.mark.django_db
def test_generic_trap_creates_single_durable_alert():
    org, dc, device, _ = _device_setup()
    SNMPTrapSource.objects.create(organization=org, data_center=dc, device=device, source_ip="10.10.10.50", is_enabled=True)
    SNMPTrapOIDMapping.objects.create(
        device_type=device.device_type,
        device_model=device.device_model,
        vendor=None,
        trap_oid="1.3.6.1.4.1.99999.1.9",
        event_code="UPS_ON_BATTERY",
        event_name="UPS On Battery",
        severity="WARNING",
        message_template="UPS on battery",
        create_alert=True,
        is_active=True,
    )

    process_snmp_trap(source_ip="10.10.10.50", trap_oid="1.3.6.1.4.1.99999.1.9", raw_varbinds={"x": "y"})
    process_snmp_trap(source_ip="10.10.10.50", trap_oid="1.3.6.1.4.1.99999.1.9", raw_varbinds={"x": "y"})

    assert AlertEvent.objects.filter(device=device, metric__isnull=True, status=AlertStatus.OPEN).count() == 1


@pytest.mark.django_db
def test_pending_web_notifications_are_delivered():
    org, _, _, _ = _device_setup()
    notification = Notification.objects.create(
        organization=org,
        recipient=None,
        channel=NotificationChannel.WEB,
        subject="Test",
        message="Hello",
        status=NotificationStatus.PENDING,
    )

    deliver_pending_notifications()
    notification.refresh_from_db()
    assert notification.status == NotificationStatus.SENT
