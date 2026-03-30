from django.urls import path
from main.views import (
    ApprovalRequestApproveAPI,
    ApprovalRequestDenyAPI,
    ApprovalRequestListAPI,
    CountryAPI,
    CompanySettingsAPI,
    DashboardAPIView,
    RoleAPI,
    StateAPI,
)

urlpatterns = [
    path("dashboard/", DashboardAPIView.as_view(), name="dashboard"),
    path("company-settings/", CompanySettingsAPI.as_view(), name="company-settings"),
    path("approvals/", ApprovalRequestListAPI.as_view(), name="approval-list"),
    path("approvals/<uuid:pk>/approve/", ApprovalRequestApproveAPI.as_view(), name="approval-approve"),
    path("approvals/<uuid:pk>/deny/", ApprovalRequestDenyAPI.as_view(), name="approval-deny"),
    path("role/list/", RoleAPI.as_view({"get": "list"}), name="role-list"),
    path("countries/", CountryAPI.as_view({"get": "list"}), name="country-list"),
    path(
        "states/<int:country_id>/",
        StateAPI.as_view({"get": "list"}),
        name="state-list",
    ),
]
