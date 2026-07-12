from datetime import timedelta

from django.db.models import Avg, F, FloatField
from django.db.models.functions import Cast, Coalesce, TruncDay, TruncHour
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from apps.common.viewsets import AuditModelViewSet, ScopedModelViewSet
from .models import MetricDefinition, TelemetryPoint, LatestTelemetry, TelemetryIngestLog, DeviceEvent
from .serializers import (
    MetricDefinitionSerializer,
    TelemetryPointSerializer,
    TelemetryPointHistoryQuerySerializer,
    TelemetryPointHistorySerializer,
    LatestTelemetrySerializer,
    TelemetryIngestLogSerializer,
    DeviceEventSerializer,
    TelemetryBulkIngestSerializer,
)
from .services import ingest_points

class MetricDefinitionViewSet(AuditModelViewSet):
    queryset = MetricDefinition.objects.all(); serializer_class = MetricDefinitionSerializer; permission_module = 'telemetry'; audit_resource_type = 'MetricDefinition'; filterset_fields = ['category','data_type','is_active']; search_fields = ['code','name','unit']
class TelemetryPointViewSet(ScopedModelViewSet):
    access_scope = 'device'
    http_method_names = ['get','head','options']
    queryset = TelemetryPoint.objects.select_related('organization','data_center','device','metric').all().order_by('-time'); serializer_class = TelemetryPointSerializer; permission_module = 'telemetry'; audit_resource_type = 'TelemetryPoint'; filterset_fields = ['organization','data_center','device','metric','quality']; ordering_fields = ['time','created_at']

    def _floor_bucket_datetime(self, value, bucket_delta):
        if bucket_delta == timedelta(hours=1):
            return value.replace(minute=0, second=0, microsecond=0)
        return value.replace(hour=0, minute=0, second=0, microsecond=0)

    def _history_bucket_spec(self, start_dt, end_dt):
        duration = end_dt - start_dt
        if duration <= timedelta(days=1):
            return timedelta(hours=1), TruncHour
        return timedelta(days=1), TruncDay

    def _build_bucket_sequence(self, start_dt, end_dt, bucket_delta):
        current = start_dt
        buckets = []
        while current <= end_dt:
            buckets.append(current)
            current += bucket_delta
        return buckets

    @action(detail=False, methods=['get'])
    def history(self, request):
        query_serializer = TelemetryPointHistoryQuerySerializer(data=request.query_params)
        query_serializer.is_valid(raise_exception=True)
        validated = query_serializer.validated_data

        qs = self.filter_queryset(self.get_queryset()).filter(
            device_id=validated["device"],
            metric_id=validated["metric_obj"].id,
            time__gte=validated["start_dt"],
            time__lte=validated["end_dt"],
        ).order_by("time")

        if not qs.exists():
            payload = []
        else:
            bucket_delta, bucket_trunc = self._history_bucket_spec(validated["start_dt"], validated["end_dt"])
            current_tz = timezone.get_current_timezone()
            bucket_start = bucket_trunc("time", tzinfo=current_tz)
            start_bucket = self._floor_bucket_datetime(validated["start_dt"], bucket_delta)
            end_bucket = self._floor_bucket_datetime(validated["end_dt"], bucket_delta)

            bucketed_rows = (
                qs.annotate(
                    bucket=bucket_start,
                    numeric_value=Coalesce(
                        "value_float",
                        Cast("value_integer", FloatField()),
                        output_field=FloatField(),
                    ),
                )
                .values("bucket")
                .annotate(value_float=Avg("numeric_value"))
                .order_by("bucket")
            )
            bucket_map = {row["bucket"]: row["value_float"] for row in bucketed_rows}
            rows = []
            for bucket_time in self._build_bucket_sequence(start_bucket, end_bucket, bucket_delta):
                rows.append(
                    {
                        "time": bucket_time,
                        "metric_code": validated["metric_obj"].code,
                        "quality": "BUCKETED",
                        "source": "bucketed",
                        "value_float": bucket_map.get(bucket_time),
                        "value_integer": None,
                        "value_boolean": None,
                        "value_text": None,
                    }
                )
            payload = TelemetryPointHistorySerializer(rows, many=True).data
        return Response({
            "device": str(validated["device"]),
            "metric": validated["metric_obj"].code,
            "date_from": validated["start_dt"].isoformat(),
            "date_to": validated["end_dt"].isoformat(),
            "count": len(payload),
            "results": payload,
        })
class LatestTelemetryViewSet(ScopedModelViewSet):
    access_scope = 'device'
    http_method_names = ['get','head','options']
    queryset = LatestTelemetry.objects.select_related('organization','data_center','device','metric').all(); serializer_class = LatestTelemetrySerializer; permission_module = 'telemetry'; audit_resource_type = 'LatestTelemetry'; filterset_fields = ['organization','data_center','device','metric','quality']; ordering_fields = ['last_seen_at']
    @action(detail=False, methods=['get'])
    def summary(self, request):
        qs = self.filter_queryset(self.get_queryset())
        return Response({'total_latest_points': qs.count(), 'good': qs.filter(quality='GOOD').count(), 'bad': qs.filter(quality='BAD').count(), 'stale': qs.filter(quality='STALE').count()})
class TelemetryIngestLogViewSet(ScopedModelViewSet):
    access_scope = 'device'
    http_method_names = ['get','head','options']
    queryset = TelemetryIngestLog.objects.select_related('device','device__data_center').all().order_by('-started_at'); serializer_class = TelemetryIngestLogSerializer; permission_module = 'telemetry'; audit_resource_type = 'TelemetryIngestLog'; data_center_field = 'device__data_center'; filterset_fields = ['device','protocol','status']
class DeviceEventViewSet(ScopedModelViewSet):
    access_scope = 'device'
    queryset = DeviceEvent.objects.select_related('organization','data_center','device').all().order_by('-occurred_at'); serializer_class = DeviceEventSerializer; permission_module = 'telemetry'; audit_resource_type = 'DeviceEvent'; filterset_fields = ['organization','data_center','device','severity']; search_fields = ['event_code','event_name','message']
class TelemetryIngestViewSet(viewsets.ViewSet):
    permission_module = 'telemetry'
    @action(detail=False, methods=['post'])
    def bulk(self, request):
        serializer = TelemetryBulkIngestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ingest_id, created = ingest_points(serializer.validated_data['points'], serializer.validated_data.get('source','api'))
        return Response({'ingest_id': str(ingest_id), 'created_count': len(created)}, status=status.HTTP_201_CREATED)
