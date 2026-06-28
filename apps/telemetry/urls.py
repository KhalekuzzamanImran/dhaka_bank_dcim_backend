from rest_framework.routers import DefaultRouter
from django.urls import path, include
from .views import MetricDefinitionViewSet, TelemetryPointViewSet, LatestTelemetryViewSet, TelemetryIngestLogViewSet, DeviceEventViewSet, TelemetryIngestViewSet
router = DefaultRouter()
router.register('metrics', MetricDefinitionViewSet)
router.register('points', TelemetryPointViewSet)
router.register('latest', LatestTelemetryViewSet)
router.register('ingest-logs', TelemetryIngestLogViewSet)
router.register('events', DeviceEventViewSet)
router.register('ingest', TelemetryIngestViewSet, basename='telemetry-ingest')
urlpatterns = [path('', include(router.urls))]
