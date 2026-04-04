from rest_framework import serializers

from .models import (
    Customer,
    CUSTOMER_PAYMENT_TERMS_CHOICES,
    CUSTOMER_VAT_TREATMENT_CHOICES,
    CUSTOMER_OPENING_BALANCE_CHOICES,
    Quote,
    QuoteLine,
    QUOTE_STATUS_CHOICES,
    Invoice,
    InvoiceLine,
    INVOICE_STATUS_CHOICES,
    CustomerPayment,
    CustomerPaymentAllocation,
    CUSTOMER_PAYMENT_TYPE_CHOICES,
    CustomerRefund,
    CustomerRefundAllocation,
    CustomerCreditNote,
    CustomerCreditNoteLine,
    ZATCA_SUBMISSION_TYPE_CHOICES,
)


class CustomerSerializer(serializers.ModelSerializer):
    country_name = serializers.CharField(source="country.name", read_only=True)
    payment_terms_display = serializers.SerializerMethodField()
    vat_treatment_display = serializers.SerializerMethodField()
    opening_balance_type_display = serializers.SerializerMethodField()
    opening_balance_account_name = serializers.CharField(source="opening_balance_account.name", read_only=True)
    opening_balance_account_code = serializers.CharField(source="opening_balance_account.code", read_only=True)
    # Allow null so the frontend can explicitly clear TRN for non-registered customers
    tax_registration_number = serializers.CharField(allow_null=True, allow_blank=True, required=False, default="")

    class Meta:
        model = Customer
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
        return dict(CUSTOMER_PAYMENT_TERMS_CHOICES).get(obj.payment_terms) if obj.payment_terms else None

    def get_vat_treatment_display(self, obj) -> str:
        return dict(CUSTOMER_VAT_TREATMENT_CHOICES).get(obj.vat_treatment, obj.vat_treatment)

    def get_opening_balance_type_display(self, obj) -> str:
        return dict(CUSTOMER_OPENING_BALANCE_CHOICES).get(obj.opening_balance_type, obj.opening_balance_type)

    def validate_tax_registration_number(self, value):
        # Coerce None → "" so CharField model field is never given null
        return value if value is not None else ""

    def validate(self, attrs):
        import re
        # Coerce null TRN to empty string
        if attrs.get("tax_registration_number") is None:
            attrs["tax_registration_number"] = ""

        vat_treatment = attrs.get("vat_treatment")
        if vat_treatment is None and self.instance is not None:
            vat_treatment = self.instance.vat_treatment

        if vat_treatment == "not_vat_registered_ksa":
            attrs["tax_registration_number"] = ""
        elif vat_treatment == "vat_registered_ksa":
            trn = attrs.get("tax_registration_number", "").strip()
            if not trn:
                raise serializers.ValidationError(
                    {"tax_registration_number": "Tax Registration Number is required for VAT-registered customers."}
                )
            if not re.fullmatch(r'3\d{13}3', trn):
                raise serializers.ValidationError(
                    {"tax_registration_number": "Tax Registration Number must be exactly 15 digits, starting and ending with 3."}
                )
            attrs["tax_registration_number"] = trn

        opening_type = attrs.get("opening_balance_type")
        if opening_type is None and self.instance is not None:
            opening_type = self.instance.opening_balance_type

        if "opening_balance_amount" in attrs and attrs["opening_balance_amount"] in ("", None):
            attrs["opening_balance_amount"] = 0

        if opening_type == "none":
            attrs["opening_balance_amount"] = 0
            attrs["opening_balance_as_of"] = None
            attrs["opening_balance_account"] = None
            return attrs

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


class QuoteLineSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    tax_rate_name = serializers.CharField(source="tax_rate.name", read_only=True)
    tax_rate_percent = serializers.DecimalField(source="tax_rate.rate", max_digits=5, decimal_places=2, read_only=True)
    line_subtotal = serializers.SerializerMethodField()
    line_tax_amount = serializers.SerializerMethodField()
    line_total = serializers.SerializerMethodField()

    class Meta:
        model = QuoteLine
        fields = [
            "id",
            "product",
            "product_name",
            "description",
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


class QuoteSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.company_name", read_only=True)
    status_display = serializers.SerializerMethodField()
    issuer_details = serializers.SerializerMethodField()
    lines = QuoteLineSerializer(many=True)

    class Meta:
        model = Quote
        fields = [
            "id",
            "quote_number",
            "customer",
            "customer_name",
            "date",
            "note",
            "attachment",
            "status",
            "status_display",
            "issuer_details",
            "subtotal_before_discount",
            "discount_total",
            "total_vat",
            "total_amount",
            "lines",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "issuer_details",
            "subtotal_before_discount",
            "discount_total",
            "total_vat",
            "total_amount",
            "created_at",
            "updated_at",
        ]

    def get_status_display(self, obj) -> str:
        return dict(QUOTE_STATUS_CHOICES).get(obj.status, obj.status)

    def get_issuer_details(self, obj):
        from main.models import CompanySettings

        settings_obj = CompanySettings.objects.first()
        if not settings_obj:
            return {"company_name": "", "street_address": "", "vat_registration_number": ""}
        return {
            "company_name": settings_obj.company_name,
            "street_address": settings_obj.street_address,
            "vat_registration_number": settings_obj.vat_registration_number,
            "logo": settings_obj.logo.url if settings_obj.logo else None,
        }

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        ret["lines"] = QuoteLineSerializer(
            instance.lines.filter(is_deleted=False), many=True
        ).data
        return ret

    def validate(self, attrs):
        lines = attrs.get("lines")
        if self.instance is None and (not lines or len(lines) == 0):
            raise serializers.ValidationError({"lines": "At least one quote line is required."})
        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        user = request.user if request else None
        lines_data = validated_data.pop("lines", [])
        quote = Quote.objects.create(creator=user, **validated_data)
        for idx, line in enumerate(lines_data):
            QuoteLine.objects.create(quote=quote, line_order=idx, creator=user, **line)
        quote.recalculate_totals()
        return quote

    def update(self, instance, validated_data):
        request = self.context.get("request")
        user = request.user if request else None
        lines_data = validated_data.pop("lines", None)

        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.updator = user
        instance.save()

        if lines_data is not None:
            instance.lines.filter(is_deleted=False).update(is_deleted=True, updator=user)
            for idx, line in enumerate(lines_data):
                QuoteLine.objects.create(quote=instance, line_order=idx, creator=user, **line)

        instance.recalculate_totals()
        return instance


class InvoiceLineSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    account_code = serializers.CharField(source="account.code", read_only=True)
    account_name = serializers.CharField(source="account.name", read_only=True)
    tax_rate_name = serializers.CharField(source="tax_rate.name", read_only=True)
    tax_rate_percent = serializers.DecimalField(source="tax_rate.rate", max_digits=5, decimal_places=2, read_only=True)
    line_subtotal = serializers.SerializerMethodField()
    line_tax_amount = serializers.SerializerMethodField()
    line_total = serializers.SerializerMethodField()

    class Meta:
        model = InvoiceLine
        fields = [
            "id",
            "product",
            "product_name",
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


class InvoiceSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.company_name", read_only=True)
    status_display = serializers.SerializerMethodField()
    issuer_details = serializers.SerializerMethodField()
    balance_amount = serializers.SerializerMethodField()
    lines = InvoiceLineSerializer(many=True)

    class Meta:
        model = Invoice
        fields = [
            "id",
            "invoice_number",
            "external_reference",
            "customer",
            "customer_name",
            "date",
            "due_date",
            "note",
            "attachment",
            "status",
            "status_display",
            "posted_at",
            "qr_code_text",
            "zatca_uuid",
            "zatca_previous_hash",
            "zatca_invoice_hash",
            "zatca_signed_hash",
            "zatca_submission_status",
            "zatca_submission_type",
            "zatca_submission_reference",
            "zatca_submission_error",
            "zatca_submitted_at",
            "zatca_cleared_at",
            "journal_entry",
            "subtotal",
            "total_vat",
            "total_amount",
            "paid_amount",
            "balance_amount",
            "issuer_details",
            "lines",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "status",
            "posted_at",
            "journal_entry",
            "zatca_uuid",
            "zatca_previous_hash",
            "zatca_invoice_hash",
            "zatca_signed_hash",
            "zatca_submission_status",
            "zatca_submission_type",
            "zatca_submission_reference",
            "zatca_submission_error",
            "zatca_submitted_at",
            "zatca_cleared_at",
            "subtotal",
            "total_vat",
            "total_amount",
            "paid_amount",
            "balance_amount",
            "issuer_details",
            "created_at",
            "updated_at",
        ]

    def get_status_display(self, obj) -> str:
        return dict(INVOICE_STATUS_CHOICES).get(obj.status, obj.status)

    def get_balance_amount(self, obj) -> str:
        return str(obj.balance_amount)

    def get_issuer_details(self, obj):
        from main.models import CompanySettings

        settings_obj = CompanySettings.objects.first()
        if not settings_obj:
            return {"company_name": "", "street_address": "", "vat_registration_number": ""}
        return {
            "company_name": settings_obj.company_name,
            "street_address": settings_obj.street_address,
            "vat_registration_number": settings_obj.vat_registration_number,
            "logo": settings_obj.logo.url if settings_obj.logo else None,
        }

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        ret["lines"] = InvoiceLineSerializer(
            instance.lines.filter(is_deleted=False), many=True
        ).data
        return ret

    def validate(self, attrs):
        lines = attrs.get("lines")
        if self.instance is None and (not lines or len(lines) == 0):
            raise serializers.ValidationError({"lines": "At least one invoice line is required."})
        if self.instance is None and not (attrs.get("external_reference") or "").strip():
            raise serializers.ValidationError(
                {"external_reference": "external_reference is required for deduplication safety."}
            )
        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        user = request.user if request else None
        lines_data = validated_data.pop("lines", [])
        invoice = Invoice.objects.create(creator=user, **validated_data)
        for idx, line in enumerate(lines_data):
            InvoiceLine.objects.create(invoice=invoice, line_order=idx, creator=user, **line)
        invoice.recalculate_totals()
        return invoice

    def update(self, instance, validated_data):
        request = self.context.get("request")
        user = request.user if request else None
        lines_data = validated_data.pop("lines", None)
        if instance.status in ("confirmed", "posted", "reported"):
            raise serializers.ValidationError(
                {"error": "INVOICE_LOCKED", "message": "Invoice cannot be edited after it has been confirmed."}
            )
        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.updator = user
        instance.save()
        if lines_data is not None:
            instance.lines.filter(is_deleted=False).update(is_deleted=True, updator=user)
            for idx, line in enumerate(lines_data):
                InvoiceLine.objects.create(invoice=instance, line_order=idx, creator=user, **line)
        instance.recalculate_totals()
        return instance


class InvoicePostSerializer(serializers.Serializer):
    qr_code_text = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        invoice_id = self.context.get("invoice_id")
        invoice = Invoice.objects.filter(pk=invoice_id, is_deleted=False).first() if invoice_id else None
        if not invoice:
            raise serializers.ValidationError({"error": "NOT_FOUND", "message": "Invoice not found."})
        if invoice.status in ("confirmed", "posted", "reported"):
            raise serializers.ValidationError(
                {"error": "INVOICE_ALREADY_CONFIRMED", "message": "Invoice is already confirmed."}
            )
        if not invoice.lines.filter(is_deleted=False).exists():
            raise serializers.ValidationError({"error": "NO_LINES", "message": "Invoice must include at least one line."})
        return attrs


class ZatcaSubmitSerializer(serializers.Serializer):
    submission_type = serializers.ChoiceField(choices=[k for k, _ in ZATCA_SUBMISSION_TYPE_CHOICES])


class CustomerPaymentAllocationSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="invoice.invoice_number", read_only=True)
    invoice_date = serializers.DateField(source="invoice.date", read_only=True)
    invoice_total = serializers.DecimalField(source="invoice.total_amount", max_digits=18, decimal_places=2, read_only=True)
    invoice_balance = serializers.SerializerMethodField()

    class Meta:
        model = CustomerPaymentAllocation
        fields = ["id", "invoice", "invoice_number", "invoice_date", "invoice_total", "invoice_balance", "amount"]
        read_only_fields = ["id", "invoice_number", "invoice_date", "invoice_total", "invoice_balance"]

    def get_invoice_balance(self, obj) -> str:
        return str(obj.invoice.balance_amount)


class CustomerPaymentSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.company_name", read_only=True)
    paid_through_code = serializers.CharField(source="paid_through.code", read_only=True)
    paid_through_name = serializers.CharField(source="paid_through.name", read_only=True)
    payment_type_display = serializers.SerializerMethodField()
    amount_applied = serializers.SerializerMethodField()
    remaining_amount = serializers.SerializerMethodField()
    allocations = CustomerPaymentAllocationSerializer(many=True, required=False, read_only=True)

    class Meta:
        model = CustomerPayment
        fields = [
            "id",
            "payment_number",
            "customer",
            "customer_name",
            "paid_through",
            "paid_through_code",
            "paid_through_name",
            "payment_type",
            "payment_type_display",
            "amount_received",
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
            "customer_name",
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
        return dict(CUSTOMER_PAYMENT_TYPE_CHOICES).get(obj.payment_type, obj.payment_type)

    def get_amount_applied(self, obj) -> str:
        return str(obj.amount_applied)

    def get_remaining_amount(self, obj) -> str:
        return str(obj.remaining_amount)

    def validate(self, attrs):
        amount_received = attrs.get("amount_received")
        if amount_received is None and self.instance is not None:
            amount_received = self.instance.amount_received
        if amount_received is None or float(amount_received) <= 0:
            raise serializers.ValidationError({"amount_received": "Amount received must be greater than 0."})
        return attrs


class CustomerCreditNoteLineSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    account_code = serializers.CharField(source="account.code", read_only=True)
    account_name = serializers.CharField(source="account.name", read_only=True)
    tax_rate_name = serializers.CharField(source="tax_rate.name", read_only=True)
    tax_rate_percent = serializers.DecimalField(source="tax_rate.rate", max_digits=5, decimal_places=2, read_only=True)
    line_subtotal = serializers.SerializerMethodField()
    line_tax_amount = serializers.SerializerMethodField()
    line_total = serializers.SerializerMethodField()

    class Meta:
        model = CustomerCreditNoteLine
        fields = [
            "id",
            "product",
            "product_name",
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


class CustomerCreditNoteSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.company_name", read_only=True)
    issuer_details = serializers.SerializerMethodField()
    balance_amount = serializers.SerializerMethodField()
    lines = CustomerCreditNoteLineSerializer(many=True)

    class Meta:
        model = CustomerCreditNote
        fields = [
            "id",
            "credit_note_number",
            "external_reference",
            "customer",
            "customer_name",
            "date",
            "note",
            "attachment",
            "status",
            "posted_at",
            "qr_code_text",
            "zatca_uuid",
            "zatca_previous_hash",
            "zatca_invoice_hash",
            "zatca_signed_hash",
            "zatca_submission_status",
            "zatca_submission_type",
            "zatca_submission_reference",
            "zatca_submission_error",
            "zatca_submitted_at",
            "zatca_cleared_at",
            "journal_entry",
            "subtotal",
            "total_vat",
            "total_amount",
            "refunded_amount",
            "balance_amount",
            "issuer_details",
            "lines",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "posted_at",
            "journal_entry",
            "zatca_uuid",
            "zatca_previous_hash",
            "zatca_invoice_hash",
            "zatca_signed_hash",
            "zatca_submission_status",
            "zatca_submission_type",
            "zatca_submission_reference",
            "zatca_submission_error",
            "zatca_submitted_at",
            "zatca_cleared_at",
            "subtotal",
            "total_vat",
            "total_amount",
            "refunded_amount",
            "balance_amount",
            "issuer_details",
            "created_at",
            "updated_at",
        ]

    def get_balance_amount(self, obj) -> str:
        return str(obj.balance_amount)

    def get_issuer_details(self, obj):
        from main.models import CompanySettings
        settings_obj = CompanySettings.objects.first()
        if not settings_obj:
            return {"company_name": "", "street_address": "", "vat_registration_number": ""}
        return {
            "company_name": settings_obj.company_name,
            "street_address": settings_obj.street_address,
            "vat_registration_number": settings_obj.vat_registration_number,
            "logo": settings_obj.logo.url if settings_obj.logo else None,
        }

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        ret["lines"] = CustomerCreditNoteLineSerializer(
            instance.lines.filter(is_deleted=False), many=True
        ).data
        return ret

    def validate(self, attrs):
        lines = attrs.get("lines")
        if self.instance is None and (not lines or len(lines) == 0):
            raise serializers.ValidationError({"lines": "At least one credit note line is required."})
        if self.instance is None and not (attrs.get("external_reference") or "").strip():
            raise serializers.ValidationError(
                {"external_reference": "external_reference is required for deduplication safety."}
            )
        return attrs

    def create(self, validated_data):
        request = self.context.get("request")
        user = request.user if request else None
        lines_data = validated_data.pop("lines", [])
        note = CustomerCreditNote.objects.create(creator=user, **validated_data)
        for idx, line in enumerate(lines_data):
            CustomerCreditNoteLine.objects.create(credit_note=note, line_order=idx, creator=user, **line)
        note.recalculate_totals()
        return note

    def update(self, instance, validated_data):
        request = self.context.get("request")
        user = request.user if request else None
        lines_data = validated_data.pop("lines", None)
        if instance.status in ("confirmed", "posted", "reported"):
            raise serializers.ValidationError(
                {"error": "CREDIT_NOTE_LOCKED", "message": "Credit note cannot be edited after it has been confirmed."}
            )
        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.updator = user
        instance.save()
        if lines_data is not None:
            instance.lines.filter(is_deleted=False).update(is_deleted=True, updator=user)
            for idx, line in enumerate(lines_data):
                CustomerCreditNoteLine.objects.create(credit_note=instance, line_order=idx, creator=user, **line)
        instance.recalculate_totals()
        return instance


class CustomerCreditNotePostSerializer(serializers.Serializer):
    qr_code_text = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        credit_note_id = self.context.get("credit_note_id")
        note = CustomerCreditNote.objects.filter(pk=credit_note_id, is_deleted=False).first() if credit_note_id else None
        if not note:
            raise serializers.ValidationError({"error": "NOT_FOUND", "message": "Credit note not found."})
        if note.status in ("confirmed", "posted", "reported"):
            raise serializers.ValidationError(
                {"error": "CREDIT_NOTE_ALREADY_CONFIRMED", "message": "Credit note is already confirmed."}
            )
        if not note.lines.filter(is_deleted=False).exists():
            raise serializers.ValidationError(
                {"error": "NO_LINES", "message": "Credit note must include at least one line."}
            )
        return attrs


class CustomerRefundAllocationSerializer(serializers.ModelSerializer):
    credit_note_number = serializers.CharField(source="credit_note.credit_note_number", read_only=True)
    credit_note_date = serializers.DateField(source="credit_note.date", read_only=True)
    credit_note_total = serializers.DecimalField(source="credit_note.total_amount", max_digits=18, decimal_places=2, read_only=True)
    credit_note_balance = serializers.SerializerMethodField()

    class Meta:
        model = CustomerRefundAllocation
        fields = ["id", "credit_note", "credit_note_number", "credit_note_date", "credit_note_total", "credit_note_balance", "amount"]
        read_only_fields = ["id", "credit_note_number", "credit_note_date", "credit_note_total", "credit_note_balance"]

    def get_credit_note_balance(self, obj) -> str:
        return str(obj.credit_note.balance_amount)


class CustomerRefundSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.company_name", read_only=True)
    paid_through_code = serializers.CharField(source="paid_through.code", read_only=True)
    paid_through_name = serializers.CharField(source="paid_through.name", read_only=True)
    amount_applied = serializers.SerializerMethodField()
    remaining_amount = serializers.SerializerMethodField()
    allocations = CustomerRefundAllocationSerializer(many=True, required=False, read_only=True)

    class Meta:
        model = CustomerRefund
        fields = [
            "id",
            "refund_number",
            "customer",
            "customer_name",
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
            "customer_name",
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

