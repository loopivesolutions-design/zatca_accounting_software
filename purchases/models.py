from decimal import Decimal

from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from main.models import BaseModel
from main.money import get_vat_rounding_strategy, money, vat_amount


PAYMENT_TERMS_CHOICES = (
    ("due_on_receipt", "Due on Receipt"),
    ("net_15", "Net 15 days"),
    ("net_30", "Net 30 days"),
    ("net_45", "Net 45 days"),
    ("net_60", "Net 60 days"),
    ("net_90", "Net 90 days"),
)

VAT_TREATMENT_CHOICES = (
    ("vat_registered_ksa", "VAT registered in KSA"),
    ("not_vat_registered_ksa", "Not VAT registered in KSA"),
    ("outside_ksa", "Outside KSA"),
)

OPENING_BALANCE_CHOICES = (
    ("none", "No opening balance"),
    ("i_owe_vendor", "I owe this vendor"),
    ("vendor_owes_me", "Vendor owes me"),
)


class Supplier(BaseModel):
    """
    Supplier / Vendor master record.
    This matches the Purchase → Suppliers → Add Supplier form.
    """

    company_name = models.CharField(_("Company Name (EN)"), max_length=255)
    company_name_ar = models.CharField(_("Company Name (AR)"), max_length=255, blank=True)

    primary_contact_name = models.CharField(_("Primary Contact Name"), max_length=255, blank=True)
    email = models.EmailField(_("Email"), blank=True)
    phone = models.CharField(_("Phone Number"), max_length=32, blank=True)

    vat_treatment = models.CharField(
        _("VAT Treatment"),
        max_length=30,
        choices=VAT_TREATMENT_CHOICES,
        default="vat_registered_ksa",
        db_index=True,
    )
    tax_registration_number = models.CharField(_("Tax Registration Number"), max_length=50, blank=True)

    # Address
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

    # Financial settings
    payment_terms = models.CharField(
        _("Payment Terms"),
        max_length=20,
        choices=PAYMENT_TERMS_CHOICES,
        blank=True,
        db_index=True,
    )
    opening_balance_type = models.CharField(
        _("Opening Balance"),
        max_length=20,
        choices=OPENING_BALANCE_CHOICES,
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
        related_name="suppliers_opening_balance",
        verbose_name=_("As of (Account)"),
        help_text=_("Optional account used for opening balance posting later (e.g. Accounts Payable)."),
    )

    is_active = models.BooleanField(_("Is Active"), default=True, db_index=True)

    class Meta:
        db_table = "supplier"
        ordering = ["company_name", "created_at"]

    def __str__(self) -> str:
        return self.company_name


BILL_STATUS_CHOICES = (
    ("draft", "Draft"),
    ("posted", "Posted"),
)

SUPPLIER_PAYMENT_TYPE_CHOICES = (
    ("bill_payment", "Bill Payments"),
    ("advance_payment", "Advance Payments"),
)

DEBIT_NOTE_STATUS_CHOICES = (
    ("draft", "Draft"),
    ("posted", "Posted"),
)


