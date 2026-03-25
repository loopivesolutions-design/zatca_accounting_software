import re
from decimal import Decimal

from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from main.models import BaseModel


class ProductCategory(BaseModel):
    """
    Product category with optional self-referential parent (unlimited depth).

    Tax rates are assigned at the individual product level, not here.
    A single category can contain products with different tax rates
    (e.g. standard 15%, zero-rated 0%, exempt), which is required for
    ZATCA compliance where each invoice line carries its own tax classification.
    """

    name = models.CharField(_("Category Name (EN)"), max_length=255)
    name_ar = models.CharField(_("Category Name (AR)"), max_length=255, blank=True)
    description = models.TextField(_("Description"), blank=True)
    parent = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="children",
        verbose_name=_("Parent Category"),
    )
    is_active = models.BooleanField(_("Is Active"), default=True, db_index=True)

    class Meta:
        db_table = "product_category"
        verbose_name = _("product category")
        verbose_name_plural = _("product categories")
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def has_children(self) -> bool:
        return self.children.filter(is_deleted=False).exists()


class UnitOfMeasure(BaseModel):
    """Simple unit of measure (e.g. `cm`, `pc`, `box`)."""

    name = models.CharField(_("Unit of Measure"), max_length=50, unique=True)

    class Meta:
        db_table = "unit_of_measure"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Product(BaseModel):
    """
    Sellable / purchasable item (product or service).

    - Tax rates are assigned per product via FK to `TaxRate`
      (one for sales, one for purchases).
    - Revenue / expense / inventory accounts are selected from the Chart of
      Accounts, ensuring each line item posts to the correct ledgers.
    """

    name = models.CharField(_("Name of Item"), max_length=255)
    code = models.CharField(_("Item Code"), max_length=50, unique=True)
    description = models.TextField(_("Description"), blank=True)

    category = models.ForeignKey(
        ProductCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
        verbose_name=_("Category"),
    )
    unit_of_measure = models.ForeignKey(
        "products.UnitOfMeasure",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
        verbose_name=_("Unit of Measure"),
    )

    image = models.ImageField(
        _("Image"),
        upload_to="product-images/",
        null=True,
        blank=True,
    )

    is_active = models.BooleanField(_("Is Active"), default=True, db_index=True)

    # Selling
    selling_price = models.DecimalField(
        _("Selling Price"),
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text=_("Default selling price in SAR."),
    )

    # Purchase
    purchase_price = models.DecimalField(
        _("Purchase Rate"),
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text=_("Default purchase cost in SAR."),
    )
    # Weighted average cost per unit (updated from purchase transactions when available).
    # Until then, use purchase_price for display; inventory value = stock_quantity * avg_unit_cost.
    avg_unit_cost = models.DecimalField(
        _("Avg unit cost"),
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text=_("Weighted average cost per unit. Used for inventory value calculation."),
    )

    # Simple on-hand quantity snapshot; full inventory module can extend later.
    stock_quantity = models.DecimalField(
        _("Stock Quantity"),
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text=_("Current on-hand stock quantity."),
    )

    # Accounting links
    revenue_account = models.ForeignKey(
        "accounting.Account",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products_revenue",
        verbose_name=_("Revenue Account"),
    )
    expense_account = models.ForeignKey(
        "accounting.Account",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products_expense",
        verbose_name=_("Expense Account"),
    )
    inventory_account = models.ForeignKey(
        "accounting.Account",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products_inventory",
        verbose_name=_("Inventory Asset Account"),
    )

    sales_tax_rate = models.ForeignKey(
        "accounting.TaxRate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products_sales_tax",
        verbose_name=_("Revenue Tax Rate"),
    )
    purchase_tax_rate = models.ForeignKey(
        "accounting.TaxRate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products_purchase_tax",
        verbose_name=_("Purchase Tax Rate"),
    )

    class Meta:
        db_table = "product"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


# ──────────────────────────────────────────────────────────────────────────────
# Inventory
# ──────────────────────────────────────────────────────────────────────────────

INVENTORY_ADJUSTMENT_STATUS_CHOICES = (
    ("draft", "Draft"),
    ("posted", "Posted"),
)


