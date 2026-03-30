"""
Saudi VAT / document totals — Decimal-only; delegates to model `recalculate_totals` where defined.
"""

from __future__ import annotations

from decimal import Decimal


class TaxService:
    @staticmethod
    def recalculate_totals(document) -> None:
        """Recalculate subtotal, VAT, and total on a document with lines (invoice, credit note, bill, etc.)."""
        if hasattr(document, "recalculate_totals"):
            document.recalculate_totals()
            return
        raise TypeError(f"{type(document).__name__} does not support VAT/total recalculation.")

    @staticmethod
    def money(value) -> Decimal:
        from main.money import money as _money

        return _money(value)
