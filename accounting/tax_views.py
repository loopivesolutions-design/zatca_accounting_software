"""
Tax Rate API views
==================
Editing rules (Wafeq-style):
  Locked forever   — tax_type, rate          (immutable after creation)
  System-managed   — zatca_category          (auto-computed; never user input)
  Always editable  — name, name_ar, description

Deletion rules:
  System defaults           → cannot delete
  Used in transactions      → cannot delete (must be kept for historical records)
  Unused, non-default rates → can delete

Endpoints
---------
  GET    /tax-rates/          — paginated list
  POST   /tax-rates/          — create (zatca_category auto-assigned by system)
  GET    /tax-rates/choices/  — dropdown choices (tax_type only)
  GET    /tax-rates/<uuid>/   — retrieve
  PATCH  /tax-rates/<uuid>/   — update editable fields only (name, name_ar, description)
  DELETE /tax-rates/<uuid>/   — soft-delete (blocked for defaults and used rates)
"""

from django.db.models import Q
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import TaxRate
from .tax_serializers import TaxRateSerializer, TaxRateChoicesSerializer, LOCKED_FIELDS, EDITABLE_FIELDS, _auto_zatca_category


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

class TaxRatePagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 200


def _get_tax_rate(pk) -> TaxRate | None:
    try:
        return TaxRate.objects.get(pk=pk, is_deleted=False)
    except TaxRate.DoesNotExist:
        return None


def _not_found():
    return Response(
        {"error": "NOT_FOUND", "message": "Tax rate not found."},
        status=status.HTTP_404_NOT_FOUND,
    )


def _locked_fields_error(violations: set, tax_rate_name: str):
    """Return a structured 422 when a caller tries to change an immutable field."""
    return Response(
        {
            "error": "TAX_RATE_FIELDS_LOCKED",
            "message": (
                f"The fields {sorted(violations)} on '{tax_rate_name}' are permanently "
                f"locked after creation. They affect invoice calculations, VAT reports, "
                f"and ZATCA XML — changing them would corrupt historical records."
            ),
            "locked_fields": sorted(LOCKED_FIELDS),
            "system_managed_fields": ["zatca_category"],
            "editable_fields": sorted(EDITABLE_FIELDS),
            "suggestion": (
                "Edit only name, name_ar, or description. "
                "If you need a different rate or type, create a new tax rate."
            ),
        },
        status=status.HTTP_422_UNPROCESSABLE_ENTITY,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Choices
# ──────────────────────────────────────────────────────────────────────────────

class TaxRateChoicesAPI(APIView):
    """
    GET /tax-rates/choices/
    Returns dropdown choices for the Create Tax Rate form.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(TaxRateChoicesSerializer({}).data)


# ──────────────────────────────────────────────────────────────────────────────
# List + Create
# ──────────────────────────────────────────────────────────────────────────────

class TaxRateListCreateAPI(APIView):
    """
    GET  — paginated list
           ?tax_type=sales|purchases|reverse_charge|out_of_scope
           ?zatca_category=S|Z|E|O
           ?active=true|false
           ?search=<text>          matches name (EN/AR) or description
           ?page=<int>
           ?page_size=<int>

    POST — create a new tax rate (all fields settable at creation)
    """
    permission_classes = [IsAuthenticated]
    pagination_class = TaxRatePagination

    def get(self, request):
        qs = TaxRate.objects.filter(is_deleted=False)

        tax_type = request.query_params.get("tax_type")
        if tax_type:
            qs = qs.filter(tax_type=tax_type)

        zatca_category = request.query_params.get("zatca_category")
        if zatca_category:
            qs = qs.filter(zatca_category=zatca_category)

        active_param = request.query_params.get("active")
        if active_param is not None:
            qs = qs.filter(is_active=active_param.lower() == "true")

        search = request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(name_ar__icontains=search)
                | Q(description__icontains=search)
            )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(TaxRateSerializer(page, many=True).data)

    def post(self, request):
        serializer = TaxRateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tax_rate = serializer.save(creator=request.user)
        return Response(TaxRateSerializer(tax_rate).data, status=status.HTTP_201_CREATED)


# ──────────────────────────────────────────────────────────────────────────────
# Retrieve + Update + Delete
# ──────────────────────────────────────────────────────────────────────────────

class TaxRateDetailAPI(APIView):
    """
    GET    — retrieve
    PATCH  — update editable fields: name, name_ar, description
             Attempting to change tax_type / rate / zatca_category returns 422.
    DELETE — soft-delete (blocked for system defaults and rates used in transactions)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        tax_rate = _get_tax_rate(pk)
        if not tax_rate:
            return _not_found()
        return Response(TaxRateSerializer(tax_rate).data)

    def patch(self, request, pk):
        tax_rate = _get_tax_rate(pk)
        if not tax_rate:
            return _not_found()

        # Reject any attempt to change permanently locked fields
        violations = LOCKED_FIELDS & set(request.data.keys())
        if violations:
            return _locked_fields_error(violations, tax_rate.name)

        serializer = TaxRateSerializer(tax_rate, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        tax_rate = serializer.save(updator=request.user)
        return Response(TaxRateSerializer(tax_rate).data)

    def delete(self, request, pk):
        tax_rate = _get_tax_rate(pk)
        if not tax_rate:
            return _not_found()

        # System defaults cannot be deleted
        if tax_rate.is_default:
            return Response(
                {
                    "error": "TAX_RATE_IS_DEFAULT",
                    "message": (
                        f"'{tax_rate.name}' is a system default tax rate and cannot be deleted."
                    ),
                    "suggestion": "Create a custom tax rate if you need a different one.",
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        # Rates used in transactions cannot be deleted
        if tax_rate.has_transactions():
            return Response(
                {
                    "error": "TAX_RATE_HAS_TRANSACTIONS",
                    "message": (
                        f"'{tax_rate.name}' has been used in transactions and cannot be deleted. "
                        f"It must be kept to preserve historical records."
                    ),
                    "suggestion": (
                        "Stop using this tax rate in new transactions. "
                        "It will remain visible in historical records."
                    ),
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        tax_rate.is_deleted = True
        tax_rate.save(update_fields=["is_deleted", "updated_at"])
        return Response(
            {"message": f"Tax rate '{tax_rate.name}' deleted successfully."},
            status=status.HTTP_200_OK,
        )
