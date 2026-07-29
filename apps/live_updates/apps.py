from django.apps import AppConfig


class LiveUpdatesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.live_updates"

    def ready(self):
        from . import signals  # noqa: F401

