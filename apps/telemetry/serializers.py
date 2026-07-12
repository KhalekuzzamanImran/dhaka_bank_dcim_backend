from datetime import datetime, time, timedelta

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import serializers

from .models import MetricDefinition, TelemetryPoint, LatestTelemetry, TelemetryIngestLog, DeviceEvent


def _parse_history_datetime(raw_value, *, end_of_day=False):
    if raw_value in (None, ""):
        return None

    parsed = parse_datetime(raw_value)
    if parsed is None:
        parsed_date = parse_date(raw_value)
        if parsed_date is not None:
            parsed = datetime.combine(parsed_date, time.max if end_of_day else time.min)

    if parsed is None:
        raise serializers.ValidationError(f"Invalid datetime value: {raw_value!r}")

    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed

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


class TelemetryPointHistoryQuerySerializer(serializers.Serializer):
    device = serializers.UUIDField()
    metric = serializers.CharField(required=False, allow_blank=True)
    metric_code = serializers.CharField(required=False, allow_blank=True)
    date_from = serializers.CharField(required=False, allow_blank=True)
    date_to = serializers.CharField(required=False, allow_blank=True)
    time__gte = serializers.CharField(required=False, allow_blank=True)
    time__lte = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        metric_value = (attrs.get("metric_code") or attrs.get("metric") or "ups_load_percent").strip()
        metric = MetricDefinition.objects.filter(code=metric_value).first()
        if metric is None:
            metric = MetricDefinition.objects.filter(pk=metric_value).first()
        if metric is None:
            raise serializers.ValidationError({"metric": "Unknown telemetry metric."})

        start_raw = attrs.get("date_from") or attrs.get("time__gte")
        end_raw = attrs.get("date_to") or attrs.get("time__lte")
        start_dt = _parse_history_datetime(start_raw) if start_raw else None
        end_dt = _parse_history_datetime(end_raw, end_of_day=True) if end_raw else None

        if start_dt is None and end_dt is None:
            end_dt = timezone.now()
            start_dt = end_dt - timedelta(hours=24)
        elif start_dt is None or end_dt is None:
            raise serializers.ValidationError("Both date_from and date_to are required when range is not provided.")

        if start_dt > end_dt:
            raise serializers.ValidationError("date_from must be earlier than or equal to date_to.")

        attrs["metric_obj"] = metric
        attrs["start_dt"] = start_dt
        attrs["end_dt"] = end_dt
        return attrs


class TelemetryPointHistorySerializer(serializers.Serializer):
    time = serializers.DateTimeField()
    metric_code = serializers.CharField()
    quality = serializers.CharField()
    source = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    value = serializers.SerializerMethodField()

    def get_value(self, obj):
        for key in ("value_float", "value_integer", "value_boolean", "value_text"):
            value = obj.get(key)
            if value not in (None, ""):
                return value
        return None

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
