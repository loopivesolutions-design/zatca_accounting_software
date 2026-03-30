from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import models
from django.db import transaction
from django.utils import timezone

from sales.models import ZatcaOutboxEvent, Invoice, CustomerCreditNote, ZatcaSubmissionLog
from sales.zatca_services import submit_to_zatca, ZatcaValidationError, ZatcaSubmissionTransportError
from accounting.models import AccountingPeriod
from main.async_jobs import IdempotentJob


class Command(BaseCommand):
    help = "Process ZATCA outbox events with retries/backoff."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument("--max-attempts", type=int, default=5)
        parser.add_argument("--alert-stuck-minutes", type=int, default=15)

    def handle(self, *args, **options):
        limit = int(options["limit"])
        max_attempts = int(options["max_attempts"])
        alert_stuck_minutes = int(options["alert_stuck_minutes"])
        now = timezone.now()

        job_guard = IdempotentJob()
        job_guard.job_scope = "sales.zatca.outbox.process"

        processed = 0
        while processed < limit:
            with transaction.atomic():
                # Claim one eligible event atomically; skip rows locked by other workers.
                ev = (
                    ZatcaOutboxEvent.objects.select_for_update(skip_locked=True)
                    .filter(is_deleted=False)
                    .filter(status__in=["pending", "retrying"])
                    .filter(models.Q(next_retry_at__isnull=True) | models.Q(next_retry_at__lte=now))
                    .order_by("created_at")
                    .first()
                )
                if not ev:
                    break
                ev.status = "processing"
                ev.locked_at = timezone.now()
                ev.attempt_count = (ev.attempt_count or 0) + 1
                ev.save(update_fields=["status", "locked_at", "attempt_count", "updated_at"])

            try:
                job_key = f"{ev.document_type}:{ev.document_id}:{ev.submission_type}:{ev.idempotency_key}"
                claim = job_guard.execute_once(key=job_key, details={"event_id": str(ev.id)})
                if not claim.executed:
                    if claim.status == "already_processed":
                        ev.status = "succeeded"
                        ev.last_error = "Deduplicated: already processed by idempotent job guard."
                        ev.next_retry_at = None
                        ev.save(update_fields=["status", "last_error", "next_retry_at", "updated_at"])
                        ZatcaSubmissionLog.objects.filter(
                            document_type=ev.document_type, idempotency_key=ev.idempotency_key, is_deleted=False
                        ).update(
                            status="succeeded",
                            last_error=ev.last_error,
                            next_retry_at=None,
                            updated_at=timezone.now(),
                        )
                    elif claim.status in {"in_progress", "conflict"}:
                        if ev.attempt_count >= max_attempts:
                            ev.status = "failed_final"
                            ev.next_retry_at = None
                        else:
                            backoff_seconds = min(3600, 2 ** min(ev.attempt_count, 10))
                            ev.status = "retrying"
                            ev.next_retry_at = timezone.now() + timedelta(seconds=backoff_seconds)
                        ev.last_error = (
                            "Async idempotency claim unresolved: "
                            f"{claim.status}. Event rescheduled or moved to failed_final."
                        )
                        ev.save(update_fields=["status", "last_error", "next_retry_at", "updated_at"])
                        ZatcaSubmissionLog.objects.filter(
                            document_type=ev.document_type, idempotency_key=ev.idempotency_key, is_deleted=False
                        ).update(
                            status="retrying" if ev.status == "retrying" else "failed_final",
                            last_error=ev.last_error,
                            next_retry_at=ev.next_retry_at,
                            updated_at=timezone.now(),
                        )
                        if (
                            claim.status == "in_progress"
                            and ev.locked_at
                            and timezone.now() - ev.locked_at > timedelta(minutes=alert_stuck_minutes)
                        ):
                            self.stderr.write(
                                self.style.WARNING(
                                    f"Potentially stuck outbox event {ev.id}: "
                                    f"in_progress > {alert_stuck_minutes} minutes."
                                )
                            )
                    else:
                        ev.status = "failed_final"
                        ev.last_error = f"Unexpected async idempotency status: {claim.status}"
                        ev.next_retry_at = None
                        ev.save(update_fields=["status", "last_error", "next_retry_at", "updated_at"])
                        ZatcaSubmissionLog.objects.filter(
                            document_type=ev.document_type, idempotency_key=ev.idempotency_key, is_deleted=False
                        ).update(
                            status="failed_final",
                            last_error=ev.last_error,
                            next_retry_at=None,
                            updated_at=timezone.now(),
                        )
                    processed += 1
                    continue

                doc = None
                if ev.document_type == "invoice":
                    doc = Invoice.objects.filter(pk=ev.document_id, is_deleted=False).first()
                elif ev.document_type == "credit_note":
                    doc = CustomerCreditNote.objects.filter(pk=ev.document_id, is_deleted=False).first()
                if not doc:
                    raise ValueError("Document not found for outbox event.")

                if hasattr(doc, "date") and AccountingPeriod.is_date_closed(doc.date):
                    raise ValueError(f"Submission not allowed: {doc.date} is in a closed accounting period.")

                submit_to_zatca(doc, submission_type=ev.submission_type, idempotency_key=ev.idempotency_key)
                doc.save(
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

                ZatcaSubmissionLog.objects.filter(
                    document_type=ev.document_type, idempotency_key=ev.idempotency_key, is_deleted=False
                ).update(
                    status="succeeded",
                    response_reference=getattr(doc, "zatca_submission_reference", "") or "",
                    updated_at=timezone.now(),
                )

                ev.status = "succeeded"
                ev.next_retry_at = None
                ev.last_error = ""
                ev.save(update_fields=["status", "next_retry_at", "last_error", "updated_at"])
                job_guard.mark_success(key=job_key, response_body={"event_id": str(ev.id), "status": "succeeded"})
            except ZatcaValidationError as exc:
                ev.status = "failed_final"
                ev.last_error = "ZATCA validation failed."
                ev.next_retry_at = None
                ev.save(update_fields=["status", "last_error", "next_retry_at", "updated_at"])
                ZatcaSubmissionLog.objects.filter(
                    document_type=ev.document_type, idempotency_key=ev.idempotency_key, is_deleted=False
                ).update(
                    status="failed_final",
                    last_error="ZATCA validation failed.",
                    updated_at=timezone.now(),
                )
                if "doc" in locals() and doc is not None:
                    from zatca_adapter.services import apply_document_zatca_submission_status

                    try:
                        apply_document_zatca_submission_status(doc, "failed_final")
                        doc.zatca_submission_error = "ZATCA validation failed."
                        doc.save(
                            update_fields=["zatca_submission_status", "zatca_submission_error", "updated_at"]
                        )
                    except Exception:
                        pass
                job_guard.mark_failure(key=job_key, message="ZATCA validation failed.")
            except ZatcaSubmissionTransportError as exc:
                retryable = bool(exc.retryable)
                if retryable and ev.attempt_count < max_attempts:
                    backoff_seconds = min(3600, 2 ** min(ev.attempt_count, 10))
                    ev.status = "retrying"
                    ev.next_retry_at = timezone.now() + timedelta(seconds=backoff_seconds)
                else:
                    ev.status = "failed_final"
                    ev.next_retry_at = None
                ev.last_error = str(exc)
                ev.save(update_fields=["status", "next_retry_at", "last_error", "updated_at"])
                ZatcaSubmissionLog.objects.filter(
                    document_type=ev.document_type, idempotency_key=ev.idempotency_key, is_deleted=False
                ).update(
                    status="retrying" if ev.status == "retrying" else "failed_final",
                    last_error=str(exc),
                    next_retry_at=ev.next_retry_at,
                    provider_status=(
                        str(getattr(doc, "_zatca_provider_status", "") or "")
                        if "doc" in locals() and doc is not None
                        else ""
                    ),
                    provider_request_id=(
                        str(getattr(doc, "_zatca_provider_request_id", "") or "")
                        if "doc" in locals() and doc is not None
                        else ""
                    ),
                    provider_correlation_id=(
                        str(getattr(doc, "_zatca_provider_correlation_id", "") or "")
                        if "doc" in locals() and doc is not None
                        else ""
                    ),
                    response_headers=exc.response_headers or {},
                    updated_at=timezone.now(),
                )
                if "doc" in locals() and doc is not None:
                    from zatca_adapter.services import apply_document_zatca_submission_status

                    try:
                        if ev.status == "retrying":
                            apply_document_zatca_submission_status(doc, "retrying")
                        else:
                            apply_document_zatca_submission_status(doc, "failed_final")
                        doc.zatca_submission_error = str(exc)
                        doc.save(
                            update_fields=["zatca_submission_status", "zatca_submission_error", "updated_at"]
                        )
                    except Exception:
                        pass
                job_guard.mark_failure(key=job_key, message=str(exc))
            except Exception as exc:
                if ev.attempt_count >= max_attempts:
                    ev.status = "failed_final"
                    ev.next_retry_at = None
                else:
                    backoff_seconds = min(3600, 2 ** min(ev.attempt_count, 10))
                    ev.status = "retrying"
                    ev.next_retry_at = timezone.now() + timedelta(seconds=backoff_seconds)
                ev.last_error = str(exc)
                ev.save(update_fields=["status", "next_retry_at", "last_error", "updated_at"])
                ZatcaSubmissionLog.objects.filter(
                    document_type=ev.document_type, idempotency_key=ev.idempotency_key, is_deleted=False
                ).update(
                    status="failed_final" if ev.status == "failed_final" else "retrying",
                    last_error=str(exc),
                    next_retry_at=ev.next_retry_at,
                    updated_at=timezone.now(),
                )
                job_guard.mark_failure(key=job_key, message=str(exc))

            processed += 1

        self.stdout.write(self.style.SUCCESS(f"Processed {processed} outbox event(s)."))
