from django.db import transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounting.models import AccountingPeriod
from accounting.permissions import CanClosePeriod
from main.approvals import create_approval_request, maker_checker_enabled
from main.idempotency import begin_idempotent, finalize_idempotent_failure, finalize_idempotent_success


class AccountingPeriodListCreateAPI(APIView):
    """
    GET  /accounting/periods/
    POST /accounting/periods/
    """

    permission_classes = [IsAuthenticated, CanClosePeriod]

    def get(self, request):
        qs = AccountingPeriod.objects.filter(is_deleted=False).order_by("-start_date")
        results = []
        for p in qs:
            results.append(
                {
                    "id": str(p.id),
                    "name": p.name,
                    "start_date": str(p.start_date),
                    "end_date": str(p.end_date),
                    "is_closed": p.is_closed,
                    "closed_at": p.closed_at.isoformat() if p.closed_at else None,
                    "closed_by": str(p.closed_by_id) if p.closed_by_id else None,
                    "reopened_at": p.reopened_at.isoformat() if p.reopened_at else None,
                    "reopened_by": str(p.reopened_by_id) if p.reopened_by_id else None,
                    "close_reason": p.close_reason,
                    "reopen_reason": p.reopen_reason,
                }
            )
        return Response({"results": results})

    def post(self, request):
        name = (request.data.get("name") or "").strip()
        start_date = request.data.get("start_date")
        end_date = request.data.get("end_date")
        if not name or not start_date or not end_date:
            return Response(
                {"error": "VALIDATION_ERROR", "message": "name, start_date, end_date are required."},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        period = AccountingPeriod.objects.create(
            name=name,
            start_date=start_date,
            end_date=end_date,
            creator=request.user,
        )
        return Response({"id": str(period.id)}, status=status.HTTP_201_CREATED)


class AccountingPeriodCloseAPI(APIView):
    """
    POST /accounting/periods/<uuid>/close/
    POST /accounting/periods/<uuid>/reopen/
    """

    permission_classes = [IsAuthenticated, CanClosePeriod]

    def post(self, request, pk, action):
        rec, early = begin_idempotent(request, scope=f"accounting.period.{action}")
        if early:
            return early

        period = AccountingPeriod.objects.filter(pk=pk, is_deleted=False).first()
        if not period:
            return finalize_idempotent_failure(rec, error="NOT_FOUND", message="Period not found.", http_status=404)  # type: ignore[arg-type]
        reason = (request.data.get("reason") or "").strip()
        with transaction.atomic():
            if action == "close":
                if period.is_closed:
                    return finalize_idempotent_failure(rec, error="ALREADY_CLOSED", message="Period already closed.", http_status=422)  # type: ignore[arg-type]
                period.is_closed = True
                # explicit timestamp
                from django.utils import timezone
                period.closed_at = timezone.now()
                period.closed_by = request.user
                period.close_reason = reason
                period.save(update_fields=["is_closed", "closed_at", "closed_by", "close_reason", "updated_at"])
                response = Response({"message": "Period closed."})
                finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
                return response
            if action == "reopen":
                if not period.is_closed:
                    return finalize_idempotent_failure(rec, error="NOT_CLOSED", message="Period is not closed.", http_status=422)  # type: ignore[arg-type]

                if maker_checker_enabled("accounting.period.reopen"):
                    approval = create_approval_request(
                        scope="accounting.period.reopen",
                        object_type="accounting.AccountingPeriod",
                        object_id=period.id,
                        payload={"reason": reason},
                        requested_by=request.user,
                    )
                    response = Response(
                        {"message": "Approval required.", "approval_id": str(approval.id), "status": approval.status},
                        status=status.HTTP_202_ACCEPTED,
                    )
                    finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
                    return response

                from django.utils import timezone
                period.is_closed = False
                period.reopened_at = timezone.now()
                period.reopened_by = request.user
                period.reopen_reason = reason
                period.save(update_fields=["is_closed", "reopened_at", "reopened_by", "reopen_reason", "updated_at"])
                response = Response({"message": "Period reopened."})
                finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
                return response
        return finalize_idempotent_failure(rec, error="INVALID_ACTION", message="Invalid action.", http_status=400)  # type: ignore[arg-type]

