"""
Lease reclaim for IdempotencyRecord rows stuck in `processing` (worker crash, etc.).
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from main.models import IdempotencyRecord

DEFAULT_STALE_PROCESSING_SECONDS = 300


def reclaim_stale_processing_records(*, max_age_seconds: int = DEFAULT_STALE_PROCESSING_SECONDS) -> int:
    """
    Mark stale `processing` records as failed so clients may retry with a new Idempotency-Key
    (or the same key after delete path in begin_idempotent).
    Returns number of rows updated.
    """
    if max_age_seconds < 1:
        return 0
    cutoff = timezone.now() - timedelta(seconds=max_age_seconds)
    qs = IdempotencyRecord.objects.filter(
        state="processing",
        is_deleted=False,
        updated_at__lt=cutoff,
    )
    body = {
        "error": "STALE_PROCESSING",
        "message": "Request lease expired while processing; retry with a new Idempotency-Key.",
    }
    updated = qs.update(
        state="failed",
        response_status=503,
        response_body=body,
        error_message="STALE_PROCESSING_LEASE_EXPIRED",
    )
    return updated


def idempotency_record_is_stale(rec: IdempotencyRecord, *, max_age_seconds: int = DEFAULT_STALE_PROCESSING_SECONDS) -> bool:
    if rec.state != "processing":
        return False
    cutoff = timezone.now() - timedelta(seconds=max_age_seconds)
    return rec.updated_at < cutoff
