from django.core.management.base import BaseCommand
from django.db import transaction

from accounting.models import Account
from products.models import Warehouse


class Command(BaseCommand):
    help = "Create (or ensure) the default Primary Warehouse and link it to CoA account 1151."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force-link",
            action="store_true",
            help="Force linking the primary warehouse to account 1151 even if already linked to another account.",
        )

    def handle(self, *args, **options):
        force_link = bool(options.get("force_link"))

        try:
            coa = Account.objects.get(code="1151", is_deleted=False)
        except Account.DoesNotExist:
            self.stdout.write(
                self.style.ERROR(
                    "CoA account code 1151 not found. Run: python manage.py seed_chart_of_accounts first."
                )
            )
            return

        with transaction.atomic():
            wh, created = Warehouse.objects.get_or_create(
                code="WH-001",
                defaults={
                    "name": coa.name or "Primary Warehouse",
                    "name_ar": "المستودع الرئيسي",
                    "is_active": True,
                },
            )

            # If created, link immediately
            if created:
                wh.coa_account = coa
                wh.save(update_fields=["coa_account", "updated_at"])
                self.stdout.write(self.style.SUCCESS(f"Created warehouse '{wh.code} - {wh.name}' and linked to 1151."))
                return

            # Existing warehouse: ensure link + keep names aligned
            if wh.coa_account_id is None or force_link:
                wh.coa_account = coa
                wh.save(update_fields=["coa_account", "updated_at"])

            # Align CoA name to warehouse name (warehouse is the source of truth)
            if wh.coa_account_id == coa.id and coa.name != wh.name:
                coa.name = wh.name
                coa.save(update_fields=["name", "updated_at"])

            self.stdout.write(
                self.style.SUCCESS(
                    f"Primary warehouse ensured: '{wh.code} - {wh.name}' (linked_account=1151)."
                )
            )

