import os
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import status

from main.management.commands.create_groups_and_permissions import IsAdmin

from main.models import ApprovalRequest
from main.approval_policy import get_approval_policy
from main.approval_wiring import APPROVAL_SCOPES_WITH_EXECUTORS


def maker_checker_enabled(scope: str) -> bool:
    global_enabled = str(os.getenv("MAKER_CHECKER_ENABLED", "false")).strip().lower() in {"1", "true", "yes", "on"}
    policy = get_approval_policy(scope)
    if not (global_enabled and policy.requires_approval):
        return False
    # Optional per-scope override: MAKER_CHECKER_SALES_INVOICE_POST=true etc.
    key = "MAKER_CHECKER_" + scope.upper().replace(".", "_")
    override = os.getenv(key)
    if override is None:
        return True
    return str(override).strip().lower() in {"1", "true", "yes", "on"}


def require_admin_approver(user) -> bool:
    return IsAdmin().has_permission(type("Req", (), {"user": user})(), None)  # reuse existing logic


@transaction.atomic
def create_approval_request(*, scope: str, object_type: str, object_id, payload: dict[str, Any], requested_by) -> ApprovalRequest:
    return ApprovalRequest.objects.create(
        scope=scope,
        object_type=object_type,
        object_id=object_id,
        payload=payload or {},
        status="pending",
        requested_by=requested_by if getattr(requested_by, "is_authenticated", False) else None,
        creator=requested_by if getattr(requested_by, "is_authenticated", False) else None,
    )


