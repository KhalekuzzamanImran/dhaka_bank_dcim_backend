from rest_framework import serializers

from .models import DeviceType, Vendor, DeviceModel, Device, DeviceProtocolConfig, DeviceCredential, PollingProfile, DevicePollingConfig, SNMPOIDMapping, ModbusRegisterMapping
from .services import build_device_activity_feed

class DeviceTypeSerializer(serializers.ModelSerializer):
    class Meta: model = DeviceType; fields = '__all__'
class VendorSerializer(serializers.ModelSerializer):
    class Meta: model = Vendor; fields = '__all__'
class DeviceModelSerializer(serializers.ModelSerializer):
    class Meta: model = DeviceModel; fields = '__all__'
class DeviceSerializer(serializers.ModelSerializer):
    data_center_name = serializers.CharField(source='data_center.name', read_only=True)
    device_type_name = serializers.CharField(source='device_type.name', read_only=True)
    stale_after_seconds = serializers.SerializerMethodField()

    class Meta:
        model = Device
        fields = [field.name for field in Device._meta.fields] + [
            'data_center_name',
            'device_type_name',
            'stale_after_seconds',
        ]

    def get_stale_after_seconds(self, obj):
        polling_config = getattr(obj, 'polling_config', None)
        profile = getattr(polling_config, 'polling_profile', None)
        return int(getattr(profile, 'stale_after_seconds', 180) or 180)


class DeviceDetailSerializer(DeviceSerializer):
    active_alarms_count = serializers.SerializerMethodField()
    active_alarms = serializers.SerializerMethodField()
    recent_events_count = serializers.SerializerMethodField()
    recent_events = serializers.SerializerMethodField()

    class Meta(DeviceSerializer.Meta):
        fields = DeviceSerializer.Meta.fields + [
            "active_alarms_count",
            "active_alarms",
            "recent_events_count",
            "recent_events",
        ]

    def _activity_feed(self, obj):
        cache_key = "_device_activity_feed"
        cached = getattr(self, cache_key, None)
        if cached and cached.get("device_id") == str(obj.pk):
            return cached
        feed = build_device_activity_feed(obj)
        setattr(self, cache_key, feed)
        return feed

    def get_active_alarms_count(self, obj):
        return self._activity_feed(obj)["active_alarms_count"]

    def get_active_alarms(self, obj):
        return self._activity_feed(obj)["active_alarms"]

    def get_recent_events_count(self, obj):
        return self._activity_feed(obj)["recent_events_count"]

    def get_recent_events(self, obj):
        return self._activity_feed(obj)["recent_events"]
class DeviceProtocolConfigSerializer(serializers.ModelSerializer):
    class Meta: model = DeviceProtocolConfig; fields = '__all__'
class DeviceCredentialSerializer(serializers.ModelSerializer):
    secret_summary = serializers.SerializerMethodField(read_only=True)
    class Meta:
        model = DeviceCredential
        fields = ['id','device','protocol','username','snmp_version','snmp_v3_auth_protocol','snmp_v3_priv_protocol','is_active','created_at','updated_at','secret_summary','password_encrypted','snmp_community_encrypted','snmp_v3_auth_key_encrypted','snmp_v3_priv_key_encrypted']
        extra_kwargs = {
            'password_encrypted': {'write_only': True, 'required': False},
            'snmp_community_encrypted': {'write_only': True, 'required': False},
            'snmp_v3_auth_key_encrypted': {'write_only': True, 'required': False},
            'snmp_v3_priv_key_encrypted': {'write_only': True, 'required': False},
        }
    def get_secret_summary(self, obj):
        return {'has_password': bool(obj.password_encrypted), 'has_snmp_community': bool(obj.snmp_community_encrypted), 'has_snmp_v3_auth_key': bool(obj.snmp_v3_auth_key_encrypted), 'has_snmp_v3_priv_key': bool(obj.snmp_v3_priv_key_encrypted)}
class PollingProfileSerializer(serializers.ModelSerializer):
    class Meta: model = PollingProfile; fields = '__all__'
class DevicePollingConfigSerializer(serializers.ModelSerializer):
    class Meta: model = DevicePollingConfig; fields = '__all__'
class SNMPOIDMappingSerializer(serializers.ModelSerializer):
    class Meta: model = SNMPOIDMapping; fields = '__all__'
class ModbusRegisterMappingSerializer(serializers.ModelSerializer):
    class Meta: model = ModbusRegisterMapping; fields = '__all__'
