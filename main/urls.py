from django.urls import path
from main.views import (
    CountryAPI,
    CompanySettingsAPI,
    DashboardAPIView,
    RoleAPI,
    StateAPI,
)

urlpatterns = [
    path("dashboard/", DashboardAPIView.as_view(), name="dashboard"),
    path("company-settings/", CompanySettingsAPI.as_view(), name="company-settings"),
    path("role/list/", RoleAPI.as_view({"get": "list"}), name="role-list"),
    path("countries/", CountryAPI.as_view({"get": "list"}), name="country-list"),
    path(
        "states/<int:country_id>/",
        StateAPI.as_view({"get": "list"}),
        name="state-list",
    ),
]
