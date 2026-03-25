from django.urls import path
from .views import (
    ProductCategoryListCreateAPI,
    ProductCategoryDetailAPI,
    ProductCategoryChoicesAPI,
    ProductCategoryTreeAPI,
    ProductCategoryBulkAPI,
    UnitOfMeasureListCreateAPI,
    UnitOfMeasureDetailAPI,
    ProductListCreateAPI,
    ProductDetailAPI,
    WarehouseListCreateAPI,
    WarehouseDetailAPI,
    WarehouseBulkAPI,
    InventoryAdjustmentListCreateAPI,
    InventoryAdjustmentDetailAPI,
    InventoryAdjustmentPostAPI,
)

app_name = "products"

urlpatterns = [
    # Dropdown choices (parent category selector)
    path("categories/choices/", ProductCategoryChoicesAPI.as_view(), name="category-choices"),

    # Nested tree of all active categories
    path("categories/tree/", ProductCategoryTreeAPI.as_view(), name="category-tree"),

    # Bulk actions: set_status | delete | duplicate
    path("categories/bulk/", ProductCategoryBulkAPI.as_view(), name="category-bulk"),

    # Paginated flat list + create
    path("categories/", ProductCategoryListCreateAPI.as_view(), name="category-list-create"),

    # Retrieve / update / delete
    path("categories/<uuid:pk>/", ProductCategoryDetailAPI.as_view(), name="category-detail"),

    # Units of measure
    path("uom/", UnitOfMeasureListCreateAPI.as_view(), name="uom-list-create"),
    path("uom/<uuid:pk>/", UnitOfMeasureDetailAPI.as_view(), name="uom-detail"),

    # Products / Items
    path("items/", ProductListCreateAPI.as_view(), name="product-list-create"),
    path("items/<uuid:pk>/", ProductDetailAPI.as_view(), name="product-detail"),

    # Warehouses
    path("warehouses/", WarehouseListCreateAPI.as_view(), name="warehouse-list-create"),
    path("warehouses/<uuid:pk>/", WarehouseDetailAPI.as_view(), name="warehouse-detail"),
    path("warehouses/bulk/", WarehouseBulkAPI.as_view(), name="warehouse-bulk"),

    # Inventory adjustments
    path("inventory/adjustments/", InventoryAdjustmentListCreateAPI.as_view(), name="inventory-adjustment-list-create"),
    path("inventory/adjustments/<uuid:pk>/", InventoryAdjustmentDetailAPI.as_view(), name="inventory-adjustment-detail"),
    path("inventory/adjustments/<uuid:pk>/post/", InventoryAdjustmentPostAPI.as_view(), name="inventory-adjustment-post"),
]
