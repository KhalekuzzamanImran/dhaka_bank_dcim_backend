from rest_framework.routers import DefaultRouter
from .views import ReportTemplateViewSet, ReportJobViewSet

router = DefaultRouter()
router.register(r"report-templates", ReportTemplateViewSet, basename="report-template")
router.register(r"report-jobs", ReportJobViewSet, basename="report-job")

urlpatterns = router.urls
