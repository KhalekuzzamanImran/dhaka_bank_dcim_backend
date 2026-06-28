from rest_framework.decorators import action
from rest_framework.response import Response
from apps.common.viewsets import AuditModelViewSet
from .models import User
from .serializers import UserSerializer, MeSerializer

class UserViewSet(AuditModelViewSet):
    queryset = User.objects.all().order_by('username')
    serializer_class = UserSerializer
    permission_module = 'user'
    audit_resource_type = 'User'
    search_fields = ['username','email','full_name','phone']
    ordering_fields = ['username','email','date_joined','is_active']

    def get_serializer_class(self):
        if self.action == 'me':
            return MeSerializer
        return super().get_serializer_class()

    @action(detail=False, methods=['get','patch'])
    def me(self, request):
        if request.method == 'GET':
            return Response(MeSerializer(request.user).data)
        serializer = MeSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
