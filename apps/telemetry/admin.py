from django.contrib import admin
from .models import MetricDefinition, TelemetryPoint, LatestTelemetry, TelemetryIngestLog, DeviceEvent

admin.site.register(MetricDefinition)
admin.site.register(TelemetryPoint)
admin.site.register(LatestTelemetry)
admin.site.register(TelemetryIngestLog)
admin.site.register(DeviceEvent)
