from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce

from purchases.models import Bill
from sales.models import CustomerCreditNote, Invoice
from main.models import MaintenanceAuditLog


class Command(BaseCommand):
    help = "Reconcile denormalized financial aggregates (paid_amount/refunded_amount) against allocation sums."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persist fixes. Without this flag, command runs in report-only mode.",
        )
        parser.add_argument(
            "--confirm",
            default="",
            help='Required when using --apply. Must be exactly: "APPLY_RECONCILIATION".',
        )
        parser.add_argument(
            "--reason",
            default="",
            help="Required when using --apply. Reason for audit trail.",
        )

    @staticmethod
    def _money(value) -> Decimal:
        return (value or Decimal("0")).quantize(Decimal("0.01"))

    def handle(self, *args, **options):
        apply_changes = bool(options.get("apply"))
        confirm = str(options.get("confirm") or "").strip()
        reason = str(options.get("reason") or "").strip()
        if apply_changes:
            if confirm != "APPLY_RECONCILIATION":
                raise SystemExit('Refused: --confirm must be "APPLY_RECONCILIATION" when using --apply.')
            if not reason:
                raise SystemExit("Refused: --reason is required when using --apply.")
        stats = {
            "invoice_checked": 0,
            "invoice_fixed": 0,
            "bill_checked": 0,
            "bill_fixed": 0,
            "credit_note_checked": 0,
            "credit_note_fixed": 0,
        }

        def reconcile_queryset(qs, relation_name: str, field_name: str, checked_key: str, fixed_key: str):
            for obj in qs.iterator():
                stats[checked_key] += 1
                relation = getattr(obj, relation_name)
                allocated = relation.filter(is_deleted=False).aggregate(
                    total=Coalesce(
                        Sum("amount"),
                        Value(Decimal("0.00")),
                        output_field=DecimalField(max_digits=18, decimal_places=2),
                    )
                )["total"]
                expected = self._money(allocated)
                current = self._money(getattr(obj, field_name))
                if current == expected:
                    continue
                stats[fixed_key] += 1
                self.stdout.write(
                    f"{obj.__class__.__name__} {obj.pk}: {field_name} drift {current} -> {expected}"
                )
                if apply_changes:
                    setattr(obj, field_name, expected)
                    obj.save(update_fields=[field_name, "updated_at"])

        with transaction.atomic():
            reconcile_queryset(
                Invoice.objects.filter(is_deleted=False).order_by("created_at"),
                "payment_allocations",
                "paid_amount",
                "invoice_checked",
                "invoice_fixed",
            )
            reconcile_queryset(
                Bill.objects.filter(is_deleted=False).order_by("created_at"),
                "payment_allocations",
                "paid_amount",
                "bill_checked",
                "bill_fixed",
            )
            reconcile_queryset(
                CustomerCreditNote.objects.filter(is_deleted=False).order_by("created_at"),
                "refund_allocations",
                "refunded_amount",
                "credit_note_checked",
                "credit_note_fixed",
            )

            if not apply_changes:
                # Keep command side-effect free by default.
                transaction.set_rollback(True)

        self.stdout.write(
            "Checked: invoices={invoice_checked}, bills={bill_checked}, credit_notes={credit_note_checked}".format(
                **stats
            )
        )
        self.stdout.write(
            "Drift rows: invoices={invoice_fixed}, bills={bill_fixed}, credit_notes={credit_note_fixed}".format(
                **stats
            )
        )

        if apply_changes:
            MaintenanceAuditLog.objects.create(
                action="reconcile_financial_aggregates",
                reason=reason,
                metadata=stats,
            )
            self.stdout.write(self.style.SUCCESS("Reconciliation applied."))
        else:
            self.stdout.write(self.style.WARNING("Dry run only. Use --apply to persist fixes."))
