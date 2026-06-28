from django.db import models
from apps.common.models import TimeStampedModel, StatusChoices


class DataCenter(TimeStampedModel):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="data_centers")
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=100)
    address = models.TextField(blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    country = models.CharField(max_length=100, default="Bangladesh")
    latitude = models.DecimalField(max_digits=10, decimal_places=7, blank=True, null=True)
    longitude = models.DecimalField(max_digits=10, decimal_places=7, blank=True, null=True)
    timezone = models.CharField(max_length=100, default="Asia/Dhaka")
    status = models.CharField(max_length=20, choices=StatusChoices.choices, default=StatusChoices.ACTIVE, db_index=True)

    class Meta:
        db_table = "data_centers"
        constraints = [models.UniqueConstraint(fields=["organization", "code"], name="uq_org_datacenter_code")]
        indexes = [models.Index(fields=["organization", "status"]), models.Index(fields=["code"])]

    def __str__(self):
        return self.name


class RoomType(models.TextChoices):
    SERVER_ROOM = "SERVER_ROOM", "Server Room"
    POWER_ROOM = "POWER_ROOM", "Power Room"
    NETWORK_ROOM = "NETWORK_ROOM", "Network Room"
    CONTROL_ROOM = "CONTROL_ROOM", "Control Room"
    OTHER = "OTHER", "Other"


class Room(TimeStampedModel):
    data_center = models.ForeignKey(DataCenter, on_delete=models.CASCADE, related_name="rooms")
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=100)
    room_type = models.CharField(max_length=50, choices=RoomType.choices, default=RoomType.SERVER_ROOM)
    floor_name = models.CharField(max_length=100, blank=True, null=True)
    description = models.TextField(blank=True, null=True)

    class Meta:
        db_table = "rooms"
        constraints = [models.UniqueConstraint(fields=["data_center", "code"], name="uq_dc_room_code")]
        indexes = [models.Index(fields=["data_center"]), models.Index(fields=["room_type"])]

    def __str__(self):
        return f"{self.data_center.name} - {self.name}"


class Row(TimeStampedModel):
    data_center = models.ForeignKey(DataCenter, on_delete=models.CASCADE, related_name="rows")
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="rows")
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    position_x = models.PositiveIntegerField(default=0)
    position_y = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "rows"
        constraints = [models.UniqueConstraint(fields=["room", "code"], name="uq_room_row_code")]
        indexes = [models.Index(fields=["data_center"]), models.Index(fields=["room"]), models.Index(fields=["code"])]

    def __str__(self):
        return f"{self.room.name} - {self.name}"


class Rack(TimeStampedModel):
    data_center = models.ForeignKey(DataCenter, on_delete=models.CASCADE, related_name="racks")
    room = models.ForeignKey(Room, on_delete=models.SET_NULL, related_name="racks", blank=True, null=True)
    row = models.ForeignKey(Row, on_delete=models.SET_NULL, related_name="racks", blank=True, null=True)
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=100)
    rack_u_height = models.PositiveIntegerField(default=42)
    power_capacity_kw = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    position_in_row = models.PositiveIntegerField(blank=True, null=True)
    row_label = models.CharField(max_length=50, blank=True, null=True)  # legacy/display helper
    position_label = models.CharField(max_length=50, blank=True, null=True)
    status = models.CharField(max_length=20, choices=StatusChoices.choices, default=StatusChoices.ACTIVE)

    class Meta:
        db_table = "racks"
        constraints = [models.UniqueConstraint(fields=["data_center", "code"], name="uq_dc_rack_code")]
        indexes = [models.Index(fields=["data_center"]), models.Index(fields=["room"]), models.Index(fields=["row"]), models.Index(fields=["status"])]

    def __str__(self):
        return self.name
