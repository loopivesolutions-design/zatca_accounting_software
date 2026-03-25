import csv
from django.http import HttpResponse
from django.db.models import Q
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from main.pagination import CustomPagination
from main.management.commands.create_groups_and_permissions import IsAdmin
from .models import Account
from .serializers import AccountFlatSerializer, AccountTreeSerializer, AccountChoicesSerializer
from .validators import AccountValidator
from .exceptions import AccountError


def _account_error_response(exc: AccountError) -> Response:
    """Convert any AccountError subclass into a structured 400/403 JSON response."""
    return Response(exc.to_dict(), status=status.HTTP_400_BAD_REQUEST)


class AccountListCreateAPI(APIView):
    """
    GET  /accounting/chart-of-accounts/         — paginated flat list
    POST /accounting/chart-of-accounts/         — create a new account

    Query params (GET):
      ?search=<string>          — filter by code / name / name_ar
      ?include_archived=true    — include archived accounts (default: excluded)
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        include_archived = request.query_params.get("include_archived", "false").lower() == "true"
        qs = Account.objects.filter(is_deleted=False).select_related("parent")

        if not include_archived:
            qs = qs.filter(is_archived=False)

        search = request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(name_ar__icontains=search)
                | Q(code__icontains=search)
            )

        paginator = CustomPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = AccountFlatSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        serializer = AccountFlatSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(creator=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class AccountDetailAPI(APIView):
    """
    GET    /accounting/chart-of-accounts/<uuid>/  — retrieve
    PATCH  /accounting/chart-of-accounts/<uuid>/  — partial update (with locking rules)
    DELETE /accounting/chart-of-accounts/<uuid>/  — soft delete (with deletion rules)
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def _get_account(self, pk):
        try:
            return Account.objects.select_related("parent").get(pk=pk, is_deleted=False)
        except Account.DoesNotExist:
            return None

    def get(self, request, pk):
        account = self._get_account(pk)
        if not account:
            return Response(
                {"error": "NOT_FOUND", "message": "Account not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(AccountFlatSerializer(account).data)

    def patch(self, request, pk):
        account = self._get_account(pk)
        if not account:
            return Response(
                {"error": "NOT_FOUND", "message": "Account not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Run business-rule validation before touching the DB
        try:
            AccountValidator.validate_update(account, request.data)
        except AccountError as exc:
            return _account_error_response(exc)

        serializer = AccountFlatSerializer(account, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(updator=request.user)
        return Response(serializer.data)

    def delete(self, request, pk):
        account = self._get_account(pk)
        if not account:
            return Response(
                {"error": "NOT_FOUND", "message": "Account not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            AccountValidator.validate_delete(account)
        except AccountError as exc:
            return _account_error_response(exc)

        account.is_deleted = True
        account.updator = request.user
        account.save(update_fields=["is_deleted", "updator", "updated_at"])
        return Response({"message": "Account deleted successfully."}, status=status.HTTP_200_OK)


class AccountArchiveAPI(APIView):
    """
    POST /accounting/chart-of-accounts/<uuid>/archive/    — archive account
    POST /accounting/chart-of-accounts/<uuid>/unarchive/  — restore account

    Archiving is the safe alternative to deletion when transactions exist.
    Archived accounts:
      - Cannot be used in new transactions
      - Remain visible in historical reports
      - Remain linked to all past transactions
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def _get_account(self, pk):
        try:
            return Account.objects.get(pk=pk, is_deleted=False)
        except Account.DoesNotExist:
            return None

    def post(self, request, pk, action):
        account = self._get_account(pk)
        if not account:
            return Response(
                {"error": "NOT_FOUND", "message": "Account not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if action == "archive":
            try:
                AccountValidator.validate_archive(account)
            except AccountError as exc:
                return _account_error_response(exc)

            if account.is_archived:
                return Response(
                    {"error": "ALREADY_ARCHIVED", "message": "Account is already archived."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            account.is_archived = True
            account.updator = request.user
            account.save(update_fields=["is_archived", "updator", "updated_at"])
            return Response({
                "message": f"'{account.name}' has been archived.",
                "id": str(account.id),
                "is_archived": True,
            })

        else:  # unarchive
            if not account.is_archived:
                return Response(
                    {"error": "NOT_ARCHIVED", "message": "Account is not archived."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            account.is_archived = False
            account.updator = request.user
            account.save(update_fields=["is_archived", "updator", "updated_at"])
            return Response({
                "message": f"'{account.name}' has been restored.",
                "id": str(account.id),
                "is_archived": False,
            })


class AccountEditMetadataAPI(APIView):
    """
    GET /accounting/chart-of-accounts/<uuid>/edit-metadata/

    Returns which fields can/cannot be edited for a given account.
    The frontend uses this to enable/disable form fields before showing
    the Edit Account modal.

    Response:
    {
        "has_transactions": bool,
        "lock_reason": "SYSTEM_ACCOUNT" | "ZATCA_MAPPED" | "HAS_TRANSACTIONS" | null,
        "locked_fields": [...],
        "editable_fields": [...]
    }
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request, pk):
        try:
            account = Account.objects.get(pk=pk, is_deleted=False)
        except Account.DoesNotExist:
            return Response(
                {"error": "NOT_FOUND", "message": "Account not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(AccountValidator.get_edit_metadata(account))


class AccountTreeAPI(APIView):
    """
    GET /accounting/chart-of-accounts/tree/
    Returns the full nested tree (root accounts with recursive children).

    Query params:
      ?root_only=true        — returns only root-level accounts (no recursion)
      ?include_archived=true — include archived accounts (default: excluded)
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        root_only = request.query_params.get("root_only", "false").lower() == "true"
        include_archived = request.query_params.get("include_archived", "false").lower() == "true"

        roots = Account.objects.filter(parent__isnull=True, is_deleted=False)
        if not include_archived:
            roots = roots.filter(is_archived=False)
        roots = roots.order_by("code")

        ctx = {"include_archived": include_archived}

        if root_only:
            return Response(AccountFlatSerializer(roots, many=True).data)

        return Response(AccountTreeSerializer(roots, many=True, context=ctx).data)


class AccountChildrenAPI(APIView):
    """
    GET /accounting/chart-of-accounts/<uuid>/children/
    Returns direct children of an account (one level only — for lazy tree loading).
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request, pk):
        try:
            parent = Account.objects.get(pk=pk, is_deleted=False)
        except Account.DoesNotExist:
            return Response(
                {"error": "NOT_FOUND", "message": "Account not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        include_archived = request.query_params.get("include_archived", "false").lower() == "true"
        children = parent.children.filter(is_deleted=False)
        if not include_archived:
            children = children.filter(is_archived=False)
        return Response(AccountFlatSerializer(children.order_by("code"), many=True).data)


class AccountChoicesAPI(APIView):
    """
    GET /accounting/chart-of-accounts/choices/
    Returns dropdown options for cash_flow_type, account_type, zatca_mapping,
    and all active (non-archived) accounts for the Parent Account dropdown.
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        serializer = AccountChoicesSerializer()
        return Response(serializer.to_representation(None))


class AccountExportAPI(APIView):
    """
    GET /accounting/chart-of-accounts/export/
    Downloads all accounts as a CSV file.
    """
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        include_archived = request.query_params.get("include_archived", "false").lower() == "true"
        qs = Account.objects.filter(is_deleted=False).select_related("parent").order_by("code")
        if not include_archived:
            qs = qs.filter(is_archived=False)

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="chart_of_accounts.csv"'

        writer = csv.writer(response)
        writer.writerow([
            "Code", "Account Name", "Account Name (AR)", "Parent Code",
            "Cash Flow Type", "Account Type", "Account Sub-Type",
            "ZATCA Mapping", "Enable Payment", "Show in Expense Claim",
            "Is Locked", "Is Archived",
        ])
        for acc in qs:
            writer.writerow([
                acc.code,
                acc.name,
                acc.name_ar,
                acc.parent.code if acc.parent else "",
                acc.get_cash_flow_type_display(),
                acc.get_account_type_display(),
                acc.account_sub_type,
                acc.get_zatca_mapping_display(),
                acc.enable_payment,
                acc.show_in_expense_claim,
                acc.is_locked,
                acc.is_archived,
            ])
        return response
