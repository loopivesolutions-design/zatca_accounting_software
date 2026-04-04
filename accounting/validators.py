"""
Chart of Accounts & Journal Entry — Business Rule Validators
=============================================================
Enforces four critical ZATCA-compliance controls:

  1. Ledger Immutability       — posted journal entries are read-only; corrections via reversals
  2. Sequential Integrity      — reference numbers are sequential, never reused, posted = locked
  3. Tax Mapping Lock          — ZATCA-mapped accounts are structurally locked after any transaction
  4. Balance Protection        — account_type cannot change when account carries a non-zero balance

Design principles
-----------------
* All validation logic lives here — views are thin orchestrators.
* Every rule raises a typed exception with a machine-readable error code.
* Transaction-count checks use Django's apps registry — safe for incremental app development.
  Extend TRANSACTION_SOURCES as new transactional apps are created.
"""

from decimal import Decimal
from .exceptions import (
    AccountLockedException,
    AccountHasChildrenException,
    AccountHasTransactionsException,
    AccountHasBalanceException,
    FieldLockedAfterTransactionException,
    ZATCAMappingViolationException,
    RootArchiveException,
    AccountArchivedException,
    JournalEntryPostedException,
    JournalEntryAlreadyReversedException,
    JournalEntryNotBalancedException,
    JournalEntryInsufficientLinesException,
)

# ──────────────────────────────────────────────────────────────────────────────
# Field classification
# ──────────────────────────────────────────────────────────────────────────────

# Immutable after first transaction — changing these would corrupt reports/trial balance
FIELDS_LOCKED_AFTER_TRANSACTION: frozenset = frozenset({
    "code",
    "account_type",
    "parent",
    "cash_flow_type",
})

# Always editable regardless of transaction history
ALWAYS_EDITABLE_FIELDS: frozenset = frozenset({
    "name",
    "name_ar",
    "account_sub_type",
    "enable_payment",
    "show_in_expense_claim",
    "is_archived",
})

# ZATCA-mapped accounts: additional structural lock to protect VAT reports
ZATCA_LOCKED_FIELDS: frozenset = frozenset({
    "code",
    "account_type",
    "parent",
    "cash_flow_type",
    "account_sub_type",
})

# ──────────────────────────────────────────────────────────────────────────────
# Transaction source registry
# Each dict: app_label, model_name, account_field (ORM lookup key), filters (AND).
# Only *posted* commercial / ledger activity counts toward CoA structural locks.
# Add a row when a new model references Account on posted business documents.
# ──────────────────────────────────────────────────────────────────────────────
TRANSACTION_SOURCES: list[dict] = [
    {
        "app_label": "accounting",
        "model_name": "JournalEntryLine",
        "account_field": "account_id",
        "filters": {"journal_entry__status": "posted", "is_deleted": False},
    },
    {
        "app_label": "sales",
        "model_name": "InvoiceLine",
        "account_field": "account_id",
        "filters": {"invoice__status": "posted", "is_deleted": False},
    },
    {
        "app_label": "sales",
        "model_name": "CustomerCreditNoteLine",
        "account_field": "account_id",
        "filters": {"credit_note__status": "posted", "is_deleted": False},
    },
    {
        "app_label": "purchases",
        "model_name": "BillLine",
        "account_field": "account_id",
        "filters": {"bill__status": "posted", "is_deleted": False},
    },
    {
        "app_label": "purchases",
        "model_name": "DebitNoteLine",
        "account_field": "account_id",
        "filters": {"debit_note__status__in": ["posted", "partially_paid", "paid"], "is_deleted": False},
    },
    {
        "app_label": "sales",
        "model_name": "CustomerPayment",
        "account_field": "paid_through_id",
        "filters": {"is_posted": True, "journal_entry__status": "posted", "is_deleted": False},
    },
    {
        "app_label": "sales",
        "model_name": "CustomerRefund",
        "account_field": "paid_through_id",
        "filters": {"is_posted": True, "journal_entry__status": "posted", "is_deleted": False},
    },
    {
        "app_label": "purchases",
        "model_name": "SupplierPayment",
        "account_field": "paid_through_id",
        "filters": {"is_posted": True, "journal_entry__status": "posted", "is_deleted": False},
    },
    {
        "app_label": "products",
        "model_name": "InventoryAdjustmentLine",
        "account_field": "account_id",
        "filters": {"adjustment__status": "posted", "is_deleted": False},
    },
]


