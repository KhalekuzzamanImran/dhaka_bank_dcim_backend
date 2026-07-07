from django.db.models import Count
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common.access import filter_queryset_for_user
from apps.common.viewsets import ScopedModelViewSet
from apps.common.audit import write_audit
from .models import AlertRule, AlertEvent, AlertStatus
from .serializers import AlertRuleSerializer, AlertEventSerializer
from .services import acknowledge_alert, manually_resolve_alert


def get_alert_queryset_for_user(user):
    return filter_queryset_for_user(
        AlertEvent.objects.select_related("organization", "data_center", "device", "metric", "alert_rule").all().order_by("-triggered_at"),
        user,
        access_scope="mixed",
    )


class AlertRuleViewSet(ScopedModelViewSet):
    access_scope = "mixed"
    queryset = AlertRule.objects.select_related("organization", "data_center", "device_type", "device", "metric").all()
    serializer_class = AlertRuleSerializer
    permission_module = "alert"
    audit_resource_type = "AlertRule"
    filterset_fields = ["organization", "data_center", "device_type", "device", "metric", "severity", "is_active"]


class AlertEventViewSet(ScopedModelViewSet):
    access_scope = "mixed"
    queryset = AlertEvent.objects.select_related("organization", "data_center", "device", "metric", "alert_rule").all().order_by("-triggered_at")
    serializer_class = AlertEventSerializer
    permission_module = "alert"
    audit_resource_type = "AlertEvent"
    filterset_fields = ["organization", "data_center", "device", "metric", "severity", "status"]
    search_fields = ["message"]

    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        event = self.get_object()
        comment = request.data.get("comment")
        event = acknowledge_alert(event, request.user, comment=comment)
        write_audit("ALERT_ACKNOWLEDGED", "AlertEvent", event.pk, organization=event.organization, actor=request.user, message=comment)
        return Response(AlertEventSerializer(event).data)

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        event = self.get_object()
        comment = request.data.get("comment")
        event = manually_resolve_alert(event, request.user, comment=comment)
        write_audit("ALERT_RESOLVED", "AlertEvent", event.pk, organization=event.organization, actor=request.user, message=comment)
        return Response(AlertEventSerializer(event).data)

    @action(detail=False, methods=["get"])
    def summary(self, request):
        qs = self.get_queryset()
        open_qs = qs.filter(status__in=[AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED])
        data = {
            "open_total": open_qs.count(),
            "critical_open": open_qs.filter(severity="CRITICAL").count(),
            "warning_open": open_qs.filter(severity="WARNING").count(),
            "acknowledged_total": qs.filter(status=AlertStatus.ACKNOWLEDGED).count(),
            "resolved_today": qs.filter(status=AlertStatus.RESOLVED, resolved_at__date=timezone.localdate()).count(),
            "unacknowledged_critical": qs.filter(status=AlertStatus.OPEN, severity="CRITICAL").count(),
            "by_severity": {row["severity"]: row["total"] for row in open_qs.values("severity").annotate(total=Count("id")).order_by("severity")},
            "by_status": {row["status"]: row["total"] for row in qs.values("status").annotate(total=Count("id")).order_by("status")},
        }
        return Response(data)

    @action(detail=False, methods=["get"])
    def active_by_severity(self, request):
        qs = self.get_queryset().filter(status__in=[AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED])
        data = list(qs.values("severity").annotate(total=Count("id")).order_by("severity"))
        return Response(data)

    @action(detail=False, methods=["get"])
    def top_devices(self, request):
        qs = self.get_queryset().filter(status__in=[AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED])
        data = list(qs.values("device_id", "device__name").annotate(total=Count("id")).order_by("-total")[:10])
        return Response(data)

    @action(detail=False, methods=["get"])
    def recent(self, request):
        qs = self.get_queryset()[:20]
        return Response(AlertEventSerializer(qs, many=True).data)


class AlertSummaryAPIView(APIView):
    permission_module = "alert"

    def get(self, request):
        qs = get_alert_queryset_for_user(request.user)
        open_qs = qs.filter(status__in=[AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED])
        data = {
            "open_total": open_qs.count(),
            "critical_open": open_qs.filter(severity="CRITICAL").count(),
            "warning_open": open_qs.filter(severity="WARNING").count(),
            "acknowledged_total": qs.filter(status=AlertStatus.ACKNOWLEDGED).count(),
            "resolved_today": qs.filter(status=AlertStatus.RESOLVED, resolved_at__date=timezone.localdate()).count(),
            "unacknowledged_critical": qs.filter(status=AlertStatus.OPEN, severity="CRITICAL").count(),
            "by_severity": {row["severity"]: row["total"] for row in qs.values("severity").annotate(total=Count("id")).order_by("severity")},
            "by_status": {row["status"]: row["total"] for row in qs.values("status").annotate(total=Count("id")).order_by("status")},
            "by_data_center": {row["data_center__name"]: row["total"] for row in qs.values("data_center__name").annotate(total=Count("id")).order_by("data_center__name")},
            "by_device_type": {row["device__device_type__name"]: row["total"] for row in qs.values("device__device_type__name").annotate(total=Count("id")).order_by("device__device_type__name")},
        }
        return Response(data)


class AlertActiveBySeverityAPIView(APIView):
    permission_module = "alert"

    def get(self, request):
        qs = get_alert_queryset_for_user(request.user).filter(status__in=[AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED])
        return Response(list(qs.values("severity").annotate(total=Count("id")).order_by("severity")))


class AlertTopDevicesAPIView(APIView):
    permission_module = "alert"

    def get(self, request):
        qs = get_alert_queryset_for_user(request.user).filter(status__in=[AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED])
        data = list(
            qs.values("device_id", "device__name", "severity")
            .annotate(total=Count("id"))
            .order_by("-total", "device__name")[:10]
        )
        return Response(data)


class AlertRecentAPIView(APIView):
    permission_module = "alert"

    def get(self, request):
        qs = get_alert_queryset_for_user(request.user)[:20]
        return Response(AlertEventSerializer(qs, many=True).data)
