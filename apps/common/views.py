from django.db import connection
from django.core.cache import cache
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny

class HealthCheckView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    def get(self, request):
        return Response({'status': 'ok', 'service': 'bank-dcim-backend'})

class ReadinessCheckView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    def get(self, request):
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        cache.set('ready-check', 'ok', 10)
        return Response({'status': 'ready', 'database': 'ok', 'cache': cache.get('ready-check')})
