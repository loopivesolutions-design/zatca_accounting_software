from django.conf import settings

from accounting.models import Account, SystemAccount


def get_system_account(key: str, *, fallback_code: str | None = None) -> Account:
    link = (
        SystemAccount.objects.select_related("account")
        .filter(key=key, is_deleted=False)
        .first()
    )
    if link and link.account and not link.account.is_deleted and not link.account.is_archived:
        return link.account

    strict = getattr(settings, "ACCOUNTING_STRICT_SYSTEM_ACCOUNTS", False)
    if strict:
        raise ValueError(
            f"System account mapping missing for key '{key}' (ACCOUNTING_STRICT_SYSTEM_ACCOUNTS is enabled; "
            "run seed_system_accounts and verify SystemAccount rows)."
        )

    if fallback_code:
        fallback = Account.objects.filter(code=fallback_code, is_deleted=False, is_archived=False).first()
        if fallback:
            return fallback

    raise ValueError(f"System account mapping missing for key '{key}'.")

