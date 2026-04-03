from decimal import Decimal

from accounting.journal_post_gate import permit_journal_post
from accounting.models import Account, JournalEntry, JournalEntryLine, AccountingPeriod
from accounting.system_accounts import get_system_account


def _post_journal(entry) -> None:
    with permit_journal_post():
        entry.post()


def _assert_open_period(date_value):
    if AccountingPeriod.is_date_closed(date_value):
        raise ValueError(f"Posting date {date_value} is in a closed accounting period.")


def post_bill_journal(*, bill, user, payable_account_id=None, vat_account_id=None, posting_date=None, memo=""):
    _assert_open_period(posting_date or bill.bill_date)
    if payable_account_id:
        payable_account = Account.objects.filter(pk=payable_account_id, is_deleted=False).first()
        if not payable_account:
            raise ValueError("Invalid payable_account provided.")
    else:
        payable_account = get_system_account("ACCOUNTS_PAYABLE", fallback_code="211")

    if vat_account_id:
        vat_account = Account.objects.filter(pk=vat_account_id, is_deleted=False).first()
        if not vat_account:
            raise ValueError("Invalid vat_account provided.")
    else:
        vat_account = get_system_account("VAT_INPUT", fallback_code="116")

    je = JournalEntry.objects.create(
        date=posting_date or bill.bill_date,
        description=memo or f"Purchase Bill {bill.bill_number}",
        status="draft",
        creator=user,
    )

    order = 0
    tax_total = Decimal("0")
    for line in bill.lines.filter(is_deleted=False).select_related("account", "tax_rate"):
        base_amount = line.subtotal()
        if base_amount > 0:
            JournalEntryLine.objects.create(
                journal_entry=je,
                account=line.account,
                description=f"Bill {bill.bill_number} - {line.description}",
                debit=base_amount,
                credit=Decimal("0"),
                line_order=order,
                creator=user,
            )
            order += 1
        tax_total += line.tax_amount()

    if tax_total > 0:
        if not vat_account:
            raise ValueError("VAT Receivable account (code 116) required for taxable bill lines.")
        JournalEntryLine.objects.create(
            journal_entry=je,
            account=vat_account,
            description=f"VAT on Bill {bill.bill_number}",
            debit=tax_total,
            credit=Decimal("0"),
            line_order=order,
            creator=user,
        )
        order += 1

    JournalEntryLine.objects.create(
        journal_entry=je,
        account=payable_account,
        description=f"Accounts Payable - Bill {bill.bill_number}",
        debit=Decimal("0"),
        credit=bill.total_amount,
        line_order=order,
        creator=user,
    )
    _post_journal(je)
    return je


def post_debit_note_journal(*, debit_note, user, posting_date=None, memo=""):
    """Post a debit note (purchase return): DR Accounts Payable / CR Expense & VAT Input."""
    _assert_open_period(posting_date or debit_note.date)
    ap_account = get_system_account("ACCOUNTS_PAYABLE", fallback_code="211")
    vat_account = get_system_account("VAT_INPUT", fallback_code="116")

    je = JournalEntry.objects.create(
        date=posting_date or debit_note.date,
        description=memo or f"Debit Note {debit_note.debit_note_number}",
        status="draft",
        creator=user,
    )

    order = 0
    tax_total = Decimal("0")

    for line in debit_note.lines.filter(is_deleted=False).select_related("account", "tax_rate"):
        base_amount = line.subtotal()
        if base_amount > 0:
            JournalEntryLine.objects.create(
                journal_entry=je,
                account=line.account,
                description=f"Debit Note {debit_note.debit_note_number} - {line.description}",
                debit=Decimal("0"),
                credit=base_amount,
                line_order=order,
                creator=user,
            )
            order += 1
        tax_total += line.tax_amount()

    if tax_total > 0:
        if not vat_account:
            raise ValueError("VAT Input account (code 116) required for taxable debit note lines.")
        JournalEntryLine.objects.create(
            journal_entry=je,
            account=vat_account,
            description=f"VAT on Debit Note {debit_note.debit_note_number}",
            debit=Decimal("0"),
            credit=tax_total,
            line_order=order,
            creator=user,
        )
        order += 1

    JournalEntryLine.objects.create(
        journal_entry=je,
        account=ap_account,
        description=f"Accounts Payable - Debit Note {debit_note.debit_note_number}",
        debit=debit_note.total_amount,
        credit=Decimal("0"),
        line_order=order,
        creator=user,
    )
    _post_journal(je)
    return je


