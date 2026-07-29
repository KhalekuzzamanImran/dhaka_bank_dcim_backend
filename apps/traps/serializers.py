from rest_framework import serializers
from .models import SNMPTrapSource, SNMPTrapOIDMapping, SNMPTrapEvent


class SNMPTrapSourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = SNMPTrapSource
        fields = "__all__"


class SNMPTrapOIDMappingSerializer(serializers.ModelSerializer):
    class Meta:
        model = SNMPTrapOIDMapping
        fields = "__all__"


class SNMPTrapEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = SNMPTrapEvent
        fields = "__all__"
        read_only_fields = [
            "is_processed",
            "is_mapped",
            "received_at",
            "resolution_source",
            "mib_module",
            "mib_symbol",
            "mib_description",
            "mib_status",
            "requires_mapping_review",
        ]