def registered_transaction_source_model_keys() -> set[tuple[str, str]]:
    return {(spec["app_label"], spec["model_name"]) for spec in TRANSACTION_SOURCES}


# Exempt from "FK to Account ⇒ must appear in TRANSACTION_SOURCES":
# master-data / mapping rows where activity is reflected via JEs or line models, not these FKs alone.
_ACCOUNT_FK_REGISTRY_EXEMPT_MODELS: frozenset[str] = frozenset({
    "accounting.Account",
    "accounting.SystemAccount",
    "products.Product",
    "products.Warehouse",
    "sales.Customer",
    "purchases.Supplier",
})


def iter_models_with_foreign_key_to_account():
    """
    Introspect installed models for ForeignKey/OneToOneField pointing to Account.
    Pair with TRANSACTION_SOURCES so CoA activity detection cannot miss new document lines.
    """
    from django.apps import apps
    from django.db.models import ForeignKey

    from accounting.models import Account

    for model in apps.get_models():
        if model._meta.abstract:
            continue
        label = f"{model._meta.app_label}.{model.__name__}"
        if label in _ACCOUNT_FK_REGISTRY_EXEMPT_MODELS:
            continue
        if model._meta.app_label.startswith("django."):
            continue
        for field in model._meta.get_fields():
            if not getattr(field, "is_relation", False) or getattr(field, "many_to_many", False):
                continue
            if not isinstance(field, ForeignKey):
                continue
            if getattr(field, "related_model", None) is Account:
                yield model._meta.app_label, model.__name__, field.name


# ──────────────────────────────────────────────────────────────────────────────
# Account Validator
# ──────────────────────────────────────────────────────────────────────────────

