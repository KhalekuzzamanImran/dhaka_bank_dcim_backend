from rest_framework import viewsets

from apps.common.access import filter_queryset_for_user
from apps.common.audit import write_audit


class AuditModelViewSet(viewsets.ModelViewSet):
    audit_resource_type = None
    permission_module = None

    def perform_create(self, serializer):
        instance = serializer.save()
        write_audit(
            "CREATE",
            self.audit_resource_type or instance.__class__.__name__,
            instance.pk,
            new_value=serializer.data,
            organization=getattr(instance, "organization", None),
        )

    def perform_update(self, serializer):
        instance = self.get_object()
        old_value = {f.name: str(getattr(instance, f.name, "")) for f in instance._meta.fields if hasattr(instance, f.name)}
        instance = serializer.save()
        write_audit(
            "UPDATE",
            self.audit_resource_type or instance.__class__.__name__,
            instance.pk,
            old_value=old_value,
            new_value=serializer.data,
            organization=getattr(instance, "organization", None),
        )

    def perform_destroy(self, instance):
        write_audit("DELETE", self.audit_resource_type or instance.__class__.__name__, instance.pk, organization=getattr(instance, "organization", None))
        instance.delete()


class ScopedModelViewSet(AuditModelViewSet):
    access_scope = "mixed"
    organization_field = "organization"
    data_center_field = "data_center"
    room_field = "room"
    rack_field = "rack"
    device_field = "device"

    def get_queryset(self):
        qs = super().get_queryset()
        return filter_queryset_for_user(
            qs,
            self.request.user,
            access_scope=self.access_scope,
            organization_field=self.organization_field,
            data_center_field=self.data_center_field,
            room_field=self.room_field,
            rack_field=self.rack_field,
            device_field=self.device_field,
        )
