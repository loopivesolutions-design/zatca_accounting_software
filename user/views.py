"""ZATCA user app: admin auth, login, and User & Role Management only."""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet
from rest_framework.generics import ListAPIView
from rest_framework.filters import SearchFilter
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.permissions import BasePermission
from rest_framework_simplejwt.tokens import RefreshToken
from django.shortcuts import get_object_or_404

from main.pagination import CustomPagination
from main.management.commands.create_groups_and_permissions import IsAdmin
from main.idempotency import begin_idempotent, finalize_idempotent_failure, finalize_idempotent_success

from .serializers import (
    AdminLoginSerializer,
    AdminUserSerializer,
    EmailPasswordLoginSerializer,
    RoleSerializer,
    RoleDetailSerializer,
    UserManagementListSerializer,
    UserManagementDetailSerializer,
    PendingInvitationSerializer,
    SendInvitationBulkSerializer,
    AcceptInvitationSerializer,
    send_invitation_email,
)
from .models import CustomUser, Role, RolePermission, UserInvitation


class IsSuperUser(BasePermission):
    """Allow access only to superusers."""
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_superuser)


class AdminSignupAPI(APIView):
    """Create an admin user. Only superusers can call this."""
    permission_classes = [IsSuperUser]
    serializer_class = AdminUserSerializer

    def post(self, request):
        serializer = AdminUserSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(
                {"message": "Admin user created successfully."},
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class AdminLoginAPI(APIView):
    """Admin login (email + password, returns JWT)."""
    permission_classes = [AllowAny]
    serializer_class = AdminLoginSerializer

    def post(self, request):
        serializer = AdminLoginSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.validated_data["user"]
            refresh = RefreshToken.for_user(user)
            return Response(
                {
                    "message": "Login successful.",
                    "user": {
                        "email": user.email,
                        "first_name": user.first_name,
                        "last_name": user.last_name,
                        "role": user.role.name if user.role else None,
                    },
                    "refresh": str(refresh),
                    "access": str(refresh.access_token),
                },
                status=status.HTTP_200_OK,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class LoginAPI(APIView):
    """Email + password login for any user (returns JWT)."""
    permission_classes = [AllowAny]
    serializer_class = EmailPasswordLoginSerializer

    def post(self, request):
        serializer = EmailPasswordLoginSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "message": "Login successful.",
                "user_id": user.id,
                "refresh_token": str(refresh),
                "access_token": str(refresh.access_token),
                "role": user.role.name if user.role else None,
            },
            status=status.HTTP_200_OK,
        )


class RoleViewSet(ModelViewSet):
    """List, create, retrieve, update, delete roles (ZATCA Settings > Role)."""
    permission_classes = [IsAuthenticated, IsAdmin]
    serializer_class = RoleSerializer
    queryset = Role.objects.all().order_by("name")
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]


class RolePermissionsAPIView(APIView):
    """Get or update permissions for a role (ZATCA Settings > Role permissions matrix)."""
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request, pk):
        role = get_object_or_404(Role, pk=pk)
        serializer = RoleDetailSerializer(role)
        return Response(serializer.data)

    def put(self, request, pk):
        role = get_object_or_404(Role, pk=pk)
        permissions = request.data.get("permissions", [])
        for item in permissions:
            module = item.get("module")
            if not module:
                continue
            RolePermission.objects.update_or_create(
                role=role,
                module=module,
                defaults={
                    "can_view": item.get("can_view", False),
                    "can_create": item.get("can_create", False),
                    "can_edit": item.get("can_edit", False),
                    "can_delete": item.get("can_delete", False),
                    "can_approve": item.get("can_approve", False),
                },
            )
        serializer = RoleDetailSerializer(role)
        return Response(serializer.data)


class UserManagementListAPI(ListAPIView):
    """List users — Users tab (Name, Email, Role, Last Login)."""
    permission_classes = [IsAuthenticated, IsAdmin]
    serializer_class = UserManagementListSerializer
    pagination_class = CustomPagination
    filter_backends = [SearchFilter]
    search_fields = ["first_name", "last_name", "email"]

    def get_queryset(self):
        qs = CustomUser.objects.filter(is_deleted=False).select_related("role")
        allowed = ("id", "email", "last_login", "first_name", "last_name", "created_at")
        order = self.request.query_params.get("ordering", "-created_at")
        if order.lstrip("-") in allowed:
            qs = qs.order_by(order)
        else:
            qs = qs.order_by("-created_at")
        return qs


