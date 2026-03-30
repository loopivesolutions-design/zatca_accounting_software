"""
Shared ZATCA submission path after a ZatcaSubmissionLog row exists (sync simulation or outbox queue).
"""

from __future__ import annotations

from typing import Any

from django.conf import settings as django_settings
from django.utils import timezone
from rest_framework import status

from .models import CustomerCreditNote, Invoice, ZatcaOutboxEvent
from zatca_adapter.zatca_actor_context import zatca_actor_scope

from .zatca_services import ZatcaSubmissionTransportError, ZatcaValidationError, submit_to_zatca


def run_zatca_submit_pipeline(
    *,
    document: Invoice | CustomerCreditNote,
    document_type: str,
    submission_type: str,
    idempotency_key: str,
    user,
    log,
) -> tuple[int, dict[str, Any]]:
    simulation_mode = bool(getattr(django_settings, "ZATCA_SIMULATION_MODE", False))
    if not simulation_mode:
        ZatcaOutboxEvent.objects.create(
            document_type=document_type,
            document_id=document.id,
            submission_type=submission_type,
            idempotency_key=idempotency_key,
            status="pending",
            attempt_count=0,
            creator=user,
        )
        return status.HTTP_202_ACCEPTED, {
            "message": "Submission queued.",
            "status": "submitted",
            "idempotency_key": idempotency_key,
        }

    try:
        with zatca_actor_scope(user):
            submit_to_zatca(document, submission_type=submission_type, idempotency_key=idempotency_key)
    except ZatcaValidationError as exc:
        document.zatca_submission_status = "failed_final"
        document.zatca_submission_error = "ZATCA validation failed."
        document.save(update_fields=["zatca_submission_status", "zatca_submission_error", "updated_at"])
        log.status = "failed_final"
        log.last_error = "ZATCA validation failed."
        log.provider_status = "validation_failed"
        log.save(update_fields=["status", "last_error", "provider_status", "updated_at"])
        return status.HTTP_422_UNPROCESSABLE_ENTITY, {
            "error": "ZATCA_VALIDATION_ERROR",
            "message": "ZATCA validation failed.",
            "details": exc.errors,
        }
    except ZatcaSubmissionTransportError as exc:
        document.zatca_submission_status = "failed_final"
        document.zatca_submission_error = str(exc)
        document.save(update_fields=["zatca_submission_status", "zatca_submission_error", "updated_at"])
        log.status = "retrying" if exc.retryable else "failed_final"
        log.last_error = str(exc)
        log.provider_request_id = str(getattr(document, "_zatca_provider_request_id", "") or "")
        log.provider_correlation_id = str(getattr(document, "_zatca_provider_correlation_id", "") or "")
        log.provider_status = str(getattr(document, "_zatca_provider_status", "") or "")
        log.response_headers = exc.response_headers or {}
        log.next_retry_at = timezone.now() if exc.retryable else None
        log.save(
            update_fields=[
                "status",
                "last_error",
                "provider_request_id",
                "provider_correlation_id",
                "provider_status",
                "response_headers",
                "next_retry_at",
                "updated_at",
            ]
        )
        return status.HTTP_503_SERVICE_UNAVAILABLE, {"error": "ZATCA_SUBMISSION_FAILED", "message": str(exc)}
    except ValueError as exc:
        document.zatca_submission_status = "failed_final"
        document.zatca_submission_error = str(exc)
        document.save(update_fields=["zatca_submission_status", "zatca_submission_error", "updated_at"])
        log.status = "failed_final"
        log.last_error = str(exc)
        log.provider_status = "failed"
        log.save(update_fields=["status", "last_error", "provider_status", "updated_at"])
        return status.HTTP_503_SERVICE_UNAVAILABLE, {"error": "ZATCA_SUBMISSION_FAILED", "message": str(exc)}

    log.status = "succeeded"
    log.response_reference = document.zatca_submission_reference or ""
    log.provider_request_id = str(getattr(document, "_zatca_provider_request_id", "") or "")
    log.provider_correlation_id = str(getattr(document, "_zatca_provider_correlation_id", "") or "")
    log.provider_status = str(getattr(document, "_zatca_provider_status", "") or "")
    log.save(
        update_fields=[
            "status",
            "response_reference",
            "provider_request_id",
            "provider_correlation_id",
            "provider_status",
            "updated_at",
        ]
    )

    document.save(
        update_fields=[
            "zatca_submission_type",
            "zatca_submitted_at",
            "zatca_submission_status",
            "zatca_submission_error",
            "zatca_submission_reference",
            "zatca_cleared_at",
            "updated_at",
        ]
    )

    from .serializers import CustomerCreditNoteSerializer, InvoiceSerializer

    if document_type == "invoice":
        return status.HTTP_200_OK, dict(InvoiceSerializer(document).data)
    return status.HTTP_200_OK, dict(CustomerCreditNoteSerializer(document).data)
