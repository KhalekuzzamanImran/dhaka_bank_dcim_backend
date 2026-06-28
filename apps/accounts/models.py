import uuid
from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    full_name = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    is_mfa_enabled = models.BooleanField(default=False)
    password_changed_at = models.DateTimeField(blank=True, null=True)
    failed_login_attempts = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(blank=True, null=True)
    last_login_ip = models.GenericIPAddressField(blank=True, null=True)
    class Meta:
        db_table = "users"
    def __str__(self):
        return self.username
