from __future__ import annotations

from .base import BaseReportGenerator, GeneratorContext, ReportDataset, ReportTable
from .registry import register_generator


@register_generator("device_inventory")
class DeviceInventoryGenerator(BaseReportGenerator):
    definition_code = "DEVICE_INVENTORY"
    generator_key = "device_inventory"
    supported_formats = ("CSV", "XLSX", "PDF")

    def build_dataset(self, context: GeneratorContext) -> ReportDataset:
        from apps.devices.models import Device

        parameters = context.parameters or {}
        qs = Device.objects.select_related(
            "organization",
            "data_center",
            "room",
            "rack",
            "device_type",
            "device_model__vendor",
        ).filter(organization_id=context.organization.id)

        selected_device_ids = [entry.get("id") for entry in context.scope_snapshot.get("selected_devices", []) if entry.get("id")]
        selected_room_ids = [entry.get("id") for entry in context.scope_snapshot.get("selected_rooms", []) if entry.get("id")]
        selected_rack_ids = [entry.get("id") for entry in context.scope_snapshot.get("selected_racks", []) if entry.get("id")]

        if context.data_center:
            qs = qs.filter(data_center_id=context.data_center.id)
        if selected_device_ids:
            qs = qs.filter(pk__in=selected_device_ids)
        if selected_room_ids:
            qs = qs.filter(room_id__in=selected_room_ids)
        if selected_rack_ids:
            qs = qs.filter(rack_id__in=selected_rack_ids)

        for key in ("device_type_id", "device_model_id", "status", "is_active"):
            value = parameters.get(key)
            if value in (None, ""):
                continue
            if key == "status":
                qs = qs.filter(status=value)
            elif key == "is_active":
                qs = qs.filter(is_active=bool(value))
            else:
                qs = qs.filter(**{key: value})

        rows = (
            {
                "device_id": str(device.id),
                "organization": getattr(device.organization, "name", None),
                "data_center": getattr(device.data_center, "name", None),
                "room": getattr(device.room, "name", None),
                "rack": getattr(device.rack, "name", None),
                "device": device.name,
                "code": device.code,
                "hostname": device.hostname,
                "ip_address": device.ip_address,
                "device_type": getattr(device.device_type, "name", None),
                "device_model": getattr(device.device_model, "name", None),
                "vendor": getattr(getattr(device.device_model, "vendor", None), "name", None),
                "status": device.status,
                "is_active": device.is_active,
                "last_seen": device.last_seen_at,
            }
            for device in qs.order_by("name", "code").iterator(chunk_size=2000)
        )
        columns = [
            "device_id",
            "organization",
            "data_center",
            "room",
            "rack",
            "device",
            "code",
            "hostname",
            "ip_address",
            "device_type",
            "device_model",
            "vendor",
            "status",
            "is_active",
            "last_seen",
        ]
        return ReportDataset(
            title="Device Inventory",
            subtitle="Current device inventory",
            metadata={"report_type": context.definition.code},
            tables=[ReportTable(name="Inventory", columns=columns, rows=rows, primary=True)],
        )
