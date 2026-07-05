from apps.common.viewsets import ScopedModelViewSet
from .models import DataCenter, Room, Row, Rack
from .serializers import DataCenterSerializer, RoomSerializer, RowSerializer, RackSerializer


class DataCenterViewSet(ScopedModelViewSet):
    access_scope = 'data_center'
    data_center_field = 'id'
    queryset = DataCenter.objects.select_related('organization').all()
    serializer_class = DataCenterSerializer
    permission_module = 'datacenter'
    audit_resource_type = 'DataCenter'
    filterset_fields = ['organization', 'status']
    search_fields = ['name', 'code', 'city', 'address']
    ordering_fields = ['name', 'code', 'created_at']


class RoomViewSet(ScopedModelViewSet):
    access_scope = 'room'
    room_field = 'id'
    queryset = Room.objects.select_related('data_center').all()
    serializer_class = RoomSerializer
    permission_module = 'datacenter'
    audit_resource_type = 'Room'
    filterset_fields = ['data_center', 'room_type']
    search_fields = ['name', 'code', 'floor_name']


class RowViewSet(ScopedModelViewSet):
    access_scope = 'room'
    queryset = Row.objects.select_related('data_center', 'room').all()
    serializer_class = RowSerializer
    permission_module = 'datacenter'
    audit_resource_type = 'Row'
    filterset_fields = ['data_center', 'room']
    search_fields = ['name', 'code', 'description']


class RackViewSet(ScopedModelViewSet):
    access_scope = 'rack'
    rack_field = 'id'
    queryset = Rack.objects.select_related('data_center', 'room', 'row').all()
    serializer_class = RackSerializer
    permission_module = 'datacenter'
    audit_resource_type = 'Rack'
    filterset_fields = ['data_center', 'room', 'row', 'status']
    search_fields = ['name', 'code', 'row_label', 'position_label']
