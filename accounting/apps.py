from django.apps import AppConfig


class AccountingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounting"
    verbose_name = "Accounting"

    def ready(self) -> None:
        from . import checks  # noqa: F401 — register Django system checks
        from .decimal_context import configure_decimal_context

        configure_decimal_context()
