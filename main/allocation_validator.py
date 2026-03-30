class AllocationValidator:
    """
    Single semantic gatekeeper for allocation safety checks.
    Keeps invariants centralized and reusable across sales/purchases flows.

    Call-site matrix (extend when adding payment/refund endpoints):
    - sales.views.CustomerPaymentListCreateAPI._replace_allocations
      → validate_customer_payment_invoice
    - sales.views.CustomerPaymentDetailAPI (via _replace_allocations)
      → validate_customer_payment_invoice
    - sales.views.CustomerRefundListCreateAPI._replace_allocations
      → validate_customer_refund_credit_note
    - purchases.supplier_payment_posting.apply_supplier_payment_allocations
      → validate_supplier_payment_bill
    - sales.customer_cash_posting (payment/refund create & update)
      → validate_customer_payment_invoice / validate_customer_refund_credit_note (includes closed-period checks)
    - accounting.journal_views.JournalEntryPostAPI
      → validate_manual_journal_post_preconditions
    - products.views.InventoryAdjustmentPostAPI / products.inventory_posting
      → validate_inventory_adjustment_postable

    Manual journal entries: balanced posting is enforced in JournalEntry.post();
    these guards centralize period/status preconditions for API paths.
    """

    @staticmethod
    def assert_period_open_for_date(d, *, label: str = "Posting") -> None:
        from accounting.models import AccountingPeriod

        if AccountingPeriod.is_date_closed(d):
            raise ValueError(f"{label} not allowed: {d} is in a closed accounting period.")

    @classmethod
    def validate_manual_journal_post_preconditions(cls, entry) -> None:
        """Draft manual JE: open period + status (post() re-validates balance/archived accounts)."""
        if getattr(entry, "status", None) != "draft":
            raise ValueError("Only draft journal entries can be posted.")
        cls.assert_period_open_for_date(entry.date, label="Journal entry posting")

    @classmethod
    def validate_inventory_adjustment_postable(cls, adj, lines: list) -> None:
        """Raises ValueError if an inventory adjustment cannot be posted."""
        cls.assert_period_open_for_date(adj.date, label="Inventory adjustment posting")
        if not lines:
            raise ValueError("Add at least one line before posting.")
        from decimal import Decimal

        for line in lines:
            if line.quantity_delta == 0:
                raise ValueError("Qty +/- cannot be 0.")
            if line.inventory_value_delta == 0:
                raise ValueError("Inventory value +/- cannot be 0.")
            if not line.product.inventory_account_id:
                raise ValueError(
                    f"Item '{line.product.name}' must have an Inventory Asset Account to post adjustments."
                )

    @staticmethod
    def _validate_currency_match(document, payment_or_refund, *, doc_label: str) -> None:
        doc_currency = getattr(document, "currency", "") or ""
        pay_currency = getattr(payment_or_refund, "currency", "") or ""
        if doc_currency and pay_currency and str(doc_currency) != str(pay_currency):
            raise ValueError(f"{doc_label} currency must match payment/refund currency.")

    @classmethod
    def validate_customer_payment_invoice(cls, invoice, payment) -> None:
        if invoice.status != "posted":
            raise ValueError(f"Invoice {invoice.invoice_number} must be posted before payment.")
        if invoice.customer_id != payment.customer_id:
            raise ValueError("Invoice customer must match payment customer.")
        cls._validate_currency_match(invoice, payment, doc_label="Invoice")
        cls.assert_period_open_for_date(invoice.date, label="Payment allocation")
        cls.assert_period_open_for_date(payment.payment_date, label="Payment allocation")

    @classmethod
    def validate_customer_refund_credit_note(cls, credit_note, refund) -> None:
        if credit_note.status != "posted":
            raise ValueError(f"Credit note {credit_note.credit_note_number} must be posted before refund.")
        if credit_note.customer_id != refund.customer_id:
            raise ValueError("Credit note customer must match refund customer.")
        cls._validate_currency_match(credit_note, refund, doc_label="Credit note")
        cls.assert_period_open_for_date(credit_note.date, label="Refund allocation")
        cls.assert_period_open_for_date(refund.refund_date, label="Refund allocation")

    @classmethod
    def validate_supplier_payment_bill(cls, bill, payment) -> None:
        if bill.status != "posted":
            raise ValueError(f"Bill {bill.bill_number} must be posted before payment.")
        if bill.supplier_id != payment.supplier_id:
            raise ValueError("Bill supplier must match payment supplier.")
        cls._validate_currency_match(bill, payment, doc_label="Bill")
        cls.assert_period_open_for_date(bill.bill_date, label="Supplier payment allocation")
        cls.assert_period_open_for_date(payment.payment_date, label="Supplier payment allocation")

