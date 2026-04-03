"""
Shared customer receipt / refund posting (allocations + journal) for API and approval executor.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from accounting.services.posting import post_customer_payment_journal, post_customer_refund_journal
from main.allocation_validator import AllocationValidator

from .models import (
    CustomerCreditNote,
    CustomerPayment,
    CustomerPaymentAllocation,
    CustomerRefund,
    CustomerRefundAllocation,
    Invoice,
)


def _invoice_applied_sum_locked(invoice: Invoice) -> Decimal:
    total = invoice.payment_allocations.filter(is_deleted=False).aggregate(total=Sum("amount")).get("total")
    return (total or Decimal("0")).quantize(Decimal("0.01"))


def _credit_note_refunded_sum_locked(note: CustomerCreditNote) -> Decimal:
    total = note.refund_allocations.filter(is_deleted=False).aggregate(total=Sum("amount")).get("total")
    return (total or Decimal("0")).quantize(Decimal("0.01"))


_ACTIVE_INVOICE_STATUSES = {"confirmed", "posted", "reported"}


def _sync_invoice_payment_status(invoice: Invoice) -> None:
    """Update invoice status based on its current paid_amount vs total_amount."""
    paid = (invoice.paid_amount or Decimal("0")).quantize(Decimal("0.01"))
    total = (invoice.total_amount or Decimal("0")).quantize(Decimal("0.01"))
    if paid >= total and total > 0:
        invoice.status = "paid"
    elif paid > 0:
        invoice.status = "partially_paid"
    elif invoice.status in ("paid", "partially_paid"):
        # Rolled back to zero — restore to the nearest active status
        invoice.status = "reported" if invoice.journal_entry_id else "confirmed"
    invoice.save(update_fields=["paid_amount", "status", "updated_at"])


def rollback_customer_payment_allocations(payment: CustomerPayment) -> None:
    for allocation in payment.allocations.filter(is_deleted=False).select_related("invoice"):
        invoice = Invoice.objects.select_for_update().get(pk=allocation.invoice_id)
        if (invoice.paid_amount or Decimal("0")).quantize(Decimal("0.01")) != _invoice_applied_sum_locked(invoice):
            raise ValueError(
                f"DRIFT_DETECTED: invoice {invoice.invoice_number} paid_amount is out of sync with allocations."
            )
        invoice.paid_amount = (invoice.paid_amount or Decimal("0")) - allocation.amount
        if invoice.paid_amount < 0:
            invoice.paid_amount = Decimal("0")
        _sync_invoice_payment_status(invoice)
    payment.allocations.filter(is_deleted=False).update(is_deleted=True)


def apply_customer_payment_allocations(payment: CustomerPayment, allocations, user) -> None:
    locked = CustomerPayment.objects.select_for_update().filter(pk=payment.pk, is_deleted=False).first()
    if not locked:
        raise ValueError("Customer payment not found.")
    payment = locked
    total_applied = Decimal("0")
    rows = allocations or []
    if payment.payment_type == "advance_payment":
        rows = []

    for row in rows:
        invoice_id = row.get("invoice")
        amount = Decimal(str(row.get("amount", "0")))
        if amount <= 0:
            continue
        invoice = Invoice.objects.select_for_update().filter(pk=invoice_id, is_deleted=False).first()
        if not invoice:
            raise ValueError(f"Invalid invoice: {invoice_id}")
        if (invoice.paid_amount or Decimal("0")).quantize(Decimal("0.01")) != _invoice_applied_sum_locked(invoice):
            raise ValueError(
                f"DRIFT_DETECTED: invoice {invoice.invoice_number} paid_amount is out of sync with allocations."
            )
        AllocationValidator.validate_customer_payment_invoice(invoice, payment)
        if amount > invoice.balance_amount:
            raise ValueError(f"Applied amount exceeds current invoice balance for {invoice.invoice_number}.")

        CustomerPaymentAllocation.objects.create(
            payment=payment,
            invoice=invoice,
            amount=amount,
            creator=user,
        )
        invoice.paid_amount = (invoice.paid_amount or Decimal("0")) + amount
        _sync_invoice_payment_status(invoice)
        total_applied += amount

    if total_applied > payment.amount_received:
        raise ValueError("Total allocations cannot exceed amount received.")


def create_customer_payment_from_payload(*, payload: dict, user) -> CustomerPayment:
    from .serializers import CustomerPaymentSerializer

    raw = dict(payload or {})
    allocations = raw.get("allocations")
    if not isinstance(allocations, list):
        allocations = []
    payment_data = {k: v for k, v in raw.items() if k != "allocations"}

    serializer = CustomerPaymentSerializer(data=payment_data)
    serializer.is_valid(raise_exception=True)

    with transaction.atomic():
        payment = serializer.save(creator=user)
        apply_customer_payment_allocations(payment, allocations, user)
        je = post_customer_payment_journal(payment=payment, user=user)
        payment.journal_entry = je
        payment.save(update_fields=["journal_entry", "updated_at"])
        payment.refresh_from_db()
    return payment


def update_customer_payment_from_payload(*, payment_id, payload: dict, user) -> CustomerPayment:
    from .serializers import CustomerPaymentSerializer

    raw = dict(payload or {})
    allocations = raw.pop("allocations", None)

    with transaction.atomic():
        payment = CustomerPayment.objects.select_for_update().filter(pk=payment_id, is_deleted=False).first()
        if not payment:
            raise LookupError("Customer payment not found.")
        if payment.is_posted:
            raise ValueError("Posted payment cannot be edited.")

        rollback_customer_payment_allocations(payment)
        serializer = CustomerPaymentSerializer(payment, data=raw, partial=True)
        serializer.is_valid(raise_exception=True)
        payment = serializer.save(updator=user)
        if allocations is not None:
            apply_customer_payment_allocations(payment, allocations, user)
        payment.refresh_from_db()
    return payment


def rollback_customer_refund_allocations(refund: CustomerRefund) -> None:
    for allocation in refund.allocations.filter(is_deleted=False).select_related("credit_note"):
        credit_note = CustomerCreditNote.objects.select_for_update().get(pk=allocation.credit_note_id)
        if (credit_note.refunded_amount or Decimal("0")).quantize(Decimal("0.01")) != _credit_note_refunded_sum_locked(
            credit_note
        ):
            raise ValueError(
                f"DRIFT_DETECTED: credit note {credit_note.credit_note_number} refunded_amount is out of sync with allocations."
            )
        credit_note.refunded_amount = (credit_note.refunded_amount or Decimal("0")) - allocation.amount
        if credit_note.refunded_amount < 0:
            credit_note.refunded_amount = Decimal("0")
        credit_note.save(update_fields=["refunded_amount", "updated_at"])
    refund.allocations.filter(is_deleted=False).update(is_deleted=True)


def apply_customer_refund_allocations(refund: CustomerRefund, allocations, user) -> None:
    locked = CustomerRefund.objects.select_for_update().filter(pk=refund.pk, is_deleted=False).first()
    if not locked:
        raise ValueError("Customer refund not found.")
    refund = locked
    total_applied = Decimal("0")
    for row in allocations or []:
        credit_note_id = row.get("credit_note")
        amount = Decimal(str(row.get("amount", "0")))
        if amount <= 0:
            continue
        credit_note = CustomerCreditNote.objects.select_for_update().filter(pk=credit_note_id, is_deleted=False).first()
        if not credit_note:
            raise ValueError(f"Invalid credit note: {credit_note_id}")
        if (credit_note.refunded_amount or Decimal("0")).quantize(Decimal("0.01")) != _credit_note_refunded_sum_locked(
            credit_note
        ):
            raise ValueError(
                f"DRIFT_DETECTED: credit note {credit_note.credit_note_number} refunded_amount is out of sync with allocations."
            )
        AllocationValidator.validate_customer_refund_credit_note(credit_note, refund)
        if amount > credit_note.balance_amount:
            raise ValueError(
                f"Applied amount exceeds current credit note balance for {credit_note.credit_note_number}."
            )

        CustomerRefundAllocation.objects.create(
            refund=refund,
            credit_note=credit_note,
            amount=amount,
            creator=user,
        )
        credit_note.refunded_amount = (credit_note.refunded_amount or Decimal("0")) + amount
        credit_note.save(update_fields=["refunded_amount", "updated_at"])
        total_applied += amount

    if total_applied > refund.amount_refunded:
        raise ValueError("Total allocations cannot exceed amount refunded.")


def create_customer_refund_from_payload(*, payload: dict, user) -> CustomerRefund:
    from .serializers import CustomerRefundSerializer

    raw = dict(payload or {})
    allocations = raw.get("allocations")
    if not isinstance(allocations, list):
        allocations = []
    refund_data = {k: v for k, v in raw.items() if k != "allocations"}

    serializer = CustomerRefundSerializer(data=refund_data)
    serializer.is_valid(raise_exception=True)

    with transaction.atomic():
        refund = serializer.save(creator=user)
        apply_customer_refund_allocations(refund, allocations, user)
        je = post_customer_refund_journal(refund=refund, user=user)
        refund.journal_entry = je
        refund.save(update_fields=["journal_entry", "updated_at"])
        refund.refresh_from_db()
    return refund


def update_customer_refund_from_payload(*, refund_id, payload: dict, user) -> CustomerRefund:
    from .serializers import CustomerRefundSerializer

    raw = dict(payload or {})
    allocations = raw.pop("allocations", None)

    with transaction.atomic():
        refund = CustomerRefund.objects.select_for_update().filter(pk=refund_id, is_deleted=False).first()
        if not refund:
            raise LookupError("Customer refund not found.")
        if refund.is_posted:
            raise ValueError("Posted refund cannot be edited.")

        rollback_customer_refund_allocations(refund)
        serializer = CustomerRefundSerializer(refund, data=raw, partial=True)
        serializer.is_valid(raise_exception=True)
        refund = serializer.save(updator=user)
        if allocations is not None:
            apply_customer_refund_allocations(refund, allocations, user)
        refund.refresh_from_db()
    return refund
