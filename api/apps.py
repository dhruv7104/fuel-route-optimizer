"""
AppConfig: pre-loads geocoded stations into memory on Django startup
for O(1) bounding-box lookups during API requests.
"""
from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "api"

    def ready(self):
        """Load geocoded stations into memory once at startup."""
        from .services import station_store
        station_store.load()