def post_invoice_journal(*, invoice, user):
    from sales.zatca_chain_health import assert_zatca_hash_chain_allows_new_sales_posting

    assert_zatca_hash_chain_allows_new_sales_posting()
    _assert_open_period(invoice.date)
    ar_account = get_system_account("ACCOUNTS_RECEIVABLE", fallback_code="112")
    vat_account = get_system_account("VAT_OUTPUT", fallback_code="2110")

    je = JournalEntry.objects.create(
        date=invoice.date,
        description=f"Sales Invoice {invoice.invoice_number}",
        status="draft",
        creator=user,
    )
    order = 0
    tax_total = Decimal("0")
    for line in invoice.lines.filter(is_deleted=False).select_related("account", "tax_rate"):
        base_amount = line.subtotal()
        if base_amount > 0:
            JournalEntryLine.objects.create(
                journal_entry=je,
                account=line.account,
                description=f"Invoice {invoice.invoice_number} - {line.description}",
                debit=Decimal("0"),
                credit=base_amount,
                line_order=order,
                creator=user,
            )
            order += 1
        tax_total += line.tax_amount()

    if tax_total > 0:
        if not vat_account:
            raise ValueError("VAT Payable account (code 2110) required for taxable invoice lines.")
        JournalEntryLine.objects.create(
            journal_entry=je,
            account=vat_account,
            description=f"VAT on Invoice {invoice.invoice_number}",
            debit=Decimal("0"),
            credit=tax_total,
            line_order=order,
            creator=user,
        )
        order += 1

    JournalEntryLine.objects.create(
        journal_entry=je,
        account=ar_account,
        description=f"Accounts Receivable - Invoice {invoice.invoice_number}",
        debit=invoice.total_amount,
        credit=Decimal("0"),
        line_order=order,
        creator=user,
    )
    _post_journal(je)
    return je


def post_credit_note_journal(*, note, user):
    from sales.zatca_chain_health import assert_zatca_hash_chain_allows_new_sales_posting

    assert_zatca_hash_chain_allows_new_sales_posting()
    _assert_open_period(note.date)
    ar_account = get_system_account("ACCOUNTS_RECEIVABLE", fallback_code="112")
    vat_account = get_system_account("VAT_OUTPUT", fallback_code="2110")

    je = JournalEntry.objects.create(
        date=note.date,
        description=f"Customer Credit Note {note.credit_note_number}",
        status="draft",
        creator=user,
    )
    order = 0
    tax_total = Decimal("0")
    for line in note.lines.filter(is_deleted=False).select_related("account", "tax_rate"):
        base_amount = line.subtotal()
        if base_amount > 0:
            JournalEntryLine.objects.create(
                journal_entry=je,
                account=line.account,
                description=f"Credit Note {note.credit_note_number} - {line.description}",
                debit=base_amount,
                credit=Decimal("0"),
                line_order=order,
                creator=user,
            )
            order += 1
        tax_total += line.tax_amount()

    if tax_total > 0:
        if not vat_account:
            raise ValueError("VAT Payable account (code 2110) required for taxable credit-note lines.")
        JournalEntryLine.objects.create(
            journal_entry=je,
            account=vat_account,
            description=f"VAT reversal on Credit Note {note.credit_note_number}",
            debit=tax_total,
            credit=Decimal("0"),
            line_order=order,
            creator=user,
        )
        order += 1

    JournalEntryLine.objects.create(
        journal_entry=je,
        account=ar_account,
        description=f"Accounts Receivable reversal - Credit Note {note.credit_note_number}",
        debit=Decimal("0"),
        credit=note.total_amount,
        line_order=order,
        creator=user,
    )
    _post_journal(je)
    return je