class UserManagementDetailAPI(APIView):
    """Retrieve, update, or soft-delete a single user."""
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request, pk):
        user = get_object_or_404(CustomUser, pk=pk, is_deleted=False)
        serializer = UserManagementDetailSerializer(user)
        return Response(serializer.data)

    def patch(self, request, pk):
        user = get_object_or_404(CustomUser, pk=pk, is_deleted=False)
        serializer = UserManagementDetailSerializer(user, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        user = get_object_or_404(CustomUser, pk=pk, is_deleted=False)
        user.is_deleted = True
        user.is_active = False
        user.save()
        return Response({"message": "User deleted successfully."}, status=status.HTTP_200_OK)


class PendingInvitationsAPI(ListAPIView):
    """
    List invitations. Optional ?status= filter: pending | accepted | rejected
    """
    permission_classes = [IsAuthenticated, IsAdmin]
    serializer_class = PendingInvitationSerializer
    pagination_class = CustomPagination

    def get_queryset(self):
        qs = UserInvitation.objects.select_related("role", "invited_by").order_by("-created_at")
        status_filter = self.request.query_params.get("status", "").lower()
        if status_filter == "pending":
            qs = qs.filter(accepted_at__isnull=True, is_expired=False)
        elif status_filter == "accepted":
            qs = qs.filter(accepted_at__isnull=False)
        elif status_filter == "rejected":
            qs = qs.filter(accepted_at__isnull=True, is_expired=True)
        return qs


class AcceptInvitationAPI(APIView):
    """
    GET  — verify token, return invitation details (name, email, role).
    POST — set password and activate the account.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        token = request.query_params.get("token", "")
        if not token:
            return Response({"error": "token is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            inv = UserInvitation.objects.select_related("role").get(
                token=token, is_expired=False, accepted_at__isnull=True
            )
        except UserInvitation.DoesNotExist:
            return Response({"error": "Invalid or expired invitation token."}, status=status.HTTP_400_BAD_REQUEST)
        return Response({
            "token": token,
            "first_name": inv.first_name,
            "last_name": inv.last_name,
            "email": inv.email,
            "role": inv.role.name if inv.role else None,
        })

    def post(self, request):
        serializer = AcceptInvitationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response({
            "message": "Account activated successfully. You can now log in.",
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "role": user.role.name if user.role else None,
        }, status=status.HTTP_201_CREATED)


class SendInvitationAPI(APIView):
    """Send invitations — Add User modal > Send Invitation button."""
    permission_classes = [IsAuthenticated, IsAdmin]

    def post(self, request):
        rec, early = begin_idempotent(request, scope="user.invitations.send")
        if early:
            return early
        serializer = SendInvitationBulkSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        result = serializer.save()
        response = Response(
            {"message": "Invitations sent.", "invitations": len(result["invitations"])},
            status=status.HTTP_201_CREATED,
        )
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response


class ResendInvitationAPI(APIView):
    """Resend the invitation email for a pending invitation."""
    permission_classes = [IsAuthenticated, IsAdmin]

    def post(self, request, pk):
        rec, early = begin_idempotent(request, scope="user.invitations.resend")
        if early:
            return early
        try:
            inv = UserInvitation.objects.select_related("role", "invited_by").get(
                pk=pk,
                accepted_at__isnull=True,
                is_expired=False,
            )
        except UserInvitation.DoesNotExist:
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error="NOT_FOUND",
                message="Invitation not found or already accepted/expired.",
                http_status=status.HTTP_404_NOT_FOUND,
            )

        import secrets
        inv.token = secrets.token_urlsafe(32)
        inv.save(update_fields=["token", "updated_at"])

        send_invitation_email(inv, request.user)
        response = Response({"message": f"Invitation resent to {inv.email}."}, status=status.HTTP_200_OK)
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response
