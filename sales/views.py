from django.db.models import Q
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    Customer,
    CUSTOMER_PAYMENT_TERMS_CHOICES,
    CUSTOMER_VAT_TREATMENT_CHOICES,
    CUSTOMER_OPENING_BALANCE_CHOICES,
    Quote,
    QUOTE_STATUS_CHOICES,
    Invoice,
    INVOICE_STATUS_CHOICES,
    CustomerPayment,
    CustomerPaymentAllocation,
    CUSTOMER_PAYMENT_TYPE_CHOICES,
    CustomerRefund,
    CustomerRefundAllocation,
    CustomerCreditNote,
)
from decimal import Decimal

from .serializers import (
    CustomerSerializer,
    QuoteSerializer,
    InvoiceSerializer,
    InvoicePostSerializer,
    CustomerPaymentSerializer,
    CustomerRefundSerializer,
    CustomerCreditNoteSerializer,
    CustomerCreditNotePostSerializer,
)


class CustomerPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 200


def _get_customer(pk):
    try:
        return Customer.objects.get(pk=pk, is_deleted=False)
    except Customer.DoesNotExist:
        return None


class CustomerListCreateAPI(APIView):
    """
    GET  /sales/customers/
    POST /sales/customers/
    """

    permission_classes = [IsAuthenticated]
    pagination_class = CustomerPagination

    def get(self, request):
        qs = Customer.objects.filter(is_deleted=False).select_related("country", "opening_balance_account")

        search = request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(company_name__icontains=search)
                | Q(company_name_ar__icontains=search)
                | Q(primary_contact_name__icontains=search)
                | Q(email__icontains=search)
                | Q(phone__icontains=search)
                | Q(tax_registration_number__icontains=search)
            )

        active_param = request.query_params.get("active")
        if active_param is not None:
            qs = qs.filter(is_active=active_param.lower() == "true")

        vat_treatment = request.query_params.get("vat_treatment")
        if vat_treatment:
            qs = qs.filter(vat_treatment=vat_treatment)

        country = request.query_params.get("country")
        if country:
            qs = qs.filter(country_id=country)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs.order_by("-created_at"), request)
        return paginator.get_paginated_response(CustomerSerializer(page, many=True).data)

    def post(self, request):
        serializer = CustomerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        customer = serializer.save(creator=request.user)
        return Response(CustomerSerializer(customer).data, status=status.HTTP_201_CREATED)


class CustomerDetailAPI(APIView):
    """
    GET/PATCH/DELETE /sales/customers/<uuid>/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        customer = _get_customer(pk)
        if not customer:
            return Response({"error": "NOT_FOUND", "message": "Customer not found."}, status=404)
        return Response(CustomerSerializer(customer).data)

    def patch(self, request, pk):
        customer = _get_customer(pk)
        if not customer:
            return Response({"error": "NOT_FOUND", "message": "Customer not found."}, status=404)
        serializer = CustomerSerializer(customer, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        customer = serializer.save(updator=request.user)
        return Response(CustomerSerializer(customer).data)

    def delete(self, request, pk):
        customer = _get_customer(pk)
        if not customer:
            return Response({"error": "NOT_FOUND", "message": "Customer not found."}, status=404)
        customer.is_deleted = True
        customer.save(update_fields=["is_deleted", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class CustomerChoicesAPI(APIView):
    """
    GET /sales/customers/choices/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            {
                "payment_terms": [{"id": k, "label": v} for k, v in CUSTOMER_PAYMENT_TERMS_CHOICES],
                "vat_treatments": [{"id": k, "label": v} for k, v in CUSTOMER_VAT_TREATMENT_CHOICES],
                "opening_balance_types": [{"id": k, "label": v} for k, v in CUSTOMER_OPENING_BALANCE_CHOICES],
            }
        )


class QuotePagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 200


def _get_quote(pk):
    try:
        return Quote.objects.get(pk=pk, is_deleted=False)
    except Quote.DoesNotExist:
        return None


