from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from accounting.services.posting import post_supplier_refund_journal

from .models import DebitNote, SupplierRefund, SupplierRefundAllocation


def sync_debit_note_refund_status(debit_note: DebitNote) -> None:
    """Recompute debit_note.refunded_amount and status from live allocations."""
    total = debit_note.refund_allocations.filter(is_deleted=False).aggregate(total=Sum("amount")).get("total")
    debit_note.refunded_amount = (total or Decimal("0")).quantize(Decimal("0.01"))
    refunded = debit_note.refunded_amount
    note_total = (debit_note.total_amount or Decimal("0")).quantize(Decimal("0.01"))
    if refunded >= note_total and note_total > 0:
        debit_note.status = "paid"
    elif refunded > 0:
        debit_note.status = "partially_paid"
    elif debit_note.status in ("paid", "partially_paid"):
        debit_note.status = "posted"
    debit_note.save(update_fields=["refunded_amount", "status", "updated_at"])


def apply_supplier_refund_allocations(refund: SupplierRefund, allocations, user) -> None:
    locked = SupplierRefund.objects.select_for_update().filter(pk=refund.pk, is_deleted=False).first()
    if not locked:
        raise ValueError("Supplier refund not found.")
    refund = locked
    total_applied = Decimal("0")
    rows = allocations or []

    affected_debit_note_ids = []
    for row in rows:
        debit_note_id = row.get("debit_note")
        amount = Decimal(str(row.get("amount", "0")))
        if amount <= 0:
            continue
        debit_note = DebitNote.objects.select_for_update().filter(pk=debit_note_id, is_deleted=False).first()
        if not debit_note:
            raise ValueError(f"Invalid debit note: {debit_note_id}")
        if debit_note.status not in ("posted", "partially_paid"):
            raise ValueError(f"Debit note {debit_note.debit_note_number} must be posted before receiving refund.")
        if debit_note.supplier_id != refund.supplier_id:
            raise ValueError("Debit note supplier must match refund supplier.")
        if amount > debit_note.balance_amount:
            raise ValueError(f"Applied amount exceeds debit note balance for {debit_note.debit_note_number}.")

        SupplierRefundAllocation.objects.create(
            refund=refund,
            debit_note=debit_note,
            amount=amount,
            creator=user,
        )
        total_applied += amount
        if debit_note_id not in affected_debit_note_ids:
            affected_debit_note_ids.append(debit_note_id)

    if total_applied > refund.amount_refunded:
        raise ValueError("Total applied amount cannot exceed amount_refunded.")

    for dn_id in affected_debit_note_ids:
        dn = DebitNote.objects.select_for_update().get(pk=dn_id)
        sync_debit_note_refund_status(dn)


def rollback_supplier_refund_allocations(refund: SupplierRefund) -> None:
    affected_dn_ids = list(
        refund.allocations.filter(is_deleted=False).values_list("debit_note_id", flat=True)
    )
    refund.allocations.filter(is_deleted=False).update(is_deleted=True)
    for dn_id in affected_dn_ids:
        dn = DebitNote.objects.select_for_update().get(pk=dn_id)
        sync_debit_note_refund_status(dn)


def create_supplier_refund_from_payload(*, payload: dict, user) -> SupplierRefund:
    from .serializers import SupplierRefundSerializer

    raw = dict(payload or {})
    allocations = raw.pop("allocations", [])
    if not isinstance(allocations, list):
        allocations = []

    serializer = SupplierRefundSerializer(data=raw)
    serializer.is_valid(raise_exception=True)

    with transaction.atomic():
        refund = serializer.save(creator=user)
        apply_supplier_refund_allocations(refund, allocations, user)
        je = post_supplier_refund_journal(refund=refund, user=user)
        refund.journal_entry = je
        refund.save(update_fields=["journal_entry", "updated_at"])
        refund.refresh_from_db()
    return refund
