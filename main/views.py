from rest_framework.viewsets import ModelViewSet
from rest_framework.response import Response
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404

from main.management.commands.create_groups_and_permissions import IsAdmin
from main.models import Country, State, CompanySettings, ApprovalRequest
from main.approvals import require_admin_approver, execute_approved_action
from main.serializers import CountrySerializer, RoleSerializer, StateSerializer, CompanySettingsSerializer
from user.models import Role, CustomUser
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django.db import transaction

from main.idempotency import begin_idempotent, finalize_idempotent_failure, finalize_idempotent_success


class DashboardAPIView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        total_users = CustomUser.objects.filter(is_deleted=False).count()
        return Response(
            {"total_users": total_users},
            status=200,
        )


class RoleAPI(ModelViewSet):
    serializer_class = RoleSerializer
    permission_classes = [IsAdmin]

    def get_queryset(self):
        return Role.objects.all().order_by("-id")

    def list(self, request):
        queryset = self.get_queryset()
        serializer = RoleSerializer(queryset, many=True)
        return Response(serializer.data)

    def retrieve(self, request, pk=None):
        role = get_object_or_404(Role, id=pk)
        serializer = RoleSerializer(role)
        return Response(serializer.data)


class CountryAPI(ModelViewSet):
    serializer_class = CountrySerializer

    def get_queryset(self):
        return Country.objects.all().order_by("-id")

    def list(self, request):
        queryset = self.get_queryset()
        serializer = CountrySerializer(queryset, many=True)
        return Response(serializer.data)

    def retrieve(self, request, pk=None):
        country = get_object_or_404(Country, id=pk)
        serializer = CountrySerializer(country)
        return Response(serializer.data)


class StateAPI(ModelViewSet):
    serializer_class = StateSerializer

    def get_queryset(self, country_id=None):
        queryset = State.objects.all()
        if country_id:
            queryset = queryset.filter(country_id=country_id)
        return queryset

    def list(self, request, country_id=None):
        country_id = country_id or request.resolver_match.kwargs.get("country_id")
        if not country_id:
            return Response({"error": "country_id is required."}, status=400)
        queryset = self.get_queryset(country_id=country_id)
        serializer = self.serializer_class(queryset, many=True)
        return Response(serializer.data)


class CompanySettingsAPI(APIView):
    """
    GET  /main/company-settings/  — get organization settings (singleton)
    PATCH /main/company-settings/ — update organization settings (multipart supported for logo)
    """

    permission_classes = [IsAdmin]

    def _get_obj(self):
        obj = CompanySettings.objects.first()
        if obj:
            return obj
        return CompanySettings.objects.create()

    def get(self, request):
        obj = self._get_obj()
        return Response(CompanySettingsSerializer(obj).data)

    def patch(self, request):
        obj = self._get_obj()
        serializer = CompanySettingsSerializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        obj = serializer.save()
        return Response(CompanySettingsSerializer(obj).data)

    def post(self, request):
        """
        POST acts as an upsert for frontend simplicity:
        - if singleton row exists → update it
        - else → create it
        Supports multipart/form-data for logo upload.
        """
        obj = CompanySettings.objects.first()
        if obj is None:
            serializer = CompanySettingsSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            obj = serializer.save()
            return Response(CompanySettingsSerializer(obj).data, status=201)

        serializer = CompanySettingsSerializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        obj = serializer.save()
        return Response(CompanySettingsSerializer(obj).data, status=200)


class ApprovalRequestListAPI(APIView):
    """
    GET  /main/approvals/?status=pending|approved|denied|executed|failed&scope=<scope>
    """

    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        qs = ApprovalRequest.objects.filter(is_deleted=False).select_related("requested_by", "approved_by")
        status_param = (request.query_params.get("status") or "").strip()
        if status_param:
            qs = qs.filter(status=status_param)
        scope = (request.query_params.get("scope") or "").strip()
        if scope:
            qs = qs.filter(scope=scope)
        results = []
        for a in qs.order_by("-created_at")[:500]:
            results.append(
                {
                    "id": str(a.id),
                    "scope": a.scope,
                    "object_type": a.object_type,
                    "object_id": str(a.object_id),
                    "status": a.status,
                    "requested_by": str(a.requested_by_id) if a.requested_by_id else None,
                    "approved_by": str(a.approved_by_id) if a.approved_by_id else None,
                    "approved_at": a.approved_at.isoformat() if a.approved_at else None,
                    "executed_at": a.executed_at.isoformat() if a.executed_at else None,
                    "result_status": a.result_status,
                    "result_body": a.result_body,
                    "error_message": a.error_message,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                }
            )
        return Response({"results": results})


