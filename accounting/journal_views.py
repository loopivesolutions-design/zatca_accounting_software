"""
Journal Entry API views
=======================
Enforces all four ZATCA compliance rules:

  Rule 1 — Ledger Immutability   : posted entries cannot be edited or deleted
  Rule 2 — Sequential Integrity  : references are auto-sequential; no deletion of posted entries
  Rule 3 — Tax Mapping Lock      : enforced in AccountValidator (account edits)
  Rule 4 — Balance Protection    : enforced in AccountValidator (account type changes)

Endpoints
---------
  GET    /journal-entries/                  — paginated list, filter by status/date
  POST   /journal-entries/                  — create draft entry
  GET    /journal-entries/<uuid>/           — retrieve with full line detail
  PATCH  /journal-entries/<uuid>/           — update draft (blocked if posted)
  DELETE /journal-entries/<uuid>/           — delete draft (blocked if posted)
  POST   /journal-entries/<uuid>/post/      — post draft → immutable
  POST   /journal-entries/<uuid>/reverse/   — create reversal of a posted entry
"""

from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from main.idempotency import begin_idempotent, finalize_idempotent_failure, finalize_idempotent_success
from .accounting_engine import AccountingEngine
from .exceptions import AccountError
from .models import JournalEntry
from .journal_serializers import (
    JournalEntryListSerializer,
    JournalEntrySerializer,
    JournalEntryDetailSerializer,
    JournalEntryReverseSerializer,
)
from .validators import JournalEntryValidator
from main.allocation_validator import AllocationValidator
from main.approvals import create_approval_request, maker_checker_enabled


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

class JournalEntryPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 200


def _je_error_response(exc: AccountError):
    return Response(exc.to_dict(), status=status.HTTP_422_UNPROCESSABLE_ENTITY)


def _get_entry(pk, user=None) -> JournalEntry | None:
    try:
        return JournalEntry.objects.select_related("reversal_of").get(
            pk=pk, is_deleted=False
        )
    except JournalEntry.DoesNotExist:
        return None


# ──────────────────────────────────────────────────────────────────────────────
# List + Create
# ──────────────────────────────────────────────────────────────────────────────

class JournalEntryListCreateAPI(APIView):
    """
    GET  — paginated journal entry list
           Query params:
             ?status=draft|posted        filter by status
             ?date_from=YYYY-MM-DD       entries on or after this date
             ?date_to=YYYY-MM-DD         entries on or before this date
             ?search=text                match reference or description
             ?include_reversals=false    exclude reversal entries (default: include)
    POST — create a new draft journal entry
    """
    permission_classes = [IsAuthenticated]
    pagination_class = JournalEntryPagination

    def get(self, request):
        qs = JournalEntry.objects.filter(is_deleted=False).prefetch_related("lines")

        # Status filter
        status_param = request.query_params.get("status")
        if status_param in ("draft", "posted"):
            qs = qs.filter(status=status_param)

        # Date range
        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")
        if date_from:
            qs = qs.filter(date__gte=date_from)
        if date_to:
            qs = qs.filter(date__lte=date_to)

        # Exclude reversals (optional)
        if request.query_params.get("include_reversals", "true").lower() == "false":
            qs = qs.filter(is_reversal=False)

        # Search
        search = request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(reference__icontains=search) | Q(description__icontains=search)
            )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request)
        serializer = JournalEntryListSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        rec, early = begin_idempotent(request, scope="accounting.journal_entry.create")
        if early:
            return early

        serializer = JournalEntrySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = serializer.save(creator=request.user)
        out = JournalEntryDetailSerializer(entry)
        response = Response(out.data, status=status.HTTP_201_CREATED)
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response


# ──────────────────────────────────────────────────────────────────────────────
# Retrieve + Update + Delete (draft only)
# ──────────────────────────────────────────────────────────────────────────────

