from django.db import models
from apps.common.models import TimeStampedModel

class DeviceCategory(models.TextChoices):
    POWER = "POWER", "Power"
    COOLING = "COOLING", "Cooling"
    ENVIRONMENT = "ENVIRONMENT", "Environment"
    NETWORK = "NETWORK", "Network"
    SECURITY = "SECURITY", "Security"
    GENERATOR = "GENERATOR", "Generator"
    OTHER = "OTHER", "Other"

class DeviceType(TimeStampedModel):
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=100, unique=True)
    category = models.CharField(max_length=50, choices=DeviceCategory.choices)
    description = models.TextField(blank=True, null=True)
    class Meta:
        db_table = "device_types"
        indexes = [models.Index(fields=["category"]), models.Index(fields=["code"])]
    def __str__(self): return self.name

class Vendor(TimeStampedModel):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=100, unique=True)
    website = models.URLField(blank=True, null=True)
    class Meta: db_table = "vendors"
    def __str__(self): return self.name

class DeviceModel(TimeStampedModel):
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="device_models")
    device_type = models.ForeignKey(DeviceType, on_delete=models.PROTECT, related_name="device_models")
    name = models.CharField(max_length=255)
    model_number = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    class Meta:
        db_table = "device_models"
        constraints = [models.UniqueConstraint(fields=["vendor", "model_number"], name="uq_vendor_model_number")]
    def __str__(self): return f"{self.vendor.name} {self.model_number}"

class DeviceStatus(models.TextChoices):
    ONLINE = "ONLINE", "Online"
    OFFLINE = "OFFLINE", "Offline"
    DEGRADED = "DEGRADED", "Degraded"
    MAINTENANCE = "MAINTENANCE", "Maintenance"
    UNKNOWN = "UNKNOWN", "Unknown"


class ModbusGatewayConnectionStatus(models.TextChoices):
    DISCONNECTED = "DISCONNECTED", "Disconnected"
    CONNECTED = "CONNECTED", "Connected"


