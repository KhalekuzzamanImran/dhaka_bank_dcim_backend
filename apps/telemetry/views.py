from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from apps.common.viewsets import AuditModelViewSet, ScopedModelViewSet
from .models import MetricDefinition, TelemetryPoint, LatestTelemetry, TelemetryIngestLog, DeviceEvent
from .serializers import MetricDefinitionSerializer, TelemetryPointSerializer, LatestTelemetrySerializer, TelemetryIngestLogSerializer, DeviceEventSerializer, TelemetryBulkIngestSerializer
from .services import ingest_points

class MetricDefinitionViewSet(AuditModelViewSet):
    queryset = MetricDefinition.objects.all(); serializer_class = MetricDefinitionSerializer; permission_module = 'telemetry'; audit_resource_type = 'MetricDefinition'; filterset_fields = ['category','data_type','is_active']; search_fields = ['code','name','unit']
class TelemetryPointViewSet(ScopedModelViewSet):
    http_method_names = ['get','head','options']
    queryset = TelemetryPoint.objects.select_related('organization','data_center','device','metric').all().order_by('-time'); serializer_class = TelemetryPointSerializer; permission_module = 'telemetry'; audit_resource_type = 'TelemetryPoint'; filterset_fields = ['organization','data_center','device','metric','quality']; ordering_fields = ['time','created_at']
    @action(detail=False, methods=['get'])
    def history(self, request):
        qs = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(TelemetryPointSerializer(page, many=True).data)
        return Response(TelemetryPointSerializer(qs[:1000], many=True).data)
class LatestTelemetryViewSet(ScopedModelViewSet):
    http_method_names = ['get','head','options']
    queryset = LatestTelemetry.objects.select_related('organization','data_center','device','metric').all(); serializer_class = LatestTelemetrySerializer; permission_module = 'telemetry'; audit_resource_type = 'LatestTelemetry'; filterset_fields = ['organization','data_center','device','metric','quality']; ordering_fields = ['last_seen_at']
    @action(detail=False, methods=['get'])
    def summary(self, request):
        qs = self.filter_queryset(self.get_queryset())
        return Response({'total_latest_points': qs.count(), 'good': qs.filter(quality='GOOD').count(), 'bad': qs.filter(quality='BAD').count(), 'stale': qs.filter(quality='STALE').count()})
class TelemetryIngestLogViewSet(ScopedModelViewSet):
    http_method_names = ['get','head','options']
    queryset = TelemetryIngestLog.objects.select_related('device','device__data_center').all().order_by('-started_at'); serializer_class = TelemetryIngestLogSerializer; permission_module = 'telemetry'; audit_resource_type = 'TelemetryIngestLog'; data_center_field = 'device__data_center'; filterset_fields = ['device','protocol','status']
class DeviceEventViewSet(ScopedModelViewSet):
    queryset = DeviceEvent.objects.select_related('organization','data_center','device').all().order_by('-occurred_at'); serializer_class = DeviceEventSerializer; permission_module = 'telemetry'; audit_resource_type = 'DeviceEvent'; filterset_fields = ['organization','data_center','device','severity']; search_fields = ['event_code','event_name','message']
class TelemetryIngestViewSet(viewsets.ViewSet):
    permission_module = 'telemetry'
    @action(detail=False, methods=['post'])
    def bulk(self, request):
        serializer = TelemetryBulkIngestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ingest_id, created = ingest_points(serializer.validated_data['points'], serializer.validated_data.get('source','api'))
        return Response({'ingest_id': str(ingest_id), 'created_count': len(created)}, status=status.HTTP_201_CREATED)
