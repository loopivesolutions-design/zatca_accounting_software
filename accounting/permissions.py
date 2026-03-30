from rest_framework.permissions import BasePermission

from user.models import RolePermission


def _has_module_action(user, module: str, action_field: str) -> bool:
    if not user or not user.is_authenticated:
        return False
    if getattr(user, "is_superuser", False) or getattr(user, "is_staff", False):
        return True
    role = getattr(user, "role", None)
    if not role:
        return False
    return RolePermission.objects.filter(
        role=role,
        module=module,
        **{action_field: True},
        is_deleted=False,
    ).exists()


class CanPostSales(BasePermission):
    def has_permission(self, request, view):
        return _has_module_action(request.user, "sales", "can_approve")


class CanSubmitZatca(BasePermission):
    def has_permission(self, request, view):
        return _has_module_action(request.user, "vat_zatca", "can_approve")


class CanViewZatca(BasePermission):
    def has_permission(self, request, view):
        return _has_module_action(request.user, "vat_zatca", "can_view")


class CanPostPurchases(BasePermission):
    def has_permission(self, request, view):
        return _has_module_action(request.user, "purchase", "can_approve")


class CanClosePeriod(BasePermission):
    def has_permission(self, request, view):
        return _has_module_action(request.user, "accounting", "can_approve")

