from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from main.models import BaseModel
from main.money import get_vat_rounding_strategy, money, vat_amount


CUSTOMER_PAYMENT_TERMS_CHOICES = (
    ("due_on_receipt", "Due on Receipt"),
    ("net_15", "Net 15 days"),
    ("net_30", "Net 30 days"),
    ("net_45", "Net 45 days"),
    ("net_60", "Net 60 days"),
    ("net_90", "Net 90 days"),
)

CUSTOMER_VAT_TREATMENT_CHOICES = (
    ("vat_registered_ksa", "VAT registered in KSA"),
    ("not_vat_registered_ksa", "Not VAT registered in KSA"),
    ("outside_ksa", "Outside KSA"),
)

CUSTOMER_OPENING_BALANCE_CHOICES = (
    ("none", "No opening balance"),
    ("i_owe_customer", "I owe this customer"),
    ("customer_owes_me", "Customer owes me"),
)

QUOTE_STATUS_CHOICES = (
    ("draft", "Draft"),
    ("sent", "Sent"),
    ("accepted", "Accepted"),
    ("rejected", "Rejected"),
)

INVOICE_STATUS_CHOICES = (
    ("draft", "Draft"),
    ("posted", "Posted"),
    ("paid", "Paid"),
    ("partially_paid", "Partially Paid"),
    ("overdue", "Overdue"),
)

ZATCA_SUBMISSION_STATUS_CHOICES = (
    ("not_submitted", "Not Submitted"),
    ("signed", "Signed (artifacts ready)"),
    ("submitted", "Submitted (awaiting authority)"),
    ("retrying", "Retrying"),
    ("reported", "Reported"),
    ("cleared", "Cleared"),
    ("rejected", "Rejected"),
    ("failed_final", "Failed Final"),
)

ZATCA_SUBMISSION_TYPE_CHOICES = (
    ("clearance", "Clearance"),
    ("reporting", "Reporting"),
)

ZATCA_WORKFLOW_STATUS_CHOICES = (
    ("pending", "Pending"),
    ("retrying", "Retrying"),
    ("succeeded", "Succeeded"),
    ("failed_final", "Failed Final"),
)

CUSTOMER_PAYMENT_TYPE_CHOICES = (
    ("invoice_payment", "Invoice Payments"),
    ("advance_payment", "Advance Payments"),
)

CUSTOMER_CREDIT_NOTE_STATUS_CHOICES = (
    ("draft", "Draft"),
    ("posted", "Posted"),
)


class ZatcaHashSealMixin:
    """Prevent tampering with stored ZATCA hash inputs after first seal."""

    _ZATCA_IMMUTABLE_HASH_FIELDS = ("zatca_previous_hash", "zatca_invoice_hash", "zatca_canonical_xml")

    def save(self, *args, **kwargs):
        if self.pk:
            previous = (
                type(self)
                .objects.filter(pk=self.pk)
                .only("zatca_previous_hash", "zatca_invoice_hash", "zatca_canonical_xml")
                .first()
            )
            if previous and (previous.zatca_invoice_hash or "").strip():
                for f in self._ZATCA_IMMUTABLE_HASH_FIELDS:
                    if (getattr(self, f) or "") != (getattr(previous, f) or ""):
                        raise ValidationError(f"ZATCA hash artifacts are immutable once set ({f}).")
        super().save(*args, **kwargs)


