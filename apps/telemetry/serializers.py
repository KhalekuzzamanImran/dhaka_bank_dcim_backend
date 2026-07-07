from rest_framework import serializers
from .models import MetricDefinition, TelemetryPoint, LatestTelemetry, TelemetryIngestLog, DeviceEvent

class MetricDefinitionSerializer(serializers.ModelSerializer):
    class Meta: model = MetricDefinition; fields = '__all__'
class TelemetryPointSerializer(serializers.ModelSerializer):
    metric_code = serializers.CharField(source='metric.code', read_only=True)
    device_name = serializers.CharField(source='device.name', read_only=True)
    class Meta: model = TelemetryPoint; fields = '__all__'
class LatestTelemetrySerializer(serializers.ModelSerializer):
    metric_code = serializers.CharField(source='metric.code', read_only=True)
    device_name = serializers.CharField(source='device.name', read_only=True)
    class Meta: model = LatestTelemetry; fields = '__all__'
class TelemetryIngestLogSerializer(serializers.ModelSerializer):
    class Meta: model = TelemetryIngestLog; fields = '__all__'
class DeviceEventSerializer(serializers.ModelSerializer):
    class Meta: model = DeviceEvent; fields = '__all__'

class TelemetryIngestItemSerializer(serializers.Serializer):
    device = serializers.UUIDField()
    metric_code = serializers.CharField(max_length=150)
    time = serializers.DateTimeField(required=False)
    value_float = serializers.FloatField(required=False, allow_null=True)
    value_integer = serializers.IntegerField(required=False, allow_null=True)
    value_boolean = serializers.BooleanField(required=False, allow_null=True)
    value_text = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    raw_value_text = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    quality = serializers.CharField(required=False, default='GOOD')
    source = serializers.CharField(required=False, allow_blank=True, allow_null=True)

class TelemetryBulkIngestSerializer(serializers.Serializer):
    source = serializers.CharField(required=False, default='api')
    points = TelemetryIngestItemSerializer(many=True)