class Warehouse(BaseModel):
    """Physical or logical stock location (e.g. Primary Warehouse)."""

    name = models.CharField(_("Warehouse Name (EN)"), max_length=255)
    name_ar = models.CharField(_("Warehouse Name (AR)"), max_length=255, blank=True)
    code = models.CharField(_("Code"), max_length=50, unique=True, db_index=True)
    phone = models.CharField(_("Phone"), max_length=32, blank=True)

    # Address (optional)
    street_address = models.CharField(_("Street Address (EN)"), max_length=255, blank=True)
    street_address_ar = models.CharField(_("Street Address (AR)"), max_length=255, blank=True)
    building_number = models.CharField(_("Building Number"), max_length=50, blank=True)
    district = models.CharField(_("District (EN)"), max_length=100, blank=True)
    district_ar = models.CharField(_("District (AR)"), max_length=100, blank=True)
    city = models.CharField(_("City (EN)"), max_length=100, blank=True)
    city_ar = models.CharField(_("City (AR)"), max_length=100, blank=True)
    postal_code = models.CharField(_("Postal Code"), max_length=20, blank=True)

    is_active = models.BooleanField(_("Is Active"), default=True, db_index=True)
    coa_account = models.OneToOneField(
        "accounting.Account",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="warehouse",
        verbose_name=_("Chart of Accounts Link"),
        help_text=_("Linked CoA account (e.g. 1151 Primary Warehouse). Kept in sync on rename."),
    )

    class Meta:
        db_table = "warehouse"
        ordering = ["code", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "code"],
                name="warehouse_unique_name_code",
            )
        ]

    def __str__(self) -> str:
        return f"{self.code} - {self.name}"

    def has_transactions(self) -> bool:
        """
        Warehouse is considered 'locked' once any posted inventory transaction exists.
        (Currently: posted InventoryAdjustments. Extend later for purchases/sales stock moves.)
        """
        return self.inventory_adjustments.filter(is_deleted=False, status="posted").exists()

    def save(self, *args, **kwargs):
        # Track name changes to sync CoA account name
        old_name = None
        if self.pk:
            try:
                old = Warehouse.objects.get(pk=self.pk)
                old_name = old.name
            except Warehouse.DoesNotExist:
                old_name = None

        super().save(*args, **kwargs)

        # Sync CoA account name if linked and warehouse name changed
        if self.coa_account_id and old_name is not None and old_name != self.name:
            from accounting.models import Account

            Account.objects.filter(pk=self.coa_account_id).update(name=self.name)

    @property
    def address_display(self) -> str:
        parts = [
            self.street_address,
            self.building_number,
            self.district,
            self.city,
            self.postal_code,
        ]
        return ", ".join([p for p in parts if p])


class InventoryAdjustment(BaseModel):
    """
    Inventory adjustment header (one adjustment can contain multiple lines).
    Draft adjustments are editable; posted adjustments are immutable.
    Posting creates a Journal Entry and updates product stock quantities.
    """

    adjustment_id = models.CharField(
        _("Adjustment ID"),
        max_length=20,
        unique=True,
        blank=True,
        db_index=True,
        help_text="Auto-generated sequential ID (e.g. ADJ-000001). Assigned on posting.",
    )
    reference = models.CharField(_("Reference"), max_length=64, blank=True, db_index=True)
    date = models.DateField(_("Date"), db_index=True, default=timezone.now)
    warehouse = models.ForeignKey(
        "products.Warehouse",
        on_delete=models.PROTECT,
        related_name="inventory_adjustments",
        verbose_name=_("Warehouse"),
    )
    status = models.CharField(
        _("Status"),
        max_length=10,
        choices=INVENTORY_ADJUSTMENT_STATUS_CHOICES,
        default="draft",
        db_index=True,
    )
    posted_at = models.DateTimeField(_("Posted At"), null=True, blank=True)
    journal_entry = models.OneToOneField(
        "accounting.JournalEntry",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inventory_adjustment",
        verbose_name=_("Journal Entry"),
        help_text="Journal entry created when this adjustment is posted.",
    )

    class Meta:
        db_table = "inventory_adjustment"
        ordering = ["-date", "-created_at"]

    def __str__(self) -> str:
        return self.adjustment_id or f"DRAFT-{str(self.pk)[:8]}"

    @classmethod
    def _next_adjustment_id(cls) -> str:
        with transaction.atomic():
            cls.objects.select_for_update().filter(adjustment_id__startswith="ADJ-").exists()
            from django.db.models import Max

            result = cls.objects.aggregate(max_id=Max("adjustment_id"))
            max_id = result.get("max_id") or "ADJ-000000"
            match = re.search(r"ADJ-(\d+)$", max_id)
            num = int(match.group(1)) + 1 if match else 1
            return f"ADJ-{num:06d}"

    def total_adjustment_amount(self) -> Decimal:
        total = (
            self.lines.filter(is_deleted=False)
            .aggregate(total=models.Sum("inventory_value_delta"))
            .get("total")
        )
        return total or Decimal("0")


class InventoryAdjustmentLine(BaseModel):
    adjustment = models.ForeignKey(
        InventoryAdjustment,
        on_delete=models.PROTECT,
        related_name="lines",
        verbose_name=_("Inventory Adjustment"),
    )
    product = models.ForeignKey(
        "products.Product",
        on_delete=models.PROTECT,
        related_name="inventory_adjustment_lines",
        verbose_name=_("Item"),
    )
    description = models.TextField(_("Item Description"), blank=True)
    quantity_delta = models.DecimalField(
        _("Qty +/-"),
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Quantity added (+) or removed (-).",
    )
    inventory_value_delta = models.DecimalField(
        _("Inventory value +/-"),
        max_digits=18,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Total value of the quantity being adjusted (positive or negative).",
    )
    account = models.ForeignKey(
        "accounting.Account",
        on_delete=models.PROTECT,
        related_name="inventory_adjustment_lines",
        verbose_name=_("Account"),
        help_text="Offset account representing the reason (e.g., shrinkage, opening balance).",
    )
    line_order = models.PositiveIntegerField(_("Line Order"), default=0)

    class Meta:
        db_table = "inventory_adjustment_line"
        ordering = ["line_order", "created_at"]

    def __str__(self) -> str:
        return f"{self.product.code} | {self.quantity_delta} | {self.inventory_value_delta}"

