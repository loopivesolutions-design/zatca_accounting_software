from rest_framework.viewsets import ModelViewSet
from rest_framework.response import Response
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404

from main.management.commands.create_groups_and_permissions import IsAdmin
from main.models import Country, State, CompanySettings
from main.serializers import CountrySerializer, RoleSerializer, StateSerializer, CompanySettingsSerializer
from user.models import Role, CustomUser


class DashboardAPIView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        total_users = CustomUser.objects.filter(is_deleted=False).count()
        return Response(
            {"total_users": total_users},
            status=200,
        )


class RoleAPI(ModelViewSet):
    serializer_class = RoleSerializer
    permission_classes = [IsAdmin]

    def get_queryset(self):
        return Role.objects.all().order_by("-id")

    def list(self, request):
        queryset = self.get_queryset()
        serializer = RoleSerializer(queryset, many=True)
        return Response(serializer.data)

    def retrieve(self, request, pk=None):
        role = get_object_or_404(Role, id=pk)
        serializer = RoleSerializer(role)
        return Response(serializer.data)


class CountryAPI(ModelViewSet):
    serializer_class = CountrySerializer

    def get_queryset(self):
        return Country.objects.all().order_by("-id")

    def list(self, request):
        queryset = self.get_queryset()
        serializer = CountrySerializer(queryset, many=True)
        return Response(serializer.data)

    def retrieve(self, request, pk=None):
        country = get_object_or_404(Country, id=pk)
        serializer = CountrySerializer(country)
        return Response(serializer.data)


class StateAPI(ModelViewSet):
    serializer_class = StateSerializer

    def get_queryset(self, country_id=None):
        queryset = State.objects.all()
        if country_id:
            queryset = queryset.filter(country_id=country_id)
        return queryset

    def list(self, request, country_id=None):
        country_id = country_id or request.resolver_match.kwargs.get("country_id")
        if not country_id:
            return Response({"error": "country_id is required."}, status=400)
        queryset = self.get_queryset(country_id=country_id)
        serializer = self.serializer_class(queryset, many=True)
        return Response(serializer.data)


class CompanySettingsAPI(APIView):
    """
    GET  /main/company-settings/  — get organization settings (singleton)
    PATCH /main/company-settings/ — update organization settings (multipart supported for logo)
    """

    permission_classes = [IsAdmin]

    def _get_obj(self):
        obj = CompanySettings.objects.first()
        if obj:
            return obj
        return CompanySettings.objects.create()

    def get(self, request):
        obj = self._get_obj()
        return Response(CompanySettingsSerializer(obj).data)

    def patch(self, request):
        obj = self._get_obj()
        serializer = CompanySettingsSerializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        obj = serializer.save()
        return Response(CompanySettingsSerializer(obj).data)

    def post(self, request):
        """
        POST acts as an upsert for frontend simplicity:
        - if singleton row exists → update it
        - else → create it
        Supports multipart/form-data for logo upload.
        """
        obj = CompanySettings.objects.first()
        if obj is None:
            serializer = CompanySettingsSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            obj = serializer.save()
            return Response(CompanySettingsSerializer(obj).data, status=201)

        serializer = CompanySettingsSerializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        obj = serializer.save()
        return Response(CompanySettingsSerializer(obj).data, status=200)
