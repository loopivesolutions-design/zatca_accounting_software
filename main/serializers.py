from rest_framework import serializers
from user.models import Role
from main.models import Country, State, CompanySettings


class RoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Role
        fields = ["id", "name"]


class CountrySerializer(serializers.ModelSerializer):
    class Meta:
        model = Country
        fields = "__all__"


class StateSerializer(serializers.ModelSerializer):
    country = CountrySerializer()

    class Meta:
        model = State
        fields = "__all__"


class CompanySettingsSerializer(serializers.ModelSerializer):
    country_name = serializers.CharField(source="country.name", read_only=True)

    class Meta:
        model = CompanySettings
        fields = [
            "id",
            "logo",
            "company_name",
            "company_name_ar",
            "street_address",
            "street_address_ar",
            "building_number",
            "district",
            "district_ar",
            "city",
            "city_ar",
            "country",
            "country_name",
            "postal_code",
            "cr_number",
            "vat_registration_number",
            "industry",
            "email",
            "phone",
        ]
        read_only_fields = ["id", "country_name"]
