import json
import os
import sys

sys.path.insert(0, "/app")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
import django
from django.core.serializers.json import DjangoJSONEncoder
django.setup()

from apps.accounts.models import User
from apps.access_control.models import Role, Permission
from apps.organizations.models import Organization
from apps.datacenters.models import DataCenter, Room, Rack
from apps.devices.models import Device, DeviceType, DeviceModel, Vendor, DeviceProtocolConfig
from apps.telemetry.models import MetricDefinition, LatestTelemetry, TelemetryPoint, DeviceEvent
from apps.alerts.models import AlertRule, AlertEvent, AlertEventLog
from apps.notifications.models import Notification
from apps.reports.models import ReportDefinition, ReportTemplate, ReportSchedule, ReportJob
from apps.devices.services.overview_summary import _build_overview_summary

def iso(dt):
    return dt.isoformat() if dt else None

def serialize_device(d):
    return {
        "id": str(d.id),
        "name": d.name,
        "code": d.code,
        "hostname": d.hostname,
        "ip_address": d.ip_address,
        "port": d.port,
        "serial_number": d.serial_number,
        "asset_tag": d.asset_tag,
        "installed_at": iso(d.installed_at),
        "warranty_expiry_date": iso(d.warranty_expiry_date),
        "status": d.status,
        "is_active": d.is_active,
        "last_seen_at": iso(d.last_seen_at),
        "updated_at": iso(d.updated_at),
        "metadata": d.metadata or {},
        "organization": str(d.organization_id) if d.organization_id else None,
        "organization_name": d.organization.name if d.organization else None,
        "data_center": str(d.data_center_id) if d.data_center_id else None,
        "data_center_name": d.data_center.name if d.data_center else None,
        "room": str(d.room_id) if d.room_id else None,
        "room_name": d.room.name if d.room else None,
        "rack": str(d.rack_id) if d.rack_id else None,
        "rack_name": d.rack.name if d.rack else None,
        "device_type": str(d.device_type_id) if d.device_type_id else None,
        "device_type_name": d.device_type.name if d.device_type else None,
        "device_type_code": d.device_type.code if d.device_type else None,
        "device_model": str(d.device_model_id) if d.device_model_id else None,
        "device_model_name": d.device_model.name if d.device_model else None,
    }

