"""
Journal Entry serializers
=========================
Covers four operations:
  CREATE  — build a draft entry with its lines
  READ    — full detail (lines + account summaries)
  UPDATE  — allowed only on draft entries
  POST    — transition draft → posted (immutable)
  REVERSE — create a reversal entry from a posted one
"""

from decimal import Decimal
from django.utils import timezone
from rest_framework import serializers

from .models import JournalEntry, JournalEntryLine
from .validators import JournalEntryValidator


# ──────────────────────────────────────────────────────────────────────────────
# Line serializers
# ──────────────────────────────────────────────────────────────────────────────

class JournalEntryLineWriteSerializer(serializers.ModelSerializer):
    """Used when creating or updating journal entry lines."""

    class Meta:
        model = JournalEntryLine
        fields = ["id", "account", "description", "debit", "credit", "line_order"]
        extra_kwargs = {
            "id": {"read_only": True},
            "line_order": {"required": False},
        }

    def validate(self, data):
        debit = data.get("debit", Decimal("0")) or Decimal("0")
        credit = data.get("credit", Decimal("0")) or Decimal("0")
        JournalEntryValidator.validate_line(debit, credit)
        return data


class JournalEntryLineReadSerializer(serializers.ModelSerializer):
    """Used when reading journal entry lines — includes account details."""

    account_code = serializers.CharField(source="account.code", read_only=True)
    account_name = serializers.CharField(source="account.name", read_only=True)
    account_type = serializers.CharField(source="account.account_type", read_only=True)

    class Meta:
        model = JournalEntryLine
        fields = [
            "id",
            "account",
            "account_code",
            "account_name",
            "account_type",
            "description",
            "debit",
            "credit",
            "line_order",
        ]


# ──────────────────────────────────────────────────────────────────────────────
# Journal Entry — list serializer (compact)
# ──────────────────────────────────────────────────────────────────────────────

class JournalEntryListSerializer(serializers.ModelSerializer):
    """Compact representation for list views."""

    total_debit = serializers.SerializerMethodField()
    is_reversed = serializers.SerializerMethodField()
    reversal_reference = serializers.SerializerMethodField()

    class Meta:
        model = JournalEntry
        fields = [
            "id",
            "reference",
            "date",
            "description",
            "status",
            "is_reversal",
            "is_reversed",
            "reversal_reference",
            "total_debit",
            "posted_at",
            "created_at",
        ]

    def get_total_debit(self, obj):
        lines = obj.lines.filter(is_deleted=False)
        return str(sum(l.debit for l in lines))

    def get_is_reversed(self, obj):
        return hasattr(obj, "reversed_by_entry") and obj.reversed_by_entry is not None

    def get_reversal_reference(self, obj):
        try:
            return obj.reversed_by_entry.reference
        except Exception:
            return None


# ──────────────────────────────────────────────────────────────────────────────
# Journal Entry — detail / create / update serializer
# ──────────────────────────────────────────────────────────────────────────────

class JournalEntrySerializer(serializers.ModelSerializer):
    """
    Full serializer for create / update / detail operations.

    On write  : accepts nested `lines` (minimum 2).
    On read   : returns lines with full account details.
    """

    lines = JournalEntryLineWriteSerializer(many=True, required=True)

    # Read-only computed fields
    total_debit = serializers.SerializerMethodField()
    total_credit = serializers.SerializerMethodField()
    is_reversed = serializers.SerializerMethodField()
    reversal_reference = serializers.SerializerMethodField()

    class Meta:
        model = JournalEntry
        fields = [
            "id",
            "reference",
            "date",
            "description",
            "status",
            "is_reversal",
            "is_reversed",
            "reversal_reference",
            "reversal_of",
            "total_debit",
            "total_credit",
            "posted_at",
            "created_at",
            "updated_at",
            "lines",
        ]
        read_only_fields = [
            "id", "reference", "status", "is_reversal",
            "reversal_of", "posted_at", "created_at", "updated_at",
        ]

    def get_total_debit(self, obj):
        lines = obj.lines.filter(is_deleted=False)
        return str(sum(l.debit for l in lines))

    def get_total_credit(self, obj):
        lines = obj.lines.filter(is_deleted=False)
        return str(sum(l.credit for l in lines))

    def get_is_reversed(self, obj):
        try:
            return obj.reversed_by_entry is not None
        except Exception:
            return False

    def get_reversal_reference(self, obj):
        try:
            return obj.reversed_by_entry.reference
        except Exception:
            return None

    def validate_lines(self, lines):
        if len(lines) < 2:
            raise serializers.ValidationError(
                "A journal entry requires at least 2 lines."
            )
        return lines

    def create(self, validated_data):
        lines_data = validated_data.pop("lines")
        entry = JournalEntry.objects.create(**validated_data)
        for idx, line_data in enumerate(lines_data):
            line_data.setdefault("line_order", idx)
            JournalEntryLine.objects.create(
                journal_entry=entry,
                creator=validated_data.get("creator"),
                **line_data,
            )
        return entry

    def update(self, instance, validated_data):
        """
        Update a draft journal entry.
        Replaces all existing lines with the new set.
        Rule: posted entries are read-only (enforced in the view before calling update).
        """
        lines_data = validated_data.pop("lines", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if lines_data is not None:
            # Soft-delete existing lines and recreate
            instance.lines.update(is_deleted=True)
            for idx, line_data in enumerate(lines_data):
                line_data.setdefault("line_order", idx)
                JournalEntryLine.objects.create(
                    journal_entry=instance,
                    creator=self.context["request"].user,
                    **line_data,
                )
        return instance


# ──────────────────────────────────────────────────────────────────────────────
# Read-only detail serializer (with full account info on lines)
# ──────────────────────────────────────────────────────────────────────────────

class JournalEntryDetailSerializer(JournalEntrySerializer):
    """Extends JournalEntrySerializer with full account detail on each line."""

    lines = serializers.SerializerMethodField()

    def get_lines(self, obj):
        qs = obj.lines.filter(is_deleted=False).select_related("account")
        return JournalEntryLineReadSerializer(qs, many=True).data


# ──────────────────────────────────────────────────────────────────────────────
# Reversal request serializer
# ──────────────────────────────────────────────────────────────────────────────

class JournalEntryReverseSerializer(serializers.Serializer):
    """Input for the /reverse/ endpoint."""

    description = serializers.CharField(
        required=False, allow_blank=True,
        help_text="Optional memo for the reversal entry. Defaults to 'Reversal of <reference>'.",
    )
    date = serializers.DateField(
        required=False,
        help_text="Optional date for the reversal entry. Defaults to today.",
    )
    auto_post = serializers.BooleanField(
        default=False,
        help_text=(
            "If true, immediately post the reversal entry after creating it. "
            "Defaults to false (creates as draft for review)."
        ),
    )

    def validate_date(self, value):
        if value and value < self.context.get("original_date"):
            raise serializers.ValidationError(
                "Reversal date cannot be before the original entry date."
            )
        return value
