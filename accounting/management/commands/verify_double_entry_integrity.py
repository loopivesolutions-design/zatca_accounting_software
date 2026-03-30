from django.core.management.base import BaseCommand

from accounting.double_entry_audit import verify_global_double_entry_balance


class Command(BaseCommand):
    help = "Verify Σ posted debits = Σ posted credits across all journal lines (trial-balance identity)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fail-on-error",
            action="store_true",
            help="Exit with code 1 if imbalance detected (for CI).",
        )

    def handle(self, *args, **options):
        ok, msg = verify_global_double_entry_balance()
        if ok:
            self.stdout.write(self.style.SUCCESS(msg))
            return
        self.stdout.write(self.style.ERROR(msg))
        if options.get("fail_on_error"):
            raise SystemExit(1)
