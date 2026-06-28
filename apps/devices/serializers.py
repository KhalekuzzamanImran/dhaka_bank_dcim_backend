from rest_framework import serializers
from .models import DeviceType, Vendor, DeviceModel, Device, DeviceProtocolConfig, DeviceCredential, PollingProfile, DevicePollingConfig, SNMPOIDMapping, ModbusRegisterMapping

class DeviceTypeSerializer(serializers.ModelSerializer):
    class Meta: model = DeviceType; fields = '__all__'
class VendorSerializer(serializers.ModelSerializer):
    class Meta: model = Vendor; fields = '__all__'
class DeviceModelSerializer(serializers.ModelSerializer):
    class Meta: model = DeviceModel; fields = '__all__'
class DeviceSerializer(serializers.ModelSerializer):
    data_center_name = serializers.CharField(source='data_center.name', read_only=True)
    device_type_name = serializers.CharField(source='device_type.name', read_only=True)
    class Meta: model = Device; fields = '__all__'
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
