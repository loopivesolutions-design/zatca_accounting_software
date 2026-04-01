"""
Product Category serializers
"""

from django.db.models import Count, Q, Value
from rest_framework import serializers
from .models import (
    ProductCategory,
    UnitOfMeasure,
    Product,
    Warehouse,
    InventoryAdjustment,
    InventoryAdjustmentLine,
)
from accounting.models import Account


def _annotate_product_count(qs):
    """
    Annotate each ProductCategory queryset row with the number of active products
    directly assigned to it.  Gracefully falls back to Value(0) if the Product
    model (or its table) does not exist yet.
    """
    try:
        from django.apps import apps
        apps.get_model("products", "Product")  # raises LookupError if not registered
        return qs.annotate(
            product_count=Count(
                "products",
                filter=Q(products__is_deleted=False),
            )
        )
    except LookupError:
        return qs.annotate(product_count=Value(0))


class ProductCategorySerializer(serializers.ModelSerializer):
    """Flat serializer — used for list, create, update, and detail."""

    parent_name = serializers.SerializerMethodField()
    # product_count is populated by queryset annotation in views (avoids N+1 queries).
    # Falls back to 0 when queried without annotation (e.g. on creation).
    product_count = serializers.SerializerMethodField()
    has_children = serializers.SerializerMethodField()

    class Meta:
        model = ProductCategory
        fields = [
            "id",
            "name",
            "name_ar",
            "description",
            "parent",
            "parent_name",
            "product_count",
            "has_children",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_parent_name(self, obj) -> str | None:
        return obj.parent.name if obj.parent_id else None

    def get_product_count(self, obj) -> int:
        # Reads the annotated value set by the view queryset.
        # Returns 0 for newly created instances (not yet annotated).
        return getattr(obj, "product_count", 0)

    def get_has_children(self, obj) -> bool:
        return obj.has_children

    def validate_parent(self, value):
        """Prevent circular references — a category cannot be its own ancestor."""
        if value and self.instance and self.instance.pk:
            node = value
            while node is not None:
                if node.pk == self.instance.pk:
                    raise serializers.ValidationError(
                        "A category cannot be its own parent or ancestor."
                    )
                node = node.parent
        return value


class ProductCategoryTreeSerializer(serializers.ModelSerializer):
    """Recursive nested serializer for the full category tree."""

    children = serializers.SerializerMethodField()
    product_count = serializers.SerializerMethodField()

    class Meta:
        model = ProductCategory
        fields = [
            "id",
            "name",
            "name_ar",
            "description",
            "product_count",
            "is_active",
            "children",
        ]

    def get_product_count(self, obj) -> int:
        return getattr(obj, "product_count", 0)

    def get_children(self, obj):
        include_inactive = self.context.get("include_inactive", False)
        qs = obj.children.filter(is_deleted=False)
        if not include_inactive:
            qs = qs.filter(is_active=True)
        qs = _annotate_product_count(qs)
        return ProductCategoryTreeSerializer(
            qs, many=True, context=self.context
        ).data


class ProductCategoryChoiceSerializer(serializers.ModelSerializer):
    """Compact serializer for parent-category dropdown."""

    class Meta:
        model = ProductCategory
        fields = ["id", "name", "name_ar", "parent"]


class UnitOfMeasureSerializer(serializers.ModelSerializer):
    class Meta:
        model = UnitOfMeasure
        fields = ["id", "name", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


def _product_is_locked(product) -> bool:
    """True if this product is used in any invoice, bill, credit note, quote, or PO (cannot delete)."""
    from django.apps import apps
    # App.model names that may have a FK or product_id to Product
    for label in (
        "sales.InvoiceLine",
        "sales.CreditNoteLine",
        "sales.QuoteLine",
        "purchases.BillLine",
        "purchases.PurchaseOrderLine",
    ):
        try:
            Model = apps.get_model(label)
            if hasattr(Model, "product_id") and Model.objects.filter(product_id=product.pk).exists():
                return True
            if hasattr(Model, "product") and Model.objects.filter(product=product).exists():
                return True
        except LookupError:
            continue
    return False


class ProductSerializer(serializers.ModelSerializer):
    """Flat serializer for the Items list / detail pages."""

    category_name = serializers.SerializerMethodField()
    unit_of_measure_name = serializers.SerializerMethodField()
    revenue_account_name = serializers.SerializerMethodField()
    expense_account_name = serializers.SerializerMethodField()
    inventory_account_name = serializers.SerializerMethodField()
    sales_tax_rate_name = serializers.SerializerMethodField()
    purchase_tax_rate_name = serializers.SerializerMethodField()
    # List page extras: avg cost, inventory value, locked, attachment
    avg_unit_cost = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    inventory_value = serializers.SerializerMethodField()
    is_locked = serializers.SerializerMethodField()
    has_attachment = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "code",
            "description",
            "category",
            "category_name",
            "unit_of_measure",
            "unit_of_measure_name",
            "image",
            "has_attachment",
            "is_active",
            "selling_price",
            "purchase_price",
            "avg_unit_cost",
            "stock_quantity",
            "inventory_value",
            "is_locked",
            "revenue_account",
            "revenue_account_name",
            "expense_account",
            "expense_account_name",
            "inventory_account",
            "inventory_account_name",
            "sales_tax_rate",
            "sales_tax_rate_name",
            "purchase_tax_rate",
            "purchase_tax_rate_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
            "avg_unit_cost",
        ]

    def get_category_name(self, obj) -> str | None:
        return obj.category.name if obj.category_id else None

    def get_unit_of_measure_name(self, obj) -> str | None:
        return obj.unit_of_measure.name if obj.unit_of_measure_id else None

    def _get_related_name(self, obj, attr: str) -> str | None:
        rel = getattr(obj, attr, None)
        return rel.name if rel else None

    def get_revenue_account_name(self, obj) -> str | None:
        return self._get_related_name(obj, "revenue_account")

    def get_expense_account_name(self, obj) -> str | None:
        return self._get_related_name(obj, "expense_account")

    def get_inventory_account_name(self, obj) -> str | None:
        return self._get_related_name(obj, "inventory_account")

    def get_sales_tax_rate_name(self, obj) -> str | None:
        return self._get_related_name(obj, "sales_tax_rate")

    def get_purchase_tax_rate_name(self, obj) -> str | None:
        return self._get_related_name(obj, "purchase_tax_rate")

    def get_inventory_value(self, obj) -> str:
        from decimal import Decimal
        qty = obj.stock_quantity or Decimal("0")
        cost = obj.avg_unit_cost or obj.purchase_price or Decimal("0")
        return str(qty * cost)

    def get_is_locked(self, obj) -> bool:
        return _product_is_locked(obj)

    def get_has_attachment(self, obj) -> bool:
        return bool(obj.image)


# ──────────────────────────────────────────────────────────────────────────────
# Inventory
# ──────────────────────────────────────────────────────────────────────────────


class WarehouseSerializer(serializers.ModelSerializer):
    address_display = serializers.CharField(read_only=True)
    is_locked = serializers.SerializerMethodField()
    coa_account = serializers.PrimaryKeyRelatedField(
        queryset=Account.objects.all(),
        required=False,
        allow_null=True,
    )
    coa_account_code = serializers.CharField(source="coa_account.code", read_only=True)
    coa_account_name = serializers.CharField(source="coa_account.name", read_only=True)

    class Meta:
        model = Warehouse
        fields = [
            "id",
            "code",
            "name",
            "name_ar",
            "phone",
            "street_address",
            "street_address_ar",
            "building_number",
            "district",
            "district_ar",
            "city",
            "city_ar",
            "postal_code",
            "address_display",
            "is_active",
            "is_locked",
            "coa_account",
            "coa_account_code",
            "coa_account_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_is_locked(self, obj) -> bool:
        return obj.has_transactions()

    def validate_coa_account(self, value):
        if value is None:
            return value
        from .models import Warehouse
        qs = Warehouse.objects.filter(coa_account=value, is_deleted=False)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                "This account is already linked to another warehouse. "
                "Each account can only be assigned to one warehouse."
            )
        return value


class InventoryAdjustmentLineSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_code = serializers.CharField(source="product.code", read_only=True)
    warehouse_id = serializers.UUIDField(source="adjustment.warehouse_id", read_only=True)
    warehouse_name = serializers.CharField(source="adjustment.warehouse.name", read_only=True)
    status = serializers.CharField(source="adjustment.status", read_only=True)
    date = serializers.DateField(source="adjustment.date", read_only=True)
    reference = serializers.CharField(source="adjustment.reference", read_only=True)
    adjustment_id = serializers.CharField(source="adjustment.adjustment_id", read_only=True)
    total_adjustment_amount = serializers.SerializerMethodField()
    account_name = serializers.CharField(source="account.name", read_only=True)
    account_code = serializers.CharField(source="account.code", read_only=True)

    class Meta:
        model = InventoryAdjustmentLine
        fields = [
            "id",
            "adjustment_id",
            "reference",
            "status",
            "date",
            "warehouse_id",
            "warehouse_name",
            "product",
            "product_code",
            "product_name",
            "description",
            "quantity_delta",
            "inventory_value_delta",
            "account",
            "account_code",
            "account_name",
            "total_adjustment_amount",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_total_adjustment_amount(self, obj) -> str:
        return str(obj.adjustment.total_adjustment_amount())


class InventoryAdjustmentSerializer(serializers.ModelSerializer):
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    total_adjustment_amount = serializers.SerializerMethodField()
    lines = InventoryAdjustmentLineSerializer(many=True)

    class Meta:
        model = InventoryAdjustment
        fields = [
            "id",
            "adjustment_id",
            "reference",
            "date",
            "warehouse",
            "warehouse_name",
            "status",
            "posted_at",
            "journal_entry",
            "total_adjustment_amount",
            "lines",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "adjustment_id", "posted_at", "journal_entry", "created_at", "updated_at"]

    def get_total_adjustment_amount(self, obj) -> str:
        return str(obj.total_adjustment_amount())

    def create(self, validated_data):
        lines_data = validated_data.pop("lines", [])
        user = self.context.get("request").user if self.context.get("request") else None
        adjustment = InventoryAdjustment.objects.create(**validated_data, creator=user)
        for idx, line in enumerate(lines_data):
            InventoryAdjustmentLine.objects.create(
                adjustment=adjustment,
                line_order=idx,
                creator=user,
                **line,
            )
        return adjustment

    def update(self, instance, validated_data):
        # Only draft adjustments are editable; enforce in view.
        lines_data = validated_data.pop("lines", None)
        user = self.context.get("request").user if self.context.get("request") else None
        for attr, val in validated_data.items():
            setattr(instance, attr, val)
        instance.updator = user
        instance.save()

        if lines_data is not None:
            # Soft-delete existing lines then recreate to keep it simple.
            instance.lines.filter(is_deleted=False).update(is_deleted=True, updator=user)
            for idx, line in enumerate(lines_data):
                InventoryAdjustmentLine.objects.create(
                    adjustment=instance,
                    line_order=idx,
                    creator=user,
                    **line,
                )
        return instance

