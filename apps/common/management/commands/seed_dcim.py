from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.organizations.models import Organization
from apps.datacenters.models import DataCenter, Room, Row, Rack
from apps.devices.models import DeviceType, DeviceCategory, Vendor, DeviceModel, Device, DeviceProtocolConfig, DeviceCredential, PollingProfile, DevicePollingConfig, ProtocolType, SNMPVersion, SNMPOIDMapping, ModbusRegisterMapping
from apps.telemetry.models import MetricDefinition, MetricCategory, MetricDataType
from apps.access_control.models import Role, Permission, RolePermission, RoleScope, UserResourceAccess
from collectors.snmp_collector.security import encrypt_secret
from apps.traps.models import SNMPTrapSource, SNMPTrapOIDMapping
from django.utils import timezone

PERMISSIONS = [
    'organization.view','organization.create','organization.update','organization.delete',
    'datacenter.view','datacenter.create','datacenter.update','datacenter.delete',
    'room.view','room.create','room.update','room.delete',
    'rack.view','rack.create','rack.update','rack.delete',
    'device.view','device.create','device.update','device.delete','device.credential.manage',
    'telemetry.view','telemetry.create','telemetry.export',
    'alert.view','alert.create','alert.update','alert.delete','alert.acknowledge','alert.resolve',
    'report.view','report.create','report.update','report.delete','report.generate','report.download',
    'user.view','user.create','user.update','user.delete','user.assign_role',
    'dashboard.view','dashboard.create','dashboard.update','dashboard.delete',
    'maintenance.view','maintenance.create','maintenance.update','maintenance.delete',
    'notification.view','notification.create','notification.update','notification.delete',
    'trap.view',
    'access.view','access.manage',
    'audit.view',
]
METRICS = [
    ('ups_input_voltage','UPS Input Voltage',MetricCategory.POWER,MetricDataType.FLOAT,'V'),
    ('ups_output_voltage','UPS Output Voltage',MetricCategory.POWER,MetricDataType.FLOAT,'V'),
    ('ups_load_percent','UPS Load Percent',MetricCategory.POWER,MetricDataType.FLOAT,'%'),
    ('ups_battery_charge','UPS Battery Charge',MetricCategory.POWER,MetricDataType.FLOAT,'%'),
    ('pac_temperature','PAC Temperature',MetricCategory.COOLING,MetricDataType.FLOAT,'°C'),
    ('pac_humidity','PAC Humidity',MetricCategory.ENVIRONMENT,MetricDataType.FLOAT,'%'),
    ('pdu_current','PDU Current',MetricCategory.POWER,MetricDataType.FLOAT,'A'),
    ('ats_source_status','ATS Source Status',MetricCategory.STATUS,MetricDataType.TEXT,None),
    ('water_leak_status','Water Leak Status',MetricCategory.ALARM,MetricDataType.BOOLEAN,None),
    ('snmp_sys_uptime','SNMP System Uptime',MetricCategory.STATUS,MetricDataType.INTEGER,'ticks'),
    ('meter_voltage_l1','Meter Voltage L1',MetricCategory.POWER,MetricDataType.FLOAT,'V'),
]
class Command(BaseCommand):
    help = 'Seed production-ready DCIM baseline data.'
    def handle(self, *args, **options):
        User = get_user_model()
        admin, _ = User.objects.get_or_create(username='admin', defaults={'email':'admin@example.com','full_name':'System Admin','is_staff':True,'is_superuser':True})
        admin.set_password('admin12345'); admin.save()
        org, _ = Organization.objects.get_or_create(code='DHAKA_BANK', defaults={'name':'Dhaka Bank','status':'ACTIVE'})
        dc, _ = DataCenter.objects.get_or_create(organization=org, code='PDC', defaults={'name':'Primary Data Center','city':'Dhaka','country':'Bangladesh','status':'ACTIVE'})
        room, _ = Room.objects.get_or_create(data_center=dc, code='SR-01', defaults={'name':'Server Room 01','room_type':'SERVER_ROOM','floor_name':'1st Floor'})
        row, _ = Row.objects.get_or_create(data_center=dc, room=room, code='ROW-A', defaults={'name':'Row A','position_x':1,'position_y':1})
        rack, _ = Rack.objects.get_or_create(data_center=dc, code='RACK-01', defaults={'room':room,'row':row,'name':'Rack 01','rack_u_height':42,'status':'ACTIVE','position_in_row':1})
        for code in PERMISSIONS:
            module = code.split('.')[0]
            Permission.objects.get_or_create(code=code, defaults={'module':module, 'description':code})
        roles = {
            'SUPER_ADMIN': Role.objects.update_or_create(code='SUPER_ADMIN', defaults={'name':'Super Admin','scope':RoleScope.GLOBAL,'status':'ACTIVE'})[0],
            'BANK_ADMIN': Role.objects.update_or_create(code='BANK_ADMIN', defaults={'name':'Bank Admin','scope':RoleScope.ORGANIZATION,'status':'ACTIVE'})[0],
            'DATA_CENTER_ADMIN': Role.objects.update_or_create(code='DATA_CENTER_ADMIN', defaults={'name':'Data Center Admin','scope':RoleScope.DATA_CENTER,'status':'ACTIVE'})[0],
            'ROOM_ADMIN': Role.objects.update_or_create(code='ROOM_ADMIN', defaults={'name':'Room Admin','scope':RoleScope.ROOM,'status':'ACTIVE'})[0],
            'RACK_ADMIN': Role.objects.update_or_create(code='RACK_ADMIN', defaults={'name':'Rack Admin','scope':RoleScope.RACK,'status':'ACTIVE'})[0],
            'DEVICE_ADMIN': Role.objects.update_or_create(code='DEVICE_ADMIN', defaults={'name':'Device Admin','scope':RoleScope.DEVICE,'status':'ACTIVE'})[0],
            'VIEWER': Role.objects.update_or_create(code='VIEWER', defaults={'name':'Viewer','scope':RoleScope.DATA_CENTER,'status':'ACTIVE'})[0],
        }
        all_permission_codes = list(PERMISSIONS)
        if not all_permission_codes:
            all_permission_codes = [p.code for p in Permission.objects.all()]
        code_map = {p.code: p for p in Permission.objects.all()}
        for code in all_permission_codes:
            perm = code_map.get(code)
            if perm is None:
                perm = Permission.objects.create(code=code, module=code.split('.')[0], description=code)
                code_map[code] = perm
            else:
                Permission.objects.update_or_create(code=code, defaults={'module': code.split('.')[0], 'description': code})
        permission_qs = Permission.objects.all()
        role_permissions = {
            'SUPER_ADMIN': permission_qs,
            'BANK_ADMIN': permission_qs.filter(code__in=[
                'organization.view','organization.create','organization.update','organization.delete',
                'datacenter.view','datacenter.create','datacenter.update','datacenter.delete',
                'room.view','room.create','room.update','room.delete',
                'rack.view','rack.create','rack.update','rack.delete',
                'device.view','device.create','device.update','device.delete','device.credential.manage',
                'telemetry.view','telemetry.export',
                'alert.view','alert.acknowledge','alert.resolve',
                'trap.view',
                'notification.view',
                'report.view','report.create','report.update','report.delete','report.generate','report.download',
                'maintenance.view','maintenance.create','maintenance.update','maintenance.delete',
                'audit.view',
                'user.view','user.create','user.update','user.delete','user.assign_role',
                'access.view','access.manage',
                'dashboard.view','dashboard.create','dashboard.update','dashboard.delete',
            ]),
            'DATA_CENTER_ADMIN': permission_qs.filter(code__in=[
                'datacenter.view',
                'room.view','room.create','room.update',
                'rack.view','rack.create','rack.update',
                'device.view','device.create','device.update',
                'telemetry.view','telemetry.export',
                'alert.view','alert.acknowledge','alert.resolve',
                'trap.view',
                'notification.view',
                'report.view','report.generate','report.download',
                'maintenance.view','maintenance.create','maintenance.update',
                'audit.view',
            ]),
            'ROOM_ADMIN': permission_qs.filter(code__in=[
                'room.view',
                'rack.view','rack.create','rack.update',
                'device.view','device.create','device.update',
                'telemetry.view',
                'alert.view','alert.acknowledge','alert.resolve',
                'trap.view',
                'notification.view',
                'report.view',
                'maintenance.view','maintenance.create','maintenance.update',
            ]),
            'RACK_ADMIN': permission_qs.filter(code__in=[
                'rack.view',
                'device.view','device.update',
                'telemetry.view',
                'alert.view','alert.acknowledge',
                'trap.view',
                'notification.view',
                'maintenance.view','maintenance.create','maintenance.update',
            ]),
            'DEVICE_ADMIN': permission_qs.filter(code__in=[
                'device.view','device.update',
                'telemetry.view',
                'alert.view','alert.acknowledge',
                'trap.view',
                'notification.view',
                'maintenance.view','maintenance.create','maintenance.update',
            ]),
            'VIEWER': permission_qs.filter(code__endswith='.view'),
        }
        for role_code, perms in role_permissions.items():
            role = roles[role_code]
            for p in perms:
                RolePermission.objects.update_or_create(role=role, permission=p)
        UserResourceAccess.objects.update_or_create(
            user=admin,
            organization=None,
            data_center=None,
            room=None,
            rack=None,
            device=None,
            role=roles['SUPER_ADMIN'],
            defaults={'assigned_by':admin,'is_active':True},
        )
        for name, cat in [('UPS',DeviceCategory.POWER),('PAC',DeviceCategory.COOLING),('ATS',DeviceCategory.POWER),('PDU',DeviceCategory.POWER),('AVR',DeviceCategory.POWER),('Temperature Sensor',DeviceCategory.ENVIRONMENT),('Water Leak Sensor',DeviceCategory.ENVIRONMENT)]:
            DeviceType.objects.get_or_create(code=name.upper().replace(' ','_'), defaults={'name':name,'category':cat})
        vendor, _ = Vendor.objects.get_or_create(code='GENERIC', defaults={'name':'Generic Vendor'})
        ups_type = DeviceType.objects.get(code='UPS')
        model, _ = DeviceModel.objects.get_or_create(vendor=vendor, device_type=ups_type, model_number='GENERIC-UPS', defaults={'name':'Generic UPS'})
        device, _ = Device.objects.get_or_create(data_center=dc, code='UPS-01', defaults={'organization':org,'room':room,'rack':rack,'device_type':ups_type,'device_model':model,'name':'UPS 01','ip_address':'10.10.10.10','status':'UNKNOWN'})
        for code,name,cat,dtype,unit in METRICS:
            MetricDefinition.objects.get_or_create(code=code, defaults={'name':name,'category':cat,'data_type':dtype,'unit':unit,'is_active':True})

        # Production SNMP worker baseline configuration for the sample UPS.
        snmp_profile, _ = PollingProfile.objects.get_or_create(
            name='SNMP Critical 60s', protocol=ProtocolType.SNMP,
            defaults={'interval_seconds':60,'timeout_seconds':5,'retry_count':2,'stale_after_seconds':180,'is_active':True}
        )
        DeviceProtocolConfig.objects.update_or_create(
            device=device, protocol=ProtocolType.SNMP, host=device.ip_address or '10.10.10.10', port=161,
            defaults={'timeout_seconds':5,'retry_count':2,'is_primary':True,'is_enabled':True}
        )
        DeviceCredential.objects.update_or_create(
            device=device, protocol=ProtocolType.SNMP,
            defaults={'snmp_version':SNMPVersion.V2C,'snmp_community_encrypted':encrypt_secret('public'),'is_active':True}
        )
        DevicePollingConfig.objects.update_or_create(
            device=device,
            defaults={'polling_profile':snmp_profile,'is_enabled':True,'next_poll_at':timezone.now()}
        )
        uptime_metric = MetricDefinition.objects.get(code='snmp_sys_uptime')
        SNMPOIDMapping.objects.get_or_create(
            device_type=ups_type, vendor=None, device_model=None, metric=uptime_metric, oid='1.3.6.1.2.1.1.3.0',
            defaults={'data_type':'integer','scale_factor':1,'offset_value':0,'is_active':True}
        )

        # Production Modbus worker baseline sample meter.
        meter_type, _ = DeviceType.objects.get_or_create(code='ENERGY_METER', defaults={'name':'Energy Meter','category':DeviceCategory.POWER})
        meter_model, _ = DeviceModel.objects.get_or_create(vendor=vendor, device_type=meter_type, model_number='GENERIC-METER', defaults={'name':'Generic Energy Meter'})
        meter, _ = Device.objects.get_or_create(data_center=dc, code='METER-01', defaults={'organization':org,'room':room,'rack':rack,'device_type':meter_type,'device_model':meter_model,'name':'Energy Meter 01','ip_address':'10.10.10.20','status':'UNKNOWN'})
        modbus_profile, _ = PollingProfile.objects.get_or_create(name='Modbus Normal 60s', protocol=ProtocolType.MODBUS_TCP, defaults={'interval_seconds':60,'timeout_seconds':5,'retry_count':2,'stale_after_seconds':180,'is_active':True})
        DeviceProtocolConfig.objects.update_or_create(device=meter, protocol=ProtocolType.MODBUS_TCP, host=meter.ip_address or '10.10.10.20', port=502, defaults={'timeout_seconds':5,'retry_count':2,'is_primary':True,'is_enabled':True})
        DevicePollingConfig.objects.update_or_create(device=meter, defaults={'polling_profile':modbus_profile,'is_enabled':True,'next_poll_at':timezone.now()})
        voltage_metric = MetricDefinition.objects.get(code='meter_voltage_l1')
        ModbusRegisterMapping.objects.get_or_create(device_type=meter_type, vendor=None, device_model=None, metric=voltage_metric, register_address=0, defaults={'slave_id':1,'function_code':3,'register_count':2,'data_type':'float32','scale_factor':1,'offset_value':0,'unit':'V','is_active':True})

        # SNMP Trap Receiver baseline sample.
        SNMPTrapSource.objects.get_or_create(organization=org, data_center=dc, device=device, source_ip=device.ip_address or '10.10.10.10', defaults={'is_enabled':True,'description':'Sample UPS trap source'})
        SNMPTrapOIDMapping.objects.get_or_create(device_type=ups_type, vendor=None, device_model=None, trap_oid='1.3.6.1.4.1.99999.1.1', defaults={'event_code':'UPS_ON_BATTERY','event_name':'UPS On Battery','severity':'WARNING','message_template':'UPS switched to battery mode','create_alert':True,'is_active':True})

        self.stdout.write(self.style.SUCCESS('Seed completed. Admin: admin / admin12345'))
