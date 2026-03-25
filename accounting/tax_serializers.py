"""
Tax Rate serializers
====================
ZATCA Category is NEVER supplied by the user — it is always auto-computed.
Users only provide: name, name_ar, tax_type, rate, description.

Auto-assignment rules (matches FATOORAH XML spec):
  out_of_scope                → O  (Out of Scope)
  any rate > 0                → S  (Standard Rate)
  reverse_charge, rate == 0   → Z  (rare; defaults to Zero-rate)
  rate == 0, other types      → Z  (Zero-rate; system defaults cover E/Exempt cases)

Editing rules (Wafeq-style — immutable after creation):
  Locked forever  — tax_type, rate  (affect VAT calculations + ZATCA XML)
  Always editable — name, name_ar, description
  System-managed  — zatca_category  (auto-computed; never user input)
"""

from rest_framework import serializers
from .models import TaxRate, TAX_TYPE_CHOICES, ZATCA_CATEGORY_CHOICES

# Fields the user can set only at creation (locked afterwards)
LOCKED_FIELDS = frozenset({"tax_type", "rate"})

# Fields the user can always edit
EDITABLE_FIELDS = frozenset({"name", "name_ar", "description"})


def _auto_zatca_category(tax_type: str, rate) -> str:
    """
    Derive the ZATCA XML category code from tax_type and rate.
    This is the system rule — users never choose this manually.

      O — Out of Scope  (tax_type == out_of_scope)
      S — Standard Rate (rate > 0)
      Z — Zero Rate     (rate == 0, all other types)

    Note: E (Exempt) is reserved for system-seeded rates only (e.g. "Exempt Purchases",
    "Exempt"). Users creating custom 0% rates always get Z.
    """
    if tax_type == "out_of_scope":
        return "O"
    if float(rate) > 0:
        return "S"
    return "Z"


class TaxRateSerializer(serializers.ModelSerializer):
    """
    Single serializer for create, update, retrieve, and list.

    ON CREATE  — user provides: name, name_ar, tax_type, rate, description.
                 System auto-assigns zatca_category.
    ON UPDATE  — only name, name_ar, description are accepted.
                 tax_type, rate, zatca_category are permanently locked.
    ON READ    — all fields including zatca_category are returned.
    """

    tax_type_display = serializers.CharField(source="get_tax_type_display", read_only=True)
    zatca_category_display = serializers.CharField(source="get_zatca_category_display", read_only=True)
    has_transactions = serializers.SerializerMethodField()
    edit_metadata = serializers.SerializerMethodField()

    class Meta:
        model = TaxRate
        fields = [
            "id",
            "name",
            "name_ar",
            "tax_type",
            "tax_type_display",
            "rate",
            "description",
            "zatca_category",
            "zatca_category_display",
            "is_default",
            "is_active",
            "has_transactions",
            "edit_metadata",
            "created_at",
            "updated_at",
        ]
        # zatca_category is system-managed — never writable by user
        read_only_fields = [
            "id",
            "is_default",
            "zatca_category",
            "zatca_category_display",
            "created_at",
            "updated_at",
        ]

    def get_fields(self):
        """Lock tax_type and rate on update (instance already exists)."""
        fields = super().get_fields()
        if self.instance is not None:
            for field_name in LOCKED_FIELDS:
                if field_name in fields:
                    fields[field_name].read_only = True
        return fields

    def get_has_transactions(self, obj) -> bool:
        return obj.has_transactions()

    def get_edit_metadata(self, obj) -> dict:
        return {
            "locked_fields": sorted(LOCKED_FIELDS),
            "editable_fields": sorted(EDITABLE_FIELDS),
            "system_managed_fields": ["zatca_category"],
            "has_transactions": obj.has_transactions(),
            "lock_reason": "IMMUTABLE_AFTER_CREATION",
            "lock_message": (
                "Tax Type and Tax Rate are permanently locked after creation. "
                "ZATCA Category is always auto-assigned by the system. "
                "Changing these would corrupt historical invoice calculations and ZATCA XML."
            ),
        }

    def validate_rate(self, value):
        if value < 0 or value > 100:
            raise serializers.ValidationError("Tax rate must be between 0 and 100.")
        return value

    def create(self, validated_data):
        """Auto-assign zatca_category before saving — never trust user input for this."""
        validated_data["zatca_category"] = _auto_zatca_category(
            validated_data["tax_type"],
            validated_data["rate"],
        )
        return super().create(validated_data)


class TaxRateChoicesSerializer(serializers.Serializer):
    """Returns dropdown choices for the Create Tax Rate form (no zatca_category — system-managed)."""

    tax_types = serializers.SerializerMethodField()

    def get_tax_types(self, _):
        return [{"value": v, "label": l} for v, l in TAX_TYPE_CHOICES]