class Customer(BaseModel):
    company_name = models.CharField(_("Company Name (EN)"), max_length=255)
    company_name_ar = models.CharField(_("Company Name (AR)"), max_length=255, blank=True)

    primary_contact_name = models.CharField(_("Primary Contact Name"), max_length=255, blank=True)
    email = models.EmailField(_("Email"), blank=True)
    phone = models.CharField(_("Phone Number"), max_length=32, blank=True)

    vat_treatment = models.CharField(
        _("VAT Treatment"),
        max_length=30,
        choices=CUSTOMER_VAT_TREATMENT_CHOICES,
        default="vat_registered_ksa",
        db_index=True,
    )
    tax_registration_number = models.CharField(_("Tax Registration Number"), max_length=50, blank=True)

    country = models.ForeignKey("main.Country", on_delete=models.SET_NULL, null=True, blank=True)
    street_address = models.CharField(_("Street Address (EN)"), max_length=255, blank=True)
    street_address_ar = models.CharField(_("Street Address (AR)"), max_length=255, blank=True)
    building_number = models.CharField(_("Building Number"), max_length=50, blank=True)
    land_identifier = models.CharField(_("Land Identifier"), max_length=50, blank=True)
    district = models.CharField(_("District (EN)"), max_length=100, blank=True)
    district_ar = models.CharField(_("District (AR)"), max_length=100, blank=True)
    city = models.CharField(_("City (EN)"), max_length=100, blank=True)
    city_ar = models.CharField(_("City (AR)"), max_length=100, blank=True)
    postal_code = models.CharField(_("Postal Code"), max_length=20, blank=True)

    payment_terms = models.CharField(
        _("Payment Terms"),
        max_length=20,
        choices=CUSTOMER_PAYMENT_TERMS_CHOICES,
        blank=True,
        db_index=True,
    )
    opening_balance_type = models.CharField(
        _("Opening Balance"),
        max_length=20,
        choices=CUSTOMER_OPENING_BALANCE_CHOICES,
        default="none",
        db_index=True,
    )
    opening_balance_amount = models.DecimalField(
        _("Opening Balance Amount"),
        max_digits=18,
        decimal_places=2,
        default=Decimal("0"),
        help_text=_("Absolute amount. Direction is determined by opening_balance_type."),
    )
    opening_balance_as_of = models.DateField(_("Opening Balance As of"), null=True, blank=True)
    opening_balance_account = models.ForeignKey(
        "accounting.Account",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="customers_opening_balance",
        verbose_name=_("As of (Account)"),
        help_text=_("Optional account used for opening balance posting later (e.g. Accounts Receivable)."),
    )

    is_active = models.BooleanField(_("Is Active"), default=True, db_index=True)

    class Meta:
        db_table = "customer"
        ordering = ["company_name", "created_at"]

    def __str__(self) -> str:
        return self.company_name


class Quote(BaseModel):
    quote_number = models.CharField(_("Quote Number"), max_length=30, unique=True, db_index=True)
    customer = models.ForeignKey(
        "sales.Customer",
        on_delete=models.PROTECT,
        related_name="quotes",
        verbose_name=_("Customer"),
    )
    date = models.DateField(_("Date"), db_index=True)
    note = models.TextField(_("Note"), blank=True)
    attachment = models.FileField(_("Attachment"), upload_to="quote-attachments/", null=True, blank=True)
    status = models.CharField(
        _("Status"),
        max_length=12,
        choices=QUOTE_STATUS_CHOICES,
        default="draft",
        db_index=True,
    )

    subtotal_before_discount = models.DecimalField(
        _("Subtotal before discount"),
        max_digits=18,
        decimal_places=2,
        default=Decimal("0"),
    )
    discount_total = models.DecimalField(_("Discount total"), max_digits=18, decimal_places=2, default=Decimal("0"))
    total_vat = models.DecimalField(_("Total VAT"), max_digits=18, decimal_places=2, default=Decimal("0"))
    total_amount = models.DecimalField(_("Total Amount"), max_digits=18, decimal_places=2, default=Decimal("0"))

    class Meta:
        db_table = "sales_quote"
        ordering = ["-date", "-created_at"]

    def __str__(self) -> str:
        return self.quote_number

    def recalculate_totals(self) -> None:
        lines = self.lines.filter(is_deleted=False)
        subtotal = Decimal("0")
        discount = Decimal("0")
        vat = Decimal("0")
        total = Decimal("0")
        strategy = get_vat_rounding_strategy()
        for line in lines:
            subtotal += money(line.gross_amount())
            discount += money(line.discount_amount())
            vat += line.tax_amount(strategy=strategy)
            total += line.total(strategy=strategy)

        self.subtotal_before_discount = money(subtotal)
        self.discount_total = money(discount)
        self.total_vat = money(vat) if strategy == "invoice" else money(vat)
        self.total_amount = money(total) if strategy == "invoice" else money(total)
        self.save(
            update_fields=[
                "subtotal_before_discount",
                "discount_total",
                "total_vat",
                "total_amount",
                "updated_at",
            ]
        )


