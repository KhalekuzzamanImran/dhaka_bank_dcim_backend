from django.db.models import Prefetch
from zoneinfo import ZoneInfo
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.common.access import filter_queryset_for_user
from apps.common.viewsets import ScopedModelViewSet
from apps.common.audit import write_audit
from .models import AlertComment, AlertEvent, AlertEventLog, AlertRule
from .serializers import (
    AlertAcknowledgeSerializer,
    AlertEventDetailSerializer,
    AlertEventListSerializer,
    AlertResolveSerializer,
    AlertRuleSerializer,
)
from .services import acknowledge_alert, manually_resolve_alert
from .services.summary import (
    build_active_by_severity,
    build_dashboard_payload,
    build_recent_alerts,
    build_top_devices,
)


def _alert_event_queryset():
    return (
        AlertEvent.objects.select_related(
            "organization",
            "data_center",
            "device",
            "device__room",
            "device__rack",
            "device__device_type",
            "metric",
            "alert_rule",
            "acknowledged_by",
            "resolved_by",
        )
        .prefetch_related(
            Prefetch("comments", queryset=AlertComment.objects.select_related("user").order_by("created_at")),
            Prefetch("logs", queryset=AlertEventLog.objects.select_related("actor").order_by("created_at")),
        )
        .all()
        .order_by("-triggered_at")
    )


def get_alert_queryset_for_user(user):
    return filter_queryset_for_user(_alert_event_queryset(), user, access_scope="mixed")


def _summary_timezone_for_queryset(queryset):
    """Prefer the single data center timezone when the alert scope is narrow.

    If the queryset spans multiple data centers, fall back to the current Django
    timezone and let the summary service compute dates safely in that timezone.
    """

    timezone_names = list(
        queryset.order_by().values_list("data_center__timezone", flat=True).distinct()[:2]
    )
    timezone_names = [name for name in timezone_names if name]
    if len(timezone_names) == 1:
        try:
            return ZoneInfo(timezone_names[0])
        except Exception:
            return None
    return None


class AlertRuleViewSet(ScopedModelViewSet):
    access_scope = "mixed"
    queryset = AlertRule.objects.select_related("organization", "data_center", "device_type", "device", "metric").all()
    serializer_class = AlertRuleSerializer
    permission_module = "alert"
    audit_resource_type = "AlertRule"
    filterset_fields = ["organization", "data_center", "device_type", "device", "metric", "severity", "is_active"]


class AlertEventViewSet(ScopedModelViewSet):
    access_scope = "mixed"
    queryset = _alert_event_queryset()
    serializer_class = AlertEventDetailSerializer
    permission_module = "alert"
    audit_resource_type = "AlertEvent"
    filterset_fields = ["organization", "data_center", "device", "metric", "severity", "status"]
    search_fields = ["message"]

    def get_serializer_class(self):
        if self.action in {"list", "recent"}:
            return AlertEventListSerializer
        if self.action == "retrieve":
            return AlertEventDetailSerializer
        return AlertEventDetailSerializer

    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        event = self.get_object()
        serializer = AlertAcknowledgeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        comment = serializer.validated_data.get("comment")
        event = acknowledge_alert(event, request.user, comment=comment)
        write_audit("ALERT_ACKNOWLEDGED", "AlertEvent", event.pk, organization=event.organization, actor=request.user, message=comment)
        return Response(AlertEventDetailSerializer(event, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        event = self.get_object()
        serializer = AlertResolveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        comment = serializer.validated_data.get("comment")
        event = manually_resolve_alert(event, request.user, comment=comment)
        write_audit("ALERT_RESOLVED", "AlertEvent", event.pk, organization=event.organization, actor=request.user, message=comment)
        return Response(AlertEventDetailSerializer(event, context=self.get_serializer_context()).data)

    @action(detail=False, methods=["get"])
    def summary(self, request):
        qs = self.get_queryset()
        return Response(build_dashboard_payload(qs, business_timezone=_summary_timezone_for_queryset(qs)))

    @action(detail=False, methods=["get"])
    def active_by_severity(self, request):
        return Response(build_active_by_severity(self.get_queryset()))

    @action(detail=False, methods=["get"])
    def top_devices(self, request):
        return Response(build_top_devices(self.get_queryset()))

    @action(detail=False, methods=["get"])
    def recent(self, request):
        return Response(build_recent_alerts(self.get_queryset(), limit=20, context=self.get_serializer_context()))


class AlertSummaryAPIView(APIView):
    permission_module = "alert"

    def get(self, request):
        qs = get_alert_queryset_for_user(request.user)
        return Response(build_dashboard_payload(qs, business_timezone=_summary_timezone_for_queryset(qs)))


class AlertActiveBySeverityAPIView(APIView):
    permission_module = "alert"

    def get(self, request):
        return Response(build_active_by_severity(get_alert_queryset_for_user(request.user)))


class AlertTopDevicesAPIView(APIView):
    permission_module = "alert"

    def get(self, request):
        return Response(build_top_devices(get_alert_queryset_for_user(request.user)))


class AlertRecentAPIView(APIView):
    permission_module = "alert"

    def get(self, request):
        return Response(build_recent_alerts(get_alert_queryset_for_user(request.user), limit=20, context={"request": request}))
