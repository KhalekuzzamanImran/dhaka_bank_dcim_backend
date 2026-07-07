from rest_framework import serializers
from .models import AlertRule, AlertEvent, AlertConditionState, AlertEventLog, AlertComment, AlertEscalationPolicy, AlertSuppressionWindow

class AlertRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = AlertRule
        fields = "__all__"

class AlertEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = AlertEvent
        fields = "__all__"


class AlertConditionStateSerializer(serializers.ModelSerializer):
    class Meta:
        model = AlertConditionState
        fields = "__all__"


class AlertEventLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AlertEventLog
        fields = "__all__"


class AlertCommentSerializer(serializers.ModelSerializer):
    class Meta:
        model = AlertComment
        fields = "__all__"


class AlertEscalationPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = AlertEscalationPolicy
        fields = "__all__"


class AlertSuppressionWindowSerializer(serializers.ModelSerializer):
    class Meta:
        model = AlertSuppressionWindow
        fields = "__all__"
