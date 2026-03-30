from django.core.management.base import BaseCommand
from django.db.utils import OperationalError

from accounting.double_entry_audit import verify_global_double_entry_balance
from accounting.models import SystemAccount, TaxRate

# Keys required for core AR/AP/VAT posting (see accounting.services.posting).
REQUIRED_SYSTEM_ACCOUNT_KEYS = (
    "ACCOUNTS_RECEIVABLE",
    "ACCOUNTS_PAYABLE",
    "VAT_OUTPUT",
    "VAT_INPUT",
)


class Command(BaseCommand):
    help = "Validate accounting go-live controls (system account registry, etc.)."

    def add_arguments(self, parser):
        parser.add_argument("--strict", action="store_true", help="Exit non-zero if any check fails.")

    def handle(self, *args, **options):
        strict = bool(options.get("strict"))
        errors: list[str] = []

        try:
            for key in REQUIRED_SYSTEM_ACCOUNT_KEYS:
                row = SystemAccount.objects.filter(key=key, is_deleted=False).select_related("account").first()
                if not row or not row.account_id or row.account.is_deleted or row.account.is_archived:
                    errors.append(f"Missing or invalid SystemAccount for key '{key}'.")
        except OperationalError as exc:
            errors.append(f"Database not ready for SystemAccount checks (migrate?): {exc}")

        try:
            ok_gl, gl_msg = verify_global_double_entry_balance()
            if not ok_gl:
                errors.append(gl_msg)
        except OperationalError as exc:
            errors.append(f"Double-entry check skipped (database?): {exc}")

        try:
            if not TaxRate.objects.filter(is_deleted=False, zatca_category="S").exists():
                errors.append(
                    "No TaxRate with ZATCA category 'S' (standard VAT). Run seed_tax_rates or create a 15% standard rate for Saudi VAT."
                )
        except OperationalError as exc:
            errors.append(f"TaxRate checks skipped (database?): {exc}")

        for err in errors:
            self.stdout.write(self.style.ERROR(f"ERROR: {err}"))

        if errors:
            msg = f"Accounting readiness checks failed ({len(errors)} error(s))."
            if strict:
                raise SystemExit(msg)
            self.stdout.write(self.style.WARNING(msg))
            return

        self.stdout.write(self.style.SUCCESS("Accounting readiness checks passed."))
