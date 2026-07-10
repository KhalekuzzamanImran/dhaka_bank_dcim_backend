from rest_framework.routers import DefaultRouter
from django.urls import include, path

from .views import NotificationViewSet

router = DefaultRouter()
router.register(r"", NotificationViewSet, basename="notification")

legacy_router = DefaultRouter()
legacy_router.register(r"notifications", NotificationViewSet, basename="notification-legacy")

urlpatterns = router.urls + [path("", include(legacy_router.urls))]
