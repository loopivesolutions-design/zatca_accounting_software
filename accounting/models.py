import re
from decimal import Decimal
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from main.models import BaseModel


CASH_FLOW_TYPE_CHOICES = (
    ("cash", "Cash"),
    ("operating", "Operating"),
    ("investing", "Investing"),
    ("financing", "Financing"),
)

ACCOUNT_TYPE_CHOICES = (
    ("asset", "Asset"),
    ("liability", "Liability"),
    ("equity", "Equity"),
    ("revenue", "Revenue"),
    ("expense", "Expense"),
)

# ZATCA e-invoicing compliance mappings.
# Accounts with a mapping are structurally locked to protect VAT report integrity.
ZATCA_MAPPING_CHOICES = (
    ("vat_output",          "Output VAT (VAT Payable)"),
    ("vat_input",           "Input VAT (VAT Receivable)"),
    ("sales_revenue",       "Sales Revenue"),
    ("accounts_receivable", "Accounts Receivable"),
    ("accounts_payable",    "Accounts Payable"),
    ("retained_earnings",   "Retained Earnings"),
    ("cash_and_bank",       "Cash and Bank"),
)


class Account(BaseModel):
    """
    Chart of Accounts — supports unlimited self-referential hierarchy.
    Root accounts (Assets, Liabilities, Equity, Revenue, Expenses) have parent=None.
    """
    parent = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="children",
        verbose_name=_("Parent Account"),
    )
    name = models.CharField(_("Account Name (EN)"), max_length=255)
    name_ar = models.CharField(_("Account Name (AR)"), max_length=255, blank=True)
    code = models.CharField(_("Code"), max_length=64, unique=True, db_index=True)
    cash_flow_type = models.CharField(
        _("Cash Flow Type"),
        max_length=20,
        choices=CASH_FLOW_TYPE_CHOICES,
        blank=True,
    )
    account_type = models.CharField(
        _("Account Type"),
        max_length=20,
        choices=ACCOUNT_TYPE_CHOICES,
        blank=True,
    )
    account_sub_type = models.CharField(
        _("Account Sub-Type"),
        max_length=100,
        blank=True,
        help_text="Detailed account classification shown in the Account Type column (e.g. Cash and Cash Equivalents, Accounts Payable).",
    )
    zatca_mapping = models.CharField(
        _("ZATCA Mapping"),
        max_length=30,
        choices=ZATCA_MAPPING_CHOICES,
        blank=True,
        db_index=True,
        help_text="Maps this account to a ZATCA e-invoicing category. Structurally locks the account to protect VAT report integrity.",
    )
    enable_payment = models.BooleanField(_("Enable Payment"), default=False)
    show_in_expense_claim = models.BooleanField(_("Show in Expense Claim"), default=False)
    is_locked = models.BooleanField(_("Locked (system account)"), default=False)
    is_archived = models.BooleanField(
        _("Archived"),
        default=False,
        db_index=True,
        help_text="Archived accounts cannot be used in new transactions but remain in historical reports.",
    )

    class Meta:
        db_table = "accounting_account"
        verbose_name = _("account")
        verbose_name_plural = _("accounts")
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def has_transactions(self) -> bool:
        """
        Returns True if any transaction (journal entry, invoice, payment, etc.)
        references this account. Uses dynamic model lookup so it is safe before
        transactional apps are installed.
        Extend validators.TRANSACTION_SOURCES to cover new apps.
        """
        from .validators import AccountValidator
        return AccountValidator.has_transactions(self)

    @property
    def level(self):
        """Depth in the tree: 0 = root, 1 = first child, etc."""
        depth = 0
        node = self
        while node.parent_id is not None:
            depth += 1
            node = node.parent
        return depth

    def get_balance(self) -> Decimal:
        """
        Returns the account's net balance from posted journal entries.
        Positive = net debit balance  (normal for Assets, Expenses)
        Negative = net credit balance (normal for Liabilities, Equity, Revenue)
        """
        from .validators import AccountValidator
        return AccountValidator.get_account_balance(self)

    @property
    def full_path(self):
        """Breadcrumb path: Assets > Current Assets > Cash and Cash Equivalents."""
        parts = [self.name]
        node = self
        while node.parent_id is not None:
            node = node.parent
            parts.insert(0, node.name)
        return " > ".join(parts)