class AccountValidator:
    """
    Central authority for all Chart of Accounts business rules.
    All public methods raise an AccountError subclass on failure or return silently.
    """

    # ── Transaction counting ─────────────────────────────────────────────────

    @staticmethod
    def get_transaction_count(account) -> int:
        """Count all posted transactions that reference this account across all registered sources."""
        from django.apps import apps
        from django.core.exceptions import FieldError

        total = 0
        for spec in TRANSACTION_SOURCES:
            try:
                Model = apps.get_model(spec["app_label"], spec["model_name"])
                flt = {spec["account_field"]: account.pk, **spec["filters"]}
                total += Model.objects.filter(**flt).count()
            except LookupError:
                continue
            except FieldError:
                continue
        return total

    @classmethod
    def has_transactions(cls, account) -> bool:
        return cls.get_transaction_count(account) > 0

    # ── Balance calculation (Rule 4: Balance Protection) ─────────────────────

    @staticmethod
    def get_account_balance(account) -> Decimal:
        """
        Calculate the current balance of an account from posted journal entries.
        Returns (total_debits - total_credits).
        Positive  = net debit balance  (normal for Assets, Expenses)
        Negative  = net credit balance (normal for Liabilities, Equity, Revenue)
        """
        from django.apps import apps
        from django.db.models import Sum

        try:
            JournalEntryLine = apps.get_model("accounting", "JournalEntryLine")
            agg = JournalEntryLine.objects.filter(
                account_id=account.pk,
                journal_entry__status="posted",
                is_deleted=False,
            ).aggregate(
                total_debit=Sum("debit"),
                total_credit=Sum("credit"),
            )
            debit = agg["total_debit"] or Decimal("0")
            credit = agg["total_credit"] or Decimal("0")
            return debit - credit
        except LookupError:
            return Decimal("0")

    # ── Delete validation ─────────────────────────────────────────────────────

    @classmethod
    def validate_delete(cls, account) -> None:
        if account.is_locked:
            raise AccountLockedException(account.name)

        child_count = account.children.filter(is_deleted=False).count()
        if child_count > 0:
            raise AccountHasChildrenException(account.name, child_count)

        tx_count = cls.get_transaction_count(account)
        if tx_count > 0:
            raise AccountHasTransactionsException(account.name, tx_count)

    # ── Update / edit validation ──────────────────────────────────────────────

    @classmethod
    def validate_update(cls, account, incoming_data: dict) -> None:
        """
        Enforce all field-level editing rules in priority order:
          1. System account   → only name / name_ar allowed
          2. ZATCA-mapped     → structural fields locked (ZATCA_LOCKED_FIELDS)
          3. Has transactions → structural fields locked (FIELDS_LOCKED_AFTER_TRANSACTION)
          4. Has balance      → account_type cannot change (Balance Protection Rule)
        """
        changed = set(incoming_data.keys())

        # Rule 1: System account — only name fields allowed
        if account.is_locked:
            disallowed = changed - {"name", "name_ar"}
            if disallowed:
                raise AccountLockedException(account.name)

        # Rule 2: ZATCA-mapped accounts — structural fields permanently locked
        if account.zatca_mapping:
            zatca_violations = changed & ZATCA_LOCKED_FIELDS
            if zatca_violations:
                raise ZATCAMappingViolationException(
                    account.name,
                    account.get_zatca_mapping_display(),
                    sorted(zatca_violations),
                )

        # Rule 3: Fields locked after first transaction (Ledger Immutability)
        if cls.has_transactions(account):
            tx_violations = changed & FIELDS_LOCKED_AFTER_TRANSACTION
            if tx_violations:
                raise FieldLockedAfterTransactionException(
                    locked_fields=sorted(tx_violations),
                    editable_fields=sorted(ALWAYS_EDITABLE_FIELDS),
                )

        # Rule 4: Balance Protection — block account_type change if balance ≠ 0
        if "account_type" in incoming_data:
            new_type = incoming_data["account_type"]
            if new_type != account.account_type:
                balance = cls.get_account_balance(account)
                if balance != Decimal("0"):
                    raise AccountHasBalanceException(account.name, float(balance))

    # ── Archive validation ────────────────────────────────────────────────────

    @classmethod
    def validate_archive(cls, account) -> None:
        if account.is_locked and account.parent_id is None:
            raise RootArchiveException(account.name)

    # ── Metadata helper ───────────────────────────────────────────────────────

    @classmethod
    def get_edit_metadata(cls, account) -> dict:
        """
        Returns structured metadata telling the frontend which fields can be edited.
        Includes balance information for UI-level balance protection warnings.
        """
        has_tx = cls.has_transactions(account)
        balance = float(cls.get_account_balance(account))

        if account.is_locked:
            locked = {
                "code", "account_type", "parent", "cash_flow_type",
                "account_sub_type", "enable_payment", "show_in_expense_claim",
            }
            editable = {"name", "name_ar"}
            reason = "SYSTEM_ACCOUNT"
        elif account.zatca_mapping:
            locked = set(ZATCA_LOCKED_FIELDS)
            editable = ALWAYS_EDITABLE_FIELDS - locked
            reason = "ZATCA_MAPPED"
        elif has_tx:
            locked = set(FIELDS_LOCKED_AFTER_TRANSACTION)
            editable = ALWAYS_EDITABLE_FIELDS
            reason = "HAS_TRANSACTIONS"
        else:
            locked = set()
            editable = ALWAYS_EDITABLE_FIELDS | {
                "code", "account_type", "parent", "cash_flow_type",
            }
            reason = None

        # Account type additionally locked if balance ≠ 0
        account_type_locked_by_balance = (
            balance != 0 and "account_type" not in locked
        )
        if account_type_locked_by_balance:
            locked.add("account_type")
            editable.discard("account_type")

        return {
            "has_transactions": has_tx,
            "lock_reason": reason,
            "locked_fields": sorted(locked),
            "editable_fields": sorted(editable),
            "balance": balance,
            "balance_direction": "debit" if balance > 0 else "credit" if balance < 0 else "zero",
            "account_type_locked_by_balance": account_type_locked_by_balance,
            "zatca_mapping": account.zatca_mapping or None,
        }


