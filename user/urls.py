from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from .views import (
    AdminLoginAPI,
    AdminSignupAPI,
    LoginAPI,
    RoleViewSet,
    RolePermissionsAPIView,
    UserManagementListAPI,
    UserManagementDetailAPI,
    PendingInvitationsAPI,
    SendInvitationAPI,
    ResendInvitationAPI,
    AcceptInvitationAPI,
)

app_name = "user"

urlpatterns = [
    # JWT
    path("token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),

    # Auth
    path("admin/create/", AdminSignupAPI.as_view(), name="admin-create"),
    path("admin/login/", AdminLoginAPI.as_view(), name="admin-login"),
    path("login/", LoginAPI.as_view(), name="login"),

    # Roles
    path("management/roles/", RoleViewSet.as_view({"get": "list", "post": "create"}), name="role-list-create"),
    path("management/roles/<uuid:pk>/", RoleViewSet.as_view({"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}), name="role-detail"),
    path("management/roles/<uuid:pk>/permissions/", RolePermissionsAPIView.as_view(), name="role-permissions"),

    # Users tab
    path("management/users/", UserManagementListAPI.as_view(), name="user-list"),
    path("management/users/<uuid:pk>/", UserManagementDetailAPI.as_view(), name="user-detail"),

    # Invitations
    path("management/invitations/", PendingInvitationsAPI.as_view(), name="invitation-list"),
    path("management/invitations/send/", SendInvitationAPI.as_view(), name="send-invitation"),
    path("management/invitations/<uuid:pk>/resend/", ResendInvitationAPI.as_view(), name="resend-invitation"),
    path("accept-invitation/", AcceptInvitationAPI.as_view(), name="accept-invitation"),
]