class QuoteLine(BaseModel):
    quote = models.ForeignKey(
        "sales.Quote",
        on_delete=models.PROTECT,
        related_name="lines",
        verbose_name=_("Quote"),
    )
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quote_lines",
        verbose_name=_("Item"),
    )
    description = models.CharField(_("Description"), max_length=500)
    quantity = models.DecimalField(_("Quantity"), max_digits=12, decimal_places=2, default=Decimal("1"))
    unit_price = models.DecimalField(_("Unit Price"), max_digits=18, decimal_places=2, default=Decimal("0"))
    tax_rate = models.ForeignKey(
        "accounting.TaxRate",
        on_delete=models.SET_NULL,
        related_name="sales_quote_lines",
        null=True,
        blank=True,
        verbose_name=_("Tax Rate"),
    )
    discount_percent = models.DecimalField(_("Discount %"), max_digits=6, decimal_places=2, default=Decimal("0"))
    line_order = models.PositiveIntegerField(_("Line Order"), default=0)

    class Meta:
        db_table = "sales_quote_line"
        ordering = ["line_order", "created_at"]

    def gross_amount(self) -> Decimal:
        return (self.quantity or Decimal("0")) * (self.unit_price or Decimal("0"))

    def discount_amount(self) -> Decimal:
        gross = self.gross_amount()
        return (gross * (self.discount_percent or Decimal("0"))) / Decimal("100")

    def subtotal(self) -> Decimal:
        return money(self.gross_amount() - self.discount_amount())

    def tax_amount(self, *, strategy: str | None = None) -> Decimal:
        if not self.tax_rate:
            return Decimal("0")
        return vat_amount(self.subtotal(), self.tax_rate.rate, strategy=strategy)

    def total(self, *, strategy: str | None = None) -> Decimal:
        strat = (strategy or get_vat_rounding_strategy()).strip().lower()
        vat = self.tax_amount(strategy=strat)
        return money(self.subtotal() + (money(vat) if strat == "invoice" else vat))


class Invoice(ZatcaHashSealMixin, BaseModel):
    invoice_number = models.CharField(_("Invoice Number"), max_length=30, unique=True, db_index=True)
    external_reference = models.CharField(
        _("External Reference"),
        max_length=100,
        blank=True,
        db_index=True,
        help_text=_("Client/system reference for idempotent business deduplication."),
    )
    customer = models.ForeignKey(
        "sales.Customer",
        on_delete=models.PROTECT,
        related_name="invoices",
        verbose_name=_("Customer"),
    )
    date = models.DateField(_("Date"), db_index=True)
    due_date = models.DateField(_("Due Date"), null=True, blank=True, db_index=True)
    note = models.TextField(_("Note"), blank=True)
    attachment = models.FileField(_("Attachment"), upload_to="invoice-attachments/", null=True, blank=True)
    status = models.CharField(
        _("Status"),
        max_length=20,
        choices=INVOICE_STATUS_CHOICES,
        default="draft",
        db_index=True,
    )
    posted_at = models.DateTimeField(_("Posted At"), null=True, blank=True)
    qr_code_text = models.TextField(_("QR Code Text"), blank=True)
    zatca_uuid = models.CharField(_("ZATCA UUID"), max_length=64, blank=True, db_index=True)
    zatca_previous_hash = models.CharField(_("ZATCA Previous Invoice Hash"), max_length=128, blank=True)
    zatca_invoice_hash = models.CharField(_("ZATCA Invoice Hash"), max_length=128, blank=True)
    zatca_signed_hash = models.CharField(_("ZATCA Signed Hash"), max_length=128, blank=True)
    zatca_xml = models.TextField(_("ZATCA UBL XML"), blank=True)
    zatca_canonical_xml = models.TextField(_("ZATCA Canonical XML"), blank=True)
    zatca_signature_value = models.TextField(_("ZATCA Signature Value (Base64)"), blank=True)
    zatca_signed_xml = models.TextField(_("ZATCA Signed XML"), blank=True)
    zatca_certificate = models.ForeignKey(
        "sales.ZatcaCertificate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices",
        verbose_name=_("ZATCA Certificate Used"),
    )
    zatca_submission_status = models.CharField(
        _("ZATCA Submission Status"),
        max_length=20,
        choices=ZATCA_SUBMISSION_STATUS_CHOICES,
        default="not_submitted",
        db_index=True,
    )
    zatca_submission_type = models.CharField(
        _("ZATCA Submission Type"),
        max_length=20,
        choices=ZATCA_SUBMISSION_TYPE_CHOICES,
        blank=True,
    )
    zatca_submission_reference = models.CharField(_("ZATCA Submission Reference"), max_length=100, blank=True)
    zatca_submission_error = models.TextField(_("ZATCA Submission Error"), blank=True)
    zatca_submitted_at = models.DateTimeField(_("ZATCA Submitted At"), null=True, blank=True)
    zatca_cleared_at = models.DateTimeField(_("ZATCA Cleared/Reported At"), null=True, blank=True)
    journal_entry = models.OneToOneField(
        "accounting.JournalEntry",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sales_invoice",
        verbose_name=_("Journal Entry"),
    )

    subtotal = models.DecimalField(_("Subtotal"), max_digits=18, decimal_places=2, default=Decimal("0"))
    total_vat = models.DecimalField(_("Total VAT"), max_digits=18, decimal_places=2, default=Decimal("0"))
    total_amount = models.DecimalField(_("Total Amount"), max_digits=18, decimal_places=2, default=Decimal("0"))
    paid_amount = models.DecimalField(_("Paid Amount"), max_digits=18, decimal_places=2, default=Decimal("0"))

    class Meta:
        db_table = "sales_invoice"
        ordering = ["-date", "-created_at"]
        constraints = [
            models.CheckConstraint(
                check=~models.Q(status="posted") | models.Q(journal_entry__isnull=False),
                name="invoice_posted_requires_journal_entry",
            ),
            models.CheckConstraint(
                check=Q(paid_amount__gte=0) & Q(paid_amount__lte=F("total_amount")),
                name="invoice_paid_lte_total_nonneg",
            ),
            models.UniqueConstraint(
                fields=["customer", "external_reference"],
                condition=~models.Q(external_reference=""),
                name="uniq_invoice_customer_external_reference",
            ),
        ]

    def __str__(self) -> str:
        return self.invoice_number

    @property
    def balance_amount(self) -> Decimal:
        balance = (self.total_amount or Decimal("0")) - (self.paid_amount or Decimal("0"))
        return balance if balance > 0 else Decimal("0")

    def recalculate_totals(self) -> None:
        lines = self.lines.filter(is_deleted=False)
        subtotal = Decimal("0")
        vat = Decimal("0")
        total = Decimal("0")
        strategy = get_vat_rounding_strategy()
        for line in lines:
            subtotal += line.subtotal()
            vat += line.tax_amount(strategy=strategy)
            total += line.total(strategy=strategy)
        self.subtotal = money(subtotal)
        self.total_vat = money(vat) if strategy == "invoice" else money(vat)
        self.total_amount = money(total) if strategy == "invoice" else money(total)
        self.save(update_fields=["subtotal", "total_vat", "total_amount", "updated_at"])


