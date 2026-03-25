from rest_framework import serializers
from .models import Account, CASH_FLOW_TYPE_CHOICES, ACCOUNT_TYPE_CHOICES, ZATCA_MAPPING_CHOICES
from .validators import AccountValidator


class AccountFlatSerializer(serializers.ModelSerializer):
    """Flat representation — used for list, create, update."""
    parent_name = serializers.SerializerMethodField()
    cash_flow_type_display = serializers.CharField(
        source="get_cash_flow_type_display", read_only=True
    )
    account_type_display = serializers.CharField(
        source="get_account_type_display", read_only=True
    )
    zatca_mapping_display = serializers.CharField(
        source="get_zatca_mapping_display", read_only=True
    )
    has_children = serializers.SerializerMethodField()
    has_transactions = serializers.SerializerMethodField()
    edit_metadata = serializers.SerializerMethodField()

    class Meta:
        model = Account
        fields = [
            "id",
            "parent",
            "parent_name",
            "name",
            "name_ar",
            "code",
            "cash_flow_type",
            "cash_flow_type_display",
            "account_type",
            "account_type_display",
            "account_sub_type",
            "zatca_mapping",
            "zatca_mapping_display",
            "enable_payment",
            "show_in_expense_claim",
            "is_locked",
            "is_archived",
            "has_children",
            "has_transactions",
            "edit_metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "is_locked"]

    def get_parent_name(self, obj):
        return obj.parent.name if obj.parent else None

    def get_has_children(self, obj):
        return obj.children.filter(is_deleted=False).exists()

    def get_has_transactions(self, obj):
        return AccountValidator.has_transactions(obj)

    def get_edit_metadata(self, obj):
        return AccountValidator.get_edit_metadata(obj)

    def validate_code(self, value):
        qs = Account.objects.filter(code=value, is_deleted=False)
        instance = self.instance
        if instance:
            qs = qs.exclude(pk=instance.pk)
        if qs.exists():
            raise serializers.ValidationError("An account with this code already exists.")
        return value

    def validate(self, attrs):
        parent = attrs.get("parent") or (self.instance.parent if self.instance else None)
        if self.instance and parent:
            node = parent
            while node is not None:
                if node.pk == self.instance.pk:
                    raise serializers.ValidationError(
                        {"parent": "Circular hierarchy detected."}
                    )
                node = node.parent
        return attrs


class AccountTreeSerializer(serializers.ModelSerializer):
    """Recursive nested representation — used for the tree endpoint."""
    children = serializers.SerializerMethodField()
    cash_flow_type_display = serializers.CharField(
        source="get_cash_flow_type_display", read_only=True
    )
    account_type_display = serializers.CharField(
        source="get_account_type_display", read_only=True
    )
    zatca_mapping_display = serializers.CharField(
        source="get_zatca_mapping_display", read_only=True
    )

    class Meta:
        model = Account
        fields = [
            "id",
            "name",
            "name_ar",
            "code",
            "cash_flow_type",
            "cash_flow_type_display",
            "account_type",
            "account_type_display",
            "account_sub_type",
            "zatca_mapping",
            "zatca_mapping_display",
            "enable_payment",
            "show_in_expense_claim",
            "is_locked",
            "is_archived",
            "children",
        ]

    def get_children(self, obj):
        qs = obj.children.filter(is_deleted=False).order_by("code")
        # Respect include_archived context flag (passed from view)
        if not self.context.get("include_archived", False):
            qs = qs.filter(is_archived=False)
        return AccountTreeSerializer(qs, many=True, context=self.context).data


class AccountChoicesSerializer(serializers.Serializer):
    """Returns all dropdown choices for the Create Account form."""

    def to_representation(self, instance):
        accounts = Account.objects.filter(
            is_deleted=False, is_archived=False
        ).order_by("code").values("id", "code", "name")
        return {
            "cash_flow_types": [{"value": k, "label": v} for k, v in CASH_FLOW_TYPE_CHOICES],
            "account_types": [{"value": k, "label": v} for k, v in ACCOUNT_TYPE_CHOICES],
            "zatca_mappings": [{"value": k, "label": v} for k, v in ZATCA_MAPPING_CHOICES],
            "parent_accounts": [
                {"id": str(a["id"]), "code": a["code"], "name": a["name"]}
                for a in accounts
            ],
        }
