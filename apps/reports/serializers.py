from rest_framework import serializers
from .models import ReportTemplate, ReportJob

class ReportTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReportTemplate
        fields = "__all__"

class ReportJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReportJob
        fields = "__all__"