class InvoiceLine(BaseModel):
    invoice = models.ForeignKey(
        "sales.Invoice",
        on_delete=models.PROTECT,
        related_name="lines",
        verbose_name=_("Invoice"),
    )
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoice_lines",
        verbose_name=_("Item"),
    )
    description = models.CharField(_("Description"), max_length=500)
    account = models.ForeignKey(
        "accounting.Account",
        on_delete=models.PROTECT,
        related_name="sales_invoice_lines",
        verbose_name=_("Account"),
    )
    quantity = models.DecimalField(_("Quantity"), max_digits=12, decimal_places=2, default=Decimal("1"))
    unit_price = models.DecimalField(_("Unit Price"), max_digits=18, decimal_places=2, default=Decimal("0"))
    tax_rate = models.ForeignKey(
        "accounting.TaxRate",
        on_delete=models.SET_NULL,
        related_name="sales_invoice_lines",
        null=True,
        blank=True,
        verbose_name=_("Tax Rate"),
    )
    discount_percent = models.DecimalField(_("Discount %"), max_digits=6, decimal_places=2, default=Decimal("0"))
    line_order = models.PositiveIntegerField(_("Line Order"), default=0)

    class Meta:
        db_table = "sales_invoice_line"
        ordering = ["line_order", "created_at"]

    def gross_amount(self) -> Decimal:
        return (self.quantity or Decimal("0")) * (self.unit_price or Decimal("0"))

    def discount_amount(self) -> Decimal:
        gross = self.gross_amount()
        return (gross * (self.discount_percent or Decimal("0"))) / Decimal("100")

    def subtotal(self) -> Decimal:
        return money(self.gross_amount() - self.discount_amount())

    def tax_amount(self, *, strategy: str | None = None) -> Decimal:
        if not self.tax_rate:
            return Decimal("0")
        return vat_amount(self.subtotal(), self.tax_rate.rate, strategy=strategy)

    def total(self, *, strategy: str | None = None) -> Decimal:
        strat = (strategy or get_vat_rounding_strategy()).strip().lower()
        vat = self.tax_amount(strategy=strat)
        return money(self.subtotal() + (money(vat) if strat == "invoice" else vat))


