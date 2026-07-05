from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.response import Response
from apps.common.viewsets import ScopedModelViewSet
from apps.common.audit import write_audit
from .models import AlertRule, AlertEvent, AlertStatus
from .serializers import AlertRuleSerializer, AlertEventSerializer
class AlertRuleViewSet(ScopedModelViewSet):
    access_scope = 'mixed'
    queryset = AlertRule.objects.select_related('organization','data_center','device_type','device','metric').all(); serializer_class = AlertRuleSerializer; permission_module = 'alert'; audit_resource_type = 'AlertRule'; filterset_fields = ['organization','data_center','device_type','device','metric','severity','is_active']
class AlertEventViewSet(ScopedModelViewSet):
    access_scope = 'mixed'
    queryset = AlertEvent.objects.select_related('organization','data_center','device','metric','alert_rule').all().order_by('-triggered_at'); serializer_class = AlertEventSerializer; permission_module = 'alert'; audit_resource_type = 'AlertEvent'; filterset_fields = ['organization','data_center','device','metric','severity','status']; search_fields = ['message']
    @action(detail=True, methods=['post'])
    def acknowledge(self, request, pk=None):
        event = self.get_object(); event.status = AlertStatus.ACKNOWLEDGED; event.acknowledged_by = request.user; event.acknowledged_at = timezone.now(); event.save(update_fields=['status','acknowledged_by','acknowledged_at','updated_at']); write_audit('ALERT_ACKNOWLEDGED','AlertEvent',event.pk,organization=event.organization); return Response(AlertEventSerializer(event).data)
    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        event = self.get_object(); event.status = AlertStatus.RESOLVED; event.resolved_at = timezone.now(); event.save(update_fields=['status','resolved_at','updated_at']); write_audit('ALERT_RESOLVED','AlertEvent',event.pk,organization=event.organization); return Response(AlertEventSerializer(event).data)