class QuoteListCreateAPI(APIView):
    """
    GET  /sales/quotes/
    POST /sales/quotes/
    """

    permission_classes = [IsAuthenticated]
    pagination_class = QuotePagination

    def get(self, request):
        qs = Quote.objects.filter(is_deleted=False).select_related("customer")

        search = request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(quote_number__icontains=search)
                | Q(note__icontains=search)
                | Q(customer__company_name__icontains=search)
            )
        status_param = request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        customer = request.query_params.get("customer")
        if customer:
            qs = qs.filter(customer_id=customer)
        date_from = request.query_params.get("date_from")
        if date_from:
            qs = qs.filter(date__gte=date_from)
        date_to = request.query_params.get("date_to")
        if date_to:
            qs = qs.filter(date__lte=date_to)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs.order_by("-date", "-created_at"), request)
        return paginator.get_paginated_response(QuoteSerializer(page, many=True).data)

    def post(self, request):
        serializer = QuoteSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        quote = serializer.save()
        return Response(QuoteSerializer(quote).data, status=status.HTTP_201_CREATED)


class QuoteDetailAPI(APIView):
    """
    GET/PATCH/DELETE /sales/quotes/<uuid>/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        quote = _get_quote(pk)
        if not quote:
            return Response({"error": "NOT_FOUND", "message": "Quote not found."}, status=404)
        return Response(QuoteSerializer(quote).data)

    def patch(self, request, pk):
        quote = _get_quote(pk)
        if not quote:
            return Response({"error": "NOT_FOUND", "message": "Quote not found."}, status=404)
        serializer = QuoteSerializer(quote, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        quote = serializer.save()
        return Response(QuoteSerializer(quote).data)

    def delete(self, request, pk):
        quote = _get_quote(pk)
        if not quote:
            return Response({"error": "NOT_FOUND", "message": "Quote not found."}, status=404)
        quote.is_deleted = True
        quote.save(update_fields=["is_deleted", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class QuoteSendAPI(APIView):
    """
    POST /sales/quotes/<uuid>/send/
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        quote = _get_quote(pk)
        if not quote:
            return Response({"error": "NOT_FOUND", "message": "Quote not found."}, status=404)
        quote.status = "sent"
        quote.updator = request.user
        quote.save(update_fields=["status", "updator", "updated_at"])
        return Response(QuoteSerializer(quote).data)