class CustomerPayment(BaseModel):
    payment_number = models.CharField(_("Payment Number"), max_length=30, unique=True, db_index=True)
    customer = models.ForeignKey(
        "sales.Customer",
        on_delete=models.PROTECT,
        related_name="payments",
        verbose_name=_("Customer"),
    )
    paid_through = models.ForeignKey(
        "accounting.Account",
        on_delete=models.PROTECT,
        related_name="customer_payments",
        verbose_name=_("Paid Through"),
        help_text=_("Cash/Bank account used for payment."),
    )
    payment_type = models.CharField(
        _("Payment Type"),
        max_length=20,
        choices=CUSTOMER_PAYMENT_TYPE_CHOICES,
        default="invoice_payment",
        db_index=True,
    )
    amount_received = models.DecimalField(_("Amount Received"), max_digits=18, decimal_places=2, default=Decimal("0"))
    payment_date = models.DateField(_("Payment Date"), db_index=True)
    description = models.TextField(_("Description"), blank=True)
    is_posted = models.BooleanField(_("Posted"), default=True, db_index=True)
    journal_entry = models.OneToOneField(
        "accounting.JournalEntry",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sales_customer_payment",
        verbose_name=_("Journal Entry"),
    )

    class Meta:
        db_table = "customer_payment"
        ordering = ["-payment_date", "-created_at"]

    def __str__(self) -> str:
        return self.payment_number

    @property
    def amount_applied(self) -> Decimal:
        total = self.allocations.filter(is_deleted=False).aggregate(total=models.Sum("amount")).get("total")
        return total or Decimal("0")

    @property
    def remaining_amount(self) -> Decimal:
        remaining = (self.amount_received or Decimal("0")) - self.amount_applied
        return remaining if remaining > 0 else Decimal("0")


class CustomerPaymentAllocation(BaseModel):
    payment = models.ForeignKey(
        "sales.CustomerPayment",
        on_delete=models.PROTECT,
        related_name="allocations",
        verbose_name=_("Customer Payment"),
    )
    invoice = models.ForeignKey(
        "sales.Invoice",
        on_delete=models.PROTECT,
        related_name="payment_allocations",
        verbose_name=_("Invoice"),
    )
    amount = models.DecimalField(_("Amount Applied"), max_digits=18, decimal_places=2, default=Decimal("0"))

    class Meta:
        db_table = "customer_payment_allocation"
        ordering = ["created_at"]


