"""
Global double-entry sanity: posted journal lines must satisfy Σ debits = Σ credits.
"""

from __future__ import annotations

from decimal import Decimal

from django.db.models import Sum


def global_posted_debits_credits() -> tuple[Decimal, Decimal]:
    from accounting.models import JournalEntryLine

    agg = JournalEntryLine.objects.filter(
        is_deleted=False,
        journal_entry__is_deleted=False,
        journal_entry__status="posted",
    ).aggregate(
        total_debit=Sum("debit"),
        total_credit=Sum("credit"),
    )
    td = agg["total_debit"] or Decimal("0")
    tc = agg["total_credit"] or Decimal("0")
    return td, tc


def verify_global_double_entry_balance() -> tuple[bool, str]:
    td, tc = global_posted_debits_credits()
    if td != tc:
        return False, f"Posted lines imbalance: total debit {td} != total credit {tc} (diff {td - tc})."
    return True, f"OK: posted total debit {td} == total credit {tc}."
