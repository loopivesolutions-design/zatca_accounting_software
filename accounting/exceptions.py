"""
Structured exception classes for Chart of Accounts validation.
Every exception maps directly to a JSON error response with a machine-readable code.
"""


class AccountError(Exception):
    """Base class for all account validation errors."""

    def __init__(self, code: str, message: str, **extra):
        self.code = code
        self.message = message
        self.extra = extra
        super().__init__(message)

    def to_dict(self) -> dict:
        return {"error": self.code, "message": self.message, **self.extra}


class AccountLockedException(AccountError):
    """Raised when trying to modify/delete a system-locked account."""

    def __init__(self, account_name: str):
        super().__init__(
            code="ACCOUNT_LOCKED",
            message=f"'{account_name}' is a system account and cannot be modified or deleted.",
            suggestion="System accounts can only be renamed (name / name_ar).",
        )


class AccountHasChildrenException(AccountError):
    """Raised when trying to delete an account that still has child accounts."""

    def __init__(self, account_name: str, child_count: int):
        super().__init__(
            code="ACCOUNT_HAS_CHILDREN",
            message=(
                f"'{account_name}' has {child_count} child account(s) and cannot be deleted."
            ),
            suggestion="Delete or reassign all child accounts first.",
            child_count=child_count,
        )


class AccountHasTransactionsException(AccountError):
    """Raised when trying to delete an account that has posted transactions."""

    def __init__(self, account_name: str, transaction_count: int):
        super().__init__(
            code="ACCOUNT_HAS_TRANSACTIONS",
            message=(
                f"'{account_name}' has {transaction_count} posted transaction(s) "
                f"and cannot be deleted."
            ),
            suggestion="Archive this account instead of deleting it.",
            transaction_count=transaction_count,
        )


class FieldLockedAfterTransactionException(AccountError):
    """
    Raised when trying to change a structural field after transactions exist.
    Locked fields: code, account_type, parent, cash_flow_type.
    """

    def __init__(self, locked_fields: list, editable_fields: list):
        super().__init__(
            code="FIELD_LOCKED_AFTER_TRANSACTION",
            message=(
                "These fields cannot be changed after transactions have been "
                "posted to this account."
            ),
            locked_fields=locked_fields,
            editable_fields=editable_fields,
        )


class ZATCAMappingViolationException(AccountError):
    """
    Raised when trying to change a structural field on a ZATCA-mapped account.
    Modifying these accounts would invalidate VAT reports and ZATCA XML generation.
    """

    def __init__(self, account_name: str, zatca_mapping_display: str, locked_fields: list):
        super().__init__(
            code="ZATCA_MAPPING_VIOLATION",
            message=(
                f"'{account_name}' is mapped to ZATCA reporting category "
                f"'{zatca_mapping_display}'. Changing these fields would invalidate "
                f"VAT reports and ZATCA XML generation."
            ),
            locked_fields=locked_fields,
            zatca_mapping=zatca_mapping_display,
            suggestion=(
                "Only the account name can be changed on ZATCA-mapped accounts. "
                "Contact your tax consultant before modifying these accounts."
            ),
        )


class AccountArchivedException(AccountError):
    """Raised when trying to use an archived account in a new transaction."""

    def __init__(self, account_name: str):
        super().__init__(
            code="ACCOUNT_ARCHIVED",
            message=f"'{account_name}' is archived and cannot be used in new transactions.",
            suggestion="Unarchive the account first if you need to post transactions to it.",
        )


class RootArchiveException(AccountError):
    """Raised when trying to archive a root-level system account."""

    def __init__(self, account_name: str):
        super().__init__(
            code="CANNOT_ARCHIVE_ROOT",
            message=f"'{account_name}' is a root system account and cannot be archived.",
            suggestion=(
                "Archive individual child accounts instead of the root category."
            ),
        )


class AccountHasBalanceException(AccountError):
    """
    Raised when trying to change account_type when the account has a non-zero balance.
    Changing account type with an existing balance would corrupt the balance sheet.
    """

    def __init__(self, account_name: str, balance: float):
        direction = "debit" if balance > 0 else "credit"
        super().__init__(
            code="ACCOUNT_HAS_BALANCE",
            message=(
                f"'{account_name}' has a non-zero {direction} balance ({balance:+.2f}) "
                f"and its account type cannot be changed. "
                f"Changing the type would corrupt financial statements."
            ),
            balance=balance,
            balance_direction=direction,
            suggestion=(
                "Zero out the account balance via a journal entry before changing the account type."
            ),
        )


# ── Journal Entry Exceptions ──────────────────────────────────────────────────


class JournalEntryPostedException(AccountError):
    """
    Raised when trying to edit or delete a posted journal entry.
    Rule: Posted entries are immutable — corrections require reversal entries.
    """

    def __init__(self, reference: str):
        super().__init__(
            code="JOURNAL_ENTRY_POSTED",
            message=(
                f"Journal entry '{reference}' is posted and cannot be modified or deleted. "
                f"Posted entries are immutable to preserve the audit trail."
            ),
            reference=reference,
            suggestion=(
                "Use the /reverse/ endpoint to create a correcting reversal entry, "
                "then post a corrected journal entry."
            ),
        )


class JournalEntryAlreadyReversedException(AccountError):
    """Raised when trying to reverse an entry that has already been reversed."""

    def __init__(self, reference: str):
        super().__init__(
            code="JOURNAL_ENTRY_ALREADY_REVERSED",
            message=f"Journal entry '{reference}' has already been reversed.",
            reference=reference,
        )


class JournalEntryNotBalancedException(AccountError):
    """
    Raised when total debits ≠ total credits on a journal entry.
    Double-entry accounting requires all entries to be balanced.
    """

    def __init__(self, total_debits, total_credits):
        diff = abs(float(total_debits) - float(total_credits))
        super().__init__(
            code="JOURNAL_ENTRY_NOT_BALANCED",
            message=(
                f"Journal entry is not balanced. "
                f"Total debits ({float(total_debits):.2f}) must equal "
                f"total credits ({float(total_credits):.2f}). "
                f"Difference: {diff:.2f}"
            ),
            total_debits=float(total_debits),
            total_credits=float(total_credits),
            difference=diff,
        )


class JournalEntryInsufficientLinesException(AccountError):
    """Raised when a journal entry has fewer than 2 lines."""

    def __init__(self, line_count: int):
        super().__init__(
            code="JOURNAL_ENTRY_INSUFFICIENT_LINES",
            message=(
                f"Journal entry has only {line_count} line(s). "
                f"A minimum of 2 lines is required for a valid double-entry."
            ),
            line_count=line_count,
        )
