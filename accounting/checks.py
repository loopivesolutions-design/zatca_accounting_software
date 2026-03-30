import os

from django.conf import settings
from django.core import checks

from accounting.validators import iter_models_with_foreign_key_to_account, registered_transaction_source_model_keys


@checks.register()
def strict_system_accounts_in_production(app_configs, **kwargs):
    """Production must not rely on mutable Account.code fallbacks for system postings."""
    if settings.DEBUG:
        return []
    if not getattr(settings, "ACCOUNTING_STRICT_SYSTEM_ACCOUNTS", False):
        return [
            checks.Error(
                "ACCOUNTING_STRICT_SYSTEM_ACCOUNTS must be True when DEBUG is False. "
                "Set it in the environment (see .env.example) after running seed_system_accounts.",
                id="accounting.E001",
            )
        ]
    return []


def _strict_transaction_source_registry() -> bool:
    env = (os.getenv("STRICT_TRANSACTION_SOURCE_REGISTRY", "") or "").strip().lower()
    if env in {"1", "true", "yes", "on"}:
        return True
    if env in {"0", "false", "no", "off"}:
        return False
    return not settings.DEBUG


@checks.register()
def transaction_sources_cover_account_foreign_keys(app_configs, **kwargs):
    """
    Every model with a ForeignKey to Account should have a TRANSACTION_SOURCES row
    so AccountValidator.get_transaction_count() stays aligned with real usage.
    """
    if not _strict_transaction_source_registry():
        return []
    registered = registered_transaction_source_model_keys()
    errors = []
    seen: set[tuple[str, str]] = set()
    for app_label, model_name, _field in iter_models_with_foreign_key_to_account():
        key = (app_label, model_name)
        if key in seen:
            continue
        seen.add(key)
        if key not in registered:
            label = f"{app_label}.{model_name}"
            errors.append(
                checks.Error(
                    f"Model {label} has a ForeignKey to Account but is not listed in "
                    f"accounting.validators.TRANSACTION_SOURCES — CoA immutability may under-count "
                    f"activity. Add a source spec or exempt with STRICT_TRANSACTION_SOURCE_REGISTRY=off.",
                    id="accounting.E002",
                )
            )
    return errors