# ──────────────────────────────────────────────────────────────────────────────
# Journal Entries
# Rule 1: Ledger Immutability  — posted entries are permanently read-only
# Rule 2: Sequential Integrity — auto-generated sequential reference, no gaps
# ──────────────────────────────────────────────────────────────────────────────

JOURNAL_STATUS_CHOICES = (
    ("draft",  "Draft"),
    ("posted", "Posted"),
)


class JournalEntry(BaseModel):
    """
    A double-entry journal entry.

    Lifecycle:
      draft  → editable, lines can be added/removed
      posted → immutable; corrections require a new reversal entry

    Reference numbers are auto-generated at post time using a zero-padded
    sequential format (JE-000001, JE-000002, …) within an atomic transaction
    to prevent gaps or duplicates.
    """
    reference = models.CharField(
        _("Reference"),
        max_length=20,
        unique=True,
        blank=True,
        db_index=True,
        help_text="Auto-generated sequential reference (e.g. JE-000001). Assigned on posting.",
    )
    date = models.DateField(_("Entry Date"), db_index=True)
    description = models.TextField(_("Description / Memo"), blank=True)
    status = models.CharField(
        _("Status"),
        max_length=10,
        choices=JOURNAL_STATUS_CHOICES,
        default="draft",
        db_index=True,
    )
    is_reversal = models.BooleanField(
        _("Is Reversal"),
        default=False,
        help_text="True if this entry was system-created to reverse another entry.",
    )
    reversal_of = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        related_name="reversed_by_entry",
        on_delete=models.SET_NULL,
        verbose_name=_("Reversal Of"),
        help_text="Points to the original entry that this entry reverses.",
    )
    posted_at = models.DateTimeField(_("Posted At"), null=True, blank=True)

    class Meta:
        db_table = "accounting_journal_entry"
        verbose_name = _("journal entry")
        verbose_name_plural = _("journal entries")
        ordering = ["-date", "-created_at"]

    def __str__(self):
        ref = self.reference or f"DRAFT-{str(self.pk)[:8]}"
        return f"{ref} | {self.date} | {self.description[:40]}"

    @classmethod
    def _next_reference(cls) -> str:
        """
        Atomically generate the next sequential reference number.
        Uses SELECT FOR UPDATE to prevent concurrent duplicates.
        """
        with transaction.atomic():
            cls.objects.select_for_update().filter(reference__startswith="JE-").exists()
            from django.db.models import Max
            result = cls.objects.aggregate(max_ref=Max("reference"))
            max_ref = result.get("max_ref") or "JE-000000"
            match = re.search(r"JE-(\d+)$", max_ref)
            num = int(match.group(1)) + 1 if match else 1
            return f"JE-{num:06d}"

    def post(self) -> None:
        """
        Post this journal entry:
          1. Validate it is balanced
          2. Assign a sequential reference number
          3. Set status → posted and record posted_at timestamp
        """
        from .validators import JournalEntryValidator
        JournalEntryValidator.validate_can_post(self)
        with transaction.atomic():
            if not self.reference:
                self.reference = self._next_reference()
            self.status = "posted"
            self.posted_at = timezone.now()
            self.save(update_fields=["reference", "status", "posted_at", "updated_at"])

    def create_reversal(self, description: str = "", date=None) -> "JournalEntry":
        """
        Create a reversal journal entry (draft) with all debits/credits swapped.
        The caller must post the reversal separately.
        Returns the new draft reversal entry.
        """
        from .validators import JournalEntryValidator
        JournalEntryValidator.validate_can_reverse(self)

        reversal = JournalEntry.objects.create(
            date=date or timezone.now().date(),
            description=description or f"Reversal of {self.reference or str(self.pk)[:8]}",
            status="draft",
            is_reversal=True,
            reversal_of=self,
            creator=self.creator,
        )
        for line in self.lines.filter(is_deleted=False):
            JournalEntryLine.objects.create(
                journal_entry=reversal,
                account=line.account,
                description=line.description,
                debit=line.credit,    # swap
                credit=line.debit,    # swap
                line_order=line.line_order,
            )
        return reversal


# ──────────────────────────────────────────────────────────────────────────────
# Tax Rates
# ──────────────────────────────────────────────────────────────────────────────

