from django.apps import AppConfig


class MainConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "main"

    def ready(self) -> None:
        from . import checks  # noqa: F401 — register Django system checks
        from . import clock_sync  # noqa: F401 — startup clock drift guard

        clock_sync.enforce_clock_drift_limit_on_startup()