def main():
    print("Exporting users, roles, permissions...")
    users = list(User.objects.filter(is_active=True).values(
        "id", "username", "email", "full_name", "phone", "is_active", "is_staff", "is_mfa_enabled", "last_login", "date_joined"
    ))
    for u in users:
        u["id"] = str(u["id"])
        u["last_login"] = iso(u["last_login"])
        u["date_joined"] = iso(u["date_joined"])

    roles = list(Role.objects.values("id", "name", "code", "scope", "description", "status"))
    for r in roles:
        r["id"] = str(r["id"])

    permissions = list(Permission.objects.values("id", "module", "code", "description"))
    for p in permissions:
        p["id"] = str(p["id"])

    print("Exporting orgs, dcs, rooms, racks...")
    orgs = list(Organization.objects.values("id", "name", "code", "status", "created_at"))
    for o in orgs:
        o["id"] = str(o["id"])
        o["is_active"] = o.get("status") == "ACTIVE"
        o["created_at"] = iso(o["created_at"])

    dcs = list(DataCenter.objects.values("id", "organization_id", "name", "code", "status", "created_at"))
    for dc in dcs:
        dc["id"] = str(dc["id"])
        dc["organization"] = str(dc.pop("organization_id"))
        dc["is_active"] = dc.get("status") == "ACTIVE"
        dc["created_at"] = iso(dc["created_at"])

    rooms = list(Room.objects.values("id", "data_center_id", "name", "code", "room_type", "floor_name", "created_at"))
    for rm in rooms:
        rm["id"] = str(rm["id"])
        rm["data_center"] = str(rm.pop("data_center_id"))
        rm["is_active"] = True
        rm["created_at"] = iso(rm["created_at"])

    racks = list(Rack.objects.values("id", "data_center_id", "room_id", "name", "code", "status", "created_at"))
    for rk in racks:
        rk["id"] = str(rk["id"])
        rk["data_center"] = str(rk.pop("data_center_id"))
        rk["room"] = str(rk.pop("room_id")) if rk.get("room_id") else None
        rk["is_active"] = rk.get("status") == "ACTIVE"
        rk["created_at"] = iso(rk["created_at"])

    print("Exporting vendors, models, device types...")
    vendors = list(Vendor.objects.values("id", "name", "code", "website", "created_at"))
    for v in vendors:
        v["id"] = str(v["id"])
        v["created_at"] = iso(v["created_at"])

    models = list(DeviceModel.objects.values("id", "vendor_id", "device_type_id", "name", "model_number", "description", "created_at"))
    for m in models:
        m["id"] = str(m["id"])
        m["vendor"] = str(m.pop("vendor_id")) if m.get("vendor_id") else None
        m["device_type"] = str(m.pop("device_type_id")) if m.get("device_type_id") else None
        m["code"] = m.get("model_number") or m["name"]
        m["created_at"] = iso(m["created_at"])

    dtypes = list(DeviceType.objects.values("id", "name", "code", "category", "created_at"))
    for dt in dtypes:
        dt["id"] = str(dt["id"])
        dt["created_at"] = iso(dt["created_at"])
        dt["device_count"] = Device.objects.filter(device_type_id=dt["id"]).count()

    print("Exporting devices...")
    dev_objs = Device.objects.select_related("organization", "data_center", "room", "rack", "device_type", "device_model").all().order_by("name")
    devices = [serialize_device(d) for d in dev_objs]

    print("Exporting protocol configs...")
    proto_configs = list(DeviceProtocolConfig.objects.values(
        "id", "device_id", "protocol", "host", "port", "is_primary", "extra_config", "created_at"
    ))
    for pc in proto_configs:
        pc["id"] = str(pc["id"])
        pc["device"] = str(pc.pop("device_id"))
        pc["created_at"] = iso(pc["created_at"])
        extra = pc.pop("extra_config") or {}
        pc["snmp_version"] = extra.get("snmp_version", "v1")
        if not pc.get("host"):
            d = next((x for x in devices if x["id"] == pc["device"]), None)
            pc["host"] = d["ip_address"] if d else ""
        pc["protocol_type"] = pc["protocol"]

    print("Exporting metrics...")
    metrics = list(MetricDefinition.objects.values(
        "id", "code", "name", "unit", "data_type", "category", "is_active"
    ).order_by("code"))
    for m in metrics:
        m["id"] = str(m["id"])

    print("Exporting latest telemetry...")
    latest = list(LatestTelemetry.objects.select_related("device", "metric").values(
        "device_id", "device__name", "metric__code", "metric__name", "metric__unit",
        "value_float", "value_integer", "value_boolean", "value_text", "raw_value_text", "quality", "last_seen_at"
    ).order_by("device__name", "metric__code"))
    for lt in latest:
        lt["device_id"] = str(lt["device_id"])
        lt["last_seen_at"] = iso(lt["last_seen_at"])

    print("Exporting alerts...")
    alerts = list(AlertEvent.objects.select_related("device", "metric", "organization", "data_center").values(
        "id", "organization_id", "organization__name", "data_center_id", "data_center__name",
        "device_id", "device__name", "metric__code", "severity", "status", "message",
        "occurrence_count", "value_float", "value_integer", "value_boolean", "value_text",
        "triggered_at", "last_seen_at", "resolved_at", "acknowledge_comment", "resolve_comment", "metadata"
    ).order_by("-triggered_at")[:100])
    for a in alerts:
        a["id"] = str(a["id"])
        a["organization"] = str(a.pop("organization_id")) if a.get("organization_id") else None
        a["organization_name"] = a.pop("organization__name")
        a["data_center"] = str(a.pop("data_center_id")) if a.get("data_center_id") else None
        a["data_center_name"] = a.pop("data_center__name")
        a["device"] = str(a.pop("device_id")) if a.get("device_id") else None
        a["device_name"] = a.pop("device__name")
        a["metric_code"] = a.pop("metric__code")
        a["triggered_at"] = iso(a["triggered_at"])
        a["last_seen_at"] = iso(a["last_seen_at"])
        a["resolved_at"] = iso(a["resolved_at"])

    print("Exporting alert logs...")
    logs = list(AlertEventLog.objects.select_related("alert_event", "actor").values(
        "id", "alert_event_id", "action", "old_status", "new_status", "message",
        "value_snapshot", "actor_id", "actor__username", "metadata", "created_at"
    ).order_by("-created_at")[:100])
    for l in logs:
        l["id"] = str(l["id"])
        l["alert_event"] = str(l.pop("alert_event_id"))
        l["actor"] = str(l.pop("actor_id")) if l.get("actor_id") else None
        l["actor_name"] = l.pop("actor__username")
        l["created_at"] = iso(l["created_at"])

    print("Exporting notifications...")
    notifs = list(Notification.objects.select_related("organization", "recipient").values(
        "id", "organization_id", "recipient_id", "subject", "message", "read_at", "metadata", "channel", "status", "created_at", "updated_at"
    ).order_by("-created_at")[:50])
    for n in notifs:
        n["id"] = str(n["id"])
        n["organization"] = str(n.pop("organization_id")) if n.get("organization_id") else None
        n["recipient"] = str(n.pop("recipient_id")) if n.get("recipient_id") else None
        n["read_at"] = iso(n["read_at"])
        n["created_at"] = iso(n["created_at"])
        n["updated_at"] = iso(n["updated_at"])

    print("Exporting reports...")
    report_defs = list(ReportDefinition.objects.values(
        "id", "code", "name", "description", "category", "supported_formats", "is_active"
    ))
    for rd in report_defs:
        rd["id"] = str(rd["id"])

    report_temps = list(ReportTemplate.objects.select_related("organization", "definition").values(
        "id", "organization_id", "organization__name", "definition__code", "name", "code", "description",
        "config", "primary_format", "attachment_formats", "is_active", "created_at", "updated_at"
    ))
    for rt in report_temps:
        rt["id"] = str(rt["id"])
        rt["organization"] = str(rt.pop("organization_id")) if rt.get("organization_id") else None
        rt["organization_name"] = rt.pop("organization__name")
        rt["definition_code"] = rt.pop("definition__code")
        rt["created_at"] = iso(rt["created_at"])
        rt["updated_at"] = iso(rt["updated_at"])

    report_scheds = list(ReportSchedule.objects.select_related("organization", "data_center", "created_by").values(
        "id", "organization_id", "organization__name", "data_center_id", "data_center__name",
        "name", "report_type", "frequency", "delivery_time", "primary_format", "attachment_formats",
        "recipients", "send_sms", "sms_recipients", "parameter_overrides", "status",
        "next_run_at", "last_run_at", "last_sent_at", "last_error_message",
        "created_by_id", "created_by__username", "created_at", "updated_at"
    ))
    for rs in report_scheds:
        rs["id"] = str(rs["id"])
        rs["definition_code"] = rs.pop("report_type")
        rs["organization"] = str(rs.pop("organization_id")) if rs.get("organization_id") else None
        rs["organization_name"] = rs.pop("organization__name")
        rs["data_center"] = str(rs.pop("data_center_id")) if rs.get("data_center_id") else None
        rs["data_center_name"] = rs.pop("data_center__name")
        rs["created_by"] = str(rs.pop("created_by_id")) if rs.get("created_by_id") else None
        rs["created_by_name"] = rs.pop("created_by__username")
        rs["next_run_at"] = iso(rs["next_run_at"])
        rs["last_run_at"] = iso(rs["last_run_at"])
        rs["last_sent_at"] = iso(rs["last_sent_at"])
        rs["created_at"] = iso(rs["created_at"])
        rs["updated_at"] = iso(rs["updated_at"])

    report_jobs = list(ReportJob.objects.select_related("organization", "data_center", "requested_by", "template").values(
        "id", "organization_id", "organization__name", "data_center_id", "data_center__name",
        "template_id", "template__name", "template__code", "status", "parameters",
        "file", "started_at", "completed_at", "error_message",
        "requested_by_id", "requested_by__username", "created_at", "updated_at"
    ).order_by("-created_at")[:30])
    for rj in report_jobs:
        rj["id"] = str(rj["id"])
        rj["organization"] = str(rj.pop("organization_id")) if rj.get("organization_id") else None
        rj["organization_name"] = rj.pop("organization__name")
        rj["data_center"] = str(rj.pop("data_center_id")) if rj.get("data_center_id") else None
        rj["data_center_name"] = rj.pop("data_center__name")
        rj["template_name"] = rj.pop("template__name")
        rj["template_code"] = rj.pop("template__code")
        rj["template"] = str(rj.pop("template_id")) if rj.get("template_id") else None
        rj["requested_by"] = str(rj.pop("requested_by_id")) if rj.get("requested_by_id") else None
        rj["requested_by_name"] = rj.pop("requested_by__username")
        rj["started_at"] = iso(rj["started_at"])
        rj["completed_at"] = iso(rj["completed_at"])
        rj["created_at"] = iso(rj["created_at"])
        rj["updated_at"] = iso(rj["updated_at"])
        rj["is_downloadable"] = bool(rj.get("file"))
        rj["can_retry"] = rj["status"] == "FAILED"
        rj["can_cancel"] = rj["status"] in ("PENDING", "PROCESSING")
        rj["file_url"] = f"http://mock.local/media/{rj['file']}" if rj.get("file") else None

    print("Building overview summary...")
    overview = _build_overview_summary(Device.objects.all())

    print("Sampling recent history points for key metrics...")
    history_samples = {}
    for dev in dev_objs:
        dev_id_str = str(dev.id)
        history_samples[dev_id_str] = {}
        # Fetch up to 10 latest metrics for this device
        latest_for_dev = LatestTelemetry.objects.filter(device=dev).select_related("metric")[:15]
        for lt in latest_for_dev:
            pts = list(TelemetryPoint.objects.filter(device=dev, metric=lt.metric).order_by("-time")[:20].values(
                "time", "value_float", "value_integer", "value_boolean", "value_text", "quality"
            ))
            if pts:
                for p in pts:
                    p["time"] = iso(p["time"])
                    p["metric_code"] = lt.metric.code
                history_samples[dev_id_str][lt.metric.code] = list(reversed(pts))

    result = {
        "users": users,
        "roles": roles,
        "permissions": permissions,
        "organizations": orgs,
        "data_centers": dcs,
        "rooms": rooms,
        "racks": racks,
        "vendors": vendors,
        "device_types": dtypes,
        "device_models": models,
        "devices": devices,
        "device_protocol_configs": proto_configs,
        "metrics": metrics,
        "latest_telemetry": latest,
        "alerts": alerts,
        "alert_logs": logs,
        "notifications": notifs,
        "report_definitions": report_defs,
        "report_templates": report_temps,
        "report_schedules": report_scheds,
        "report_jobs": report_jobs,
        "overview_summary": overview,
        "history_samples": history_samples,
    }

    out_file = "/app/scripts/mock_data_dump.json"
    with open(out_file, "w") as f:
        json.dump(result, f, indent=2, cls=DjangoJSONEncoder)
    print(f"Successfully dumped complete mock data to {out_file}")

if __name__ == "__main__":
    main()