TAX_TYPE_CHOICES = (
    ("sales",          "Sales"),
    ("purchases",      "Purchases"),
    ("reverse_charge", "Reverse Charge"),
    ("out_of_scope",   "Out of Scope"),
)

# ZATCA e-invoice XML VAT category codes (FATOORAH standard)
ZATCA_CATEGORY_CHOICES = (
    ("S", "Standard Rate (S) — 15%"),
    ("Z", "Zero Rate (Z) — 0%"),
    ("E", "Exempt (E) — 0%"),
    ("O", "Out of Scope (O) — 0%"),
)


class TaxRate(BaseModel):
    """
    A tax rate record used across sales invoices, purchase bills, and expense claims.

    ZATCA compliance notes
    ----------------------
    * `zatca_category` maps to the VAT category code in FATOORAH XML:
        S = Standard (15%)  Z = Zero-rate  E = Exempt  O = Out of scope
    * Default (system) tax rates are seeded by `seed_tax_rates` and cannot be deleted.
    * Once a tax rate is used in a transaction, `rate` and `tax_type` are locked
      (enforced via TaxRateValidator — extend TRANSACTION_SOURCES when invoicing is added).
    """

    name = models.CharField(_("Tax Name (EN)"), max_length=255)
    name_ar = models.CharField(_("Tax Name (AR)"), max_length=255, blank=True)
    tax_type = models.CharField(
        _("Tax Type"),
        max_length=20,
        choices=TAX_TYPE_CHOICES,
        db_index=True,
    )
    rate = models.DecimalField(
        _("Tax Rate (%)"),
        max_digits=5,
        decimal_places=2,
        help_text="Percentage value, e.g. 15 for 15%.",
    )
    description = models.TextField(_("Description"), blank=True)
    zatca_category = models.CharField(
        _("ZATCA Category"),
        max_length=1,
        choices=ZATCA_CATEGORY_CHOICES,
        blank=True,
        db_index=True,
        help_text="ZATCA e-invoice XML VAT category code (S / Z / E / O).",
    )
    is_default = models.BooleanField(
        _("System Default"),
        default=False,
        help_text="System default tax rates cannot be deleted.",
    )
    is_active = models.BooleanField(_("Is Active"), default=True, db_index=True)

    class Meta:
        db_table = "accounting_tax_rate"
        verbose_name = _("tax rate")
        verbose_name_plural = _("tax rates")
        ordering = ["tax_type", "rate", "name"]

    def __str__(self):
        return f"{self.name} ({self.rate}%)"

    def has_transactions(self) -> bool:
        """True if this tax rate has been applied to any posted transaction."""
        from django.apps import apps
        sources = [
            # ("invoicing", "InvoiceLine", "tax_rate_id"),   # add with invoicing app
            # ("purchase",  "BillLine",    "tax_rate_id"),   # add with purchase app
        ]
        for app_label, model_name, field in sources:
            try:
                Model = apps.get_model(app_label, model_name)
                if Model.objects.filter(**{field: self.pk}).exists():
                    return True
            except LookupError:
                pass
        return False


class JournalEntryLine(BaseModel):
    """
    A single debit or credit line within a journal entry.
    Each line must have exactly one of debit or credit > 0 (not both).
    """
    journal_entry = models.ForeignKey(
        JournalEntry,
        on_delete=models.PROTECT,
        related_name="lines",
        verbose_name=_("Journal Entry"),
    )
    account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        related_name="journal_lines",
        verbose_name=_("Account"),
    )
    description = models.TextField(_("Line Description"), blank=True)
    debit = models.DecimalField(
        _("Debit"), max_digits=18, decimal_places=2, default=Decimal("0")
    )
    credit = models.DecimalField(
        _("Credit"), max_digits=18, decimal_places=2, default=Decimal("0")
    )
    line_order = models.PositiveIntegerField(_("Line Order"), default=0)

    class Meta:
        db_table = "accounting_journal_entry_line"
        verbose_name = _("journal entry line")
        verbose_name_plural = _("journal entry lines")
        ordering = ["line_order", "created_at"]

    def __str__(self):
        side = f"Dr {self.debit}" if self.debit else f"Cr {self.credit}"
        return f"{self.account.code} | {side}"
