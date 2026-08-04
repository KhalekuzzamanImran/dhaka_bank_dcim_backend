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
from .views import ReportDashboardAPIView


canonical_router = DefaultRouter()
canonical_router.register(r"definitions", ReportDefinitionViewSet, basename="report-definition")
canonical_router.register(r"templates", NormalizedReportTemplateViewSet, basename="report-template")
canonical_router.register(r"schedules", NormalizedReportScheduleViewSet, basename="report-schedule")
canonical_router.register(r"schedule-runs", NormalizedReportScheduleRunViewSet, basename="report-schedule-run")
canonical_router.register(r"jobs", NormalizedReportJobViewSet, basename="report-job")
canonical_router.register(r"artifacts", NormalizedReportArtifactViewSet, basename="report-artifact")
canonical_router.register(r"deliveries", NormalizedReportDeliveryViewSet, basename="report-delivery")

urlpatterns = [
    path("dashboard/", ReportDashboardAPIView.as_view(), name="report-dashboard"),
    path("options/", ReportOptionsAPIView.as_view(), name="report-options"),
]
urlpatterns += canonical_router.urls