class JournalEntryDetailAPI(APIView):
    """
    GET    — full detail with line-level account info
    PATCH  — update header/lines (BLOCKED if posted — Rule 1: Ledger Immutability)
    DELETE — soft-delete (BLOCKED if posted — Rule 2: Sequential Integrity)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        entry = _get_entry(pk)
        if not entry:
            return Response(
                {"error": "NOT_FOUND", "message": "Journal entry not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(JournalEntryDetailSerializer(entry).data)

    def patch(self, request, pk):
        entry = _get_entry(pk)
        if not entry:
            return Response(
                {"error": "NOT_FOUND", "message": "Journal entry not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            # Rule 1: Ledger Immutability
            JournalEntryValidator.validate_can_modify(entry)
        except AccountError as exc:
            return _je_error_response(exc)

        serializer = JournalEntrySerializer(
            entry, data=request.data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        entry = serializer.save(updator=request.user)
        return Response(JournalEntryDetailSerializer(entry).data)

    def delete(self, request, pk):
        entry = _get_entry(pk)
        if not entry:
            return Response(
                {"error": "NOT_FOUND", "message": "Journal entry not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            # Rule 1 + 2: posted entries cannot be deleted
            JournalEntryValidator.validate_can_modify(entry)
        except AccountError as exc:
            return _je_error_response(exc)

        entry.is_deleted = True
        entry.save(update_fields=["is_deleted", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


# ──────────────────────────────────────────────────────────────────────────────
# Post action
# ──────────────────────────────────────────────────────────────────────────────

class JournalEntryPostAPI(APIView):
    """
    POST /journal-entries/<uuid>/post/

    Transitions a draft entry to posted (immutable).

    Validations performed:
      - Entry must be in draft status
      - Must have at least 2 lines
      - Total debits must equal total credits (balanced entry)
      - No line may reference an archived account

    On success:
      - Assigns a sequential reference number (JE-000001, …)
      - Sets status = posted and records posted_at timestamp
      - The entry is now permanently read-only (Rule 1: Ledger Immutability)
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        rec, early = begin_idempotent(request, scope="accounting.journal_entry.post")
        if early:
            return early

        entry = _get_entry(pk)
        if not entry:
            return finalize_idempotent_failure(rec, error="NOT_FOUND", message="Journal entry not found.", http_status=status.HTTP_404_NOT_FOUND)  # type: ignore[arg-type]
        try:
            AllocationValidator.validate_manual_journal_post_preconditions(entry)
        except ValueError as exc:
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error="POST_VALIDATION_ERROR",
                message=str(exc),
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        if maker_checker_enabled("accounting.journal_entry.post"):
            approval = create_approval_request(
                scope="accounting.journal_entry.post",
                object_type="accounting.JournalEntry",
                object_id=entry.id,
                payload={},
                requested_by=request.user,
            )
            response = Response(
                {"message": "Approval required.", "approval_id": str(approval.id), "status": approval.status},
                status=status.HTTP_202_ACCEPTED,
            )
            finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
            return response

        try:
            AccountingEngine.post_journal_entry(entry)
        except AccountError as exc:
            resp = _je_error_response(exc)
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error=resp.data.get("error", "POST_VALIDATION_ERROR") if isinstance(resp.data, dict) else "POST_VALIDATION_ERROR",
                message=resp.data.get("message", "Unable to post journal entry.") if isinstance(resp.data, dict) else "Unable to post journal entry.",
                http_status=resp.status_code,
            )
        except ValueError as exc:
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error="POST_VALIDATION_ERROR",
                message=str(exc),
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        response = Response(
            JournalEntryDetailSerializer(entry).data,
            status=status.HTTP_200_OK,
        )
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response


# ──────────────────────────────────────────────────────────────────────────────
# Reverse action
# ──────────────────────────────────────────────────────────────────────────────

class JournalEntryReverseAPI(APIView):
    """
    POST /journal-entries/<uuid>/reverse/

    Creates a reversal entry for a posted journal entry.
    This is the correct mechanism for correcting posted entries — never editing them.

    Request body (all optional):
      {
        "description": "Reversal of JE-000042 — incorrect account used",
        "date": "2026-03-10",
        "auto_post": true
      }

    Behaviour:
      - Creates a new draft journal entry with debits/credits swapped
      - Sets reversal_of = original entry
      - If auto_post=true, immediately posts the reversal
      - The original entry is unchanged (immutable)

    Validations:
      - Original entry must be posted
      - Original entry must not already have a reversal
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        rec, early = begin_idempotent(request, scope="accounting.journal_entry.reverse")
        if early:
            return early

        entry = _get_entry(pk)
        if not entry:
            return finalize_idempotent_failure(rec, error="NOT_FOUND", message="Journal entry not found.", http_status=status.HTTP_404_NOT_FOUND)  # type: ignore[arg-type]

        serializer = JournalEntryReverseSerializer(
            data=request.data,
            context={"original_date": entry.date},
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if maker_checker_enabled("accounting.journal_entry.reverse"):
            approval = create_approval_request(
                scope="accounting.journal_entry.reverse",
                object_type="accounting.JournalEntry",
                object_id=entry.id,
                payload=data,
                requested_by=request.user,
            )
            response = Response(
                {"message": "Approval required.", "approval_id": str(approval.id), "status": approval.status},
                status=status.HTTP_202_ACCEPTED,
            )
            finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
            return response

        try:
            reversal = entry.create_reversal(
                description=data.get("description", ""),
                date=data.get("date") or timezone.now().date(),
            )
            reversal.creator = request.user
            reversal.save(update_fields=["creator"])

            if data.get("auto_post", False):
                AccountingEngine.post_journal_entry(reversal)

        except ValueError as exc:
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error="REVERSE_VALIDATION_ERROR",
                message=str(exc),
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        except AccountError as exc:
            resp = _je_error_response(exc)
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error=resp.data.get("error", "REVERSE_VALIDATION_ERROR") if isinstance(resp.data, dict) else "REVERSE_VALIDATION_ERROR",
                message=resp.data.get("message", "Unable to reverse journal entry.") if isinstance(resp.data, dict) else "Unable to reverse journal entry.",
                http_status=resp.status_code,
            )

        response = Response(
            {
                "message": (
                    "Reversal entry posted successfully."
                    if reversal.status == "posted"
                    else "Reversal entry created as draft. POST to /post/ to finalize."
                ),
                "reversal": JournalEntryDetailSerializer(reversal).data,
                "original_reference": entry.reference,
            },
            status=status.HTTP_201_CREATED,
        )
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response
