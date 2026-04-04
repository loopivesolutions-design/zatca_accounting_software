from django.urls import path

from .views import (
    SupplierListCreateAPI,
    SupplierDetailAPI,
    SupplierChoicesAPI,
    BillListCreateAPI,
    BillDetailAPI,
    BillPostAPI,
    SupplierPaymentChoicesAPI,
    SupplierPaymentListCreateAPI,
    SupplierPaymentDetailAPI,
    SupplierOutstandingBillsAPI,
    DebitNoteListCreateAPI,
    DebitNoteDetailAPI,
    DebitNotePostAPI,
    SupplierRefundChoicesAPI,
    SupplierRefundListCreateAPI,
    SupplierRefundDetailAPI,
    SupplierOutstandingDebitNotesAPI,
)

app_name = "purchases"

urlpatterns = [
    path("suppliers/choices/", SupplierChoicesAPI.as_view(), name="supplier-choices"),
    path("suppliers/", SupplierListCreateAPI.as_view(), name="supplier-list-create"),
    path("suppliers/<uuid:pk>/", SupplierDetailAPI.as_view(), name="supplier-detail"),
    path("bills/", BillListCreateAPI.as_view(), name="bill-list-create"),
    path("bills/<uuid:pk>/", BillDetailAPI.as_view(), name="bill-detail"),
    path("bills/<uuid:pk>/post/", BillPostAPI.as_view(), name="bill-post"),
    path("supplier-payments/choices/", SupplierPaymentChoicesAPI.as_view(), name="supplier-payment-choices"),
    path("supplier-payments/", SupplierPaymentListCreateAPI.as_view(), name="supplier-payment-list-create"),
    path("supplier-payments/<uuid:pk>/", SupplierPaymentDetailAPI.as_view(), name="supplier-payment-detail"),
    path("supplier-payments/outstanding-bills/", SupplierOutstandingBillsAPI.as_view(), name="supplier-outstanding-bills"),
    path("debit-notes/", DebitNoteListCreateAPI.as_view(), name="debit-note-list-create"),
    path("debit-notes/<uuid:pk>/", DebitNoteDetailAPI.as_view(), name="debit-note-detail"),
    path("debit-notes/<uuid:pk>/post/", DebitNotePostAPI.as_view(), name="debit-note-post"),
    path("supplier-refunds/choices/", SupplierRefundChoicesAPI.as_view(), name="supplier-refund-choices"),
    path("supplier-refunds/", SupplierRefundListCreateAPI.as_view(), name="supplier-refund-list-create"),
    path("supplier-refunds/<uuid:pk>/", SupplierRefundDetailAPI.as_view(), name="supplier-refund-detail"),
    path("supplier-refunds/outstanding-debit-notes/", SupplierOutstandingDebitNotesAPI.as_view(), name="supplier-outstanding-debit-notes"),
]
