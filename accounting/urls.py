from django.urls import path
from .views import (
    AccountListCreateAPI,
    AccountDetailAPI,
    AccountArchiveAPI,
    AccountEditMetadataAPI,
    AccountTreeAPI,
    AccountChildrenAPI,
    AccountChoicesAPI,
    AccountExportAPI,
)
from .journal_views import (
    JournalEntryListCreateAPI,
    JournalEntryDetailAPI,
    JournalEntryPostAPI,
    JournalEntryReverseAPI,
)
from .tax_views import (
    TaxRateListCreateAPI,
    TaxRateDetailAPI,
    TaxRateChoicesAPI,
)

app_name = "accounting"

urlpatterns = [
    # ── Chart of Accounts ────────────────────────────────────────────────────

    # Flat list + create
    path("chart-of-accounts/", AccountListCreateAPI.as_view(), name="account-list-create"),

    # Tree view (full nested or root-only)
    path("chart-of-accounts/tree/", AccountTreeAPI.as_view(), name="account-tree"),

    # Choices for dropdowns (cash_flow_type, account_type, zatca_mapping, parent accounts)
    path("chart-of-accounts/choices/", AccountChoicesAPI.as_view(), name="account-choices"),

    # Export CSV
    path("chart-of-accounts/export/", AccountExportAPI.as_view(), name="account-export"),

    # Single account — retrieve / update / delete
    path("chart-of-accounts/<uuid:pk>/", AccountDetailAPI.as_view(), name="account-detail"),

    # Edit metadata — which fields are editable (for frontend form control)
    path("chart-of-accounts/<uuid:pk>/edit-metadata/", AccountEditMetadataAPI.as_view(), name="account-edit-metadata"),

    # Direct children (lazy tree loading)
    path("chart-of-accounts/<uuid:pk>/children/", AccountChildrenAPI.as_view(), name="account-children"),

    # Archive / Unarchive
    path(
        "chart-of-accounts/<uuid:pk>/archive/",
        AccountArchiveAPI.as_view(),
        {"action": "archive"},
        name="account-archive",
    ),
    path(
        "chart-of-accounts/<uuid:pk>/unarchive/",
        AccountArchiveAPI.as_view(),
        {"action": "unarchive"},
        name="account-unarchive",
    ),

    # ── Journal Entries ───────────────────────────────────────────────────────
    # Rule 1: Ledger Immutability  — posted entries are permanently read-only
    # Rule 2: Sequential Integrity — sequential refs, reversal instead of deletion

    # Paginated list + create draft
    path("journal-entries/", JournalEntryListCreateAPI.as_view(), name="journal-entry-list-create"),

    # Retrieve / update draft / delete draft
    path("journal-entries/<uuid:pk>/", JournalEntryDetailAPI.as_view(), name="journal-entry-detail"),

    # Post a draft entry → immutable (assigns sequential reference number)
    path("journal-entries/<uuid:pk>/post/", JournalEntryPostAPI.as_view(), name="journal-entry-post"),

    # Reverse a posted entry (creates correcting reversal entry)
    path("journal-entries/<uuid:pk>/reverse/", JournalEntryReverseAPI.as_view(), name="journal-entry-reverse"),

    # ── Tax Rates ─────────────────────────────────────────────────────────────

    # Dropdown choices (tax_type, zatca_category)
    path("tax-rates/choices/", TaxRateChoicesAPI.as_view(), name="tax-rate-choices"),

    # Paginated list + create
    path("tax-rates/", TaxRateListCreateAPI.as_view(), name="tax-rate-list-create"),

    # Retrieve / update / delete
    path("tax-rates/<uuid:pk>/", TaxRateDetailAPI.as_view(), name="tax-rate-detail"),
]
