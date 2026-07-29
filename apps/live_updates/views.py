from rest_framework.views import APIView
from rest_framework.response import Response

from .services import get_live_update_snapshot


class LiveUpdateStateAPIView(APIView):
    def get(self, request):
        return Response(get_live_update_snapshot())