def execute_approved_action(approval: ApprovalRequest, *, executor_user) -> tuple[int, dict]:
    """
    Executes the approved action. Returns (http_status, response_body).
    """
    from sales.models import Invoice, CustomerCreditNote
    from accounting.accounting_engine import AccountingEngine
    from accounting.models import AccountingPeriod, JournalEntry
    from accounting.services.posting import post_invoice_journal, post_credit_note_journal
    from sales.zatca_services import prepare_zatca_artifacts

    scope = approval.scope

    if scope == "sales.invoice.post":
        invoice = Invoice.objects.filter(pk=approval.object_id, is_deleted=False).select_for_update().first()
        if not invoice:
            return status.HTTP_404_NOT_FOUND, {"error": "NOT_FOUND", "message": "Invoice not found."}
        if invoice.status == "posted":
            return status.HTTP_200_OK, {"message": "Already posted.", "invoice_id": str(invoice.id)}
        je = post_invoice_journal(invoice=invoice, user=executor_user)
        invoice.journal_entry = je
        invoice.status = "posted"
        invoice.posted_at = timezone.now()
        prepare_zatca_artifacts(invoice, is_credit_note=False)
        invoice.updator = executor_user
        invoice.save(
            update_fields=[
                "status",
                "posted_at",
                "qr_code_text",
                "zatca_uuid",
                "zatca_previous_hash",
                "zatca_invoice_hash",
                "zatca_signed_hash",
                "zatca_xml",
                "zatca_canonical_xml",
                "zatca_signature_value",
                "zatca_signed_xml",
                "zatca_certificate",
                "zatca_submission_status",
                "zatca_submission_error",
                "updator",
                "journal_entry",
                "updated_at",
            ]
        )
        return status.HTTP_200_OK, {"message": "Invoice posted.", "invoice_id": str(invoice.id)}

    if scope == "sales.credit_note.post":
        note = CustomerCreditNote.objects.filter(pk=approval.object_id, is_deleted=False).select_for_update().first()
        if not note:
            return status.HTTP_404_NOT_FOUND, {"error": "NOT_FOUND", "message": "Credit note not found."}
        if note.status == "posted":
            return status.HTTP_200_OK, {"message": "Already posted.", "credit_note_id": str(note.id)}
        je = post_credit_note_journal(note=note, user=executor_user)
        note.journal_entry = je
        note.status = "posted"
        note.posted_at = timezone.now()
        prepare_zatca_artifacts(note, is_credit_note=True)
        note.updator = executor_user
        note.save(
            update_fields=[
                "status",
                "posted_at",
                "qr_code_text",
                "zatca_uuid",
                "zatca_previous_hash",
                "zatca_invoice_hash",
                "zatca_signed_hash",
                "zatca_xml",
                "zatca_canonical_xml",
                "zatca_signature_value",
                "zatca_signed_xml",
                "zatca_certificate",
                "zatca_submission_status",
                "zatca_submission_error",
                "updator",
                "journal_entry",
                "updated_at",
            ]
        )
        return status.HTTP_200_OK, {"message": "Credit note posted.", "credit_note_id": str(note.id)}

    if scope == "accounting.period.reopen":
        period = AccountingPeriod.objects.filter(pk=approval.object_id, is_deleted=False).select_for_update().first()
        if not period:
            return status.HTTP_404_NOT_FOUND, {"error": "NOT_FOUND", "message": "Period not found."}
        if not period.is_closed:
            return status.HTTP_422_UNPROCESSABLE_ENTITY, {"error": "NOT_CLOSED", "message": "Period is not closed."}
        reason = (approval.payload or {}).get("reason", "")
        period.is_closed = False
        period.reopened_at = timezone.now()
        period.reopened_by = executor_user
        period.reopen_reason = reason
        period.save(update_fields=["is_closed", "reopened_at", "reopened_by", "reopen_reason", "updated_at"])
        return status.HTTP_200_OK, {"message": "Period reopened.", "period_id": str(period.id)}

    if scope == "accounting.journal_entry.reverse":
        from accounting.exceptions import AccountError

        entry = JournalEntry.objects.filter(pk=approval.object_id, is_deleted=False).select_for_update().first()
        if not entry:
            return status.HTTP_404_NOT_FOUND, {"error": "NOT_FOUND", "message": "Journal entry not found."}
        data = approval.payload or {}
        reversal = entry.create_reversal(
            description=(data.get("description") or ""),
            date=data.get("date") or timezone.now().date(),
        )
        reversal.creator = executor_user
        reversal.save(update_fields=["creator"])
        if data.get("auto_post", False):
            try:
                AccountingEngine.post_journal_entry(reversal)
            except AccountError as exc:
                return status.HTTP_422_UNPROCESSABLE_ENTITY, exc.to_dict()
            except ValueError as exc:
                return status.HTTP_422_UNPROCESSABLE_ENTITY, {"error": "POST_VALIDATION_ERROR", "message": str(exc)}
        return status.HTTP_201_CREATED, {
            "message": "Reversal executed.",
            "reversal_id": str(reversal.id),
            "original_reference": entry.reference,
        }

    if scope == "purchases.bill.post":
        from django.utils.dateparse import parse_date

        from purchases.models import Bill
        from accounting.services.posting import post_bill_journal

        with transaction.atomic():
            bill = Bill.objects.filter(pk=approval.object_id, is_deleted=False).select_for_update().first()
            if not bill:
                return status.HTTP_404_NOT_FOUND, {"error": "NOT_FOUND", "message": "Bill not found."}
            if bill.status == "posted":
                return status.HTTP_200_OK, {"message": "Already posted.", "bill_id": str(bill.id)}
            data = approval.payload or {}
            posting_date = data.get("posting_date")
            if isinstance(posting_date, str):
                posting_date = parse_date(posting_date)
            try:
                je = post_bill_journal(
                    bill=bill,
                    user=executor_user,
                    payable_account_id=data.get("payable_account"),
                    vat_account_id=data.get("vat_account"),
                    posting_date=posting_date,
                    memo=data.get("memo") or "",
                )
            except ValueError as exc:
                return status.HTTP_422_UNPROCESSABLE_ENTITY, {"error": "POST_VALIDATION_ERROR", "message": str(exc)}
            bill.journal_entry = je
            bill.save(update_fields=["journal_entry", "updated_at"])
            bill.mark_posted(user=executor_user)
        return status.HTTP_200_OK, {"message": "Bill posted.", "bill_id": str(bill.id)}

    if scope in {"sales.invoice.zatca.submit", "sales.credit_note.zatca.submit"}:
        from sales.models import CustomerCreditNote, Invoice, ZatcaSubmissionLog
        from sales.serializers import CustomerCreditNoteSerializer, InvoiceSerializer
        from sales.zatca_submit_runner import run_zatca_submit_pipeline

        doc_type = "invoice" if scope == "sales.invoice.zatca.submit" else "credit_note"
        Model = Invoice if doc_type == "invoice" else CustomerCreditNote
        with transaction.atomic():
            doc = Model.objects.select_for_update().filter(pk=approval.object_id, is_deleted=False).first()
            if not doc:
                return status.HTTP_404_NOT_FOUND, {"error": "NOT_FOUND", "message": "Document not found."}
            if doc.status != "posted":
                return status.HTTP_422_UNPROCESSABLE_ENTITY, {
                    "error": "NOT_POSTED",
                    "message": "Only posted documents can be submitted to ZATCA.",
                }
            if AccountingPeriod.is_date_closed(doc.date):
                return status.HTTP_422_UNPROCESSABLE_ENTITY, {
                    "error": "PERIOD_CLOSED",
                    "message": f"Submission not allowed: {doc.date} is in a closed accounting period.",
                }
            submission_type = (approval.payload or {}).get("submission_type")
            if not submission_type:
                return status.HTTP_400_BAD_REQUEST, {"error": "INVALID_PAYLOAD", "message": "submission_type required."}
            idempotency_key = f"approval:{approval.id}"
            existing = ZatcaSubmissionLog.objects.filter(
                document_type=doc_type, idempotency_key=idempotency_key, is_deleted=False
            ).first()
            if existing:
                if doc_type == "invoice":
                    return status.HTTP_200_OK, dict(InvoiceSerializer(doc).data)
                return status.HTTP_200_OK, dict(CustomerCreditNoteSerializer(doc).data)
            log = ZatcaSubmissionLog.objects.create(
                document_type=doc_type,
                document_id=doc.id,
                submission_type=submission_type,
                idempotency_key=idempotency_key,
                status="pending",
                attempt_count=1,
                submitted_at=timezone.now(),
                provider_status="pending",
                creator=executor_user,
            )
        status_code, body = run_zatca_submit_pipeline(
            document=doc,
            document_type=doc_type,
            submission_type=submission_type,
            idempotency_key=idempotency_key,
            user=executor_user,
            log=log,
        )
        return status_code, body

    if scope == "accounting.journal_entry.post":
        from accounting.exceptions import AccountError
        from accounting.journal_serializers import JournalEntryDetailSerializer
        from main.allocation_validator import AllocationValidator

        entry = JournalEntry.objects.filter(pk=approval.object_id, is_deleted=False).first()
        if not entry:
            return status.HTTP_404_NOT_FOUND, {"error": "NOT_FOUND", "message": "Journal entry not found."}
        try:
            AllocationValidator.validate_manual_journal_post_preconditions(entry)
        except ValueError as exc:
            return status.HTTP_422_UNPROCESSABLE_ENTITY, {"error": "POST_VALIDATION_ERROR", "message": str(exc)}
        try:
            AccountingEngine.post_journal_entry(entry)
        except AccountError as exc:
            return status.HTTP_422_UNPROCESSABLE_ENTITY, exc.to_dict()
        except ValueError as exc:
            return status.HTTP_422_UNPROCESSABLE_ENTITY, {"error": "POST_VALIDATION_ERROR", "message": str(exc)}
        return status.HTTP_200_OK, dict(JournalEntryDetailSerializer(entry).data)

    if scope == "purchases.supplier_payment.create":
        from rest_framework.exceptions import ValidationError as DRFValidationError

        from purchases.serializers import SupplierPaymentSerializer
        from purchases.supplier_payment_posting import create_supplier_payment_from_payload

        try:
            payment = create_supplier_payment_from_payload(payload=approval.payload or {}, user=executor_user)
        except DRFValidationError as exc:
            return status.HTTP_400_BAD_REQUEST, {"error": "VALIDATION_ERROR", "details": exc.detail}
        except ValueError as exc:
            return status.HTTP_422_UNPROCESSABLE_ENTITY, {"error": "VALIDATION_ERROR", "message": str(exc)}
        return status.HTTP_201_CREATED, dict(SupplierPaymentSerializer(payment).data)

    if scope == "products.inventory_adjustment.post":
        from products.inventory_posting import InventoryAdjustmentPostAbort, execute_inventory_adjustment_post
        from products.serializers import InventoryAdjustmentSerializer

        try:
            adj = execute_inventory_adjustment_post(adjustment_id=approval.object_id, user=executor_user)
        except InventoryAdjustmentPostAbort as exc:
            return exc.http_status, {"error": exc.code, "message": str(exc)}
        return status.HTTP_200_OK, dict(InventoryAdjustmentSerializer(adj).data)

    if scope == "sales.customer_payment.create":
        from rest_framework.exceptions import ValidationError as DRFValidationError

        from sales.customer_cash_posting import create_customer_payment_from_payload
        from sales.serializers import CustomerPaymentSerializer

        try:
            payment = create_customer_payment_from_payload(payload=approval.payload or {}, user=executor_user)
        except DRFValidationError as exc:
            return status.HTTP_400_BAD_REQUEST, {"error": "VALIDATION_ERROR", "details": exc.detail}
        except ValueError as exc:
            return status.HTTP_422_UNPROCESSABLE_ENTITY, {"error": "VALIDATION_ERROR", "message": str(exc)}
        return status.HTTP_201_CREATED, dict(CustomerPaymentSerializer(payment).data)

    if scope == "sales.customer_payment.update":
        from rest_framework.exceptions import ValidationError as DRFValidationError

        from sales.customer_cash_posting import update_customer_payment_from_payload
        from sales.serializers import CustomerPaymentSerializer

        try:
            payment = update_customer_payment_from_payload(
                payment_id=approval.object_id,
                payload=approval.payload or {},
                user=executor_user,
            )
        except LookupError:
            return status.HTTP_404_NOT_FOUND, {"error": "NOT_FOUND", "message": "Customer payment not found."}
        except DRFValidationError as exc:
            return status.HTTP_400_BAD_REQUEST, {"error": "VALIDATION_ERROR", "details": exc.detail}
        except ValueError as exc:
            msg = str(exc)
            if msg == "Posted payment cannot be edited.":
                return status.HTTP_422_UNPROCESSABLE_ENTITY, {"error": "PAYMENT_POSTED", "message": msg}
            return status.HTTP_422_UNPROCESSABLE_ENTITY, {"error": "VALIDATION_ERROR", "message": msg}
        return status.HTTP_200_OK, dict(CustomerPaymentSerializer(payment).data)

    if scope == "sales.customer_refund.create":
        from rest_framework.exceptions import ValidationError as DRFValidationError

        from sales.customer_cash_posting import create_customer_refund_from_payload
        from sales.serializers import CustomerRefundSerializer

        try:
            refund = create_customer_refund_from_payload(payload=approval.payload or {}, user=executor_user)
        except DRFValidationError as exc:
            return status.HTTP_400_BAD_REQUEST, {"error": "VALIDATION_ERROR", "details": exc.detail}
        except ValueError as exc:
            return status.HTTP_422_UNPROCESSABLE_ENTITY, {"error": "VALIDATION_ERROR", "message": str(exc)}
        return status.HTTP_201_CREATED, dict(CustomerRefundSerializer(refund).data)

    if scope == "sales.customer_refund.update":
        from rest_framework.exceptions import ValidationError as DRFValidationError

        from sales.customer_cash_posting import update_customer_refund_from_payload
        from sales.serializers import CustomerRefundSerializer

        try:
            refund = update_customer_refund_from_payload(
                refund_id=approval.object_id,
                payload=approval.payload or {},
                user=executor_user,
            )
        except LookupError:
            return status.HTTP_404_NOT_FOUND, {"error": "NOT_FOUND", "message": "Customer refund not found."}
        except DRFValidationError as exc:
            return status.HTTP_400_BAD_REQUEST, {"error": "VALIDATION_ERROR", "details": exc.detail}
        except ValueError as exc:
            msg = str(exc)
            if msg == "Posted refund cannot be edited.":
                return status.HTTP_422_UNPROCESSABLE_ENTITY, {"error": "REFUND_POSTED", "message": msg}
            return status.HTTP_422_UNPROCESSABLE_ENTITY, {"error": "VALIDATION_ERROR", "message": msg}
        return status.HTTP_200_OK, dict(CustomerRefundSerializer(refund).data)

    # Unreachable if APPROVAL_SCOPES_WITH_EXECUTORS stays in sync with branches above.
    return status.HTTP_500_INTERNAL_SERVER_ERROR, {"error": "INTERNAL", "message": "Approval routing out of date."}