def post_supplier_payment_journal(*, payment, user):
    _assert_open_period(payment.payment_date)
    ap_account = get_system_account("ACCOUNTS_PAYABLE", fallback_code="211")
    je = JournalEntry.objects.create(
        date=payment.payment_date,
        description=f"Supplier Payment {payment.payment_number}",
        status="draft",
        creator=user,
    )
    JournalEntryLine.objects.create(
        journal_entry=je,
        account=ap_account,
        description=f"Accounts Payable settlement {payment.payment_number}",
        debit=payment.amount_paid,
        credit=Decimal("0"),
        line_order=0,
        creator=user,
    )
    JournalEntryLine.objects.create(
        journal_entry=je,
        account=payment.paid_through,
        description=f"Cash/Bank payment {payment.payment_number}",
        debit=Decimal("0"),
        credit=payment.amount_paid,
        line_order=1,
        creator=user,
    )
    _post_journal(je)
    return je


def post_customer_payment_journal(*, payment, user):
    _assert_open_period(payment.payment_date)
    ar_account = get_system_account("ACCOUNTS_RECEIVABLE", fallback_code="112")
    je = JournalEntry.objects.create(
        date=payment.payment_date,
        description=f"Customer Payment {payment.payment_number}",
        status="draft",
        creator=user,
    )
    JournalEntryLine.objects.create(
        journal_entry=je,
        account=payment.paid_through,
        description=f"Cash/Bank receipt {payment.payment_number}",
        debit=payment.amount_received,
        credit=Decimal("0"),
        line_order=0,
        creator=user,
    )
    JournalEntryLine.objects.create(
        journal_entry=je,
        account=ar_account,
        description=f"Accounts Receivable settlement {payment.payment_number}",
        debit=Decimal("0"),
        credit=payment.amount_received,
        line_order=1,
        creator=user,
    )
    _post_journal(je)
    return je


def post_supplier_refund_journal(*, refund, user):
    _assert_open_period(refund.refund_date)
    ap_account = get_system_account("ACCOUNTS_PAYABLE", fallback_code="211")
    je = JournalEntry.objects.create(
        date=refund.refund_date,
        description=f"Supplier Refund {refund.refund_number}",
        status="draft",
        creator=user,
    )
    JournalEntryLine.objects.create(
        journal_entry=je,
        account=refund.paid_through,
        description=f"Cash/Bank receipt {refund.refund_number}",
        debit=refund.amount_refunded,
        credit=Decimal("0"),
        line_order=0,
        creator=user,
    )
    JournalEntryLine.objects.create(
        journal_entry=je,
        account=ap_account,
        description=f"Accounts Payable refund {refund.refund_number}",
        debit=Decimal("0"),
        credit=refund.amount_refunded,
        line_order=1,
        creator=user,
    )
    _post_journal(je)
    return je


def post_customer_refund_journal(*, refund, user):
    _assert_open_period(refund.refund_date)
    ar_account = get_system_account("ACCOUNTS_RECEIVABLE", fallback_code="112")
    je = JournalEntry.objects.create(
        date=refund.refund_date,
        description=f"Customer Refund {refund.refund_number}",
        status="draft",
        creator=user,
    )
    JournalEntryLine.objects.create(
        journal_entry=je,
        account=ar_account,
        description=f"Accounts Receivable refund {refund.refund_number}",
        debit=refund.amount_refunded,
        credit=Decimal("0"),
        line_order=0,
        creator=user,
    )
    JournalEntryLine.objects.create(
        journal_entry=je,
        account=refund.paid_through,
        description=f"Cash/Bank refund {refund.refund_number}",
        debit=Decimal("0"),
        credit=refund.amount_refunded,
        line_order=1,
        creator=user,
    )
    _post_journal(je)
    return je