class CustomerCreditNote(ZatcaHashSealMixin, BaseModel):
    credit_note_number = models.CharField(_("Credit Note Number"), max_length=30, unique=True, db_index=True)
    external_reference = models.CharField(
        _("External Reference"),
        max_length=100,
        blank=True,
        db_index=True,
        help_text=_("Client/system reference for idempotent business deduplication."),
    )
    customer = models.ForeignKey(
        "sales.Customer",
        on_delete=models.PROTECT,
        related_name="credit_notes",
        verbose_name=_("Customer"),
    )
    date = models.DateField(_("Date"), db_index=True)
    note = models.TextField(_("Note"), blank=True)
    attachment = models.FileField(_("Attachment"), upload_to="credit-note-attachments/", null=True, blank=True)
    status = models.CharField(
        _("Status"),
        max_length=10,
        choices=CUSTOMER_CREDIT_NOTE_STATUS_CHOICES,
        default="draft",
        db_index=True,
    )
    posted_at = models.DateTimeField(_("Posted At"), null=True, blank=True)
    qr_code_text = models.TextField(_("QR Code Text"), blank=True)
    zatca_uuid = models.CharField(_("ZATCA UUID"), max_length=64, blank=True, db_index=True)
    zatca_previous_hash = models.CharField(_("ZATCA Previous Invoice Hash"), max_length=128, blank=True)
    zatca_invoice_hash = models.CharField(_("ZATCA Invoice Hash"), max_length=128, blank=True)
    zatca_signed_hash = models.CharField(_("ZATCA Signed Hash"), max_length=128, blank=True)
    zatca_xml = models.TextField(_("ZATCA UBL XML"), blank=True)
    zatca_canonical_xml = models.TextField(_("ZATCA Canonical XML"), blank=True)
    zatca_signature_value = models.TextField(_("ZATCA Signature Value (Base64)"), blank=True)
    zatca_signed_xml = models.TextField(_("ZATCA Signed XML"), blank=True)
    zatca_certificate = models.ForeignKey(
        "sales.ZatcaCertificate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="credit_notes",
        verbose_name=_("ZATCA Certificate Used"),
    )
    zatca_submission_status = models.CharField(
        _("ZATCA Submission Status"),
        max_length=20,
        choices=ZATCA_SUBMISSION_STATUS_CHOICES,
        default="not_submitted",
        db_index=True,
    )
    zatca_submission_type = models.CharField(
        _("ZATCA Submission Type"),
        max_length=20,
        choices=ZATCA_SUBMISSION_TYPE_CHOICES,
        blank=True,
    )
    zatca_submission_reference = models.CharField(_("ZATCA Submission Reference"), max_length=100, blank=True)
    zatca_submission_error = models.TextField(_("ZATCA Submission Error"), blank=True)
    zatca_submitted_at = models.DateTimeField(_("ZATCA Submitted At"), null=True, blank=True)
    zatca_cleared_at = models.DateTimeField(_("ZATCA Cleared/Reported At"), null=True, blank=True)
    journal_entry = models.OneToOneField(
        "accounting.JournalEntry",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sales_credit_note",
        verbose_name=_("Journal Entry"),
    )
    subtotal = models.DecimalField(_("Subtotal"), max_digits=18, decimal_places=2, default=Decimal("0"))
    total_vat = models.DecimalField(_("Total VAT"), max_digits=18, decimal_places=2, default=Decimal("0"))
    total_amount = models.DecimalField(_("Total Amount"), max_digits=18, decimal_places=2, default=Decimal("0"))
    refunded_amount = models.DecimalField(_("Refunded Amount"), max_digits=18, decimal_places=2, default=Decimal("0"))

    class Meta:
        db_table = "customer_credit_note"
        ordering = ["-date", "-created_at"]
        constraints = [
            models.CheckConstraint(
                check=~models.Q(status="posted") | models.Q(journal_entry__isnull=False),
                name="credit_note_posted_requires_journal_entry",
            ),
            models.CheckConstraint(
                check=Q(refunded_amount__gte=0) & Q(refunded_amount__lte=F("total_amount")),
                name="credit_note_refunded_lte_total_nonneg",
            ),
            models.UniqueConstraint(
                fields=["customer", "external_reference"],
                condition=~models.Q(external_reference=""),
                name="uniq_credit_note_customer_external_reference",
            ),
        ]

    @property
    def balance_amount(self) -> Decimal:
        balance = (self.total_amount or Decimal("0")) - (self.refunded_amount or Decimal("0"))
        return balance if balance > 0 else Decimal("0")

    def recalculate_totals(self) -> None:
        lines = self.lines.filter(is_deleted=False)
        subtotal = Decimal("0")
        vat = Decimal("0")
        total = Decimal("0")
        strategy = get_vat_rounding_strategy()
        for line in lines:
            subtotal += line.subtotal()
            vat += line.tax_amount(strategy=strategy)
            total += line.total(strategy=strategy)
        self.subtotal = money(subtotal)
        self.total_vat = money(vat) if strategy == "invoice" else money(vat)
        self.total_amount = money(total) if strategy == "invoice" else money(total)
        self.save(update_fields=["subtotal", "total_vat", "total_amount", "updated_at"])


class CustomerCreditNoteLine(BaseModel):
    credit_note = models.ForeignKey(
        "sales.CustomerCreditNote",
        on_delete=models.PROTECT,
        related_name="lines",
        verbose_name=_("Credit Note"),
    )
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="credit_note_lines",
        verbose_name=_("Item"),
    )
    description = models.CharField(_("Description"), max_length=500)
    account = models.ForeignKey(
        "accounting.Account",
        on_delete=models.PROTECT,
        related_name="sales_credit_note_lines",
        verbose_name=_("Account"),
    )
    quantity = models.DecimalField(_("Quantity"), max_digits=12, decimal_places=2, default=Decimal("1"))
    unit_price = models.DecimalField(_("Unit Price"), max_digits=18, decimal_places=2, default=Decimal("0"))
    tax_rate = models.ForeignKey(
        "accounting.TaxRate",
        on_delete=models.SET_NULL,
        related_name="sales_credit_note_lines",
        null=True,
        blank=True,
        verbose_name=_("Tax Rate"),
    )
    discount_percent = models.DecimalField(_("Discount %"), max_digits=6, decimal_places=2, default=Decimal("0"))
    line_order = models.PositiveIntegerField(_("Line Order"), default=0)

    class Meta:
        db_table = "sales_credit_note_line"
        ordering = ["line_order", "created_at"]

    def gross_amount(self) -> Decimal:
        return (self.quantity or Decimal("0")) * (self.unit_price or Decimal("0"))

    def discount_amount(self) -> Decimal:
        gross = self.gross_amount()
        return (gross * (self.discount_percent or Decimal("0"))) / Decimal("100")

    def subtotal(self) -> Decimal:
        return money(self.gross_amount() - self.discount_amount())

    def tax_amount(self, *, strategy: str | None = None) -> Decimal:
        if not self.tax_rate:
            return Decimal("0")
        return vat_amount(self.subtotal(), self.tax_rate.rate, strategy=strategy)

    def total(self, *, strategy: str | None = None) -> Decimal:
        strat = (strategy or get_vat_rounding_strategy()).strip().lower()
        vat = self.tax_amount(strategy=strat)
        return money(self.subtotal() + (money(vat) if strat == "invoice" else vat))


