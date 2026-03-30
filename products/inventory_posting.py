from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from accounting.accounting_engine import AccountingEngine
from accounting.models import JournalEntry, JournalEntryLine
from main.allocation_validator import AllocationValidator

from .models import InventoryAdjustment


class InventoryAdjustmentPostAbort(ValueError):
    __slots__ = ("code", "http_status")

    def __init__(self, code: str, message: str, http_status: int):
        self.code = code
        self.http_status = http_status
        super().__init__(message)


def execute_inventory_adjustment_post(*, adjustment_id, user) -> InventoryAdjustment:
    """
    Post a draft inventory adjustment (JE + stock updates). Idempotent if already posted.
    Raises InventoryAdjustmentPostAbort for API-mappable failures.
    """
    try:
        adj = InventoryAdjustment.objects.select_related("warehouse").get(pk=adjustment_id, is_deleted=False)
    except InventoryAdjustment.DoesNotExist:
        raise InventoryAdjustmentPostAbort("NOT_FOUND", "Inventory adjustment not found.", 404) from None

    if adj.status == "posted":
        return adj

    lines = list(
        adj.lines.filter(is_deleted=False).select_related("product", "account", "product__inventory_account")
    )
    try:
        AllocationValidator.validate_inventory_adjustment_postable(adj, lines)
    except ValueError as exc:
        msg = str(exc)
        if "closed accounting period" in msg:
            raise InventoryAdjustmentPostAbort("PERIOD_CLOSED", msg, 422) from exc
        if "Add at least one line" in msg:
            raise InventoryAdjustmentPostAbort("NO_LINES", msg, 400) from exc
        if "Qty +/-" in msg:
            raise InventoryAdjustmentPostAbort("QTY_REQUIRED", msg, 400) from exc
        if "Inventory value +/-" in msg:
            raise InventoryAdjustmentPostAbort("VALUE_REQUIRED", msg, 400) from exc
        if "Inventory Asset Account" in msg:
            raise InventoryAdjustmentPostAbort("MISSING_INVENTORY_ACCOUNT", msg, 422) from exc
        raise

    with transaction.atomic():
        adj = InventoryAdjustment.objects.select_for_update().select_related("warehouse").get(
            pk=adj.pk, is_deleted=False
        )
        if adj.status == "posted":
            return adj

        je = JournalEntry.objects.create(
            date=adj.date,
            description=f"Inventory adjustment {adj.reference or str(adj.pk)[:8]}",
            status="draft",
            creator=user,
        )

        order = 0
        for line in lines:
            amount = Decimal(line.inventory_value_delta)
            inv_account = line.product.inventory_account
            offset_account = line.account

            if amount > 0:
                JournalEntryLine.objects.create(
                    journal_entry=je,
                    account=inv_account,
                    description=f"Inventory increase - {line.product.name}",
                    debit=amount,
                    credit=Decimal("0"),
                    line_order=order,
                    creator=user,
                )
                order += 1
                JournalEntryLine.objects.create(
                    journal_entry=je,
                    account=offset_account,
                    description=f"Offset - {line.product.name}",
                    debit=Decimal("0"),
                    credit=amount,
                    line_order=order,
                    creator=user,
                )
                order += 1
            else:
                amount_abs = abs(amount)
                JournalEntryLine.objects.create(
                    journal_entry=je,
                    account=offset_account,
                    description=f"Offset - {line.product.name}",
                    debit=amount_abs,
                    credit=Decimal("0"),
                    line_order=order,
                    creator=user,
                )
                order += 1
                JournalEntryLine.objects.create(
                    journal_entry=je,
                    account=inv_account,
                    description=f"Inventory decrease - {line.product.name}",
                    debit=Decimal("0"),
                    credit=amount_abs,
                    line_order=order,
                    creator=user,
                )
                order += 1

            product = type(line.product).objects.select_for_update().get(pk=line.product.pk)
            old_qty = Decimal(product.stock_quantity or 0)
            old_cost = Decimal(product.avg_unit_cost or 0) or Decimal(product.purchase_price or 0)
            delta_qty = Decimal(line.quantity_delta)
            new_qty = old_qty + delta_qty

            if delta_qty > 0 and amount > 0 and new_qty > 0:
                new_cost = ((old_qty * old_cost) + amount) / new_qty
                product.avg_unit_cost = new_cost.quantize(Decimal("0.01"))

            product.stock_quantity = new_qty
            product.save(update_fields=["stock_quantity", "avg_unit_cost", "updated_at"])

        AccountingEngine.post_journal_entry(je)

        adj.adjustment_id = adj._next_adjustment_id()
        adj.status = "posted"
        adj.posted_at = timezone.now()
        adj.journal_entry = je
        adj.updator = user
        adj.save(update_fields=["adjustment_id", "status", "posted_at", "journal_entry", "updated_at", "updator_id"])

    return adj
