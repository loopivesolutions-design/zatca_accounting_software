"""
Registry of approval scopes that have a real executor in execute_approved_action.

Policy rows with requires_approval=True must appear here, or Django check main.E002 fails
when STRICT_APPROVAL_POLICY_INTEGRITY is on (default: production / DEBUG=False).
"""

from __future__ import annotations

from typing import FrozenSet

# Keep in sync with branches in main.approvals.execute_approved_action
APPROVAL_SCOPES_WITH_EXECUTORS: FrozenSet[str] = frozenset(
    {
        "sales.invoice.post",
        "sales.credit_note.post",
        "accounting.period.reopen",
        "accounting.journal_entry.reverse",
        "purchases.bill.post",
        "sales.invoice.zatca.submit",
        "sales.credit_note.zatca.submit",
        "accounting.journal_entry.post",
        "purchases.supplier_payment.create",
        "products.inventory_adjustment.post",
        "sales.customer_payment.create",
        "sales.customer_payment.update",
        "sales.customer_refund.create",
        "sales.customer_refund.update",
    }
)


def is_scope_fully_wired(scope: str) -> bool:
    return scope in APPROVAL_SCOPES_WITH_EXECUTORS
