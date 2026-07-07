from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import (
    AlertActiveBySeverityAPIView,
    AlertEventViewSet,
    AlertRecentAPIView,
    AlertRuleViewSet,
    AlertSummaryAPIView,
    AlertTopDevicesAPIView,
)

router = DefaultRouter()
router.register(r"alert-rules", AlertRuleViewSet, basename="alert-rule")
router.register(r"alert-events", AlertEventViewSet, basename="alert-event")

urlpatterns = router.urls + [
    path("summary/", AlertSummaryAPIView.as_view(), name="alert-summary"),
    path("active-by-severity/", AlertActiveBySeverityAPIView.as_view(), name="alert-active-by-severity"),
    path("top-devices/", AlertTopDevicesAPIView.as_view(), name="alert-top-devices"),
    path("recent/", AlertRecentAPIView.as_view(), name="alert-recent"),
]
