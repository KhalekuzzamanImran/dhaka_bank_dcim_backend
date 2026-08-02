from rest_framework.routers import DefaultRouter
from .views import ReportTemplateViewSet, ReportJobViewSet, ReportScheduleRunViewSet, ReportScheduleViewSet

router = DefaultRouter()
router.register(r"report-templates", ReportTemplateViewSet, basename="report-template")
router.register(r"report-jobs", ReportJobViewSet, basename="report-job")
router.register(r"report-schedules", ReportScheduleViewSet, basename="report-schedule")
router.register(r"report-schedule-runs", ReportScheduleRunViewSet, basename="report-schedule-run")

urlpatterns = router.urls
