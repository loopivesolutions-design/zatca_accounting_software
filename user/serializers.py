"""ZATCA user app: serializers for admin auth, login, and User & Role Management only."""
import secrets
import logging
from rest_framework import serializers
from django.contrib.auth import authenticate
from django.core.mail import send_mail
from django.conf import settings as django_settings

from user.models import CustomUser, Role, UserInvitation
from user.models import MODULE_CHOICES

logger = logging.getLogger(__name__)


class AdminUserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    class Meta:
        model = CustomUser
        fields = ["email", "first_name", "last_name", "password"]

    def create(self, validated_data):
        role, _ = Role.objects.get_or_create(name="Admin")
        user = CustomUser.objects.create_admin(
            email=validated_data["email"],
            first_name=validated_data["first_name"],
            last_name=validated_data["last_name"],
            role=role,
        )
        user.set_password(validated_data["password"])
        user.save()
        return user


class AdminLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField()

    def validate(self, data):
        user = authenticate(
            request=self.context.get("request"),
            username=data["email"],
            password=data["password"],
        )
        if not user:
            raise serializers.ValidationError("Invalid email or password.")
        if not user.is_staff:
            raise serializers.ValidationError("You do not have admin privileges.")
        if not user.is_active:
            raise serializers.ValidationError("This account is inactive.")
        data["user"] = user
        return data


class EmailPasswordLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, data):
        user = authenticate(
            request=self.context.get("request"),
            username=data["email"],
            password=data["password"],
        )
        if not user:
            raise serializers.ValidationError("Invalid email or password.")
        if not user.is_active:
            raise serializers.ValidationError("This account is inactive.")
        data["user"] = user
        return data


class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ["id", "name"]


class RoleDetailSerializer(serializers.ModelSerializer):
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = Role
        fields = ["id", "name", "permissions"]

    def get_permissions(self, obj):
        perms_by_module = {p.module: p for p in obj.permissions.all()}
        result = []
        for module_key, module_label in MODULE_CHOICES:
            p = perms_by_module.get(module_key)
            result.append({
                "module": module_key,
                "module_display": module_label,
                "can_view": p.can_view if p else False,
                "can_create": p.can_create if p else False,
                "can_edit": p.can_edit if p else False,
                "can_delete": p.can_delete if p else False,
                "can_approve": p.can_approve if p else False,
            })
        return result


class UserManagementListSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    role_name = serializers.SerializerMethodField()
    last_login = serializers.DateTimeField(format="%Y-%m-%d %H:%M", read_only=True)

    class Meta:
        model = CustomUser
        fields = ["id", "name", "email", "role", "role_name", "last_login"]

    def get_name(self, obj):
        return obj.get_full_name() or f"{obj.first_name or ''} {obj.last_name or ''}".strip()

    def get_role_name(self, obj):
        return obj.role.name if obj.role else None


class UserManagementDetailSerializer(serializers.ModelSerializer):
    """For retrieving and updating a single user."""
    name = serializers.SerializerMethodField(read_only=True)
    role_name = serializers.SerializerMethodField(read_only=True)
    last_login = serializers.DateTimeField(format="%Y-%m-%d %H:%M", read_only=True)
    created_at = serializers.DateTimeField(format="%Y-%m-%d %H:%M", read_only=True)

    class Meta:
        model = CustomUser
        fields = [
            "id", "name", "first_name", "last_name",
            "email", "phone", "role", "role_name",
            "is_active", "last_login", "created_at",
        ]
        read_only_fields = ["id", "last_login", "created_at"]

    def get_name(self, obj):
        return obj.get_full_name()

    def get_role_name(self, obj):
        return obj.role.name if obj.role else None

    def validate_email(self, value):
        user = self.instance
        if CustomUser.objects.filter(email=value, is_deleted=False).exclude(pk=user.pk).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value


class PendingInvitationSerializer(serializers.ModelSerializer):
    """For listing invitations (all statuses: pending, accepted, rejected)."""
    role_name = serializers.SerializerMethodField()
    invited_by_name = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    
    class Meta:
        model = UserInvitation
        fields = [
            "id", "first_name", "last_name", "email",
            "role", "role_name", "invited_by_name",
            "created_at", "is_expired", "status",
        ]
    
    def get_role_name(self, obj):
        return obj.role.name if obj.role else None
    
    def get_invited_by_name(self, obj):
        return obj.invited_by.get_full_name() if obj.invited_by else None

    def get_status(self, obj):
        if obj.accepted_at is not None:
            return "accepted"
        if obj.is_expired:
            return "rejected"
        return "pending"