class CustomerRefund(BaseModel):
    refund_number = models.CharField(_("Refund Number"), max_length=30, unique=True, db_index=True)
    customer = models.ForeignKey(
        "sales.Customer",
        on_delete=models.PROTECT,
        related_name="refunds",
        verbose_name=_("Customer"),
    )
    paid_through = models.ForeignKey(
        "accounting.Account",
        on_delete=models.PROTECT,
        related_name="customer_refunds",
        verbose_name=_("Paid Through"),
        help_text=_("Cash/Bank account used for refund."),
    )
    amount_refunded = models.DecimalField(_("Amount Refunded"), max_digits=18, decimal_places=2, default=Decimal("0"))
    refund_date = models.DateField(_("Refund Date"), db_index=True)
    description = models.TextField(_("Description"), blank=True)
    is_posted = models.BooleanField(_("Posted"), default=True, db_index=True)
    journal_entry = models.OneToOneField(
        "accounting.JournalEntry",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sales_customer_refund",
        verbose_name=_("Journal Entry"),
    )

    class Meta:
        db_table = "customer_refund"
        ordering = ["-refund_date", "-created_at"]

    @property
    def amount_applied(self) -> Decimal:
        total = self.allocations.filter(is_deleted=False).aggregate(total=models.Sum("amount")).get("total")
        return total or Decimal("0")

    @property
    def remaining_amount(self) -> Decimal:
        remaining = (self.amount_refunded or Decimal("0")) - self.amount_applied
        return remaining if remaining > 0 else Decimal("0")


class CustomerRefundAllocation(BaseModel):
    refund = models.ForeignKey(
        "sales.CustomerRefund",
        on_delete=models.PROTECT,
        related_name="allocations",
        verbose_name=_("Customer Refund"),
    )
    credit_note = models.ForeignKey(
        "sales.CustomerCreditNote",
        on_delete=models.PROTECT,
        related_name="refund_allocations",
        verbose_name=_("Credit Note"),
    )
    amount = models.DecimalField(_("Amount Applied"), max_digits=18, decimal_places=2, default=Decimal("0"))

    class Meta:
        db_table = "customer_refund_allocation"
        ordering = ["created_at"]


class ZatcaSubmissionLog(BaseModel):
    """Idempotent submission ledger for retry/audit workflow."""
    document_type = models.CharField(_("Document Type"), max_length=20, db_index=True)  # invoice|credit_note
    document_id = models.UUIDField(_("Document ID"), db_index=True)
    submission_type = models.CharField(
        _("Submission Type"),
        max_length=20,
        choices=ZATCA_SUBMISSION_TYPE_CHOICES,
        db_index=True,
    )
    # Unique per document_type to avoid collisions across invoices vs credit notes.
    idempotency_key = models.CharField(_("Idempotency Key"), max_length=120, db_index=True)
    status = models.CharField(
        _("Workflow Status"),
        max_length=20,
        choices=ZATCA_WORKFLOW_STATUS_CHOICES,
        default="pending",
        db_index=True,
    )
    attempt_count = models.PositiveIntegerField(_("Attempt Count"), default=0)
    next_retry_at = models.DateTimeField(_("Next Retry At"), null=True, blank=True)
    last_error = models.TextField(_("Last Error"), blank=True)
    response_reference = models.CharField(_("Response Reference"), max_length=100, blank=True)
    provider_request_id = models.CharField(_("Provider Request ID"), max_length=120, blank=True, db_index=True)
    provider_correlation_id = models.CharField(_("Provider Correlation ID"), max_length=120, blank=True, db_index=True)
    provider_status = models.CharField(_("Provider Status"), max_length=80, blank=True, db_index=True)
    response_headers = models.JSONField(_("Response Headers"), default=dict, blank=True)
    submitted_at = models.DateTimeField(_("Submitted At"), null=True, blank=True)

    class Meta:
        db_table = "sales_zatca_submission_log"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["document_type", "idempotency_key"], name="uniq_zatca_log_doc_type_key"),
        ]


