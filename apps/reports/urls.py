from django.urls import path
from rest_framework.routers import DefaultRouter

from .api_views import (
    ReportArtifactViewSet as NormalizedReportArtifactViewSet,
    ReportDefinitionViewSet,
    ReportDeliveryViewSet as NormalizedReportDeliveryViewSet,
    ReportJobViewSet as NormalizedReportJobViewSet,
    ReportOptionsAPIView,
    ReportScheduleRunViewSet as NormalizedReportScheduleRunViewSet,
    ReportScheduleViewSet as NormalizedReportScheduleViewSet,
    ReportTemplateViewSet as NormalizedReportTemplateViewSet,
)
from .views import (
    ReportArtifactViewSet as LegacyReportArtifactViewSet,
    ReportDashboardAPIView,
    ReportJobViewSet as LegacyReportJobViewSet,
    ReportScheduleRunViewSet as LegacyReportScheduleRunViewSet,
    ReportScheduleViewSet as LegacyReportScheduleViewSet,
    ReportTemplateViewSet as LegacyReportTemplateViewSet,
)


canonical_router = DefaultRouter()
canonical_router.register(r"definitions", ReportDefinitionViewSet, basename="report-definition")
canonical_router.register(r"templates", NormalizedReportTemplateViewSet, basename="report-template")
canonical_router.register(r"schedules", NormalizedReportScheduleViewSet, basename="report-schedule")
canonical_router.register(r"schedule-runs", NormalizedReportScheduleRunViewSet, basename="report-schedule-run")
canonical_router.register(r"jobs", NormalizedReportJobViewSet, basename="report-job")
canonical_router.register(r"artifacts", NormalizedReportArtifactViewSet, basename="report-artifact")
canonical_router.register(r"deliveries", NormalizedReportDeliveryViewSet, basename="report-delivery")

legacy_router = DefaultRouter()
legacy_router.register(r"report-templates", LegacyReportTemplateViewSet, basename="legacy-report-template")
legacy_router.register(r"report-jobs", LegacyReportJobViewSet, basename="legacy-report-job")
legacy_router.register(r"report-artifacts", LegacyReportArtifactViewSet, basename="legacy-report-artifact")
legacy_router.register(r"report-schedules", LegacyReportScheduleViewSet, basename="legacy-report-schedule")
legacy_router.register(r"report-schedule-runs", LegacyReportScheduleRunViewSet, basename="legacy-report-schedule-run")

urlpatterns = [
    path("dashboard/", ReportDashboardAPIView.as_view(), name="report-dashboard"),
    path("options/", ReportOptionsAPIView.as_view(), name="report-options"),
]
urlpatterns += canonical_router.urls
urlpatterns += legacy_router.urls
