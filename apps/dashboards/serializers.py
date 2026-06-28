from rest_framework import serializers
from .models import Dashboard, DashboardWidget

class DashboardSerializer(serializers.ModelSerializer):
    class Meta:
        model = Dashboard
        fields = "__all__"

class DashboardWidgetSerializer(serializers.ModelSerializer):
    class Meta:
        model = DashboardWidget
        fields = "__all__"
