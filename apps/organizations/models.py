from django.db import models
from apps.common.models import TimeStampedModel, StatusChoices

class Organization(TimeStampedModel):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50, unique=True)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=StatusChoices.choices, default=StatusChoices.ACTIVE, db_index=True)
    class Meta:
        db_table = "organizations"
        indexes = [models.Index(fields=["code"]), models.Index(fields=["status"])]
    def __str__(self):
        return self.name
