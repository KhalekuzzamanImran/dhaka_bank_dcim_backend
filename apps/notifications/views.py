from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.exceptions import MethodNotAllowed
from rest_framework.response import Response

from apps.common.viewsets import ScopedModelViewSet

from .models import Notification
from .serializers import NotificationSerializer


class NotificationViewSet(ScopedModelViewSet):
    http_method_names = ["get", "head", "options", "post"]
    access_scope = "organization"
    organization_field = "organization"
    queryset = Notification.objects.select_related("organization", "recipient").all().order_by("-created_at")
    serializer_class = NotificationSerializer
    permission_module = "notification"
    audit_resource_type = "Notification"

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if not user or not user.is_authenticated:
            return qs.none()
        if user.is_superuser or user.is_staff:
            return qs
        return qs.filter(recipient=user)

    def create(self, request, *args, **kwargs):
        raise MethodNotAllowed("POST")

    def update(self, request, *args, **kwargs):
        raise MethodNotAllowed("PUT")

    def partial_update(self, request, *args, **kwargs):
        raise MethodNotAllowed("PATCH")

    def destroy(self, request, *args, **kwargs):
        raise MethodNotAllowed("DELETE")

    def _mark_read_queryset(self):
        user = self.request.user
        if user.is_superuser or user.is_staff:
            return self.get_queryset()
        return self.get_queryset().filter(recipient=user)

    @action(detail=False, methods=["get"])
    def unread_count(self, request):
        return Response(
            {
                "unread_count": Notification.objects.filter(
                    recipient=request.user,
                    read_at__isnull=True,
                ).count()
            }
        )

    @action(detail=True, methods=["post"])
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        if notification.recipient_id != request.user.id:
            return Response(status=404)
        if notification.read_at is None:
            notification.read_at = timezone.now()
            notification.save(update_fields=["read_at", "updated_at"])
        return Response(self.get_serializer(notification).data)

    @action(detail=False, methods=["post"])
    def mark_all_read(self, request):
        qs = Notification.objects.filter(recipient=request.user, read_at__isnull=True)
        updated = qs.update(read_at=timezone.now())
        return Response({"updated_count": updated})
