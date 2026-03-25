from django.core.management.base import BaseCommand
from rest_framework.permissions import BasePermission
from user.models import Role, RolePermission, MODULE_CHOICES


class Command(BaseCommand):
    help = "Seed default module rows (all permissions False) for every role in the database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--create-roles",
            action="store_true",
            help="If no roles exist, create a minimal 'Admin' role automatically before seeding.",
        )

    def handle(self, *args, **kwargs):
        roles = Role.objects.filter(is_deleted=False)
        if not roles.exists():
            if kwargs.get("create_roles"):
                Role.objects.create(name="Admin")
                roles = Role.objects.filter(is_deleted=False)
                self.stdout.write(self.style.SUCCESS(
                    "No roles found. Created role 'Admin' automatically."
                ))
            else:
                self.stdout.write(self.style.WARNING(
                    "No roles found. Re-run with --create-roles to create a minimal 'Admin' role, "
                    "or create roles via the API first."
                ))
                return

        created_total = 0
        for role in roles:
            created_count = 0
            for module_key, module_label in MODULE_CHOICES:
                _, created = RolePermission.objects.get_or_create(
                    role=role,
                    module=module_key,
                    defaults={
                        "can_view": False,
                        "can_create": False,
                        "can_edit": False,
                        "can_delete": False,
                        "can_approve": False,
                    },
                )
                if created:
                    created_count += 1
            created_total += created_count
            self.stdout.write(
                self.style.SUCCESS(
                    f"  Role '{role.name}': {created_count} module(s) seeded, "
                    f"{len(MODULE_CHOICES) - created_count} already existed."
                )
            )

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. {created_total} new module permission row(s) created across {roles.count()} role(s)."
        ))


class IsAdmin(BasePermission):
    """Admin access: Django staff/superuser or role name 'Admin' (ZATCA)."""
    def has_permission(self, request, view):
        if getattr(request.user, "is_staff", False) or getattr(request.user, "is_superuser", False):
            return True
        role = getattr(request.user, "role", None)
        return role and role.name == "Admin"