class ZatcaSubmissionStatusLog(BaseModel):
    """Append-only audit trail for ZATCA submission status transitions on a document."""

    document_type = models.CharField(max_length=20, db_index=True)  # invoice|credit_note
    document_id = models.UUIDField(db_index=True)
    from_status = models.CharField(max_length=40, db_index=True)
    to_status = models.CharField(max_length=40, db_index=True)
    actor = models.ForeignKey(
        "user.CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="zatca_submission_status_logs",
        verbose_name=_("Actor"),
    )

    class Meta:
        db_table = "sales_zatca_submission_status_log"
        ordering = ["created_at"]


class ZatcaEvidenceBundle(BaseModel):
    """Immutable evidence bundle for audit (request/response + artifacts)."""
    document_type = models.CharField(max_length=20, db_index=True)  # invoice|credit_note
    document_id = models.UUIDField(db_index=True)
    submission_type = models.CharField(max_length=20, choices=ZATCA_SUBMISSION_TYPE_CHOICES, db_index=True)
    idempotency_key = models.CharField(max_length=120, db_index=True)
    request_payload = models.TextField(blank=True)
    response_payload = models.TextField(blank=True)
    http_status = models.PositiveIntegerField(null=True, blank=True)
    reference = models.CharField(max_length=100, blank=True)
    api_request_headers = models.JSONField(_("API Request Headers"), default=dict, blank=True)
    api_response_headers = models.JSONField(_("API Response Headers"), default=dict, blank=True)
    certificate_sha256 = models.CharField(_("Leaf certificate SHA-256 (DER)"), max_length=128, blank=True)

    def is_complete(self) -> bool:
        """Mandatory audit fields for live ZATCA evidence bundles (request/response + cert fingerprint)."""
        if not (self.request_payload or "").strip():
            return False
        if not (self.response_payload or "").strip():
            return False
        if self.http_status is None:
            return False
        if not (self.certificate_sha256 or "").strip():
            return False
        req = self.api_request_headers
        if not isinstance(req, dict) or not req:
            return False
        return True

    class Meta:
        db_table = "sales_zatca_evidence_bundle"
        ordering = ["-created_at"]


class ZatcaCertificate(BaseModel):
    """Certificate metadata and storage pointers (no private key bytes in DB)."""
    name = models.CharField(max_length=100, unique=True)
    certificate_pem = models.TextField(blank=True)
    private_key_path = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=False, db_index=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "sales_zatca_certificate"
        ordering = ["-created_at"]


ZATCA_OUTBOX_STATUS_CHOICES = (
    ("pending", "Pending"),
    ("processing", "Processing"),
    ("retrying", "Retrying"),
    ("succeeded", "Succeeded"),
    ("failed_final", "Failed Final"),
)


class ZatcaOutboxEvent(BaseModel):
    """Durable outbox for ZATCA submissions (worker processes asynchronously)."""
    document_type = models.CharField(max_length=20, db_index=True)  # invoice|credit_note
    document_id = models.UUIDField(db_index=True)
    submission_type = models.CharField(max_length=20, choices=ZATCA_SUBMISSION_TYPE_CHOICES, db_index=True)
    idempotency_key = models.CharField(max_length=120, db_index=True)
    status = models.CharField(max_length=20, choices=ZATCA_OUTBOX_STATUS_CHOICES, default="pending", db_index=True)
    attempt_count = models.PositiveIntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True, db_index=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        db_table = "sales_zatca_outbox_event"
        ordering = ["created_at"]
        constraints = [
            # Prevent accidentally queuing duplicates for the same document + key.
            models.UniqueConstraint(
                fields=["document_type", "document_id", "submission_type", "idempotency_key"],
                name="uniq_zatca_outbox_doc_key",
            ),
        ]


class ZatcaControlSequence(BaseModel):
    """
    Persistent control sequence values used by ZATCA business references (e.g., ICV).
    """
    scope = models.CharField(max_length=80, unique=True, db_index=True)
    next_value = models.BigIntegerField(default=1)

    class Meta:
        db_table = "sales_zatca_control_sequence"
        ordering = ["scope"]


class ZatcaHashChainAnchor(BaseModel):
    """
    Operational anchor for hash-chain health (DB restore / manual fix can break the chain).
    Updated by verify_zatca_hash_chain; read before posting new ZATCA-linked sales documents.
    """

    last_verified_hash = models.CharField(max_length=128, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    chain_integrity_ok = models.BooleanField(default=True, db_index=True)
    last_run_summary = models.TextField(blank=True)

    class Meta:
        db_table = "sales_zatca_hash_chain_anchor"
        verbose_name = _("ZATCA hash chain anchor")
        ordering = ["created_at"]

    @classmethod
    def get_solo(cls):
        obj = cls.objects.filter(is_deleted=False).order_by("created_at").first()
        if obj:
            return obj
        return cls.objects.create()