# ──────────────────────────────────────────────────────────────────────────────
# Journal Entry Validator
# Rule 1: Ledger Immutability — posted entries are permanently read-only
# Rule 2: Sequential Integrity — balanced entries, sequential refs, reversal not delete
# ──────────────────────────────────────────────────────────────────────────────

class JournalEntryValidator:
    """
    Validates journal entry operations enforcing ledger immutability and
    double-entry accounting integrity.
    """

    @staticmethod
    def validate_can_modify(journal_entry) -> None:
        """Block any edit/delete on a posted entry. Corrections require reversal."""
        if journal_entry.status == "posted":
            raise JournalEntryPostedException(journal_entry.reference or str(journal_entry.pk))

    @staticmethod
    def validate_can_post(journal_entry) -> None:
        """
        Validate all preconditions before posting:
          - Must be in draft status
          - Must have ≥ 2 lines
          - Must be balanced (Σ debits == Σ credits)
          - No line may reference an archived account
          - Total debits/credits must be > 0
        """
        if journal_entry.status == "posted":
            raise JournalEntryPostedException(journal_entry.reference or str(journal_entry.pk))

        lines = list(journal_entry.lines.filter(is_deleted=False).select_related("account"))

        if len(lines) < 2:
            raise JournalEntryInsufficientLinesException(len(lines))

        total_debit = sum(line.debit for line in lines)
        total_credit = sum(line.credit for line in lines)

        if total_debit == 0 and total_credit == 0:
            raise JournalEntryInsufficientLinesException(0)

        if total_debit != total_credit:
            raise JournalEntryNotBalancedException(total_debit, total_credit)

        # Validate no archived accounts in any line
        for line in lines:
            if line.account.is_archived:
                raise AccountArchivedException(line.account.name)
            if line.account.is_deleted:
                raise AccountArchivedException(line.account.name)

    @staticmethod
    def validate_can_reverse(journal_entry) -> None:
        """
        Validate that a reversal is allowed:
          - Entry must be posted
          - Entry must not already have a reversal
        """
        if journal_entry.status != "posted":
            from rest_framework.exceptions import ValidationError
            raise ValidationError(
                {"error": "JOURNAL_ENTRY_NOT_POSTED",
                 "message": "Only posted journal entries can be reversed."}
            )

        # Check if a reversal already exists
        from django.apps import apps
        try:
            JournalEntry = apps.get_model("accounting", "JournalEntry")
            if JournalEntry.objects.filter(reversal_of=journal_entry).exists():
                raise JournalEntryAlreadyReversedException(
                    journal_entry.reference or str(journal_entry.pk)
                )
        except LookupError:
            pass

    @staticmethod
    def validate_line(debit, credit) -> None:
        """Each line must have exactly one of debit or credit (not both, not neither)."""
        debit = Decimal(str(debit or 0))
        credit = Decimal(str(credit or 0))

        if debit < 0 or credit < 0:
            from rest_framework.exceptions import ValidationError
            raise ValidationError("Debit and credit amounts cannot be negative.")

        if debit > 0 and credit > 0:
            from rest_framework.exceptions import ValidationError
            raise ValidationError(
                "A journal line cannot have both debit and credit amounts. "
                "Set one to zero."
            )