class QuoteChoicesAPI(APIView):
    """
    GET /sales/quotes/choices/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            {
                "status": [{"id": k, "label": v} for k, v in QUOTE_STATUS_CHOICES],
            }
        )


class InvoicePagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 200


def _get_invoice(pk):
    try:
        return Invoice.objects.get(pk=pk, is_deleted=False)
    except Invoice.DoesNotExist:
        return None


class InvoiceListCreateAPI(APIView):
    """
    GET  /sales/invoices/
    POST /sales/invoices/
    """

    permission_classes = [IsAuthenticated]
    pagination_class = InvoicePagination

    def get(self, request):
        qs = Invoice.objects.filter(is_deleted=False).select_related("customer")

        search = request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(invoice_number__icontains=search)
                | Q(note__icontains=search)
                | Q(customer__company_name__icontains=search)
            )
        status_param = request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        customer = request.query_params.get("customer")
        if customer:
            qs = qs.filter(customer_id=customer)
        date_from = request.query_params.get("date_from")
        if date_from:
            qs = qs.filter(date__gte=date_from)
        date_to = request.query_params.get("date_to")
        if date_to:
            qs = qs.filter(date__lte=date_to)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs.order_by("-date", "-created_at"), request)
        return paginator.get_paginated_response(InvoiceSerializer(page, many=True).data)

    def post(self, request):
        serializer = InvoiceSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        invoice = serializer.save()
        return Response(InvoiceSerializer(invoice).data, status=status.HTTP_201_CREATED)


class InvoiceDetailAPI(APIView):
    """
    GET/PATCH/DELETE /sales/invoices/<uuid>/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        invoice = _get_invoice(pk)
        if not invoice:
            return Response({"error": "NOT_FOUND", "message": "Invoice not found."}, status=404)
        return Response(InvoiceSerializer(invoice).data)

    def patch(self, request, pk):
        invoice = _get_invoice(pk)
        if not invoice:
            return Response({"error": "NOT_FOUND", "message": "Invoice not found."}, status=404)
        serializer = InvoiceSerializer(invoice, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        invoice = serializer.save()
        return Response(InvoiceSerializer(invoice).data)

    def delete(self, request, pk):
        invoice = _get_invoice(pk)
        if not invoice:
            return Response({"error": "NOT_FOUND", "message": "Invoice not found."}, status=404)
        if invoice.status == "posted":
            return Response(
                {"error": "INVOICE_POSTED", "message": "Posted invoice cannot be deleted."},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        invoice.is_deleted = True
        invoice.save(update_fields=["is_deleted", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class InvoicePostAPI(APIView):
    """
    POST /sales/invoices/<uuid>/post/
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        invoice = _get_invoice(pk)
        if not invoice:
            return Response({"error": "NOT_FOUND", "message": "Invoice not found."}, status=404)
        serializer = InvoicePostSerializer(data=request.data, context={"invoice": invoice})
        serializer.is_valid(raise_exception=True)
        invoice.status = "posted"
        invoice.posted_at = timezone.now()
        invoice.qr_code_text = serializer.validated_data.get("qr_code_text", invoice.qr_code_text)
        invoice.updator = request.user
        invoice.save(update_fields=["status", "posted_at", "qr_code_text", "updator", "updated_at"])
        return Response(InvoiceSerializer(invoice).data)


class InvoiceChoicesAPI(APIView):
    """
    GET /sales/invoices/choices/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"status": [{"id": k, "label": v} for k, v in INVOICE_STATUS_CHOICES]})


class CustomerPaymentPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 200


def _get_customer_payment(pk):
    try:
        return CustomerPayment.objects.get(pk=pk, is_deleted=False)
    except CustomerPayment.DoesNotExist:
        return None


class CustomerPaymentListCreateAPI(APIView):
    """
    GET  /sales/customer-payments/
    POST /sales/customer-payments/
    """

    permission_classes = [IsAuthenticated]
    pagination_class = CustomerPaymentPagination

    def get(self, request):
        qs = CustomerPayment.objects.filter(is_deleted=False).select_related("customer", "paid_through")
        search = request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(payment_number__icontains=search)
                | Q(customer__company_name__icontains=search)
                | Q(description__icontains=search)
            )
        customer = request.query_params.get("customer")
        if customer:
            qs = qs.filter(customer_id=customer)
        payment_type = request.query_params.get("payment_type")
        if payment_type:
            qs = qs.filter(payment_type=payment_type)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs.order_by("-payment_date", "-created_at"), request)
        return paginator.get_paginated_response(CustomerPaymentSerializer(page, many=True).data)

    def post(self, request):
        serializer = CustomerPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        allocations = request.data.get("allocations", [])
        try:
            with transaction.atomic():
                payment = serializer.save(creator=request.user)
                self._replace_allocations(payment, allocations, user=request.user)
                payment.refresh_from_db()
        except ValueError as exc:
            return Response(
                {"error": "VALIDATION_ERROR", "message": str(exc)},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        return Response(CustomerPaymentSerializer(payment).data, status=status.HTTP_201_CREATED)

    def _replace_allocations(self, payment: CustomerPayment, allocations, user):
        total_applied = Decimal("0")
        if payment.payment_type == "advance_payment":
            allocations = []

        for row in allocations:
            invoice_id = row.get("invoice")
            amount = Decimal(str(row.get("amount", "0")))
            if amount <= 0:
                continue
            invoice = Invoice.objects.filter(pk=invoice_id, is_deleted=False).first()
            if not invoice:
                raise ValueError(f"Invalid invoice: {invoice_id}")
            if invoice.customer_id != payment.customer_id:
                raise ValueError("Invoice customer must match payment customer.")
            if invoice.status != "posted":
                raise ValueError(f"Invoice {invoice.invoice_number} must be posted before payment.")
            if amount > invoice.balance_amount:
                raise ValueError(f"Applied amount exceeds current invoice balance for {invoice.invoice_number}.")

            CustomerPaymentAllocation.objects.create(
                payment=payment,
                invoice=invoice,
                amount=amount,
                creator=user,
            )
            invoice.paid_amount = (invoice.paid_amount or Decimal("0")) + amount
            invoice.save(update_fields=["paid_amount", "updated_at"])
            total_applied += amount

        if total_applied > payment.amount_received:
            raise ValueError("Total allocations cannot exceed amount received.")


class CustomerPaymentDetailAPI(APIView):
    """
    GET/PATCH/DELETE /sales/customer-payments/<uuid>/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        payment = _get_customer_payment(pk)
        if not payment:
            return Response({"error": "NOT_FOUND", "message": "Customer payment not found."}, status=404)
        return Response(CustomerPaymentSerializer(payment).data)

    def patch(self, request, pk):
        payment = _get_customer_payment(pk)
        if not payment:
            return Response({"error": "NOT_FOUND", "message": "Customer payment not found."}, status=404)
        serializer = CustomerPaymentSerializer(payment, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        allocations = request.data.get("allocations", None)
        try:
            with transaction.atomic():
                self._rollback_allocations(payment)
                payment = serializer.save(updator=request.user)
                if allocations is not None:
                    self._replace_allocations(payment, allocations, user=request.user)
                payment.refresh_from_db()
        except ValueError as exc:
            return Response(
                {"error": "VALIDATION_ERROR", "message": str(exc)},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        return Response(CustomerPaymentSerializer(payment).data)

    def delete(self, request, pk):
        payment = _get_customer_payment(pk)
        if not payment:
            return Response({"error": "NOT_FOUND", "message": "Customer payment not found."}, status=404)
        with transaction.atomic():
            self._rollback_allocations(payment)
            payment.is_deleted = True
            payment.save(update_fields=["is_deleted", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _rollback_allocations(self, payment: CustomerPayment):
        for allocation in payment.allocations.filter(is_deleted=False).select_related("invoice"):
            invoice = allocation.invoice
            invoice.paid_amount = (invoice.paid_amount or Decimal("0")) - allocation.amount
            if invoice.paid_amount < 0:
                invoice.paid_amount = Decimal("0")
            invoice.save(update_fields=["paid_amount", "updated_at"])
        payment.allocations.filter(is_deleted=False).update(is_deleted=True)

    def _replace_allocations(self, payment: CustomerPayment, allocations, user):
        return CustomerPaymentListCreateAPI()._replace_allocations(payment, allocations, user)


class CustomerOutstandingInvoicesAPI(APIView):
    """
    GET /sales/customer-payments/outstanding-invoices/?customer=<uuid>
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        customer_id = request.query_params.get("customer")
        if not customer_id:
            return Response(
                {"error": "CUSTOMER_REQUIRED", "message": "customer query param is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        invoices = Invoice.objects.filter(
            is_deleted=False,
            status="posted",
            customer_id=customer_id,
        ).order_by("date", "created_at")

        results = []
        for invoice in invoices:
            balance = invoice.balance_amount
            if balance <= 0:
                continue
            results.append(
                {
                    "id": str(invoice.id),
                    "invoice_number": invoice.invoice_number,
                    "invoice_date": invoice.date,
                    "total_amount": str(invoice.total_amount),
                    "paid_amount": str(invoice.paid_amount),
                    "balance_amount": str(balance),
                }
            )
        return Response(
            {
                "results": results,
                "payment_types": [{"id": k, "label": v} for k, v in CUSTOMER_PAYMENT_TYPE_CHOICES],
            }
        )


class CustomerRefundPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 200


def _get_customer_refund(pk):
    try:
        return CustomerRefund.objects.get(pk=pk, is_deleted=False)
    except CustomerRefund.DoesNotExist:
        return None


class CustomerRefundListCreateAPI(APIView):
    """
    GET  /sales/customer-refunds/
    POST /sales/customer-refunds/
    """

    permission_classes = [IsAuthenticated]
    pagination_class = CustomerRefundPagination

    def get(self, request):
        qs = CustomerRefund.objects.filter(is_deleted=False).select_related("customer", "paid_through")
        search = request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(refund_number__icontains=search)
                | Q(customer__company_name__icontains=search)
                | Q(description__icontains=search)
            )
        customer = request.query_params.get("customer")
        if customer:
            qs = qs.filter(customer_id=customer)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs.order_by("-refund_date", "-created_at"), request)
        return paginator.get_paginated_response(CustomerRefundSerializer(page, many=True).data)

    def post(self, request):
        serializer = CustomerRefundSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        allocations = request.data.get("allocations", [])
        try:
            with transaction.atomic():
                refund = serializer.save(creator=request.user)
                self._replace_allocations(refund, allocations, user=request.user)
                refund.refresh_from_db()
        except ValueError as exc:
            return Response(
                {"error": "VALIDATION_ERROR", "message": str(exc)},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        return Response(CustomerRefundSerializer(refund).data, status=status.HTTP_201_CREATED)

    def _replace_allocations(self, refund: CustomerRefund, allocations, user):
        total_applied = Decimal("0")
        for row in allocations:
            credit_note_id = row.get("credit_note")
            amount = Decimal(str(row.get("amount", "0")))
            if amount <= 0:
                continue
            credit_note = CustomerCreditNote.objects.filter(pk=credit_note_id, is_deleted=False).first()
            if not credit_note:
                raise ValueError(f"Invalid credit note: {credit_note_id}")
            if credit_note.customer_id != refund.customer_id:
                raise ValueError("Credit note customer must match refund customer.")
            if credit_note.status != "posted":
                raise ValueError(f"Credit note {credit_note.credit_note_number} must be posted before refund.")
            if amount > credit_note.balance_amount:
                raise ValueError(
                    f"Applied amount exceeds current credit note balance for {credit_note.credit_note_number}."
                )

            CustomerRefundAllocation.objects.create(
                refund=refund,
                credit_note=credit_note,
                amount=amount,
                creator=user,
            )
            credit_note.refunded_amount = (credit_note.refunded_amount or Decimal("0")) + amount
            credit_note.save(update_fields=["refunded_amount", "updated_at"])
            total_applied += amount

        if total_applied > refund.amount_refunded:
            raise ValueError("Total allocations cannot exceed amount refunded.")


class CustomerRefundDetailAPI(APIView):
    """
    GET/PATCH/DELETE /sales/customer-refunds/<uuid>/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        refund = _get_customer_refund(pk)
        if not refund:
            return Response({"error": "NOT_FOUND", "message": "Customer refund not found."}, status=404)
        return Response(CustomerRefundSerializer(refund).data)

    def patch(self, request, pk):
        refund = _get_customer_refund(pk)
        if not refund:
            return Response({"error": "NOT_FOUND", "message": "Customer refund not found."}, status=404)
        serializer = CustomerRefundSerializer(refund, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        allocations = request.data.get("allocations", None)
        try:
            with transaction.atomic():
                self._rollback_allocations(refund)
                refund = serializer.save(updator=request.user)
                if allocations is not None:
                    self._replace_allocations(refund, allocations, user=request.user)
                refund.refresh_from_db()
        except ValueError as exc:
            return Response(
                {"error": "VALIDATION_ERROR", "message": str(exc)},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        return Response(CustomerRefundSerializer(refund).data)

    def delete(self, request, pk):
        refund = _get_customer_refund(pk)
        if not refund:
            return Response({"error": "NOT_FOUND", "message": "Customer refund not found."}, status=404)
        with transaction.atomic():
            self._rollback_allocations(refund)
            refund.is_deleted = True
            refund.save(update_fields=["is_deleted", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _rollback_allocations(self, refund: CustomerRefund):
        for allocation in refund.allocations.filter(is_deleted=False).select_related("credit_note"):
            credit_note = allocation.credit_note
            credit_note.refunded_amount = (credit_note.refunded_amount or Decimal("0")) - allocation.amount
            if credit_note.refunded_amount < 0:
                credit_note.refunded_amount = Decimal("0")
            credit_note.save(update_fields=["refunded_amount", "updated_at"])
        refund.allocations.filter(is_deleted=False).update(is_deleted=True)

    def _replace_allocations(self, refund: CustomerRefund, allocations, user):
        return CustomerRefundListCreateAPI()._replace_allocations(refund, allocations, user)


class CustomerOutstandingCreditNotesAPI(APIView):
    """
    GET /sales/customer-refunds/outstanding-credit-notes/?customer=<uuid>
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        customer_id = request.query_params.get("customer")
        if not customer_id:
            return Response(
                {"error": "CUSTOMER_REQUIRED", "message": "customer query param is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        credit_notes = CustomerCreditNote.objects.filter(
            is_deleted=False,
            status="posted",
            customer_id=customer_id,
        ).order_by("date", "created_at")

        results = []
        for note in credit_notes:
            balance = note.balance_amount
            if balance <= 0:
                continue
            results.append(
                {
                    "id": str(note.id),
                    "credit_note_number": note.credit_note_number,
                    "date": note.date,
                    "total_amount": str(note.total_amount),
                    "refunded_amount": str(note.refunded_amount),
                    "balance_amount": str(balance),
                }
            )
        return Response({"results": results})


class CustomerCreditNotePagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 200


def _get_credit_note(pk):
    try:
        return CustomerCreditNote.objects.get(pk=pk, is_deleted=False)
    except CustomerCreditNote.DoesNotExist:
        return None


class CustomerCreditNoteListCreateAPI(APIView):
    """
    GET  /sales/credit-notes/
    POST /sales/credit-notes/
    """

    permission_classes = [IsAuthenticated]
    pagination_class = CustomerCreditNotePagination

    def get(self, request):
        qs = CustomerCreditNote.objects.filter(is_deleted=False).select_related("customer")
        search = request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(credit_note_number__icontains=search)
                | Q(note__icontains=search)
                | Q(customer__company_name__icontains=search)
            )
        status_param = request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        customer = request.query_params.get("customer")
        if customer:
            qs = qs.filter(customer_id=customer)
        date_from = request.query_params.get("date_from")
        if date_from:
            qs = qs.filter(date__gte=date_from)
        date_to = request.query_params.get("date_to")
        if date_to:
            qs = qs.filter(date__lte=date_to)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs.order_by("-date", "-created_at"), request)
        return paginator.get_paginated_response(CustomerCreditNoteSerializer(page, many=True).data)

    def post(self, request):
        serializer = CustomerCreditNoteSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        note = serializer.save()
        return Response(CustomerCreditNoteSerializer(note).data, status=status.HTTP_201_CREATED)


