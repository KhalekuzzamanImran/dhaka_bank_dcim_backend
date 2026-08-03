from rest_framework.routers import DefaultRouter
from django.urls import path

from .views import (
    ReportArtifactViewSet,
    ReportDashboardAPIView,
    ReportTemplateViewSet,
    ReportJobViewSet,
    ReportScheduleRunViewSet,
    ReportScheduleViewSet,
)

router = DefaultRouter()
router.register(r"report-templates", ReportTemplateViewSet, basename="report-template")
router.register(r"report-jobs", ReportJobViewSet, basename="report-job")
router.register(r"report-artifacts", ReportArtifactViewSet, basename="report-artifact")
router.register(r"report-schedules", ReportScheduleViewSet, basename="report-schedule")
router.register(r"report-schedule-runs", ReportScheduleRunViewSet, basename="report-schedule-run")

urlpatterns = [
    path("dashboard/", ReportDashboardAPIView.as_view(), name="report-dashboard"),
]
urlpatterns += router.urls