class ApprovalRequestApproveAPI(APIView):
    """
    POST /main/approvals/<uuid>/approve/
    """

    permission_classes = [IsAuthenticated, IsAdmin]

    def post(self, request, pk):
        rec, early = begin_idempotent(request, scope="main.approval.approve")
        if early:
            return early

        try:
            with transaction.atomic():
                approval = ApprovalRequest.objects.select_for_update().filter(pk=pk, is_deleted=False).first()
                if not approval:
                    return finalize_idempotent_failure(
                        rec, error="NOT_FOUND", message="Approval request not found.", http_status=404  # type: ignore[arg-type]
                    )
                if approval.status != "pending":
                    return finalize_idempotent_failure(
                        rec, error="NOT_PENDING", message="Approval request is not pending.", http_status=422  # type: ignore[arg-type]
                    )
                if approval.requested_by_id and approval.requested_by_id == request.user.id:
                    return finalize_idempotent_failure(
                        rec,  # type: ignore[arg-type]
                        error="SELF_APPROVAL_FORBIDDEN",
                        message="Requester cannot approve their own request.",
                        http_status=403,
                    )
                if not require_admin_approver(request.user):
                    return finalize_idempotent_failure(
                        rec, error="FORBIDDEN", message="Not allowed to approve.", http_status=403  # type: ignore[arg-type]
                    )

                approval.status = "approved"
                approval.approved_by = request.user
                approval.approved_at = timezone.now()
                approval.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])

                http_status, body = execute_approved_action(approval, executor_user=request.user)
                approval.status = "executed" if 200 <= http_status < 300 else "failed"
                approval.executed_at = timezone.now()
                approval.result_status = http_status
                approval.result_body = body
                approval.error_message = "" if approval.status == "executed" else (
                    body.get("message") if isinstance(body, dict) else "Execution failed."
                )
                approval.save(
                    update_fields=["status", "executed_at", "result_status", "result_body", "error_message", "updated_at"]
                )

                response = Response(body, status=http_status)
                finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
                return response
        except Exception as exc:
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error="EXECUTION_FAILED",
                message=str(exc),
                http_status=500,
            )


class ApprovalRequestDenyAPI(APIView):
    """
    POST /main/approvals/<uuid>/deny/
    Body: {"reason": "..."}
    """

    permission_classes = [IsAuthenticated, IsAdmin]

    def post(self, request, pk):
        rec, early = begin_idempotent(request, scope="main.approval.deny")
        if early:
            return early

        with transaction.atomic():
            approval = ApprovalRequest.objects.select_for_update().filter(pk=pk, is_deleted=False).first()
            if not approval:
                return finalize_idempotent_failure(
                    rec, error="NOT_FOUND", message="Approval request not found.", http_status=404  # type: ignore[arg-type]
                )
            if approval.status != "pending":
                return finalize_idempotent_failure(
                    rec, error="NOT_PENDING", message="Approval request is not pending.", http_status=422  # type: ignore[arg-type]
                )
            if approval.requested_by_id and approval.requested_by_id == request.user.id:
                return finalize_idempotent_failure(
                    rec,  # type: ignore[arg-type]
                    error="SELF_DENY_FORBIDDEN",
                    message="Requester cannot deny their own request.",
                    http_status=403,
                )

            approval.status = "denied"
            approval.approved_by = request.user
            approval.approved_at = timezone.now()
            approval.result_status = 403
            approval.result_body = {"error": "DENIED", "message": (request.data.get("reason") or "Denied.")}
            approval.error_message = approval.result_body["message"]
            approval.save(
                update_fields=[
                    "status",
                    "approved_by",
                    "approved_at",
                    "result_status",
                    "result_body",
                    "error_message",
                    "updated_at",
                ]
            )

        response = Response(approval.result_body, status=403)
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response
