from dataclasses import dataclass

from django.db import IntegrityError

from main.idempotency_lease import reclaim_stale_processing_records
from main.models import IdempotencyRecord, ScheduledJobRun


@dataclass
class AsyncJobResult:
    executed: bool
    message: str
    status: str


class IdempotentJob:
    """
    Reusable base helper for async jobs.
    Prevents replay via idempotency key and supports run-once schedule locking.
    """

    job_scope = "async.job"

    def execute_once(self, *, key: str, details: dict | None = None) -> AsyncJobResult:
        reclaim_stale_processing_records()
        try:
            IdempotencyRecord.objects.create(
                key=key,
                scope=self.job_scope,
                method="JOB",
                path=self.job_scope,
                request_hash=key,
                state="processing",
            )
            return AsyncJobResult(executed=True, message="claimed", status="executed")
        except IntegrityError:
            existing = IdempotencyRecord.objects.filter(
                scope=self.job_scope, method="JOB", path=self.job_scope, key=key, is_deleted=False
            ).first()
            if existing and existing.state == "succeeded":
                return AsyncJobResult(executed=False, message="already_processed", status="already_processed")
            if existing and existing.state == "processing":
                return AsyncJobResult(executed=False, message="in_progress", status="in_progress")
            return AsyncJobResult(executed=False, message="conflict", status="conflict")

    def mark_success(self, *, key: str, response_body: dict | None = None) -> None:
        rec = IdempotencyRecord.objects.filter(
            scope=self.job_scope, method="JOB", path=self.job_scope, key=key, is_deleted=False
        ).first()
        if not rec:
            return
        rec.state = "succeeded"
        rec.response_status = 200
        rec.response_body = response_body or {}
        rec.error_message = ""
        rec.save(update_fields=["state", "response_status", "response_body", "error_message", "updated_at"])

    def mark_failure(self, *, key: str, message: str) -> None:
        rec = IdempotencyRecord.objects.filter(
            scope=self.job_scope, method="JOB", path=self.job_scope, key=key, is_deleted=False
        ).first()
        if not rec:
            return
        rec.state = "failed"
        rec.response_status = 500
        rec.response_body = {"error": "JOB_FAILED", "message": message}
        rec.error_message = message
        rec.save(update_fields=["state", "response_status", "response_body", "error_message", "updated_at"])

    @staticmethod
    def claim_scheduled_run(job_type: str, period_key: str, details: dict | None = None) -> bool:
        try:
            ScheduledJobRun.objects.create(
                job_type=job_type,
                period_key=period_key,
                status="succeeded",
                details=details or {},
            )
            return True
        except IntegrityError:
            return False
