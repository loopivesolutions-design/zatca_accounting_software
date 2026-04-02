"""
Product Category API views
===========================
Endpoints
---------
  GET    /products/categories/            — paginated flat list
  POST   /products/categories/            — create
  GET    /products/categories/choices/    — dropdown list for parent selector
  GET    /products/categories/tree/       — full nested tree
  POST   /products/categories/bulk/       — bulk: set_status | delete | duplicate
  GET    /products/categories/<uuid>/     — retrieve
  PATCH  /products/categories/<uuid>/     — update
  DELETE /products/categories/<uuid>/     — soft-delete (blocked if has products or children)
"""

import uuid as uuid_lib

from django.db import transaction, IntegrityError
from django.db.models import Q
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from decimal import Decimal

from main.pagination import CustomPagination
from main.idempotency import begin_idempotent, finalize_idempotent_failure, finalize_idempotent_success
from main.models import BulkExecutionItem
from main.approvals import create_approval_request, maker_checker_enabled
from main.allocation_validator import AllocationValidator
from accounting.models import AccountingPeriod

from .models import (
    ProductCategory,
    UnitOfMeasure,
    Product,
    Warehouse,
    InventoryAdjustment,
    InventoryAdjustmentLine,
)
from .inventory_posting import InventoryAdjustmentPostAbort, execute_inventory_adjustment_post
from .serializers import (
    ProductCategorySerializer,
    ProductCategoryTreeSerializer,
    ProductCategoryChoiceSerializer,
    _annotate_product_count,
    UnitOfMeasureSerializer,
    ProductSerializer,
    _product_is_locked,
    WarehouseSerializer,
    InventoryAdjustmentSerializer,
    InventoryAdjustmentLineSerializer,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

class CategoryPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 200


def _get_category(pk) -> ProductCategory | None:
    try:
        return ProductCategory.objects.get(pk=pk, is_deleted=False)
    except ProductCategory.DoesNotExist:
        return None


def _not_found():
    return Response(
        {"error": "NOT_FOUND", "message": "Category not found."},
        status=status.HTTP_404_NOT_FOUND,
    )


def _products_not_found():
    return Response(
        {"error": "NOT_FOUND", "message": "Product not found."},
        status=status.HTTP_404_NOT_FOUND,
    )


def _inventory_not_found():
    return Response(
        {"error": "NOT_FOUND", "message": "Inventory adjustment not found."},
        status=status.HTTP_404_NOT_FOUND,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Choices  (parent category dropdown)
# ──────────────────────────────────────────────────────────────────────────────

class ProductCategoryChoicesAPI(APIView):
    """
    GET /products/categories/choices/

    Returns all active categories for the Parent Category dropdown.
    Query params:
      ?exclude=<uuid>   exclude a category (used when editing to avoid self-reference)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = ProductCategory.objects.filter(is_deleted=False, is_active=True)
        exclude_pk = request.query_params.get("exclude")
        if exclude_pk:
            qs = qs.exclude(pk=exclude_pk)
        return Response(ProductCategoryChoiceSerializer(qs, many=True).data)


# ──────────────────────────────────────────────────────────────────────────────
# Units of Measure
# ──────────────────────────────────────────────────────────────────────────────


class UnitOfMeasureListCreateAPI(APIView):
    """
    GET /products/uom/
      - List all units of measure (simple dropdown)

    POST /products/uom/
      - Create a new unit (inline from the item form)
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = UnitOfMeasure.objects.filter(is_deleted=False).order_by("name")
        return Response(UnitOfMeasureSerializer(qs, many=True).data)

    def post(self, request):
        serializer = UnitOfMeasureSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        uom = serializer.save(creator=request.user)
        return Response(UnitOfMeasureSerializer(uom).data, status=status.HTTP_201_CREATED)


class UnitOfMeasureDetailAPI(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk):
        try:
            uom = UnitOfMeasure.objects.get(pk=pk, is_deleted=False)
        except UnitOfMeasure.DoesNotExist:
            return Response(
                {"error": "NOT_FOUND", "message": "Unit of measure not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Prevent deletion if any product uses this UoM
        if uom.products.filter(is_deleted=False).exists():
            return Response(
                {
                    "error": "UOM_IN_USE",
                    "message": "This unit of measure is used by one or more products and cannot be deleted.",
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        uom.is_deleted = True
        uom.save(update_fields=["is_deleted", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


# ──────────────────────────────────────────────────────────────────────────────
# Warehouses
# ──────────────────────────────────────────────────────────────────────────────


class WarehouseListCreateAPI(APIView):
    """
    GET /products/warehouses/
    POST /products/warehouses/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = Warehouse.objects.filter(is_deleted=False).order_by("name")
        q = (request.query_params.get("search") or "").strip()
        if q:
            qs = qs.filter(
                Q(name__icontains=q)
                | Q(name_ar__icontains=q)
                | Q(code__icontains=q)
                | Q(city__icontains=q)
                | Q(phone__icontains=q)
            )
        paginator = CustomPagination()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(WarehouseSerializer(page, many=True).data)

    def post(self, request):
        serializer = WarehouseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            wh = serializer.save(creator=request.user)
        except IntegrityError as exc:
            msg = str(exc)
            if "coa_account" in msg:
                return Response(
                    {"coa_account": ["This account is already linked to another warehouse."]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            raise
        return Response(WarehouseSerializer(wh).data, status=status.HTTP_201_CREATED)


class WarehouseDetailAPI(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            wh = Warehouse.objects.get(pk=pk, is_deleted=False)
        except Warehouse.DoesNotExist:
            return Response({"error": "NOT_FOUND", "message": "Warehouse not found."}, status=404)
        return Response(WarehouseSerializer(wh).data)

    def patch(self, request, pk):
        try:
            wh = Warehouse.objects.get(pk=pk, is_deleted=False)
        except Warehouse.DoesNotExist:
            return Response({"error": "NOT_FOUND", "message": "Warehouse not found."}, status=404)
        serializer = WarehouseSerializer(wh, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            wh = serializer.save(updator=request.user)
        except IntegrityError as exc:
            msg = str(exc)
            if "coa_account" in msg:
                return Response(
                    {"coa_account": ["This account is already linked to another warehouse."]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            raise
        return Response(WarehouseSerializer(wh).data)

    def delete(self, request, pk):
        try:
            wh = Warehouse.objects.get(pk=pk, is_deleted=False)
        except Warehouse.DoesNotExist:
            return Response({"error": "NOT_FOUND", "message": "Warehouse not found."}, status=404)

        # Locked if used in any posted inventory transaction (drafts do not lock)
        if wh.has_transactions():
            return Response(
                {
                    "error": "WAREHOUSE_LOCKED",
                    "message": (
                        f"Warehouse '{wh.code} - {wh.name}' has been used in transactions and cannot be deleted."
                    ),
                    "suggestion": "Deactivate the warehouse instead, or remove references from transactions first.",
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        wh.is_deleted = True
        wh.save(update_fields=["is_deleted", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class WarehouseBulkAPI(APIView):
    """
    POST /products/warehouses/bulk/

    Body:
      {
        "action": "delete" | "duplicate",
        "ids": ["<uuid>", ...]
      }
    """

    permission_classes = [IsAuthenticated]
    VALID_ACTIONS = {"delete", "duplicate"}

    def post(self, request):
        rec, early = begin_idempotent(request, scope="products.warehouse.bulk")
        if early:
            return early

        action = str(request.data.get("action", "")).strip()
        ids = request.data.get("ids", [])
        batch_id = str(request.data.get("batch_id") or request.headers.get("Idempotency-Key") or "").strip()

        if action not in self.VALID_ACTIONS:
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error="INVALID_ACTION",
                message=f"Invalid action '{action}'. Must be one of: delete, duplicate.",
                http_status=status.HTTP_400_BAD_REQUEST,
            )

        if not ids or not isinstance(ids, list):
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error="IDS_REQUIRED",
                message="'ids' must be a non-empty list of UUIDs.",
                http_status=status.HTTP_400_BAD_REQUEST,
            )
        if not batch_id:
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error="BATCH_ID_REQUIRED",
                message="'batch_id' is required for bulk item-level idempotency.",
                http_status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate UUIDs
        valid_uuids = []
        for raw in ids:
            try:
                valid_uuids.append(uuid_lib.UUID(str(raw)))
            except (ValueError, AttributeError):
                return finalize_idempotent_failure(
                    rec,  # type: ignore[arg-type]
                    error="INVALID_ID",
                    message=f"'{raw}' is not a valid UUID.",
                    http_status=status.HTTP_400_BAD_REQUEST,
                )

        qs = Warehouse.objects.filter(pk__in=valid_uuids, is_deleted=False)
        found_ids = set(qs.values_list("id", flat=True))
        not_found = [str(u) for u in valid_uuids if u not in found_ids]

        if action == "delete":
            deleted = 0
            skipped = []
            for wh in qs:
                item, created = BulkExecutionItem.objects.get_or_create(
                    scope="products.warehouse.bulk.delete",
                    batch_id=batch_id,
                    item_key=str(wh.id),
                    defaults={"state": "processing", "response_body": {}},
                )
                if not created and item.state == "succeeded":
                    continue
                if wh.has_transactions():
                    skipped.append(
                        {
                            "id": str(wh.id),
                            "code": wh.code,
                            "name": wh.name,
                            "reason": "WAREHOUSE_LOCKED",
                        }
                    )
                    item.state = "succeeded"
                    item.response_body = {"status": "skipped", "reason": "WAREHOUSE_LOCKED"}
                    item.save(update_fields=["state", "response_body", "updated_at"])
                    continue
                wh.is_deleted = True
                wh.save(update_fields=["is_deleted", "updated_at"])
                item.state = "succeeded"
                item.response_body = {"status": "deleted"}
                item.save(update_fields=["state", "response_body", "updated_at"])
                deleted += 1
            response = Response(
                {
                    "message": f"{deleted} warehouse(s) deleted" + (f", {len(skipped)} skipped." if skipped else "."),
                    "deleted": deleted,
                    "skipped": skipped,
                    "not_found": not_found,
                }
            )
            finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
            return response

        # duplicate
        created = []
        with transaction.atomic():
            for wh in qs:
                item, item_created = BulkExecutionItem.objects.get_or_create(
                    scope="products.warehouse.bulk.duplicate",
                    batch_id=batch_id,
                    item_key=str(wh.id),
                    defaults={"state": "processing", "response_body": {}},
                )
                if not item_created and item.state == "succeeded":
                    continue
                # Ensure unique code for copy
                base_code = f"{wh.code}-COPY"
                new_code = base_code
                i = 2
                while Warehouse.objects.filter(code=new_code).exists():
                    new_code = f"{base_code}-{i}"
                    i += 1

                copy = Warehouse.objects.create(
                    code=new_code,
                    name=f"{wh.name} (Copy)",
                    name_ar=wh.name_ar,
                    phone=wh.phone,
                    street_address=wh.street_address,
                    street_address_ar=wh.street_address_ar,
                    building_number=wh.building_number,
                    district=wh.district,
                    district_ar=wh.district_ar,
                    city=wh.city,
                    city_ar=wh.city_ar,
                    postal_code=wh.postal_code,
                    is_active=wh.is_active,
                    creator=request.user,
                )
                created.append({"id": str(copy.id), "code": copy.code, "name": copy.name, "copied_from": str(wh.id)})
                item.state = "succeeded"
                item.response_body = {"status": "duplicated", "new_id": str(copy.id)}
                item.save(update_fields=["state", "response_body", "updated_at"])

        response = Response(
            {"message": f"{len(created)} warehouse(s) duplicated.", "created": len(created), "warehouses": created},
            status=status.HTTP_201_CREATED,
        )
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response


# ──────────────────────────────────────────────────────────────────────────────
# Tree view
# ──────────────────────────────────────────────────────────────────────────────

class ProductCategoryTreeAPI(APIView):
    """
    GET /products/categories/tree/

    Returns root categories (parent=None) with their children nested recursively.
    Query params:
      ?include_inactive=true   include inactive categories (default: active only)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        include_inactive = request.query_params.get("include_inactive", "").lower() == "true"
        qs = ProductCategory.objects.filter(is_deleted=False, parent__isnull=True)
        if not include_inactive:
            qs = qs.filter(is_active=True)
        qs = _annotate_product_count(qs)
        serializer = ProductCategoryTreeSerializer(
            qs, many=True, context={"include_inactive": include_inactive}
        )
        return Response(serializer.data)


# ──────────────────────────────────────────────────────────────────────────────
# List + Create
# ──────────────────────────────────────────────────────────────────────────────

class ProductCategoryListCreateAPI(APIView):
    """
    GET  — paginated flat list
           ?search=<text>          match name (EN/AR) or description
           ?active=true|false      filter by status (default: all)
           ?parent=<uuid>          filter by parent category
           ?root_only=true         only root-level categories (parent=None)
           ?page=<int>
           ?page_size=<int>

    POST — create a new category
    """
    permission_classes = [IsAuthenticated]
    pagination_class = CategoryPagination

    def get(self, request):
        qs = ProductCategory.objects.filter(is_deleted=False).select_related("parent")

        # Active filter
        active_param = request.query_params.get("active")
        if active_param is not None:
            qs = qs.filter(is_active=active_param.lower() == "true")

        # Parent filter
        parent_pk = request.query_params.get("parent")
        if parent_pk:
            qs = qs.filter(parent_id=parent_pk)

        # Root only
        if request.query_params.get("root_only", "").lower() == "true":
            qs = qs.filter(parent__isnull=True)

        # Search
        search = request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(name_ar__icontains=search)
                | Q(description__icontains=search)
            )

        # Annotate each row with product count (single DB query, avoids N+1)
        qs = _annotate_product_count(qs)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(
            ProductCategorySerializer(page, many=True).data
        )

    def post(self, request):
        serializer = ProductCategorySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        category = serializer.save(creator=request.user)
        return Response(
            ProductCategorySerializer(category).data,
            status=status.HTTP_201_CREATED,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Retrieve + Update + Delete
# ──────────────────────────────────────────────────────────────────────────────

class ProductCategoryDetailAPI(APIView):
    """
    GET    — retrieve
    PATCH  — update any field
    DELETE — soft-delete
               blocked if category has child categories
               blocked if category has products assigned to it
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        qs = _annotate_product_count(
            ProductCategory.objects.filter(pk=pk, is_deleted=False)
        )
        try:
            category = qs.get()
        except ProductCategory.DoesNotExist:
            return _not_found()
        return Response(ProductCategorySerializer(category).data)

    def patch(self, request, pk):
        category = _get_category(pk)
        if not category:
            return _not_found()

        serializer = ProductCategorySerializer(
            category, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        category = serializer.save(updator=request.user)
        return Response(ProductCategorySerializer(category).data)

    def delete(self, request, pk):
        category = _get_category(pk)
        if not category:
            return _not_found()

        # Block if has active child categories
        child_count = category.children.filter(is_deleted=False).count()
        if child_count > 0:
            return Response(
                {
                    "error": "CATEGORY_HAS_CHILDREN",
                    "message": (
                        f"'{category.name}' has {child_count} sub-categor"
                        f"{'y' if child_count == 1 else 'ies'} and cannot be deleted."
                    ),
                    "child_count": child_count,
                    "suggestion": "Delete or reassign child categories first.",
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        # Block if has products assigned
        product_count = getattr(
            _annotate_product_count(
                ProductCategory.objects.filter(pk=category.pk)
            ).first(),
            "product_count",
            0,
        )
        if product_count > 0:
            return Response(
                {
                    "error": "CATEGORY_HAS_PRODUCTS",
                    "message": (
                        f"'{category.name}' has {product_count} product"
                        f"{'s' if product_count != 1 else ''} assigned and cannot be deleted."
                    ),
                    "product_count": product_count,
                    "suggestion": "Reassign or delete the products in this category first.",
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        category.is_deleted = True
        category.save(update_fields=["is_deleted", "updated_at"])
        return Response(
            {"message": f"Category '{category.name}' deleted successfully."},
            status=status.HTTP_200_OK,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Products (Items)
# ──────────────────────────────────────────────────────────────────────────────


class ProductListCreateAPI(APIView):
    """
    GET  /products/items/
        - Paginated items list
        - Filters: search, active, category

    POST /products/items/
        - Create a new item
    """

    permission_classes = [IsAuthenticated]
    pagination_class = CategoryPagination

    def get(self, request):
        qs = Product.objects.filter(is_deleted=False).select_related(
            "category",
            "unit_of_measure",
            "revenue_account",
            "expense_account",
            "inventory_account",
            "sales_tax_rate",
            "purchase_tax_rate",
        )

        search = request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(name__icontains=search)
                | Q(code__icontains=search)
                | Q(description__icontains=search)
            )

        active_param = request.query_params.get("active")
        if active_param is not None:
            qs = qs.filter(is_active=active_param.lower() == "true")

        category_pk = request.query_params.get("category")
        if category_pk:
            qs = qs.filter(category_id=category_pk)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(
            ProductSerializer(page, many=True).data
        )

    def post(self, request):
        serializer = ProductSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product = serializer.save(creator=request.user)
        return Response(ProductSerializer(product).data, status=status.HTTP_201_CREATED)


class ProductDetailAPI(APIView):
    """
    GET    /products/items/<uuid>/     — retrieve
    PATCH  /products/items/<uuid>/     — update
    DELETE /products/items/<uuid>/     — soft delete
    """

    permission_classes = [IsAuthenticated]

    def get_object(self, pk):
        try:
            return Product.objects.select_related(
                "category",
                "unit_of_measure",
                "revenue_account",
                "expense_account",
                "inventory_account",
                "sales_tax_rate",
                "purchase_tax_rate",
            ).get(pk=pk, is_deleted=False)
        except Product.DoesNotExist:
            return None

    def get(self, request, pk):
        product = self.get_object(pk)
        if not product:
            return _products_not_found()
        return Response(ProductSerializer(product).data)

    def patch(self, request, pk):
        product = self.get_object(pk)
        if not product:
            return _products_not_found()
        serializer = ProductSerializer(product, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        product = serializer.save(updator=request.user)
        return Response(ProductSerializer(product).data)

    def delete(self, request, pk):
        product = self.get_object(pk)
        if not product:
            return _products_not_found()
        if _product_is_locked(product):
            return Response(
                {
                    "error": "ITEM_LOCKED",
                    "message": (
                        f"Item '{product.name}' is used in an invoice, bill, credit note, "
                        "quote, or purchase order and cannot be deleted."
                    ),
                    "suggestion": "Remove or replace the item from those documents first.",
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        product.is_deleted = True
        product.save(update_fields=["is_deleted", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


# ──────────────────────────────────────────────────────────────────────────────
# Inventory Adjustments
# ──────────────────────────────────────────────────────────────────────────────


class InventoryAdjustmentListCreateAPI(APIView):
    """
    GET  /products/inventory/adjustments/
      - Returns a flattened list (one row per line) for the grid.
      - Filters: search, status, warehouse, item, date_from, date_to

    POST /products/inventory/adjustments/
      - Create a draft adjustment with multiple lines
    """

    permission_classes = [IsAuthenticated]
    pagination_class = CategoryPagination

    def get(self, request):
        qs = InventoryAdjustmentLine.objects.filter(is_deleted=False, adjustment__is_deleted=False).select_related(
            "adjustment",
            "adjustment__warehouse",
            "product",
            "account",
        )

        status_param = request.query_params.get("status")
        if status_param:
            qs = qs.filter(adjustment__status=status_param)

        warehouse = request.query_params.get("warehouse")
        if warehouse:
            qs = qs.filter(adjustment__warehouse_id=warehouse)

        item = request.query_params.get("item")
        if item:
            qs = qs.filter(product_id=item)

        date_from = request.query_params.get("date_from")
        if date_from:
            qs = qs.filter(adjustment__date__gte=date_from)

        date_to = request.query_params.get("date_to")
        if date_to:
            qs = qs.filter(adjustment__date__lte=date_to)

        search = request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(adjustment__reference__icontains=search)
                | Q(adjustment__adjustment_id__icontains=search)
                | Q(product__name__icontains=search)
                | Q(product__code__icontains=search)
                | Q(account__name__icontains=search)
                | Q(account__code__icontains=search)
            )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs.order_by("-adjustment__date", "-created_at"), request)
        return paginator.get_paginated_response(InventoryAdjustmentLineSerializer(page, many=True).data)

    def post(self, request):
        serializer = InventoryAdjustmentSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        adj = serializer.save()
        return Response(InventoryAdjustmentSerializer(adj).data, status=status.HTTP_201_CREATED)


class InventoryAdjustmentDetailAPI(APIView):
    permission_classes = [IsAuthenticated]

    def get_object(self, pk):
        try:
            return InventoryAdjustment.objects.select_related("warehouse").prefetch_related(
                "lines",
                "lines__product",
                "lines__account",
            ).get(pk=pk, is_deleted=False)
        except InventoryAdjustment.DoesNotExist:
            return None

    def get(self, request, pk):
        adj = self.get_object(pk)
        if not adj:
            return _inventory_not_found()
        return Response(InventoryAdjustmentSerializer(adj).data)

    def patch(self, request, pk):
        adj = self.get_object(pk)
        if not adj:
            return _inventory_not_found()
        if adj.status == "posted":
            return Response(
                {"error": "ADJUSTMENT_POSTED", "message": "Posted adjustments cannot be edited."},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        serializer = InventoryAdjustmentSerializer(adj, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        adj = serializer.save()
        return Response(InventoryAdjustmentSerializer(adj).data)

    def delete(self, request, pk):
        adj = self.get_object(pk)
        if not adj:
            return _inventory_not_found()
        if adj.status == "posted":
            return Response(
                {"error": "ADJUSTMENT_POSTED", "message": "Posted adjustments cannot be deleted."},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        adj.is_deleted = True
        adj.save(update_fields=["is_deleted", "updated_at"])
        adj.lines.filter(is_deleted=False).update(is_deleted=True)
        return Response(status=status.HTTP_204_NO_CONTENT)


class InventoryAdjustmentPostAPI(APIView):
    """
    POST /products/inventory/adjustments/<uuid>/post/
    - Validates draft adjustment
    - Creates a Journal Entry and posts it
    - Updates Product.stock_quantity and Product.avg_unit_cost
    - Marks adjustment as posted and assigns sequential adjustment_id (ADJ-000001)
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        rec, early = begin_idempotent(request, scope="products.inventory_adjustment.post")
        if early:
            return early

        try:
            adj = InventoryAdjustment.objects.select_related("warehouse").get(pk=pk, is_deleted=False)
        except InventoryAdjustment.DoesNotExist:
            resp = _inventory_not_found()
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error=resp.data.get("error", "NOT_FOUND"),
                message=resp.data.get("message", "Not found."),
                http_status=resp.status_code,
            )

        if adj.status == "posted":
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error="ADJUSTMENT_ALREADY_POSTED",
                message="Already posted.",
                http_status=422,
            )

        lines = list(
            adj.lines.filter(is_deleted=False).select_related("product", "account", "product__inventory_account")
        )
        try:
            AllocationValidator.validate_inventory_adjustment_postable(adj, lines)
        except ValueError as exc:
            msg = str(exc)
            if "closed accounting period" in msg:
                code, st = "PERIOD_CLOSED", 422
            elif "Add at least one line" in msg:
                code, st = "NO_LINES", 400
            elif "Qty +/-" in msg:
                code, st = "QTY_REQUIRED", 400
            elif "Inventory value +/-" in msg:
                code, st = "VALUE_REQUIRED", 400
            elif "Inventory Asset Account" in msg:
                code, st = "MISSING_INVENTORY_ACCOUNT", 422
            else:
                code, st = "VALIDATION_ERROR", 422
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error=code,
                message=msg,
                http_status=st,
            )

        if maker_checker_enabled("products.inventory_adjustment.post"):
            approval = create_approval_request(
                scope="products.inventory_adjustment.post",
                object_type="products.InventoryAdjustment",
                object_id=adj.id,
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
            adj = execute_inventory_adjustment_post(adjustment_id=pk, user=request.user)
        except InventoryAdjustmentPostAbort as exc:
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error=exc.code,
                message=str(exc),
                http_status=exc.http_status,
            )

        response = Response(InventoryAdjustmentSerializer(adj).data, status=200)
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response


# ──────────────────────────────────────────────────────────────────────────────
# Bulk Actions
# ──────────────────────────────────────────────────────────────────────────────

class ProductCategoryBulkAPI(APIView):
    """
    POST /products/categories/bulk/

    Perform a bulk action on a list of category IDs.

    Body:
      {
        "action": "set_status" | "delete" | "duplicate",
        "ids": ["<uuid>", ...],
        "status": "active" | "inactive"   // required only for set_status
      }

    Actions:
      set_status — Set is_active=true/false for all selected categories.
      delete     — Soft-delete each category. Categories with sub-categories or
                   products are skipped (reported in `skipped`).
      duplicate  — Create a copy of each category with name suffixed " (Copy)".
                   Children are NOT duplicated — only the top-level row.
    """
    permission_classes = [IsAuthenticated]

    # Valid action names
    VALID_ACTIONS = {"set_status", "delete", "duplicate"}

    def post(self, request):
        rec, early = begin_idempotent(request, scope="products.category.bulk")
        if early:
            return early

        action = request.data.get("action", "").strip()
        ids = request.data.get("ids", [])
        batch_id = str(request.data.get("batch_id") or request.headers.get("Idempotency-Key") or "").strip()

        # ── Validate action ──────────────────────────────────────────────────
        if action not in self.VALID_ACTIONS:
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error="INVALID_ACTION",
                message=f"Invalid action '{action}'. Must be one of: {', '.join(sorted(self.VALID_ACTIONS))}.",
                http_status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Validate ids ─────────────────────────────────────────────────────
        if not ids or not isinstance(ids, list):
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error="IDS_REQUIRED",
                message="'ids' must be a non-empty list of UUIDs.",
                http_status=status.HTTP_400_BAD_REQUEST,
            )
        if not batch_id:
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error="BATCH_ID_REQUIRED",
                message="'batch_id' is required for bulk item-level idempotency.",
                http_status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate each id is a proper UUID string
        valid_uuids = []
        for raw in ids:
            try:
                valid_uuids.append(uuid_lib.UUID(str(raw)))
            except (ValueError, AttributeError):
                return finalize_idempotent_failure(
                    rec,  # type: ignore[arg-type]
                    error="INVALID_ID",
                    message=f"'{raw}' is not a valid UUID.",
                    http_status=status.HTTP_400_BAD_REQUEST,
                )

        categories = ProductCategory.objects.filter(pk__in=valid_uuids, is_deleted=False)
        found_ids = set(categories.values_list("id", flat=True))
        not_found = [str(u) for u in valid_uuids if u not in found_ids]

        # ── Dispatch ─────────────────────────────────────────────────────────
        if action == "set_status":
            response = self._set_status(request, categories)
            finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
            return response
        if action == "delete":
            response = self._delete(request, categories, not_found, batch_id=batch_id)
            finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
            return response
        if action == "duplicate":
            response = self._duplicate(request, categories, batch_id=batch_id)
            finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
            return response

    # ── set_status ────────────────────────────────────────────────────────────

    def _set_status(self, request, categories):
        new_status = request.data.get("status", "").strip().lower()
        if new_status not in ("active", "inactive"):
            return Response(
                {
                    "error": "INVALID_STATUS",
                    "message": "'status' must be 'active' or 'inactive' for set_status action.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        is_active = new_status == "active"
        updated = categories.update(is_active=is_active)
        return Response(
            {
                "message": f"{updated} categor{'y' if updated == 1 else 'ies'} "
                           f"set to {new_status.upper()}.",
                "updated": updated,
            }
        )

    # ── delete ────────────────────────────────────────────────────────────────

    def _delete(self, request, categories, not_found, *, batch_id: str):
        deleted = []
        skipped = []

        annotated = _annotate_product_count(categories)

        for cat in annotated:
            item, created = BulkExecutionItem.objects.get_or_create(
                scope="products.category.bulk.delete",
                batch_id=batch_id,
                item_key=str(cat.id),
                defaults={"state": "processing", "response_body": {}},
            )
            if not created and item.state == "succeeded":
                deleted.append(str(cat.id))
                continue
            child_count = cat.children.filter(is_deleted=False).count()
            if child_count > 0:
                skipped.append({
                    "id": str(cat.id),
                    "name": cat.name,
                    "reason": "CATEGORY_HAS_CHILDREN",
                    "detail": f"Has {child_count} sub-categor{'y' if child_count == 1 else 'ies'}.",
                })
                item.state = "succeeded"
                item.response_body = {"status": "skipped", "reason": "CATEGORY_HAS_CHILDREN"}
                item.save(update_fields=["state", "response_body", "updated_at"])
                continue

            product_count = getattr(cat, "product_count", 0)
            if product_count > 0:
                skipped.append({
                    "id": str(cat.id),
                    "name": cat.name,
                    "reason": "CATEGORY_HAS_PRODUCTS",
                    "detail": f"Has {product_count} product{'s' if product_count != 1 else ''} assigned.",
                })
                item.state = "succeeded"
                item.response_body = {"status": "skipped", "reason": "CATEGORY_HAS_PRODUCTS"}
                item.save(update_fields=["state", "response_body", "updated_at"])
                continue

            cat.is_deleted = True
            cat.save(update_fields=["is_deleted", "updated_at"])
            deleted.append(str(cat.id))
            item.state = "succeeded"
            item.response_body = {"status": "deleted"}
            item.save(update_fields=["state", "response_body", "updated_at"])

        return Response(
            {
                "message": (
                    f"{len(deleted)} categor{'y' if len(deleted) == 1 else 'ies'} deleted"
                    + (f", {len(skipped)} skipped." if skipped else ".")
                ),
                "deleted": len(deleted),
                "skipped": skipped,
                "not_found": not_found,
            }
        )

    # ── duplicate ─────────────────────────────────────────────────────────────

    def _duplicate(self, request, categories, *, batch_id: str):
        created = []

        with transaction.atomic():
            for cat in categories.select_related("parent"):
                item, item_created = BulkExecutionItem.objects.get_or_create(
                    scope="products.category.bulk.duplicate",
                    batch_id=batch_id,
                    item_key=str(cat.id),
                    defaults={"state": "processing", "response_body": {}},
                )
                if not item_created and item.state == "succeeded":
                    continue
                copy = ProductCategory(
                    name=f"{cat.name} (Copy)",
                    name_ar=cat.name_ar,
                    description=cat.description,
                    parent=cat.parent,
                    is_active=cat.is_active,
                    creator=request.user,
                )
                copy.save()
                created.append({
                    "id": str(copy.id),
                    "name": copy.name,
                    "copied_from": str(cat.id),
                })
                item.state = "succeeded"
                item.response_body = {"status": "duplicated", "new_id": str(copy.id)}
                item.save(update_fields=["state", "response_body", "updated_at"])

        return Response(
            {
                "message": f"{len(created)} categor{'y' if len(created) == 1 else 'ies'} duplicated.",
                "created": len(created),
                "categories": created,
            },
            status=status.HTTP_201_CREATED,
        )