class CustomerCreditNoteDetailAPI(APIView):
    """
    GET/PATCH/DELETE /sales/credit-notes/<uuid>/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        note = _get_credit_note(pk)
        if not note:
            return Response({"error": "NOT_FOUND", "message": "Credit note not found."}, status=404)
        return Response(CustomerCreditNoteSerializer(note).data)

    def patch(self, request, pk):
        note = _get_credit_note(pk)
        if not note:
            return Response({"error": "NOT_FOUND", "message": "Credit note not found."}, status=404)
        serializer = CustomerCreditNoteSerializer(note, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        note = serializer.save()
        return Response(CustomerCreditNoteSerializer(note).data)

    def delete(self, request, pk):
        note = _get_credit_note(pk)
        if not note:
            return Response({"error": "NOT_FOUND", "message": "Credit note not found."}, status=404)
        if note.status == "posted":
            return Response(
                {"error": "CREDIT_NOTE_POSTED", "message": "Posted credit note cannot be deleted."},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        note.is_deleted = True
        note.save(update_fields=["is_deleted", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class CustomerCreditNotePostAPI(APIView):
    """
    POST /sales/credit-notes/<uuid>/post/
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        note = _get_credit_note(pk)
        if not note:
            return Response({"error": "NOT_FOUND", "message": "Credit note not found."}, status=404)
        serializer = CustomerCreditNotePostSerializer(data=request.data, context={"credit_note": note})
        serializer.is_valid(raise_exception=True)
        note.status = "posted"
        note.posted_at = timezone.now()
        note.qr_code_text = serializer.validated_data.get("qr_code_text", note.qr_code_text)
        note.updator = request.user
        note.save(update_fields=["status", "posted_at", "qr_code_text", "updator", "updated_at"])
        return Response(CustomerCreditNoteSerializer(note).data)

