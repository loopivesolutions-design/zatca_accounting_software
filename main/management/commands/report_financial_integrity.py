from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import F, Q, Sum, Value, DecimalField
from django.db.models.functions import Coalesce

from purchases.models import Bill, SupplierPayment
from sales.models import Invoice, CustomerCreditNote, CustomerPayment, CustomerRefund


class Command(BaseCommand):
    help = "Report core financial integrity checks (posted-without-JE, over-allocation, over-application)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fail-on-error",
            action="store_true",
            help="Exit with non-zero code if any integrity violation is found.",
        )

    def handle(self, *args, **options):
        checks = {
            "posted_invoices_without_je": Invoice.objects.filter(
                is_deleted=False, status="posted", journal_entry__isnull=True
            ).count(),
            "posted_credit_notes_without_je": CustomerCreditNote.objects.filter(
                is_deleted=False, status="posted", journal_entry__isnull=True
            ).count(),
            "posted_bills_without_je": Bill.objects.filter(
                is_deleted=False, status="posted", journal_entry__isnull=True
            ).count(),
            "customer_payments_over_allocated": CustomerPayment.objects.filter(is_deleted=False).annotate(
                allocated=Coalesce(
                    Sum("allocations__amount", filter=Q(allocations__is_deleted=False)),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=18, decimal_places=2),
                )
            ).filter(allocated__gt=F("amount_received")).count(),
            "supplier_payments_over_allocated": SupplierPayment.objects.filter(is_deleted=False).annotate(
                allocated=Coalesce(
                    Sum("allocations__amount", filter=Q(allocations__is_deleted=False)),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=18, decimal_places=2),
                )
            ).filter(allocated__gt=F("amount_paid")).count(),
            "customer_refunds_over_allocated": CustomerRefund.objects.filter(is_deleted=False).annotate(
                allocated=Coalesce(
                    Sum("allocations__amount", filter=Q(allocations__is_deleted=False)),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=18, decimal_places=2),
                )
            ).filter(allocated__gt=F("amount_refunded")).count(),
            "invoices_over_applied": Invoice.objects.filter(
                is_deleted=False, paid_amount__gt=F("total_amount")
            ).count(),
            "bills_over_applied": Bill.objects.filter(
                is_deleted=False, paid_amount__gt=F("total_amount")
            ).count(),
            "credit_notes_over_refunded": CustomerCreditNote.objects.filter(
                is_deleted=False, refunded_amount__gt=F("total_amount")
            ).count(),
            "invoice_paid_amount_drift": Invoice.objects.filter(is_deleted=False).annotate(
                applied=Coalesce(
                    Sum("payment_allocations__amount", filter=Q(payment_allocations__is_deleted=False)),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=18, decimal_places=2),
                )
            ).exclude(paid_amount=F("applied")).count(),
            "bill_paid_amount_drift": Bill.objects.filter(is_deleted=False).annotate(
                applied=Coalesce(
                    Sum("payment_allocations__amount", filter=Q(payment_allocations__is_deleted=False)),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=18, decimal_places=2),
                )
            ).exclude(paid_amount=F("applied")).count(),
            "credit_note_refunded_amount_drift": CustomerCreditNote.objects.filter(is_deleted=False).annotate(
                applied=Coalesce(
                    Sum("refund_allocations__amount", filter=Q(refund_allocations__is_deleted=False)),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=18, decimal_places=2),
                )
            ).exclude(refunded_amount=F("applied")).count(),
        }

        total_violations = sum(checks.values())
        for key, value in checks.items():
            self.stdout.write(f"{key}: {value}")

        if total_violations == 0:
            self.stdout.write(self.style.SUCCESS("Integrity check passed: no violations found."))
            return

        self.stdout.write(self.style.WARNING(f"Integrity violations found: {total_violations}"))
        if options.get("fail_on_error"):
            raise SystemExit(1)
