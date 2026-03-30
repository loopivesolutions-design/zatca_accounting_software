"""
Central posting facade. Financial modules should call these helpers instead of duplicating journal logic.
"""

from __future__ import annotations

from accounting.services import posting as posting_services


class AccountingEngine:
    """Thin wrapper over posting services for a single extension point (`AccountingEngine.post_*`)."""

    @staticmethod
    def post_journal_entry(entry) -> None:
        """Post a draft journal entry (respects JournalEntry.post gate)."""
        from accounting.journal_post_gate import permit_journal_post

        with permit_journal_post():
            entry.post()

    post_bill_journal = staticmethod(posting_services.post_bill_journal)
    post_invoice_journal = staticmethod(posting_services.post_invoice_journal)
    post_credit_note_journal = staticmethod(posting_services.post_credit_note_journal)
    post_supplier_payment_journal = staticmethod(posting_services.post_supplier_payment_journal)
    post_customer_payment_journal = staticmethod(posting_services.post_customer_payment_journal)
    post_customer_refund_journal = staticmethod(posting_services.post_customer_refund_journal)
