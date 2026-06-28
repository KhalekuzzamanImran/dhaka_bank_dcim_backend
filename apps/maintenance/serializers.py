from rest_framework import serializers
from .models import MaintenanceTicket

class MaintenanceTicketSerializer(serializers.ModelSerializer):
    class Meta:
        model = MaintenanceTicket
        fields = "__all__"
