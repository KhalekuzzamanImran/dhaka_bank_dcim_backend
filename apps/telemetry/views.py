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
from .services.history import get_telemetry_history_rows

class MetricDefinitionViewSet(AuditModelViewSet):
    queryset = MetricDefinition.objects.all(); serializer_class = MetricDefinitionSerializer; permission_module = 'telemetry'; audit_resource_type = 'MetricDefinition'; filterset_fields = ['category','data_type','is_active']; search_fields = ['code','name','unit']
class TelemetryPointViewSet(ScopedModelViewSet):
    access_scope = 'device'
    http_method_names = ['get','head','options']
    queryset = TelemetryPoint.objects.select_related('organization','data_center','device','metric').all().order_by('-time'); serializer_class = TelemetryPointSerializer; permission_module = 'telemetry'; audit_resource_type = 'TelemetryPoint'; filterset_fields = ['organization','data_center','device','metric','quality']; ordering_fields = ['time','created_at']

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
            rows = get_telemetry_history_rows(
                device_id=validated["device"],
                metric=validated["metric_obj"],
                start_dt=validated["start_dt"],
                end_dt=validated["end_dt"],
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

    @action(detail=False, methods=['get'])
    def overview(self, request):
        rows = (
            self.filter_queryset(self.get_queryset())
            .values(
                'device_id',
                'metric__code',
                'value_float',
                'value_integer',
                'value_boolean',
                'value_text',
                'quality',
                'last_seen_at',
            )
            .order_by('device_id', 'metric__code')
        )

        payload = [
            {
                'device_id': str(row['device_id']),
                'metric_code': row['metric__code'],
                'value_float': row['value_float'],
                'value_integer': row['value_integer'],
                'value_boolean': row['value_boolean'],
                'value_text': row['value_text'],
                'quality': row['quality'],
                'last_seen_at': row['last_seen_at'].isoformat() if row['last_seen_at'] else None,
            }
            for row in rows
        ]
        return Response({'count': len(payload), 'results': payload})
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
