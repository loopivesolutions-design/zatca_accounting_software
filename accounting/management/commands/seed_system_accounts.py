from django.core.management.base import BaseCommand

from accounting.models import Account, SystemAccount


MAPPING_TO_KEY = {
    "accounts_receivable": "ACCOUNTS_RECEIVABLE",
    "accounts_payable": "ACCOUNTS_PAYABLE",
    "vat_output": "VAT_OUTPUT",
    "vat_input": "VAT_INPUT",
    "sales_revenue": "SALES_REVENUE",
    "retained_earnings": "RETAINED_EARNINGS",
    "cash_and_bank": "CASH_AND_BANK",
}


class Command(BaseCommand):
    help = "Seed/refresh immutable SystemAccount registry from Account.zatca_mapping."

    def handle(self, *args, **options):
        created = 0
        updated = 0
        skipped = 0
        for mapping, key in MAPPING_TO_KEY.items():
            account = Account.objects.filter(is_deleted=False, zatca_mapping=mapping).order_by("created_at").first()
            if not account:
                skipped += 1
                self.stdout.write(self.style.WARNING(f"Skipped {key}: no account mapped to '{mapping}'"))
                continue
            obj, was_created = SystemAccount.objects.update_or_create(
                key=key,
                defaults={"account": account, "is_locked": True, "is_deleted": False},
            )
            if was_created:
                created += 1
            else:
                updated += 1
            self.stdout.write(self.style.SUCCESS(f"{obj.key} -> {obj.account.code}"))

        self.stdout.write(
            self.style.SUCCESS(f"System account seeding complete. created={created} updated={updated} skipped={skipped}")
        )

