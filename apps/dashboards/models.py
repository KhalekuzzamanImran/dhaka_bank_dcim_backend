from django.db import models
from apps.common.models import TimeStampedModel

class Dashboard(TimeStampedModel):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="dashboards")
    data_center = models.ForeignKey("datacenters.DataCenter", on_delete=models.CASCADE, related_name="dashboards", blank=True, null=True)
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    class Meta:
        db_table = "dashboards"
        constraints = [models.UniqueConstraint(fields=["organization", "code"], name="uq_org_dashboard_code")]
        indexes = [models.Index(fields=["organization", "data_center"]), models.Index(fields=["is_active"])]
    def __str__(self): return self.name

class DashboardWidget(TimeStampedModel):
    dashboard = models.ForeignKey(Dashboard, on_delete=models.CASCADE, related_name="widgets")
    title = models.CharField(max_length=255)
    widget_type = models.CharField(max_length=100)
    data_center = models.ForeignKey("datacenters.DataCenter", on_delete=models.CASCADE, related_name="dashboard_widgets", blank=True, null=True)
    device = models.ForeignKey("devices.Device", on_delete=models.SET_NULL, related_name="dashboard_widgets", blank=True, null=True)
    metric = models.ForeignKey("telemetry.MetricDefinition", on_delete=models.SET_NULL, related_name="dashboard_widgets", blank=True, null=True)
    position_x = models.PositiveIntegerField(default=0)
    position_y = models.PositiveIntegerField(default=0)
    width = models.PositiveIntegerField(default=4)
    height = models.PositiveIntegerField(default=3)
    config = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    class Meta:
        db_table = "dashboard_widgets"
        indexes = [models.Index(fields=["dashboard"]), models.Index(fields=["device"]), models.Index(fields=["metric"])]
    def __str__(self): return self.title
