"""
Management command: seed_tax_rates
===================================
Seeds the default KSA / ZATCA-compliant tax rates.

All 10 default rates match the FATOORAH e-invoicing standard and the UI
screenshots. They are marked `is_default=True` and cannot be deleted.

Usage
-----
    python manage.py seed_tax_rates           # safe: skips existing records
    python manage.py seed_tax_rates --force   # re-seeds: updates all existing records

ZATCA Category Codes (FATOORAH XML)
-------------------------------------
    S  Standard rate   (15%)
    Z  Zero rate        (0%)
    E  Exempt           (0%)
    O  Out of scope     (0%)
"""

from django.core.management.base import BaseCommand
from accounting.models import TaxRate

# Tuple layout:
#   name, name_ar, tax_type, rate, zatca_category, description
DEFAULT_TAX_RATES = [
    # ── Out of Scope ──────────────────────────────────────────────────────────
    (
        "Out of Scope",
        "غير خاضع للضريبة",
        "out_of_scope",
        "0.00",
        "O",
        "Out of Scope",
    ),
    # ── Reverse Charge ────────────────────────────────────────────────────────
    (
        "Reverse Charge",
        "إحتساب عكسي",
        "reverse_charge",
        "15.00",
        "S",
        "KSA Reverse Charge VAT",
    ),
    # ── Purchases ─────────────────────────────────────────────────────────────
    (
        "Exempt Purchases",
        "مشتريات معفاة من الضريبة",
        "purchases",
        "0.00",
        "E",
        "KSA exempt purchases",
    ),
    (
        "Zero-rated Purchases",
        "مشتريات نسبة صفر",
        "purchases",
        "0.00",
        "Z",
        "KSA Zero-rated purchases",
    ),
    (
        "VAT at Customs",
        "الضريبة القيمة المضافة في الجمارك",
        "purchases",
        "15.00",
        "S",
        "KSA Standard Rate Input VAT paid at Customs",
    ),
    (
        "VAT on Purchases",
        "الضريبة القيمة المضافة على مشتريات",
        "purchases",
        "15.00",
        "S",
        "KSA Standard Rate Input VAT",
    ),
    # ── Sales ─────────────────────────────────────────────────────────────────
    (
        "Exempt",
        "معفى",
        "sales",
        "0.00",
        "E",
        "KSA Tax Exempt Output VAT",
    ),
    (
        "Zero-Rated Exports",
        "صادرات",
        "sales",
        "0.00",
        "Z",
        "KSA Zero-Rated Exports",
    ),
    (
        "Zero-Rated Domestic Sales",
        "مبيعات داخل المملكة خاضعة للضريبة بنسبة صفر",
        "sales",
        "0.00",
        "Z",
        "KSA Zero-Rated Domestic Sales",
    ),
    (
        "VAT on Sales",
        "الضريبة القيمة المضافة على الإيرادات",
        "sales",
        "15.00",
        "S",
        "KSA Standard Rate Output VAT",
    ),
]

TAX_TYPE_LABEL = {
    "sales":          "Sales",
    "purchases":      "Purchases",
    "reverse_charge": "Reverse Charge",
    "out_of_scope":   "Out of Scope",
}


class Command(BaseCommand):
    help = (
        "Seed the default KSA/ZATCA-compliant tax rates. "
        "Use --force to update existing records."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-seed all default tax rates, updating existing records.",
        )

    def handle(self, *args, **options):
        force = options["force"]
        existing = TaxRate.objects.filter(is_default=True, is_deleted=False).count()

        if existing and not force:
            self.stdout.write(
                self.style.WARNING(
                    f"Tax rates already seeded ({existing} default records). "
                    "Use --force to re-seed."
                )
            )
            return

        if force and existing:
            self.stdout.write(
                self.style.WARNING(
                    f"--force: re-seeding {len(DEFAULT_TAX_RATES)} default tax rates..."
                )
            )

        created_count = 0
        updated_count = 0

        for name, name_ar, tax_type, rate, zatca_category, description in DEFAULT_TAX_RATES:
            obj, created = TaxRate.objects.get_or_create(
                name=name,
                defaults={
                    "name_ar": name_ar,
                    "tax_type": tax_type,
                    "rate": rate,
                    "zatca_category": zatca_category,
                    "description": description,
                    "is_default": True,
                    "is_active": True,
                    "is_deleted": False,
                },
            )

            if not created and force:
                obj.name_ar = name_ar
                obj.tax_type = tax_type
                obj.rate = rate
                obj.zatca_category = zatca_category
                obj.description = description
                obj.is_default = True
                obj.is_active = True
                obj.is_deleted = False
                obj.save()
                updated_count += 1
                status_label = self.style.MIGRATE_LABEL("UPDATED")
            elif created:
                created_count += 1
                status_label = self.style.SUCCESS("CREATED")
            else:
                status_label = self.style.NOTICE("EXISTS ")

            self.stdout.write(
                f"  {status_label}  [{TAX_TYPE_LABEL[tax_type]:14s}]  "
                f"{rate:>6}%  [{zatca_category}]  {name}"
            )

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"Done. Created: {created_count}  |  Updated: {updated_count}  "
                f"|  Total default rates: {TaxRate.objects.filter(is_default=True, is_deleted=False).count()}"
            )
        )