def send_invitation_email(invitation, invited_by):
    """Shared helper — build and dispatch an invitation email."""
    backend_url = getattr(django_settings, "BACKEND_URL", "http://127.0.0.1:8000")
    invite_link = f"{backend_url}/api/v1/user/accept-invitation/?token={invitation.token}"
    invited_by_name = invited_by.get_full_name() if invited_by else "ZATCA Team"
    recipient_name = f"{invitation.first_name} {invitation.last_name}".strip() or invitation.email
    role_name = invitation.role.name if invitation.role else "Team Member"

    subject = "You're invited to join ZATCA Accounting Software"
    message = (
        f"Hello {recipient_name},\n\n"
        f"You have been invited by {invited_by_name} to join ZATCA Accounting Software "
        f"as {role_name}.\n\n"
        f"Click the link below to accept your invitation and set up your account:\n"
        f"{invite_link}\n\n"
        f"This invitation link is unique to you. Please do not share it.\n\n"
        f"Best regards,\n"
        f"ZATCA Accounting Team"
    )
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=django_settings.DEFAULT_FROM_EMAIL,
            recipient_list=[invitation.email],
            fail_silently=False,
        )
    except Exception as e:
        logger.error(f"Failed to send invitation email to {invitation.email}: {e}")


class SendInvitationSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False, allow_blank=True)
    email = serializers.EmailField()
    role = serializers.PrimaryKeyRelatedField(
        queryset=Role.objects.all(), required=False, allow_null=True
    )

    def validate_email(self, value):
        if CustomUser.objects.filter(email=value, is_deleted=False).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value


class SendInvitationBulkSerializer(serializers.Serializer):
    users = SendInvitationSerializer(many=True)

    def create(self, validated_data):
        request = self.context.get("request")
        invited_by = request.user if request else None
        created = []

        for item in validated_data["users"]:
            name = (item.get("name") or "").strip()
            parts = name.split(None, 1)
            first_name = parts[0] if parts else ""
            last_name = parts[1] if len(parts) > 1 else ""
            role = item.get("role")
            email = item["email"].lower()
            token = secrets.token_urlsafe(32)

            inv, _ = UserInvitation.objects.update_or_create(
                email=email,
                defaults={
                    "first_name": first_name,
                    "last_name": last_name,
                    "role_id": role.id if role else None,
                    "invited_by": invited_by,
                    "is_expired": False,
                    "token": token,
                },
            )
            created.append(inv)
            self._send_invitation_email(inv, invited_by)

        return {"invitations": created}

    def _send_invitation_email(self, invitation, invited_by):
        send_invitation_email(invitation, invited_by)


class AcceptInvitationSerializer(serializers.Serializer):
    """Complete account setup after clicking the invitation link."""
    token = serializers.CharField()
    password = serializers.CharField(write_only=True, min_length=8)
    first_name = serializers.CharField(required=False, allow_blank=True)
    last_name = serializers.CharField(required=False, allow_blank=True)

    def validate_token(self, value):
        try:
            invitation = UserInvitation.objects.get(token=value, is_expired=False, accepted_at__isnull=True)
        except UserInvitation.DoesNotExist:
            raise serializers.ValidationError("Invalid or expired invitation token.")
        self._invitation = invitation
        return value

    def save(self):
        invitation = self._invitation
        first_name = self.validated_data.get("first_name") or invitation.first_name
        last_name = self.validated_data.get("last_name") or invitation.last_name
        password = self.validated_data["password"]

        # Create or activate the user
        user, created = CustomUser.objects.get_or_create(
            email=invitation.email,
            defaults={
                "first_name": first_name,
                "last_name": last_name,
                "role": invitation.role,
                "is_active": True,
            },
        )
        if not created:
            # User exists (e.g. re-invited) — update and reactivate
            user.first_name = first_name
            user.last_name = last_name
            user.role = invitation.role
            user.is_active = True

        user.set_password(password)
        user.save()

        # Mark invitation as accepted
        from django.utils import timezone
        invitation.accepted_at = timezone.now()
        invitation.is_expired = True
        invitation.save()

        return user