class ModbusGateway(TimeStampedModel):
    """An allow-listed reverse TCP-to-RTU gateway reachable over a private VPN."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="modbus_gateways")
    data_center = models.ForeignKey("datacenters.DataCenter", on_delete=models.CASCADE, related_name="modbus_gateways")
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=100)
    source_ip = models.GenericIPAddressField(unique=True, help_text="Stable VPN address observed when this gateway connects.")
    is_active = models.BooleanField(default=True)
    connection_status = models.CharField(max_length=20, choices=ModbusGatewayConnectionStatus.choices, default=ModbusGatewayConnectionStatus.DISCONNECTED)
    last_connected_at = models.DateTimeField(blank=True, null=True)
    last_disconnected_at = models.DateTimeField(blank=True, null=True)
    last_error = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "modbus_gateways"
        constraints = [models.UniqueConstraint(fields=["data_center", "code"], name="uq_dc_modbus_gateway_code")]
        indexes = [
            models.Index(fields=["organization", "data_center"], name="modbus_gate_organiz_03279f_idx"),
            models.Index(fields=["is_active", "connection_status"], name="modbus_gate_is_acti_2c8a56_idx"),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        super().clean()
        if self.organization_id and self.data_center_id:
            if self.data_center.organization_id != self.organization_id:
                raise ValidationError({"data_center": "The gateway data center must belong to the selected organization."})

    def __str__(self):
        return f"{self.name} ({self.code})"


class ModbusGatewayWriteStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    PROCESSING = "PROCESSING", "Processing"
    SUCCEEDED = "SUCCEEDED", "Succeeded"
    FAILED = "FAILED", "Failed"


class ModbusGatewayWriteRequest(TimeStampedModel):
    """Auditable, one-register write queued for the active reverse gateway session."""

    gateway = models.ForeignKey(ModbusGateway, on_delete=models.CASCADE, related_name="write_requests")
    device = models.ForeignKey("devices.Device", on_delete=models.CASCADE, related_name="modbus_gateway_write_requests")
    register_address = models.PositiveIntegerField()
    register_value = models.PositiveIntegerField()
    slave_id = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=20, choices=ModbusGatewayWriteStatus.choices, default=ModbusGatewayWriteStatus.PENDING)
    response_value = models.PositiveIntegerField(blank=True, null=True)
    error_message = models.TextField(blank=True, default="")
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "modbus_gateway_write_requests"
        indexes = [models.Index(fields=["gateway", "status", "created_at"])]

    def clean(self):
        from django.core.exceptions import ValidationError
        super().clean()
        if self.gateway_id and self.device_id and not DeviceProtocolConfig.objects.filter(
            device_id=self.device_id, modbus_gateway_id=self.gateway_id,
            protocol=ProtocolType.MODBUS_RTU_GATEWAY, is_enabled=True,
        ).exists():
            raise ValidationError({"device": "Device must have an enabled configuration for this gateway."})
        if self.register_address > 65535 or self.register_value > 65535 or self.slave_id > 247:
            raise ValidationError("Register address/value or slave ID is outside the supported Modbus range.")

    def __str__(self):
        return f"{self.gateway.code}: register {self.register_address} = {self.register_value} ({self.status})"

class Device(TimeStampedModel):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="devices")
    data_center = models.ForeignKey("datacenters.DataCenter", on_delete=models.CASCADE, related_name="devices")
    room = models.ForeignKey("datacenters.Room", on_delete=models.SET_NULL, related_name="devices", blank=True, null=True)
    rack = models.ForeignKey("datacenters.Rack", on_delete=models.SET_NULL, related_name="devices", blank=True, null=True)
    device_type = models.ForeignKey(DeviceType, on_delete=models.PROTECT, related_name="devices")
    device_model = models.ForeignKey(DeviceModel, on_delete=models.SET_NULL, related_name="devices", blank=True, null=True)
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=100)
    hostname = models.CharField(max_length=255, blank=True, null=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    port = models.PositiveIntegerField(blank=True, null=True)
    serial_number = models.CharField(max_length=255, blank=True, null=True)
    asset_tag = models.CharField(max_length=255, blank=True, null=True)
    installed_at = models.DateField(blank=True, null=True)
    warranty_expiry_date = models.DateField(blank=True, null=True)
    status = models.CharField(max_length=30, choices=DeviceStatus.choices, default=DeviceStatus.UNKNOWN, db_index=True)
    is_active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(blank=True, null=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    class Meta:
        db_table = "devices"
        constraints = [models.UniqueConstraint(fields=["data_center", "code"], name="uq_dc_device_code")]
        indexes = [models.Index(fields=["organization", "data_center"]), models.Index(fields=["data_center", "device_type"]), models.Index(fields=["ip_address"]), models.Index(fields=["status"]), models.Index(fields=["last_seen_at"]), models.Index(fields=["is_active"])]
    def __str__(self): return self.name

class ProtocolType(models.TextChoices):
    SNMP = "SNMP", "SNMP"
    MODBUS_TCP = "MODBUS_TCP", "Modbus TCP"
    MODBUS_RTU_GATEWAY = "MODBUS_RTU_GATEWAY", "Modbus RTU Gateway"
    MQTT = "MQTT", "MQTT"
    HTTP_API = "HTTP_API", "HTTP API"
    PING = "PING", "Ping"
    MANUAL = "MANUAL", "Manual"

class DeviceProtocolConfig(TimeStampedModel):
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="protocol_configs")
    protocol = models.CharField(max_length=50, choices=ProtocolType.choices)
    host = models.CharField(max_length=255, blank=True, help_text="Unused for a reverse-connected RTU gateway.")
    port = models.PositiveIntegerField(blank=True, null=True)
    modbus_gateway = models.ForeignKey(ModbusGateway, on_delete=models.PROTECT, related_name="device_configs", blank=True, null=True)
    timeout_seconds = models.PositiveIntegerField(default=5)
    retry_count = models.PositiveIntegerField(default=3)
    is_primary = models.BooleanField(default=True)
    is_enabled = models.BooleanField(default=True)
    extra_config = models.JSONField(default=dict, blank=True)
    class Meta:
        db_table = "device_protocol_configs"
        indexes = [models.Index(fields=["device", "protocol"]), models.Index(fields=["is_enabled"])]
        constraints = [models.UniqueConstraint(fields=["device", "protocol", "host", "port"], name="uq_device_protocol_endpoint")]
    def __str__(self): return f"{self.device.name} - {self.protocol}"

    def clean(self):
        from django.core.exceptions import ValidationError

        super().clean()
        if self.protocol == ProtocolType.MODBUS_RTU_GATEWAY and not self.modbus_gateway_id:
            raise ValidationError({"modbus_gateway": "Select the reverse-connected gateway for this device."})
        if self.protocol == ProtocolType.MODBUS_RTU_GATEWAY and self.modbus_gateway_id and self.device_id:
            if self.modbus_gateway.organization_id != self.device.organization_id:
                raise ValidationError({"modbus_gateway": "The gateway and battery must belong to the same organization."})
            if self.modbus_gateway.data_center_id != self.device.data_center_id:
                raise ValidationError({"modbus_gateway": "The gateway and battery must belong to the same data center."})
        if self.protocol != ProtocolType.MODBUS_RTU_GATEWAY and self.modbus_gateway_id:
            raise ValidationError({"modbus_gateway": "A gateway association is only valid for Modbus RTU Gateway protocol."})

class SNMPVersion(models.TextChoices):
    V1 = "V1", "v1"
    V2C = "V2C", "v2c"
    V3 = "V3", "v3"

class DeviceCredential(TimeStampedModel):
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="credentials")
    protocol = models.CharField(max_length=50, choices=ProtocolType.choices)
    username = models.CharField(max_length=255, blank=True, null=True)
    password_encrypted = models.TextField(blank=True, null=True)
    snmp_version = models.CharField(max_length=10, choices=SNMPVersion.choices, blank=True, null=True)
    snmp_community_encrypted = models.TextField(blank=True, null=True)
    snmp_v3_auth_protocol = models.CharField(max_length=50, blank=True, null=True)
    snmp_v3_auth_key_encrypted = models.TextField(blank=True, null=True)
    snmp_v3_priv_protocol = models.CharField(max_length=50, blank=True, null=True)
    snmp_v3_priv_key_encrypted = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    class Meta:
        db_table = "device_credentials"
        indexes = [models.Index(fields=["device", "protocol"]), models.Index(fields=["is_active"])]
    def __str__(self): return f"{self.device.name} - {self.protocol}"

class PollingPriority(models.TextChoices):
    HIGH = "HIGH", "High"
    NORMAL = "NORMAL", "Normal"
    LOW = "LOW", "Low"

class PollingProfile(TimeStampedModel):
    name = models.CharField(max_length=255)
    protocol = models.CharField(max_length=50, choices=ProtocolType.choices)
    priority = models.CharField(max_length=20, choices=PollingPriority.choices, default=PollingPriority.NORMAL)
    interval_seconds = models.PositiveIntegerField(default=60)
    timeout_seconds = models.PositiveIntegerField(default=5)
    retry_count = models.PositiveIntegerField(default=3)
    stale_after_seconds = models.PositiveIntegerField(default=180)
    is_active = models.BooleanField(default=True)
    class Meta:
        db_table = "polling_profiles"
        indexes = [models.Index(fields=["protocol"]), models.Index(fields=["priority"]), models.Index(fields=["is_active"])]
    def __str__(self): return self.name

class DevicePollingConfig(TimeStampedModel):
    device = models.OneToOneField(Device, on_delete=models.CASCADE, related_name="polling_config")
    polling_profile = models.ForeignKey(PollingProfile, on_delete=models.PROTECT, related_name="device_configs")
    is_enabled = models.BooleanField(default=True)
    last_polled_at = models.DateTimeField(blank=True, null=True)
    next_poll_at = models.DateTimeField(blank=True, null=True)
    consecutive_failures = models.PositiveIntegerField(default=0)
    last_error_message = models.TextField(blank=True, null=True)
    class Meta:
        db_table = "device_polling_configs"
        indexes = [models.Index(fields=["is_enabled"]), models.Index(fields=["next_poll_at"]), models.Index(fields=["last_polled_at"])]
    def __str__(self): return f"{self.device.name} polling"

class SNMPOIDMapping(TimeStampedModel):
    device_type = models.ForeignKey(DeviceType, on_delete=models.CASCADE, related_name="snmp_oid_mappings")
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="snmp_oid_mappings", blank=True, null=True)
    device_model = models.ForeignKey(DeviceModel, on_delete=models.CASCADE, related_name="snmp_oid_mappings", blank=True, null=True)
    metric = models.ForeignKey("telemetry.MetricDefinition", on_delete=models.CASCADE, related_name="snmp_oid_mappings")
    oid = models.CharField(max_length=255)
    data_type = models.CharField(max_length=50, default="float")
    scale_factor = models.DecimalField(max_digits=18, decimal_places=6, default=1)
    offset_value = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    is_active = models.BooleanField(default=True)
    class Meta:
        db_table = "snmp_oid_mappings"
        indexes = [models.Index(fields=["device_type"]), models.Index(fields=["vendor"]), models.Index(fields=["device_model"]), models.Index(fields=["oid"]), models.Index(fields=["is_active"])]
    def __str__(self): return f"{self.metric.code} - {self.oid}"

class ModbusFunctionCode(models.IntegerChoices):
    READ_COILS = 1, "Read Coils"
    READ_DISCRETE_INPUTS = 2, "Read Discrete Inputs"
    READ_HOLDING_REGISTERS = 3, "Read Holding Registers"
    READ_INPUT_REGISTERS = 4, "Read Input Registers"

class ModbusRegisterMapping(TimeStampedModel):
    device_type = models.ForeignKey(DeviceType, on_delete=models.CASCADE, related_name="modbus_register_mappings")
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="modbus_register_mappings", blank=True, null=True)
    device_model = models.ForeignKey(DeviceModel, on_delete=models.CASCADE, related_name="modbus_register_mappings", blank=True, null=True)
    metric = models.ForeignKey("telemetry.MetricDefinition", on_delete=models.CASCADE, related_name="modbus_register_mappings")
    slave_id = models.PositiveIntegerField(default=1)
    function_code = models.PositiveSmallIntegerField(choices=ModbusFunctionCode.choices, default=ModbusFunctionCode.READ_HOLDING_REGISTERS)
    register_address = models.PositiveIntegerField()
    register_count = models.PositiveIntegerField(default=1)
    data_type = models.CharField(max_length=50)
    byte_order = models.CharField(max_length=20, default="BIG")
    word_order = models.CharField(max_length=20, default="BIG")
    scale_factor = models.DecimalField(max_digits=18, decimal_places=6, default=1)
    offset_value = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    unit = models.CharField(max_length=50, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    class Meta:
        db_table = "modbus_register_mappings"
        indexes = [models.Index(fields=["device_type"]), models.Index(fields=["vendor"]), models.Index(fields=["device_model"]), models.Index(fields=["register_address"]), models.Index(fields=["is_active"])]
    def __str__(self): return f"{self.metric.code} - {self.register_address}"
