from rest_framework.routers import DefaultRouter
from .views import ReportTemplateViewSet, ReportJobViewSet, ReportScheduleViewSet

router = DefaultRouter()
router.register(r"report-templates", ReportTemplateViewSet, basename="report-template")
router.register(r"report-jobs", ReportJobViewSet, basename="report-job")
router.register(r"report-schedules", ReportScheduleViewSet, basename="report-schedule")

urlpatterns = router.urls
