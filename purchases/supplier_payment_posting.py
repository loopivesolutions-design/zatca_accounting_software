from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from accounting.services.posting import post_supplier_payment_journal
from main.allocation_validator import AllocationValidator

from .models import Bill, SupplierPayment, SupplierPaymentAllocation
from .serializers import SupplierPaymentSerializer


def sync_bill_payment_status(bill: Bill) -> None:
    """Recompute bill.paid_amount from live allocations and update status accordingly."""
    total = bill.payment_allocations.filter(is_deleted=False).aggregate(total=Sum("amount")).get("total")
    paid = (total or Decimal("0")).quantize(Decimal("0.01"))
    bill.paid_amount = paid

    if bill.status == "posted":
        total_amount = (bill.total_amount or Decimal("0")).quantize(Decimal("0.01"))
        if paid >= total_amount and total_amount > 0:
            bill.status = "paid"
        elif paid > 0:
            bill.status = "partially_paid"
        else:
            bill.status = "posted"

    bill.save(update_fields=["paid_amount", "status", "updated_at"])


def apply_supplier_payment_allocations(payment, allocations, user):
    locked = SupplierPayment.objects.select_for_update().filter(pk=payment.pk, is_deleted=False).first()
    if not locked:
        raise ValueError("Supplier payment not found.")
    payment = locked
    total_applied = Decimal("0")
    rows = allocations or []
    if payment.payment_type == "advance_payment":
        rows = []

    affected_bills = []
    for row in rows:
        bill_id = row.get("bill")
        amount = Decimal(str(row.get("amount", "0")))
        if amount <= 0:
            continue
        bill = Bill.objects.select_for_update().filter(pk=bill_id, is_deleted=False).first()
        if not bill:
            raise ValueError(f"Invalid bill: {bill_id}")
        applied_total = bill.payment_allocations.filter(is_deleted=False).aggregate(total=Sum("amount")).get("total")
        applied_total = (applied_total or Decimal("0")).quantize(Decimal("0.01"))
        AllocationValidator.validate_supplier_payment_bill(bill, payment)
        if amount > bill.balance_amount:
            raise ValueError(f"Applied amount exceeds current bill balance for {bill.bill_number}.")

        SupplierPaymentAllocation.objects.create(
            payment=payment,
            bill=bill,
            amount=amount,
            creator=user,
        )
        total_applied += amount
        affected_bills.append(bill)

    for bill in affected_bills:
        sync_bill_payment_status(bill)

    if total_applied > payment.amount_paid:
        raise ValueError("Total applied amount cannot exceed amount_paid.")


def create_supplier_payment_from_payload(*, payload: dict, user):
    """
    Create a supplier payment, allocations, and posting journal (same as POST supplier-payments).
    Raises ValueError on business errors; ValidationError from DRF if serializer invalid.
    """
    raw = dict(payload or {})
    allocations = raw.get("allocations")
    if not isinstance(allocations, list):
        allocations = []
    payment_data = {k: v for k, v in raw.items() if k != "allocations"}

    serializer = SupplierPaymentSerializer(data=payment_data)
    serializer.is_valid(raise_exception=True)

    with transaction.atomic():
        payment = serializer.save(creator=user)
        apply_supplier_payment_allocations(payment, allocations, user)
        je = post_supplier_payment_journal(payment=payment, user=user)
        payment.journal_entry = je
        payment.save(update_fields=["journal_entry", "updated_at"])
        payment.refresh_from_db()
    return payment
