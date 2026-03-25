import uuid
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from user.manager import CustomUserManager
from main.models import BaseModel


# ZATCA accounting software: modules and actions for RBAC
MODULE_CHOICES = (
    ("sales", "Sales"),
    ("purchase", "Purchase"),
    ("vat_zatca", "VAT&ZATCA"),
    ("customers", "Customers"),
    ("products", "Products"),
    ("banking", "Banking"),
    ("accounting", "Accounting"),
    ("reports", "Reports"),
    ("settings", "Settings"),
)


class Role(BaseModel):
    name = models.CharField(max_length=125, unique=True)

    class Meta:
        db_table = "user_role"
        verbose_name = _("role")
        verbose_name_plural = _("roles")

    def __str__(self):
        return self.name


class RolePermission(BaseModel):
    """Per-role, per-module permissions: view, create, edit, delete, approve."""
    role = models.ForeignKey(
        Role, on_delete=models.CASCADE, related_name="permissions"
    )
    module = models.CharField(max_length=64, choices=MODULE_CHOICES, db_index=True)
    can_view = models.BooleanField(default=False)
    can_create = models.BooleanField(default=False)
    can_edit = models.BooleanField(default=False)
    can_delete = models.BooleanField(default=False)
    can_approve = models.BooleanField(default=False)

    class Meta:
        db_table = "user_role_permission"
        verbose_name = _("role permission")
        verbose_name_plural = _("role permissions")
        unique_together = [("role", "module")]

    def __str__(self):
        return f"{self.role.name} / {self.get_module_display()}"


class CustomUser(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    role = models.ForeignKey(Role, on_delete=models.CASCADE, null=True, blank=True)
    first_name = models.CharField(_("First Name"), max_length=255)
    last_name = models.CharField(_("Last Name"), max_length=255)
    email = models.EmailField(_("Email"), unique=True, blank=True, null=True)
    phone = models.CharField(
        _("Phone Number with country code"),
        unique=True,
        max_length=125,
        blank=True,
        null=True,
    )
    is_deleted = models.BooleanField(_("Has the user been deleted?"), default=False)
    is_active = models.BooleanField(_("Is the user currently active?"), default=False)
    is_staff = models.BooleanField(_("Is the user a staff member?"), default=False)
    is_superuser = models.BooleanField(_("Is the user a superuser?"), default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]
    objects = CustomUserManager()

    class Meta:
        db_table = "user_customuser"
        verbose_name = _("user")
        verbose_name_plural = _("users")

    def __str__(self):
        return self.first_name

    def get_full_name(self):
        return f"{self.first_name} {self.last_name}"


class UserInvitation(BaseModel):
    """Invitation sent to a new user (Settings > Add User > Send Invitation)."""
    email = models.EmailField(_("Email"), db_index=True)
    first_name = models.CharField(_("First Name"), max_length=255, blank=True)
    last_name = models.CharField(_("Last Name"), max_length=255, blank=True)
    role = models.ForeignKey(
        Role, on_delete=models.CASCADE, null=True, blank=True, related_name="invitations"
    )
    invited_by = models.ForeignKey(
        "user.CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_invitations",
    )
    token = models.CharField(max_length=255, blank=True, db_index=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    is_expired = models.BooleanField(default=False)
    # is_deleted inherited from BaseModel

    class Meta:
        db_table = "user_invitation"
        verbose_name = _("user invitation")
        verbose_name_plural = _("user invitations")

    def __str__(self):
        return self.email
