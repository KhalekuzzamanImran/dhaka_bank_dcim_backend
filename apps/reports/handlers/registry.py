from __future__ import annotations

from .alerts import AlertExportHandler, AlertSummaryHandler
from .audit import AuditExportHandler
from .environment import RoomEnvironmentHandler
from .inventory import DeviceInventoryHandler
from .notifications import NotificationDeliveryHandler
from .telemetry import TelemetryExportHandler

REPORT_HANDLER_REGISTRY = {
    "alert_summary": AlertSummaryHandler,
    "alert_export": AlertExportHandler,
    "audit_export": AuditExportHandler,
    "device_inventory": DeviceInventoryHandler,
    "notification_delivery": NotificationDeliveryHandler,
    "room_environment": RoomEnvironmentHandler,
    "telemetry_export": TelemetryExportHandler,
}


def get_report_handler(code: str):
    handler_cls = REPORT_HANDLER_REGISTRY.get(code)
    if not handler_cls:
        return None
    return handler_cls()
