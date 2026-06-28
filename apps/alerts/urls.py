from rest_framework.routers import DefaultRouter
from .views import AlertRuleViewSet, AlertEventViewSet

router = DefaultRouter()
router.register(r"alert-rules", AlertRuleViewSet, basename="alert-rule")
router.register(r"alert-events", AlertEventViewSet, basename="alert-event")

urlpatterns = router.urls
