from rest_framework.routers import DefaultRouter
from .views import DashboardViewSet, DashboardWidgetViewSet

router = DefaultRouter()
router.register(r"dashboards", DashboardViewSet, basename="dashboard")
router.register(r"dashboard-widgets", DashboardWidgetViewSet, basename="dashboard-widget")

urlpatterns = router.urls
