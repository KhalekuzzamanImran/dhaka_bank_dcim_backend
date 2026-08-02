from __future__ import annotations

from django.urls import path
from rest_framework.routers import DefaultRouter

from .api_views import (
    ReportArtifactViewSet as ApiReportArtifactViewSet,
    ReportDashboardAPIView,
    ReportDefinitionViewSet,
    ReportDeliveryViewSet as ApiReportDeliveryViewSet,
    ReportJobViewSet as ApiReportJobViewSet,
    ReportOptionsAPIView,
    ReportScheduleViewSet as ApiReportScheduleViewSet,
    ReportTemplateViewSet as ApiReportTemplateViewSet,
)
from .views import ReportJobViewSet, ReportScheduleViewSet, ReportTemplateViewSet

legacy_router = DefaultRouter()
legacy_router.register(r"report-templates", ReportTemplateViewSet, basename="report-template")
legacy_router.register(r"report-jobs", ReportJobViewSet, basename="report-job")
legacy_router.register(r"report-schedules", ReportScheduleViewSet, basename="report-schedule")

api_router = DefaultRouter()
api_router.register(r"definitions", ReportDefinitionViewSet, basename="report-definition")
api_router.register(r"templates", ApiReportTemplateViewSet, basename="report-template-api")
api_router.register(r"jobs", ApiReportJobViewSet, basename="report-job-api")
api_router.register(r"schedules", ApiReportScheduleViewSet, basename="report-schedule-api")
api_router.register(r"artifacts", ApiReportArtifactViewSet, basename="report-artifact-api")
api_router.register(r"deliveries", ApiReportDeliveryViewSet, basename="report-delivery-api")

urlpatterns = [
    *legacy_router.urls,
    *api_router.urls,
    path("dashboard/", ReportDashboardAPIView.as_view(), name="report-dashboard"),
    path("options/", ReportOptionsAPIView.as_view(), name="report-options"),
]

