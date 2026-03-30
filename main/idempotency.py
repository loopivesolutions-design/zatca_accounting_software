import hashlib
import json

from django.db import IntegrityError
from rest_framework import status
from rest_framework.response import Response

from main.idempotency_lease import idempotency_record_is_stale
from main.models import IdempotencyRecord


def _stable_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def compute_request_hash(request) -> str:
    payload = {
        "method": request.method,
        "path": request.path,
        "query": dict(getattr(request, "query_params", {})),
        "body": getattr(request, "data", None),
        "user_id": str(getattr(getattr(request, "user", None), "id", "")),
    }
    raw = _stable_json(payload).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def get_idempotency_key(request) -> str:
    # Support both common header names to reduce client mistakes.
    return (
        (request.headers.get("Idempotency-Key") or "").strip()
        or (request.headers.get("X-Idempotency-Key") or "").strip()
    )


def begin_idempotent(request, *, scope: str, _stale_retry: int = 0) -> tuple[IdempotencyRecord | None, Response | None]:
    """
    Creates an IdempotencyRecord in 'processing' state.

    Returns:
      (record, None) to continue processing
      (None, response) to short-circuit with stored/in-progress/conflict response
    """
    key = get_idempotency_key(request)
    if not key:
        return None, Response(
            {"error": "IDEMPOTENCY_KEY_REQUIRED", "message": "Idempotency-Key header is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    request_hash = compute_request_hash(request)

    # Semantic de-duplication: if the exact same request payload was already
    # completed successfully for this scope, return the stored response even if
    # the client accidentally sends a new idempotency key (timeout unknown state).
    existing_same_success = (
        IdempotencyRecord.objects.filter(
            scope=scope,
            method=request.method,
            path=request.path,
            request_hash=request_hash,
            state="succeeded",
            is_deleted=False,
        )
        .order_by("-created_at")
        .first()
    )
    if existing_same_success:
        return None, Response(
            existing_same_success.response_body or {},
            status=existing_same_success.response_status or status.HTTP_200_OK,
        )

    try:
        rec = IdempotencyRecord.objects.create(
            key=key,
            scope=scope,
            method=request.method,
            path=request.path,
            request_hash=request_hash,
            state="processing",
            creator=getattr(request, "user", None) if getattr(request, "user", None) and request.user.is_authenticated else None,
        )
        return rec, None
    except IntegrityError:
        existing = IdempotencyRecord.objects.filter(
            key=key, scope=scope, method=request.method, path=request.path, is_deleted=False
        ).first()
        if not existing:
            # Extremely rare race; treat as retryable
            return None, Response(
                {"error": "IDEMPOTENCY_RACE", "message": "Unable to resolve idempotency key. Retry."},
                status=status.HTTP_409_CONFLICT,
            )
        if existing.request_hash != request_hash:
            return None, Response(
                {
                    "error": "IDEMPOTENCY_CONFLICT",
                    "message": "Idempotency-Key was already used for a different request.",
                },
                status=status.HTTP_409_CONFLICT,
            )
        if existing.state == "processing":
            if idempotency_record_is_stale(existing):
                existing.delete()
                try:
                    rec = IdempotencyRecord.objects.create(
                        key=key,
                        scope=scope,
                        method=request.method,
                        path=request.path,
                        request_hash=request_hash,
                        state="processing",
                        creator=getattr(request, "user", None)
                        if getattr(request, "user", None) and request.user.is_authenticated
                        else None,
                    )
                    return rec, None
                except IntegrityError:
                    if _stale_retry >= 2:
                        return None, Response(
                            {"error": "IDEMPOTENCY_RACE", "message": "Unable to resolve idempotency key. Retry."},
                            status=status.HTTP_409_CONFLICT,
                        )
                    return begin_idempotent(request, scope=scope, _stale_retry=_stale_retry + 1)
            return None, Response(
                {"error": "IDEMPOTENCY_IN_PROGRESS", "message": "Request with this Idempotency-Key is still processing."},
                status=status.HTTP_409_CONFLICT,
            )
        return None, Response(existing.response_body or {}, status=existing.response_status or status.HTTP_200_OK)


def finalize_idempotent_success(rec: IdempotencyRecord, response: Response) -> None:
    # Ensure JSON-serializable
    body = json.loads(_stable_json(getattr(response, "data", {}) or {}))
    rec.state = "succeeded"
    rec.response_status = int(getattr(response, "status_code", 200))
    rec.response_body = body
    rec.error_message = ""
    rec.save(update_fields=["state", "response_status", "response_body", "error_message", "updated_at"])


def finalize_idempotent_failure(rec: IdempotencyRecord, *, error: str, message: str, http_status: int) -> Response:
    rec.state = "failed"
    rec.response_status = int(http_status)
    rec.response_body = {"error": error, "message": message}
    rec.error_message = message
    rec.save(update_fields=["state", "response_status", "response_body", "error_message", "updated_at"])
    return Response(rec.response_body, status=http_status)