class Bill(BaseModel):
    """
    Purchase bill header.
    """

    bill_number = models.CharField(_("Bill Number"), max_length=30, unique=True, db_index=True)
    external_reference = models.CharField(
        _("External Reference"),
        max_length=100,
        blank=True,
        db_index=True,
        help_text=_("Client/system reference for idempotent business deduplication."),
    )
    supplier = models.ForeignKey(
        "purchases.Supplier",
        on_delete=models.PROTECT,
        related_name="bills",
        verbose_name=_("Supplier"),
    )
    bill_date = models.DateField(_("Bill Date"), db_index=True)
    due_date = models.DateField(_("Due Date"), null=True, blank=True, db_index=True)
    note = models.TextField(_("Note"), blank=True)
    attachment = models.FileField(_("Attachment"), upload_to="bill-attachments/", null=True, blank=True)
    posted_at = models.DateTimeField(_("Posted At"), null=True, blank=True)
    journal_entry = models.OneToOneField(
        "accounting.JournalEntry",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_bill",
        verbose_name=_("Journal Entry"),
    )
    status = models.CharField(
        _("Status"),
        max_length=10,
        choices=BILL_STATUS_CHOICES,
        default="draft",
        db_index=True,
    )

    subtotal = models.DecimalField(_("Subtotal"), max_digits=18, decimal_places=2, default=Decimal("0"))
    total_vat = models.DecimalField(_("Total VAT"), max_digits=18, decimal_places=2, default=Decimal("0"))
    total_amount = models.DecimalField(_("Total Amount"), max_digits=18, decimal_places=2, default=Decimal("0"))
    paid_amount = models.DecimalField(_("Paid Amount"), max_digits=18, decimal_places=2, default=Decimal("0"))

    class Meta:
        db_table = "purchase_bill"
        ordering = ["-bill_date", "-created_at"]
        constraints = [
            models.CheckConstraint(
                check=~models.Q(status="posted") | models.Q(journal_entry__isnull=False),
                name="bill_posted_requires_journal_entry",
            ),
            models.CheckConstraint(
                check=Q(paid_amount__gte=0) & Q(paid_amount__lte=F("total_amount")),
                name="bill_paid_lte_total_nonneg",
            ),
            models.UniqueConstraint(
                fields=["supplier", "external_reference"],
                condition=~models.Q(external_reference=""),
                name="uniq_bill_supplier_external_reference",
            ),
        ]

    def __str__(self) -> str:
        return self.bill_number

    @property
    def balance_amount(self) -> Decimal:
        balance = (self.total_amount or Decimal("0")) - (self.paid_amount or Decimal("0"))
        return balance if balance > 0 else Decimal("0")

    def recalculate_totals(self) -> None:
        lines = self.lines.filter(is_deleted=False)
        subtotal = Decimal("0")
        total_vat = Decimal("0")
        total_amount = Decimal("0")
        strategy = get_vat_rounding_strategy()
        for line in lines:
            subtotal += line.subtotal()
            total_vat += line.tax_amount(strategy=strategy)
            total_amount += line.total(strategy=strategy)
        self.subtotal = money(subtotal)
        self.total_vat = money(total_vat) if strategy == "invoice" else money(total_vat)
        self.total_amount = money(total_amount) if strategy == "invoice" else money(total_amount)
        self.save(update_fields=["subtotal", "total_vat", "total_amount", "updated_at"])

    def mark_posted(self, *, user=None) -> None:
        self.status = "posted"
        self.posted_at = timezone.now()
        self.updator = user
        self.save(update_fields=["status", "posted_at", "updator", "updated_at"])


class BillLine(BaseModel):
    bill = models.ForeignKey(
        "purchases.Bill",
        on_delete=models.PROTECT,
        related_name="lines",
        verbose_name=_("Bill"),
    )
    description = models.CharField(_("Description"), max_length=500)
    account = models.ForeignKey(
        "accounting.Account",
        on_delete=models.PROTECT,
        related_name="purchase_bill_lines",
        verbose_name=_("Account"),
    )
    quantity = models.DecimalField(_("Quantity"), max_digits=12, decimal_places=2, default=Decimal("1"))
    unit_price = models.DecimalField(_("Unit Price"), max_digits=18, decimal_places=2, default=Decimal("0"))
    tax_rate = models.ForeignKey(
        "accounting.TaxRate",
        on_delete=models.SET_NULL,
        related_name="purchase_bill_lines",
        null=True,
        blank=True,
        verbose_name=_("Tax Rate"),
    )
    discount_percent = models.DecimalField(
        _("Discount %"),
        max_digits=6,
        decimal_places=2,
        default=Decimal("0"),
    )
    line_order = models.PositiveIntegerField(_("Line Order"), default=0)

    class Meta:
        db_table = "purchase_bill_line"
        ordering = ["line_order", "created_at"]

    def __str__(self) -> str:
        return f"{self.bill.bill_number} - {self.description[:40]}"

    def subtotal(self) -> Decimal:
        gross = (self.quantity or Decimal("0")) * (self.unit_price or Decimal("0"))
        discount = (gross * (self.discount_percent or Decimal("0"))) / Decimal("100")
        return money(gross - discount)

    def tax_amount(self, *, strategy: str | None = None) -> Decimal:
        if not self.tax_rate:
            return Decimal("0")
        return vat_amount(self.subtotal(), self.tax_rate.rate, strategy=strategy)

    def total(self, *, strategy: str | None = None) -> Decimal:
        strat = (strategy or get_vat_rounding_strategy()).strip().lower()
        vat = self.tax_amount(strategy=strat)
        return money(self.subtotal() + (money(vat) if strat == "invoice" else vat))


