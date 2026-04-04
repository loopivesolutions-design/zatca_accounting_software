from rest_framework import serializers

from decimal import Decimal

from .models import (
    Supplier,
    PAYMENT_TERMS_CHOICES,
    VAT_TREATMENT_CHOICES,
    OPENING_BALANCE_CHOICES,
    Bill,
    BillLine,
    BILL_STATUS_CHOICES,
    SupplierPayment,
    SupplierPaymentAllocation,
    SUPPLIER_PAYMENT_TYPE_CHOICES,
    DebitNote,
    DebitNoteLine,
    DEBIT_NOTE_STATUS_CHOICES,
    SupplierRefund,
    SupplierRefundAllocation,
)


class SupplierSerializer(serializers.ModelSerializer):
    country_name = serializers.CharField(source="country.name", read_only=True)
    payment_terms_display = serializers.SerializerMethodField()
    vat_treatment_display = serializers.SerializerMethodField()
    opening_balance_type_display = serializers.SerializerMethodField()
    opening_balance_account_name = serializers.CharField(source="opening_balance_account.name", read_only=True)
    opening_balance_account_code = serializers.CharField(source="opening_balance_account.code", read_only=True)
    # Allow null so the frontend can explicitly clear TRN for non-registered suppliers
    tax_registration_number = serializers.CharField(allow_null=True, allow_blank=True, required=False, default="")

    class Meta:
        model = Supplier
        fields = [
            "id",
            "company_name",
            "company_name_ar",
            "primary_contact_name",
            "email",
            "phone",
            "vat_treatment",
            "vat_treatment_display",
            "tax_registration_number",
            "country",
            "country_name",
            "street_address",
            "street_address_ar",
            "building_number",
            "land_identifier",
            "district",
            "district_ar",
            "city",
            "city_ar",
            "postal_code",
            "payment_terms",
            "payment_terms_display",
            "opening_balance_type",
            "opening_balance_type_display",
            "opening_balance_amount",
            "opening_balance_as_of",
            "opening_balance_account",
            "opening_balance_account_code",
            "opening_balance_account_name",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_payment_terms_display(self, obj) -> str | None:
        return dict(PAYMENT_TERMS_CHOICES).get(obj.payment_terms) if obj.payment_terms else None

    def get_vat_treatment_display(self, obj) -> str:
        return dict(VAT_TREATMENT_CHOICES).get(obj.vat_treatment, obj.vat_treatment)

    def get_opening_balance_type_display(self, obj) -> str:
        return dict(OPENING_BALANCE_CHOICES).get(obj.opening_balance_type, obj.opening_balance_type)

    def validate(self, attrs):
        """
        Opening balance rules (matches UI behavior):
        - If opening_balance_type == 'none': ignore amount/date/account (set to 0/null)
        - Otherwise: require amount > 0 and as_of date

        TRN rules:
        - 'not_vat_registered_ksa': TRN must be blank (null/empty → coerced to "")
        - 'vat_registered_ksa' / 'outside_ksa': TRN required and must be a valid 15-digit number
        """
        # Coerce null TRN to empty string (model field is CharField, not nullable)
        if attrs.get("tax_registration_number") is None:
            attrs["tax_registration_number"] = ""

        vat_treatment = attrs.get("vat_treatment")
        if vat_treatment is None and self.instance is not None:
            vat_treatment = self.instance.vat_treatment

        if vat_treatment == "not_vat_registered_ksa":
            attrs["tax_registration_number"] = ""
        elif vat_treatment in ("vat_registered_ksa", "outside_ksa"):
            trn = attrs.get("tax_registration_number", "").strip()
            if not trn:
                raise serializers.ValidationError(
                    {"tax_registration_number": "Tax registration number is required for this VAT treatment."}
                )
            digits = "".join(c for c in trn if c.isdigit())
            if len(digits) != 15 or not (digits.startswith("3") and digits.endswith("3")):
                raise serializers.ValidationError(
                    {"tax_registration_number": "Tax registration number must be exactly 15 digits and start and end with 3."}
                )
            attrs["tax_registration_number"] = digits

        opening_type = attrs.get("opening_balance_type")
        # During PATCH, fall back to existing value if not provided
        if opening_type is None and self.instance is not None:
            opening_type = self.instance.opening_balance_type

        # Coerce empty-string amount from frontend to 0
        if "opening_balance_amount" in attrs and attrs["opening_balance_amount"] in ("", None):
            attrs["opening_balance_amount"] = 0

        if opening_type == "none":
            attrs["opening_balance_amount"] = 0
            attrs["opening_balance_as_of"] = None
            attrs["opening_balance_account"] = None
            return attrs

        # If user chose an opening balance direction, require required fields
        amount = attrs.get("opening_balance_amount")
        if amount is None and self.instance is not None:
            amount = self.instance.opening_balance_amount
        as_of = attrs.get("opening_balance_as_of") if "opening_balance_as_of" in attrs else (
            self.instance.opening_balance_as_of if self.instance is not None else None
        )

        if not amount or float(amount) <= 0:
            raise serializers.ValidationError(
                {"opening_balance_amount": "Amount is required and must be greater than 0."}
            )

        if not as_of:
            raise serializers.ValidationError(
                {"opening_balance_as_of": "As of date is required when opening balance is set."}
            )

        return attrs


class SupplierChoicesSerializer(serializers.Serializer):
    payment_terms = serializers.ListField(child=serializers.DictField())
    vat_treatments = serializers.ListField(child=serializers.DictField())
    opening_balance_types = serializers.ListField(child=serializers.DictField())


class BillLineSerializer(serializers.ModelSerializer):
    account_code = serializers.CharField(source="account.code", read_only=True)
    account_name = serializers.CharField(source="account.name", read_only=True)
    tax_rate_name = serializers.CharField(source="tax_rate.name", read_only=True)
    tax_rate_percent = serializers.DecimalField(source="tax_rate.rate", max_digits=5, decimal_places=2, read_only=True)
    line_subtotal = serializers.SerializerMethodField()
    line_tax_amount = serializers.SerializerMethodField()
    line_total = serializers.SerializerMethodField()

    class Meta:
        model = BillLine
        fields = [
            "id",
            "description",
            "account",
            "account_code",
            "account_name",
            "quantity",
            "unit_price",
            "tax_rate",
            "tax_rate_name",
            "tax_rate_percent",
            "discount_percent",
            "line_order",
            "line_subtotal",
            "line_tax_amount",
            "line_total",
        ]
        read_only_fields = ["id"]

    def get_line_subtotal(self, obj) -> str:
        return str(obj.subtotal())

    def get_line_tax_amount(self, obj) -> str:
        return str(obj.tax_amount())

    def get_line_total(self, obj) -> str:
        return str(obj.total())


class BillSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.company_name", read_only=True)
    status_display = serializers.SerializerMethodField()
    lines = BillLineSerializer(many=True)
    balance_amount = serializers.SerializerMethodField()

    class Meta:
        model = Bill
        fields = [
            "id",
            "bill_number",
            "external_reference",
            "supplier",
            "supplier_name",
            "bill_date",
            "due_date",
            "note",
            "attachment",
            "status",
            "status_display",
            "posted_at",
            "journal_entry",
            "subtotal",
            "total_vat",
            "total_amount",
            "paid_amount",
            "balance_amount",
            "lines",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "posted_at",
            "journal_entry",
            "subtotal",
            "total_vat",
            "total_amount",
            "paid_amount",
            "balance_amount",
            "created_at",
            "updated_at",
        ]

    def get_status_display(self, obj) -> str:
        from decimal import Decimal
        if obj.status in ("paid", "partially_paid"):
            return dict(BILL_STATUS_CHOICES).get(obj.status, obj.status)
        if obj.status == "posted":
            paid = obj.paid_amount or Decimal("0")
            total = obj.total_amount or Decimal("0")
            if total > 0 and paid >= total:
                return "Paid"
            if paid > 0:
                return "Partially Paid"
        return dict(BILL_STATUS_CHOICES).get(obj.status, obj.status)

    def get_balance_amount(self, obj) -> str:
        return str(obj.balance_amount)

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        ret["lines"] = BillLineSerializer(
            instance.lines.filter(is_deleted=False), many=True
        ).data
        return ret

    def validate(self, attrs):
        lines = attrs.get("lines")
        if self.instance is None and (not lines or len(lines) == 0):
            raise serializers.ValidationError({"lines": "At least one bill line is required."})
        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        user = request.user if request else None
        lines_data = validated_data.pop("lines", [])
        bill = Bill.objects.create(creator=user, **validated_data)

        for idx, line in enumerate(lines_data):
            BillLine.objects.create(
                bill=bill,
                line_order=idx,
                creator=user,
                **line,
            )

        bill.recalculate_totals()
        return bill

    def update(self, instance, validated_data):
        request = self.context.get("request")
        user = request.user if request else None
        lines_data = validated_data.pop("lines", None)

        if instance.status in ("posted", "partially_paid", "paid"):
            raise serializers.ValidationError(
                {"error": "BILL_POSTED", "message": "Posted bill cannot be edited."}
            )

        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.updator = user
        instance.save()

        if lines_data is not None:
            instance.lines.filter(is_deleted=False).update(is_deleted=True, updator=user)
            for idx, line in enumerate(lines_data):
                BillLine.objects.create(
                    bill=instance,
                    line_order=idx,
                    creator=user,
                    **line,
                )

        instance.recalculate_totals()
        return instance


class BillPostSerializer(serializers.Serializer):
    payable_account = serializers.UUIDField(required=False)
    vat_account = serializers.UUIDField(required=False)
    posting_date = serializers.DateField(required=False)
    memo = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        bill_id = self.context.get("bill_id") or self.context.get("bill")
        if isinstance(bill_id, Bill):
            bill = bill_id
        else:
            bill = Bill.objects.filter(pk=bill_id, is_deleted=False).first()
        if not bill:
            raise serializers.ValidationError({"error": "NOT_FOUND", "message": "Bill not found."})
        if bill.status in ("posted", "partially_paid", "paid"):
            raise serializers.ValidationError({"error": "BILL_ALREADY_POSTED", "message": "Bill already posted."})
        if not bill.lines.filter(is_deleted=False).exists():
            raise serializers.ValidationError({"error": "NO_LINES", "message": "Bill must include at least one line."})
        return attrs


class BillListSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.company_name", read_only=True)
    status_display = serializers.SerializerMethodField()
    payments = serializers.SerializerMethodField()
    balance = serializers.SerializerMethodField()
    line_item_description = serializers.SerializerMethodField()
    account_display = serializers.SerializerMethodField()
    qty = serializers.SerializerMethodField()
    rate = serializers.SerializerMethodField()
    tax_rate_display = serializers.SerializerMethodField()
    amount = serializers.SerializerMethodField()

    class Meta:
        model = Bill
        fields = [
            "id",
            "status",
            "status_display",
            "bill_number",
            "external_reference",
            "supplier",
            "supplier_name",
            "bill_date",
            "due_date",
            "total_amount",
            "payments",
            "balance",
            "line_item_description",
            "account_display",
            "qty",
            "rate",
            "tax_rate_display",
            "amount",
            "created_at",
            "updated_at",
        ]

    def _first_line(self, obj):
        return obj.lines.filter(is_deleted=False).order_by("line_order", "created_at").first()

    def get_status_display(self, obj) -> str:
        from decimal import Decimal
        if obj.status in ("paid", "partially_paid"):
            return dict(BILL_STATUS_CHOICES).get(obj.status, obj.status)
        if obj.status == "posted":
            paid = obj.paid_amount or Decimal("0")
            total = obj.total_amount or Decimal("0")
            if total > 0 and paid >= total:
                return "Paid"
            if paid > 0:
                return "Partially Paid"
        return dict(BILL_STATUS_CHOICES).get(obj.status, obj.status)

    def get_payments(self, obj) -> str:
        return str(obj.paid_amount or Decimal("0"))

    def get_balance(self, obj) -> str:
        return str(obj.balance_amount)

    def get_line_item_description(self, obj) -> str:
        line = self._first_line(obj)
        return line.description if line else ""

    def get_account_display(self, obj) -> str:
        line = self._first_line(obj)
        if not line:
            return ""
        return f"{line.account.code} - {line.account.name}"

    def get_qty(self, obj) -> str:
        line = self._first_line(obj)
        return str(line.quantity) if line else "0"

    def get_rate(self, obj) -> str:
        line = self._first_line(obj)
        return str(line.unit_price) if line else "0"

    def get_tax_rate_display(self, obj) -> str:
        line = self._first_line(obj)
        if not line or not line.tax_rate:
            return ""
        return f"{line.tax_rate.name} ({line.tax_rate.rate}%)"

    def get_amount(self, obj) -> str:
        line = self._first_line(obj)
        return str(line.total()) if line else "0"


class SupplierPaymentAllocationSerializer(serializers.ModelSerializer):
    bill_number = serializers.CharField(source="bill.bill_number", read_only=True)
    bill_date = serializers.DateField(source="bill.bill_date", read_only=True)
    bill_total = serializers.DecimalField(source="bill.total_amount", max_digits=18, decimal_places=2, read_only=True)
    bill_balance = serializers.SerializerMethodField()

    class Meta:
        model = SupplierPaymentAllocation
        fields = ["id", "bill", "bill_number", "bill_date", "bill_total", "bill_balance", "amount"]
        read_only_fields = ["id", "bill_number", "bill_date", "bill_total", "bill_balance"]

    def get_bill_balance(self, obj) -> str:
        return str(obj.bill.balance_amount)


class SupplierPaymentSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.company_name", read_only=True)
    paid_through_code = serializers.CharField(source="paid_through.code", read_only=True)
    paid_through_name = serializers.CharField(source="paid_through.name", read_only=True)
    payment_type_display = serializers.SerializerMethodField()
    amount_applied = serializers.SerializerMethodField()
    remaining_amount = serializers.SerializerMethodField()
    allocations = SupplierPaymentAllocationSerializer(many=True, required=False, read_only=True)

    class Meta:
        model = SupplierPayment
        fields = [
            "id",
            "payment_number",
            "supplier",
            "supplier_name",
            "paid_through",
            "paid_through_code",
            "paid_through_name",
            "payment_type",
            "payment_type_display",
            "amount_paid",
            "payment_date",
            "description",
            "is_posted",
            "journal_entry",
            "amount_applied",
            "remaining_amount",
            "allocations",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "supplier_name",
            "paid_through_code",
            "paid_through_name",
            "payment_type_display",
            "amount_applied",
            "remaining_amount",
            "journal_entry",
            "created_at",
            "updated_at",
        ]

    def get_payment_type_display(self, obj) -> str:
        return dict(SUPPLIER_PAYMENT_TYPE_CHOICES).get(obj.payment_type, obj.payment_type)

    def get_amount_applied(self, obj) -> str:
        return str(obj.amount_applied)

    def get_remaining_amount(self, obj) -> str:
        return str(obj.remaining_amount)

    def validate(self, attrs):
        amount_paid = attrs.get("amount_paid")
        if amount_paid is None and self.instance is not None:
            amount_paid = self.instance.amount_paid
        if amount_paid is None or float(amount_paid) <= 0:
            raise serializers.ValidationError({"amount_paid": "Amount paid must be greater than 0."})
        return attrs


class DebitNoteLineSerializer(serializers.ModelSerializer):
    account_code = serializers.CharField(source="account.code", read_only=True)
    account_name = serializers.CharField(source="account.name", read_only=True)
    tax_rate_name = serializers.CharField(source="tax_rate.name", read_only=True)
    tax_rate_percent = serializers.DecimalField(source="tax_rate.rate", max_digits=5, decimal_places=2, read_only=True)
    line_subtotal = serializers.SerializerMethodField()
    line_tax_amount = serializers.SerializerMethodField()
    line_total = serializers.SerializerMethodField()

    class Meta:
        model = DebitNoteLine
        fields = [
            "id",
            "description",
            "account",
            "account_code",
            "account_name",
            "quantity",
            "unit_price",
            "tax_rate",
            "tax_rate_name",
            "tax_rate_percent",
            "discount_percent",
            "line_order",
            "line_subtotal",
            "line_tax_amount",
            "line_total",
        ]
        read_only_fields = ["id"]

    def get_line_subtotal(self, obj) -> str:
        return str(obj.subtotal())

    def get_line_tax_amount(self, obj) -> str:
        return str(obj.tax_amount())

    def get_line_total(self, obj) -> str:
        return str(obj.total())


class DebitNoteSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.company_name", read_only=True)
    status_display = serializers.SerializerMethodField()
    lines = DebitNoteLineSerializer(many=True)

    class Meta:
        model = DebitNote
        fields = [
            "id",
            "debit_note_number",
            "supplier",
            "supplier_name",
            "date",
            "note",
            "status",
            "status_display",
            "posted_at",
            "journal_entry",
            "subtotal",
            "total_vat",
            "total_amount",
            "lines",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "posted_at",
            "journal_entry",
            "subtotal",
            "total_vat",
            "total_amount",
            "created_at",
            "updated_at",
        ]

    def get_status_display(self, obj) -> str:
        return dict(DEBIT_NOTE_STATUS_CHOICES).get(obj.status, obj.status)

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        ret["lines"] = DebitNoteLineSerializer(
            instance.lines.filter(is_deleted=False), many=True
        ).data
        return ret

    def validate(self, attrs):
        lines = attrs.get("lines")
        if self.instance is None and (not lines or len(lines) == 0):
            raise serializers.ValidationError({"lines": "At least one debit note line is required."})
        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        user = request.user if request else None
        lines_data = validated_data.pop("lines", [])
        note = DebitNote.objects.create(creator=user, **validated_data)
        for idx, line in enumerate(lines_data):
            DebitNoteLine.objects.create(
                debit_note=note,
                line_order=idx,
                creator=user,
                **line,
            )
        note.recalculate_totals()
        return note

    def update(self, instance, validated_data):
        request = self.context.get("request")
        user = request.user if request else None
        lines_data = validated_data.pop("lines", None)
        if instance.status == "posted":
            raise serializers.ValidationError(
                {"error": "DEBIT_NOTE_POSTED", "message": "Posted debit note cannot be edited."}
            )

        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.updator = user
        instance.save()

        if lines_data is not None:
            instance.lines.filter(is_deleted=False).update(is_deleted=True, updator=user)
            for idx, line in enumerate(lines_data):
                DebitNoteLine.objects.create(
                    debit_note=instance,
                    line_order=idx,
                    creator=user,
                    **line,
                )
        instance.recalculate_totals()
        return instance


class SupplierRefundAllocationSerializer(serializers.ModelSerializer):
    debit_note_number = serializers.CharField(source="debit_note.debit_note_number", read_only=True)
    debit_note_date = serializers.DateField(source="debit_note.date", read_only=True)
    debit_note_total = serializers.DecimalField(source="debit_note.total_amount", max_digits=18, decimal_places=2, read_only=True)
    debit_note_balance = serializers.SerializerMethodField()

    class Meta:
        model = SupplierRefundAllocation
        fields = ["id", "debit_note", "debit_note_number", "debit_note_date", "debit_note_total", "debit_note_balance", "amount"]
        read_only_fields = ["id", "debit_note_number", "debit_note_date", "debit_note_total", "debit_note_balance"]

    def get_debit_note_balance(self, obj) -> str:
        return str(obj.debit_note.balance_amount)


class SupplierRefundSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.company_name", read_only=True)
    paid_through_code = serializers.CharField(source="paid_through.code", read_only=True)
    paid_through_name = serializers.CharField(source="paid_through.name", read_only=True)
    amount_applied = serializers.SerializerMethodField()
    remaining_amount = serializers.SerializerMethodField()
    allocations = SupplierRefundAllocationSerializer(many=True, required=False, read_only=True)

    class Meta:
        model = SupplierRefund
        fields = [
            "id",
            "refund_number",
            "supplier",
            "supplier_name",
            "paid_through",
            "paid_through_code",
            "paid_through_name",
            "amount_refunded",
            "refund_date",
            "description",
            "is_posted",
            "journal_entry",
            "amount_applied",
            "remaining_amount",
            "allocations",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "supplier_name",
            "paid_through_code",
            "paid_through_name",
            "amount_applied",
            "remaining_amount",
            "journal_entry",
            "created_at",
            "updated_at",
        ]

    def get_amount_applied(self, obj) -> str:
        return str(obj.amount_applied)

    def get_remaining_amount(self, obj) -> str:
        return str(obj.remaining_amount)

    def validate(self, attrs):
        amount_refunded = attrs.get("amount_refunded")
        if amount_refunded is None and self.instance is not None:
            amount_refunded = self.instance.amount_refunded
        if amount_refunded is None or float(amount_refunded) <= 0:
            raise serializers.ValidationError({"amount_refunded": "Amount refunded must be greater than 0."})
        return attrs

