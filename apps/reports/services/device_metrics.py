from __future__ import annotations

from collections import OrderedDict

from django.core.exceptions import ValidationError
from django.db.models import Q

from apps.common.access import get_accessible_devices_for_user
from apps.devices.models import Device, ModbusRegisterMapping, SNMPOIDMapping
from apps.telemetry.models import LatestTelemetry


def get_device_for_user(user, device_id) -> Device:
    if not device_id:
        raise ValidationError({"device_id": "A device is required."})

    device = (
        get_accessible_devices_for_user(user)
        .select_related("organization", "data_center", "device_type", "device_model")
        .filter(pk=device_id)
        .first()
    )
    if device is None:
        raise ValidationError({"device_id": "Selected device does not exist or is inaccessible."})
    return device


def _dedupe_metric_options(rows) -> list[dict]:
    deduped: "OrderedDict[str, dict]" = OrderedDict()
    for row in rows:
        metric = getattr(row, "metric", None)
        if metric is None or not getattr(metric, "code", None):
            continue
        code = str(metric.code).strip().upper()
        if not code:
            continue
        current = deduped.get(code)
        option = {
            "id": str(getattr(metric, "pk", "") or ""),
            "code": code,
            "name": getattr(metric, "name", None) or code,
            "unit": getattr(metric, "unit", None) or "",
            "category": getattr(metric, "category", None) or "",
            "data_type": getattr(metric, "data_type", None) or "",
        }
        if current is None or getattr(row, "device_model_id", None):
            deduped[code] = option
    return list(deduped.values())


def get_device_metric_options(device: Device) -> list[dict]:
    if device is None:
        return []

    snmp_mappings = SNMPOIDMapping.objects.select_related("metric").filter(device_type=device.device_type, is_active=True)
    if device.device_model_id:
        snmp_mappings = snmp_mappings.filter(Q(device_model__isnull=True) | Q(device_model=device.device_model))
    else:
        snmp_mappings = snmp_mappings.filter(device_model__isnull=True)

    modbus_mappings = ModbusRegisterMapping.objects.select_related("metric").filter(device_type=device.device_type, is_active=True)
    if device.device_model_id:
        modbus_mappings = modbus_mappings.filter(Q(device_model__isnull=True) | Q(device_model=device.device_model))
    else:
        modbus_mappings = modbus_mappings.filter(device_model__isnull=True)

    options = _dedupe_metric_options(list(snmp_mappings) + list(modbus_mappings))
    if options:
        return sorted(options, key=lambda item: (item["name"].lower(), item["code"]))

    fallback_metrics = (
        LatestTelemetry.objects.select_related("metric")
        .filter(device=device, metric__is_active=True)
        .order_by("metric__name", "metric__code")
    )
    return _dedupe_metric_options(fallback_metrics)


def get_device_supported_metric_codes(device: Device) -> list[str]:
    return [option["code"] for option in get_device_metric_options(device)]


def get_devices_for_user(user, device_ids) -> list[Device]:
    normalized_ids = [str(value).strip() for value in (device_ids or []) if str(value).strip()]
    if not normalized_ids:
        return []
    devices = (
        get_accessible_devices_for_user(user)
        .select_related("organization", "data_center", "device_type", "device_model")
        .filter(pk__in=normalized_ids)
    )
    return list(devices)


def get_device_metric_options_for_devices(devices: list[Device]) -> list[dict]:
    if not devices:
        return []
    deduped: dict[str, dict] = {}
    for device in devices:
        for option in get_device_metric_options(device):
            code = str(option.get("code") or "").strip().upper()
            if not code:
                continue
            if code not in deduped:
                deduped[code] = option
    return sorted(deduped.values(), key=lambda item: (item["name"].lower(), item["code"]))


def get_devices_supported_metric_codes(devices: list[Device]) -> list[str]:
    return [option["code"] for option in get_device_metric_options_for_devices(devices)]