class SupplierPayment(BaseModel):
    payment_number = models.CharField(_("Payment Number"), max_length=30, unique=True, db_index=True)
    supplier = models.ForeignKey(
        "purchases.Supplier",
        on_delete=models.PROTECT,
        related_name="payments",
        verbose_name=_("Supplier"),
    )
    paid_through = models.ForeignKey(
        "accounting.Account",
        on_delete=models.PROTECT,
        related_name="supplier_payments",
        verbose_name=_("Paid Through"),
        help_text=_("Cash/Bank account used for payment."),
    )
    payment_type = models.CharField(
        _("Payment Type"),
        max_length=20,
        choices=SUPPLIER_PAYMENT_TYPE_CHOICES,
        default="bill_payment",
        db_index=True,
    )
    amount_paid = models.DecimalField(_("Amount Paid"), max_digits=18, decimal_places=2, default=Decimal("0"))
    payment_date = models.DateField(_("Payment Date"), db_index=True)
    description = models.TextField(_("Description"), blank=True)
    is_posted = models.BooleanField(_("Posted"), default=True, db_index=True)
    journal_entry = models.OneToOneField(
        "accounting.JournalEntry",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_supplier_payment",
        verbose_name=_("Journal Entry"),
    )

    class Meta:
        db_table = "supplier_payment"
        ordering = ["-payment_date", "-created_at"]

    def __str__(self) -> str:
        return self.payment_number

    @property
    def amount_applied(self) -> Decimal:
        total = self.allocations.filter(is_deleted=False).aggregate(total=models.Sum("amount")).get("total")
        return total or Decimal("0")

    @property
    def remaining_amount(self) -> Decimal:
        remaining = (self.amount_paid or Decimal("0")) - self.amount_applied
        return remaining if remaining > 0 else Decimal("0")


class SupplierPaymentAllocation(BaseModel):
    payment = models.ForeignKey(
        "purchases.SupplierPayment",
        on_delete=models.PROTECT,
        related_name="allocations",
        verbose_name=_("Supplier Payment"),
    )
    bill = models.ForeignKey(
        "purchases.Bill",
        on_delete=models.PROTECT,
        related_name="payment_allocations",
        verbose_name=_("Bill"),
    )
    amount = models.DecimalField(_("Amount Applied"), max_digits=18, decimal_places=2, default=Decimal("0"))

    class Meta:
        db_table = "supplier_payment_allocation"
        ordering = ["created_at"]


