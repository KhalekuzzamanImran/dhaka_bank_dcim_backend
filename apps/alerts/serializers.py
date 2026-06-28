from rest_framework import serializers
from .models import AlertRule, AlertEvent

class AlertRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = AlertRule
        fields = "__all__"

class AlertEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = AlertEvent
        fields = "__all__"