class DebitNote(BaseModel):
    debit_note_number = models.CharField(_("Debit Note Number"), max_length=30, unique=True, db_index=True)
    supplier = models.ForeignKey(
        "purchases.Supplier",
        on_delete=models.PROTECT,
        related_name="debit_notes",
        verbose_name=_("Supplier"),
    )
    date = models.DateField(_("Date"), db_index=True)
    note = models.TextField(_("Note"), blank=True)
    status = models.CharField(
        _("Status"),
        max_length=10,
        choices=DEBIT_NOTE_STATUS_CHOICES,
        default="draft",
        db_index=True,
    )
    posted_at = models.DateTimeField(_("Posted At"), null=True, blank=True)
    journal_entry = models.OneToOneField(
        "accounting.JournalEntry",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_debit_note",
        verbose_name=_("Journal Entry"),
    )
    subtotal = models.DecimalField(_("Subtotal"), max_digits=18, decimal_places=2, default=Decimal("0"))
    total_vat = models.DecimalField(_("Total VAT"), max_digits=18, decimal_places=2, default=Decimal("0"))
    total_amount = models.DecimalField(_("Total Amount"), max_digits=18, decimal_places=2, default=Decimal("0"))

    class Meta:
        db_table = "purchase_debit_note"
        ordering = ["-date", "-created_at"]

    def __str__(self) -> str:
        return self.debit_note_number

    def recalculate_totals(self) -> None:
        lines = self.lines.filter(is_deleted=False)
        subtotal = Decimal("0")
        total_vat = Decimal("0")
        total_amount = Decimal("0")
        strategy = get_vat_rounding_strategy()
        for line in lines:
            subtotal += line.subtotal()
            total_vat += line.tax_amount(strategy=strategy)
            total_amount += line.total(strategy=strategy)
        self.subtotal = money(subtotal)
        self.total_vat = money(total_vat) if strategy == "invoice" else money(total_vat)
        self.total_amount = money(total_amount) if strategy == "invoice" else money(total_amount)
        self.save(update_fields=["subtotal", "total_vat", "total_amount", "updated_at"])

    def mark_posted(self, *, user=None) -> None:
        self.status = "posted"
        self.posted_at = timezone.now()
        self.updator = user
        self.save(update_fields=["status", "posted_at", "updator", "updated_at"])


class DebitNoteLine(BaseModel):
    debit_note = models.ForeignKey(
        "purchases.DebitNote",
        on_delete=models.PROTECT,
        related_name="lines",
        verbose_name=_("Debit Note"),
    )
    description = models.CharField(_("Description"), max_length=500)
    account = models.ForeignKey(
        "accounting.Account",
        on_delete=models.PROTECT,
        related_name="purchase_debit_note_lines",
        verbose_name=_("Account"),
    )
    quantity = models.DecimalField(_("Quantity"), max_digits=12, decimal_places=2, default=Decimal("1"))
    unit_price = models.DecimalField(_("Unit Price"), max_digits=18, decimal_places=2, default=Decimal("0"))
    tax_rate = models.ForeignKey(
        "accounting.TaxRate",
        on_delete=models.SET_NULL,
        related_name="purchase_debit_note_lines",
        null=True,
        blank=True,
        verbose_name=_("Tax Rate"),
    )
    discount_percent = models.DecimalField(_("Discount %"), max_digits=6, decimal_places=2, default=Decimal("0"))
    line_order = models.PositiveIntegerField(_("Line Order"), default=0)

    class Meta:
        db_table = "purchase_debit_note_line"
        ordering = ["line_order", "created_at"]

    def subtotal(self) -> Decimal:
        gross = (self.quantity or Decimal("0")) * (self.unit_price or Decimal("0"))
        discount = (gross * (self.discount_percent or Decimal("0"))) / Decimal("100")
        return money(gross - discount)

    def tax_amount(self, *, strategy: str | None = None) -> Decimal:
        if not self.tax_rate:
            return Decimal("0")
        return vat_amount(self.subtotal(), self.tax_rate.rate, strategy=strategy)

    def total(self, *, strategy: str | None = None) -> Decimal:
        strat = (strategy or get_vat_rounding_strategy()).strip().lower()
        vat = self.tax_amount(strategy=strat)
        return money(self.subtotal() + (money(vat) if strat == "invoice" else vat))
