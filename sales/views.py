from django.db.models import Q
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from main.management.commands.create_groups_and_permissions import IsAdmin
from main.idempotency import begin_idempotent, finalize_idempotent_failure, finalize_idempotent_success, get_idempotency_key
from accounting.permissions import CanPostSales, CanSubmitZatca, CanViewZatca
from accounting.services.posting import (
    post_invoice_journal,
    post_credit_note_journal,
)
from .zatca_services import prepare_zatca_artifacts
from .zatca_services import verify_document_hash
from .zatca_submit_runner import run_zatca_submit_pipeline

import json
import os
import uuid

from main.approvals import create_approval_request, maker_checker_enabled
from accounting.models import AccountingPeriod
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
    CUSTOMER_PAYMENT_TYPE_CHOICES,
    CustomerRefund,
    CustomerCreditNote,
    ZatcaSubmissionLog,
)
from decimal import Decimal

from .customer_cash_posting import (
    _credit_note_refunded_sum_locked,
    _invoice_applied_sum_locked,
    apply_customer_payment_allocations,
    apply_customer_refund_allocations,
    create_customer_payment_from_payload,
    create_customer_refund_from_payload,
    rollback_customer_payment_allocations,
    rollback_customer_refund_allocations,
    update_customer_payment_from_payload,
    update_customer_refund_from_payload,
)

from .serializers import (
    CustomerSerializer,
    QuoteSerializer,
    InvoiceSerializer,
    InvoicePostSerializer,
    ZatcaSubmitSerializer,
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
        last = Quote.objects.order_by("-created_at").values_list("quote_number", flat=True).first()
        next_number = "QT-0001"
        if last:
            import re
            m = re.search(r"(\d+)$", last)
            if m:
                next_number = last[: last.rfind(m.group(1))] + str(int(m.group(1)) + 1).zfill(len(m.group(1)))
            else:
                next_number = last + "-1"
        return Response(
            {
                "status": [{"id": k, "label": v} for k, v in QUOTE_STATUS_CHOICES],
                "next_number": next_number,
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
        rec, early = begin_idempotent(request, scope="sales.invoice.create")
        if early:
            return early
        serializer = InvoiceSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        invoice = serializer.save()
        response = Response(InvoiceSerializer(invoice).data, status=status.HTTP_201_CREATED)
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response


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
        rec, early = begin_idempotent(request, scope="sales.invoice.update")
        if early:
            return early
        with transaction.atomic():
            invoice = Invoice.objects.select_for_update().filter(pk=pk, is_deleted=False).first()
            if not invoice:
                return finalize_idempotent_failure(rec, error="NOT_FOUND", message="Invoice not found.", http_status=404)  # type: ignore[arg-type]
            serializer = InvoiceSerializer(invoice, data=request.data, partial=True, context={"request": request})
            serializer.is_valid(raise_exception=True)
            invoice = serializer.save()
        response = Response(InvoiceSerializer(invoice).data)
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response

    def delete(self, request, pk):
        rec, early = begin_idempotent(request, scope="sales.invoice.delete")
        if early:
            return early
        with transaction.atomic():
            invoice = Invoice.objects.select_for_update().filter(pk=pk, is_deleted=False).first()
            if not invoice:
                return finalize_idempotent_failure(rec, error="NOT_FOUND", message="Invoice not found.", http_status=404)  # type: ignore[arg-type]
            if invoice.status in ("confirmed", "posted", "reported"):
                return finalize_idempotent_failure(
                    rec,  # type: ignore[arg-type]
                    error="INVOICE_LOCKED",
                    message="Invoice cannot be deleted after it has been confirmed.",
                    http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            invoice.is_deleted = True
            invoice.save(update_fields=["is_deleted", "updated_at"])
        response = Response(status=status.HTTP_204_NO_CONTENT)
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response


class InvoicePostAPI(APIView):
    """
    POST /sales/invoices/<uuid>/post/

    Confirms (locks) the invoice.  No journal entry and no XML are created here.
    Journal creation + ZATCA XML happen when the user clicks "Report to Fatoora"
    (POST /sales/invoices/<uuid>/zatca/submit/).
    """

    permission_classes = [IsAuthenticated, CanPostSales]

    def post(self, request, pk):
        rec, early = begin_idempotent(request, scope="sales.invoice.post")
        if early:
            return early

        serializer = InvoicePostSerializer(data=request.data, context={"invoice_id": str(pk)})
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                invoice = Invoice.objects.select_for_update().filter(pk=pk, is_deleted=False).first()
                if not invoice:
                    return finalize_idempotent_failure(  # type: ignore[arg-type]
                        rec,
                        error="NOT_FOUND",
                        message="Invoice not found.",
                        http_status=404,
                    )

                if invoice.status in ("confirmed", "posted", "reported"):
                    response = Response(InvoiceSerializer(invoice).data, status=status.HTTP_200_OK)
                    finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
                    return response

                if maker_checker_enabled("sales.invoice.post"):
                    approval = create_approval_request(
                        scope="sales.invoice.post",
                        object_type="sales.Invoice",
                        object_id=invoice.id,
                        payload=dict(request.data) if isinstance(request.data, dict) else {},
                        requested_by=request.user,
                    )
                    response = Response(
                        {"message": "Approval required.", "approval_id": str(approval.id), "status": approval.status},
                        status=status.HTTP_202_ACCEPTED,
                    )
                    finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
                    return response

                # Lock the invoice — no journal, no XML at this stage.
                invoice.status = "confirmed"
                invoice.posted_at = timezone.now()
                invoice.updator = request.user
                invoice.save(update_fields=["status", "posted_at", "updator", "updated_at"])
        except ValueError as exc:
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error="POST_VALIDATION_ERROR",
                message=str(exc),
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        response = Response(InvoiceSerializer(invoice).data)
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response


class InvoiceChoicesAPI(APIView):
    """
    GET /sales/invoices/choices/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        import re as _re
        last = Invoice.objects.order_by("-created_at").values_list("invoice_number", flat=True).first()
        next_number = "INV-0001"
        if last:
            m = _re.search(r"(\d+)$", last)
            if m:
                next_number = last[: last.rfind(m.group(1))] + str(int(m.group(1)) + 1).zfill(len(m.group(1)))
            else:
                next_number = last + "-1"
        return Response({"status": [{"id": k, "label": v} for k, v in INVOICE_STATUS_CHOICES], "next_number": next_number})


class CustomerPaymentChoicesAPI(APIView):
    """
    GET /sales/customer-payments/choices/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        import re as _re
        last = CustomerPayment.objects.order_by("-created_at").values_list("payment_number", flat=True).first()
        next_number = "CP-0001"
        if last:
            m = _re.search(r"(\d+)$", last)
            if m:
                next_number = last[: last.rfind(m.group(1))] + str(int(m.group(1)) + 1).zfill(len(m.group(1)))
            else:
                next_number = last + "-1"
        return Response({"next_number": next_number})


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

    permission_classes = [IsAuthenticated, CanPostSales]
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
        rec, early = begin_idempotent(request, scope="sales.customer_payment.create")
        if early:
            return early

        serializer = CustomerPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if maker_checker_enabled("sales.customer_payment.create"):
            payload = json.loads(json.dumps(request.data, default=str))
            approval = create_approval_request(
                scope="sales.customer_payment.create",
                object_type="sales.CustomerPayment.create",
                object_id=uuid.uuid4(),
                payload=payload,
                requested_by=request.user,
            )
            response = Response(
                {"message": "Approval required.", "approval_id": str(approval.id), "status": approval.status},
                status=status.HTTP_202_ACCEPTED,
            )
            finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
            return response

        try:
            payment = create_customer_payment_from_payload(
                payload=json.loads(json.dumps(request.data, default=str)),
                user=request.user,
            )
        except DRFValidationError as exc:
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error="VALIDATION_ERROR",
                message=str(exc.detail),
                http_status=status.HTTP_400_BAD_REQUEST,
            )
        except ValueError as exc:
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error="VALIDATION_ERROR",
                message=str(exc),
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        response = Response(CustomerPaymentSerializer(payment).data, status=status.HTTP_201_CREATED)
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response

    def _replace_allocations(self, payment: CustomerPayment, allocations, user):
        apply_customer_payment_allocations(payment, allocations, user)


class CustomerPaymentDetailAPI(APIView):
    """
    GET/PATCH/DELETE /sales/customer-payments/<uuid>/
    """

    permission_classes = [IsAuthenticated, CanSubmitZatca]

    def get(self, request, pk):
        payment = _get_customer_payment(pk)
        if not payment:
            return Response({"error": "NOT_FOUND", "message": "Customer payment not found."}, status=404)
        return Response(CustomerPaymentSerializer(payment).data)

    def patch(self, request, pk):
        rec, early = begin_idempotent(request, scope="sales.customer_payment.update")
        if early:
            return early

        payment = CustomerPayment.objects.filter(pk=pk, is_deleted=False).first()
        if not payment:
            return finalize_idempotent_failure(
                rec, error="NOT_FOUND", message="Customer payment not found.", http_status=404  # type: ignore[arg-type]
            )
        if payment.is_posted:
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error="PAYMENT_POSTED",
                message="Posted payment cannot be edited.",
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        if maker_checker_enabled("sales.customer_payment.update"):
            payload = json.loads(json.dumps(request.data, default=str))
            approval = create_approval_request(
                scope="sales.customer_payment.update",
                object_type="sales.CustomerPayment",
                object_id=payment.id,
                payload=payload,
                requested_by=request.user,
            )
            response = Response(
                {"message": "Approval required.", "approval_id": str(approval.id), "status": approval.status},
                status=status.HTTP_202_ACCEPTED,
            )
            finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
            return response

        try:
            payment = update_customer_payment_from_payload(
                payment_id=pk,
                payload=json.loads(json.dumps(request.data, default=str)),
                user=request.user,
            )
        except LookupError:
            return finalize_idempotent_failure(
                rec, error="NOT_FOUND", message="Customer payment not found.", http_status=404  # type: ignore[arg-type]
            )
        except DRFValidationError as exc:
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error="VALIDATION_ERROR",
                message=str(exc.detail),
                http_status=status.HTTP_400_BAD_REQUEST,
            )
        except ValueError as exc:
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error="VALIDATION_ERROR",
                message=str(exc),
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        response = Response(CustomerPaymentSerializer(payment).data)
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response

    def delete(self, request, pk):
        rec, early = begin_idempotent(request, scope="sales.customer_payment.delete")
        if early:
            return early

        with transaction.atomic():
            payment = CustomerPayment.objects.select_for_update().filter(pk=pk, is_deleted=False).first()
            if not payment:
                return finalize_idempotent_failure(
                    rec, error="NOT_FOUND", message="Customer payment not found.", http_status=404  # type: ignore[arg-type]
                )
            if payment.is_posted:
                return finalize_idempotent_failure(
                    rec,  # type: ignore[arg-type]
                    error="PAYMENT_POSTED",
                    message="Posted payment cannot be deleted.",
                    http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            self._rollback_allocations(payment)
            payment.is_deleted = True
            payment.save(update_fields=["is_deleted", "updated_at"])

        response = Response(status=status.HTTP_204_NO_CONTENT)
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response

    def _rollback_allocations(self, payment: CustomerPayment):
        rollback_customer_payment_allocations(payment)

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
            status__in=["confirmed", "posted", "reported", "partially_paid", "overdue"],
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
                    "date": str(invoice.date),
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

    permission_classes = [IsAuthenticated, CanSubmitZatca]
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
        rec, early = begin_idempotent(request, scope="sales.customer_refund.create")
        if early:
            return early

        serializer = CustomerRefundSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if maker_checker_enabled("sales.customer_refund.create"):
            payload = json.loads(json.dumps(request.data, default=str))
            approval = create_approval_request(
                scope="sales.customer_refund.create",
                object_type="sales.CustomerRefund.create",
                object_id=uuid.uuid4(),
                payload=payload,
                requested_by=request.user,
            )
            response = Response(
                {"message": "Approval required.", "approval_id": str(approval.id), "status": approval.status},
                status=status.HTTP_202_ACCEPTED,
            )
            finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
            return response

        try:
            refund = create_customer_refund_from_payload(
                payload=json.loads(json.dumps(request.data, default=str)),
                user=request.user,
            )
        except DRFValidationError as exc:
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error="VALIDATION_ERROR",
                message=str(exc.detail),
                http_status=status.HTTP_400_BAD_REQUEST,
            )
        except ValueError as exc:
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error="VALIDATION_ERROR",
                message=str(exc),
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        response = Response(CustomerRefundSerializer(refund).data, status=status.HTTP_201_CREATED)
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response

    def _replace_allocations(self, refund: CustomerRefund, allocations, user):
        apply_customer_refund_allocations(refund, allocations, user)


class CustomerRefundDetailAPI(APIView):
    """
    GET/PATCH/DELETE /sales/customer-refunds/<uuid>/
    """

    permission_classes = [IsAuthenticated, CanViewZatca]

    def get(self, request, pk):
        refund = _get_customer_refund(pk)
        if not refund:
            return Response({"error": "NOT_FOUND", "message": "Customer refund not found."}, status=404)
        return Response(CustomerRefundSerializer(refund).data)

    def patch(self, request, pk):
        rec, early = begin_idempotent(request, scope="sales.customer_refund.update")
        if early:
            return early

        refund = CustomerRefund.objects.filter(pk=pk, is_deleted=False).first()
        if not refund:
            return finalize_idempotent_failure(
                rec, error="NOT_FOUND", message="Customer refund not found.", http_status=404  # type: ignore[arg-type]
            )
        if refund.is_posted:
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error="REFUND_POSTED",
                message="Posted refund cannot be edited.",
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        if maker_checker_enabled("sales.customer_refund.update"):
            payload = json.loads(json.dumps(request.data, default=str))
            approval = create_approval_request(
                scope="sales.customer_refund.update",
                object_type="sales.CustomerRefund",
                object_id=refund.id,
                payload=payload,
                requested_by=request.user,
            )
            response = Response(
                {"message": "Approval required.", "approval_id": str(approval.id), "status": approval.status},
                status=status.HTTP_202_ACCEPTED,
            )
            finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
            return response

        try:
            refund = update_customer_refund_from_payload(
                refund_id=pk,
                payload=json.loads(json.dumps(request.data, default=str)),
                user=request.user,
            )
        except LookupError:
            return finalize_idempotent_failure(
                rec, error="NOT_FOUND", message="Customer refund not found.", http_status=404  # type: ignore[arg-type]
            )
        except DRFValidationError as exc:
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error="VALIDATION_ERROR",
                message=str(exc.detail),
                http_status=status.HTTP_400_BAD_REQUEST,
            )
        except ValueError as exc:
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error="VALIDATION_ERROR",
                message=str(exc),
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        response = Response(CustomerRefundSerializer(refund).data)
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response

    def delete(self, request, pk):
        rec, early = begin_idempotent(request, scope="sales.customer_refund.delete")
        if early:
            return early

        with transaction.atomic():
            refund = CustomerRefund.objects.select_for_update().filter(pk=pk, is_deleted=False).first()
            if not refund:
                return finalize_idempotent_failure(
                    rec, error="NOT_FOUND", message="Customer refund not found.", http_status=404  # type: ignore[arg-type]
                )
            if refund.is_posted:
                return finalize_idempotent_failure(
                    rec,  # type: ignore[arg-type]
                    error="REFUND_POSTED",
                    message="Posted refund cannot be deleted.",
                    http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            self._rollback_allocations(refund)
            refund.is_deleted = True
            refund.save(update_fields=["is_deleted", "updated_at"])

        response = Response(status=status.HTTP_204_NO_CONTENT)
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response

    def _rollback_allocations(self, refund: CustomerRefund):
        rollback_customer_refund_allocations(refund)

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
            status__in=["confirmed", "posted", "reported"],
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


class CustomerCreditNoteChoicesAPI(APIView):
    """
    GET /sales/credit-notes/choices/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        import re as _re
        last = CustomerCreditNote.objects.order_by("-created_at").values_list("credit_note_number", flat=True).first()
        next_number = "CN-0001"
        if last:
            m = _re.search(r"(\d+)$", last)
            if m:
                next_number = last[: last.rfind(m.group(1))] + str(int(m.group(1)) + 1).zfill(len(m.group(1)))
            else:
                next_number = last + "-1"
        return Response({"next_number": next_number})


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
        rec, early = begin_idempotent(request, scope="sales.credit_note.create")
        if early:
            return early
        serializer = CustomerCreditNoteSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        note = serializer.save()
        response = Response(CustomerCreditNoteSerializer(note).data, status=status.HTTP_201_CREATED)
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response


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
        rec, early = begin_idempotent(request, scope="sales.credit_note.update")
        if early:
            return early
        with transaction.atomic():
            note = CustomerCreditNote.objects.select_for_update().filter(pk=pk, is_deleted=False).first()
            if not note:
                return finalize_idempotent_failure(rec, error="NOT_FOUND", message="Credit note not found.", http_status=404)  # type: ignore[arg-type]
            serializer = CustomerCreditNoteSerializer(note, data=request.data, partial=True, context={"request": request})
            serializer.is_valid(raise_exception=True)
            note = serializer.save()
        response = Response(CustomerCreditNoteSerializer(note).data)
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response

    def delete(self, request, pk):
        rec, early = begin_idempotent(request, scope="sales.credit_note.delete")
        if early:
            return early
        with transaction.atomic():
            note = CustomerCreditNote.objects.select_for_update().filter(pk=pk, is_deleted=False).first()
            if not note:
                return finalize_idempotent_failure(rec, error="NOT_FOUND", message="Credit note not found.", http_status=404)  # type: ignore[arg-type]
            if note.status == "posted":
                return finalize_idempotent_failure(
                    rec,  # type: ignore[arg-type]
                    error="CREDIT_NOTE_POSTED",
                    message="Posted credit note cannot be deleted.",
                    http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            note.is_deleted = True
            note.save(update_fields=["is_deleted", "updated_at"])
        response = Response(status=status.HTTP_204_NO_CONTENT)
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response


class CustomerCreditNotePostAPI(APIView):
    """
    POST /sales/credit-notes/<uuid>/post/

    Confirms (locks) the credit note.  No journal entry and no XML are created here.
    Journal creation + ZATCA XML happen when the user clicks "Report to Fatoora"
    (POST /sales/credit-notes/<uuid>/zatca/submit/).
    """

    permission_classes = [IsAuthenticated, CanViewZatca]

    def post(self, request, pk):
        rec, early = begin_idempotent(request, scope="sales.credit_note.post")
        if early:
            return early

        serializer = CustomerCreditNotePostSerializer(data=request.data, context={"credit_note_id": str(pk)})
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                note = CustomerCreditNote.objects.select_for_update().filter(pk=pk, is_deleted=False).first()
                if not note:
                    return finalize_idempotent_failure(  # type: ignore[arg-type]
                        rec, error="NOT_FOUND", message="Credit note not found.", http_status=404
                    )
                if note.status in ("confirmed", "posted", "reported"):
                    response = Response(CustomerCreditNoteSerializer(note).data, status=status.HTTP_200_OK)
                    finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
                    return response

                if maker_checker_enabled("sales.credit_note.post"):
                    approval = create_approval_request(
                        scope="sales.credit_note.post",
                        object_type="sales.CustomerCreditNote",
                        object_id=note.id,
                        payload=dict(request.data) if isinstance(request.data, dict) else {},
                        requested_by=request.user,
                    )
                    response = Response(
                        {"message": "Approval required.", "approval_id": str(approval.id), "status": approval.status},
                        status=status.HTTP_202_ACCEPTED,
                    )
                    finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
                    return response

                # Lock the credit note — no journal, no XML at this stage.
                note.status = "confirmed"
                note.posted_at = timezone.now()
                note.updator = request.user
                note.save(update_fields=["status", "posted_at", "updator", "updated_at"])
        except ValueError as exc:
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error="POST_VALIDATION_ERROR",
                message=str(exc),
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        response = Response(CustomerCreditNoteSerializer(note).data)
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response


class InvoiceZatcaSubmitAPI(APIView):
    """
    POST /sales/invoices/<uuid>/zatca/submit/
    """

    permission_classes = [IsAuthenticated, CanSubmitZatca]

    def post(self, request, pk):
        rec, early = begin_idempotent(request, scope="sales.invoice.zatca.submit")
        if early:
            return early

        serializer = ZatcaSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            invoice = Invoice.objects.select_for_update().filter(pk=pk, is_deleted=False).first()
            if not invoice:
                return finalize_idempotent_failure(rec, error="NOT_FOUND", message="Invoice not found.", http_status=404)  # type: ignore[arg-type]
            if invoice.status not in ("confirmed", "posted", "reported"):
                return finalize_idempotent_failure(
                    rec,  # type: ignore[arg-type]
                    error="NOT_CONFIRMED",
                    message="Invoice must be confirmed before reporting to Fatoora.",
                    http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            if AccountingPeriod.is_date_closed(invoice.date):
                return finalize_idempotent_failure(
                    rec,  # type: ignore[arg-type]
                    error="PERIOD_CLOSED",
                    message=f"Submission not allowed: {invoice.date} is in a closed accounting period.",
                    http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )

            if maker_checker_enabled("sales.invoice.zatca.submit"):
                approval = create_approval_request(
                    scope="sales.invoice.zatca.submit",
                    object_type="sales.Invoice",
                    object_id=invoice.id,
                    payload={"submission_type": serializer.validated_data["submission_type"]},
                    requested_by=request.user,
                )
                response = Response(
                    {"message": "Approval required.", "approval_id": str(approval.id), "status": approval.status},
                    status=status.HTTP_202_ACCEPTED,
                )
                finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
                return response

            idempotency_key = get_idempotency_key(request)
            existing = ZatcaSubmissionLog.objects.filter(
                document_type="invoice", idempotency_key=idempotency_key, is_deleted=False
            ).first()
            if existing:
                response = Response(InvoiceSerializer(invoice).data, status=status.HTTP_200_OK)
                finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
                return response

            # Sign the invoice now if it hasn't been signed yet (e.g. ZATCA_SIGNING_ENABLED=False
            # was set during posting, so signing was deferred until this "Report to Fatoora" call).
            if not (invoice.zatca_signed_xml or "").strip():
                try:
                    prepare_zatca_artifacts(invoice, is_credit_note=False, force_sign=True)
                    invoice.save(
                        update_fields=[
                            "qr_code_text",
                            "zatca_uuid",
                            "zatca_previous_hash",
                            "zatca_invoice_hash",
                            "zatca_signed_hash",
                            "zatca_xml",
                            "zatca_canonical_xml",
                            "zatca_signature_value",
                            "zatca_signed_xml",
                            "zatca_certificate",
                            "zatca_submission_status",
                            "zatca_submission_error",
                            "updated_at",
                        ]
                    )
                except Exception as exc:
                    return finalize_idempotent_failure(
                        rec,  # type: ignore[arg-type]
                        error="POST_VALIDATION_ERROR",
                        message=str(exc),
                        http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    )

            log = ZatcaSubmissionLog.objects.create(
                document_type="invoice",
                document_id=invoice.id,
                submission_type=serializer.validated_data["submission_type"],
                idempotency_key=idempotency_key,
                status="pending",
                attempt_count=1,
                submitted_at=timezone.now(),
                provider_status="pending",
                creator=request.user,
            )
        status_code, body = run_zatca_submit_pipeline(
            document=invoice,
            document_type="invoice",
            submission_type=serializer.validated_data["submission_type"],
            idempotency_key=idempotency_key,
            user=request.user,
            log=log,
        )
        # On success (sync simulation path) or when queued (async outbox path):
        # create the journal entry and mark invoice as "reported".
        if status_code in (status.HTTP_200_OK, status.HTTP_202_ACCEPTED):
            if invoice.status not in ("reported", "posted"):
                try:
                    with transaction.atomic():
                        invoice_fresh = Invoice.objects.select_for_update().filter(pk=invoice.pk, is_deleted=False).first()
                        if invoice_fresh and invoice_fresh.status not in ("reported", "posted"):
                            je = post_invoice_journal(invoice=invoice_fresh, user=request.user)
                            invoice_fresh.journal_entry = je
                            invoice_fresh.status = "reported"
                            invoice_fresh.updator = request.user
                            invoice_fresh.save(update_fields=["status", "journal_entry", "updator", "updated_at"])
                            invoice = invoice_fresh
                except Exception:
                    pass  # journal creation failure is non-fatal here; document was already submitted

        response = Response(body, status=status_code)
        if status_code in (status.HTTP_200_OK, status.HTTP_202_ACCEPTED):
            finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response


class CreditNoteZatcaSubmitAPI(APIView):
    """
    POST /sales/credit-notes/<uuid>/zatca/submit/
    """

    permission_classes = [IsAuthenticated, CanSubmitZatca]

    def post(self, request, pk):
        rec, early = begin_idempotent(request, scope="sales.credit_note.zatca.submit")
        if early:
            return early

        serializer = ZatcaSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            note = CustomerCreditNote.objects.select_for_update().filter(pk=pk, is_deleted=False).first()
            if not note:
                return finalize_idempotent_failure(rec, error="NOT_FOUND", message="Credit note not found.", http_status=404)  # type: ignore[arg-type]
            if note.status not in ("confirmed", "posted", "reported"):
                return finalize_idempotent_failure(
                    rec,  # type: ignore[arg-type]
                    error="NOT_CONFIRMED",
                    message="Credit note must be confirmed before reporting to Fatoora.",
                    http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            if AccountingPeriod.is_date_closed(note.date):
                return finalize_idempotent_failure(
                    rec,  # type: ignore[arg-type]
                    error="PERIOD_CLOSED",
                    message=f"Submission not allowed: {note.date} is in a closed accounting period.",
                    http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )

            if maker_checker_enabled("sales.credit_note.zatca.submit"):
                approval = create_approval_request(
                    scope="sales.credit_note.zatca.submit",
                    object_type="sales.CustomerCreditNote",
                    object_id=note.id,
                    payload={"submission_type": serializer.validated_data["submission_type"]},
                    requested_by=request.user,
                )
                response = Response(
                    {"message": "Approval required.", "approval_id": str(approval.id), "status": approval.status},
                    status=status.HTTP_202_ACCEPTED,
                )
                finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
                return response

            idempotency_key = get_idempotency_key(request)
            existing = ZatcaSubmissionLog.objects.filter(
                document_type="credit_note", idempotency_key=idempotency_key, is_deleted=False
            ).first()
            if existing:
                response = Response(CustomerCreditNoteSerializer(note).data, status=status.HTTP_200_OK)
                finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
                return response

            # Sign the credit note now if it hasn't been signed yet (deferred signing path).
            if not (note.zatca_signed_xml or "").strip():
                try:
                    prepare_zatca_artifacts(note, is_credit_note=True, force_sign=True)
                    note.save(
                        update_fields=[
                            "qr_code_text",
                            "zatca_uuid",
                            "zatca_previous_hash",
                            "zatca_invoice_hash",
                            "zatca_signed_hash",
                            "zatca_xml",
                            "zatca_canonical_xml",
                            "zatca_signature_value",
                            "zatca_signed_xml",
                            "zatca_certificate",
                            "zatca_submission_status",
                            "zatca_submission_error",
                            "updated_at",
                        ]
                    )
                except Exception as exc:
                    return finalize_idempotent_failure(
                        rec,  # type: ignore[arg-type]
                        error="POST_VALIDATION_ERROR",
                        message=str(exc),
                        http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    )

            log = ZatcaSubmissionLog.objects.create(
                document_type="credit_note",
                document_id=note.id,
                submission_type=serializer.validated_data["submission_type"],
                idempotency_key=idempotency_key,
                status="pending",
                attempt_count=1,
                submitted_at=timezone.now(),
                provider_status="pending",
                creator=request.user,
            )
        status_code, body = run_zatca_submit_pipeline(
            document=note,
            document_type="credit_note",
            submission_type=serializer.validated_data["submission_type"],
            idempotency_key=idempotency_key,
            user=request.user,
            log=log,
        )
        if status_code in (status.HTTP_200_OK, status.HTTP_202_ACCEPTED):
            if note.status not in ("reported", "posted"):
                try:
                    with transaction.atomic():
                        note_fresh = CustomerCreditNote.objects.select_for_update().filter(pk=note.pk, is_deleted=False).first()
                        if note_fresh and note_fresh.status not in ("reported", "posted"):
                            je = post_credit_note_journal(note=note_fresh, user=request.user)
                            note_fresh.journal_entry = je
                            note_fresh.status = "reported"
                            note_fresh.updator = request.user
                            note_fresh.save(update_fields=["status", "journal_entry", "updator", "updated_at"])
                except Exception:
                    pass

        response = Response(body, status=status_code)
        if status_code in (status.HTTP_200_OK, status.HTTP_202_ACCEPTED):
            finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response


class InvoiceZatcaVerifyAPI(APIView):
    """
    GET /sales/invoices/<uuid>/zatca/verify/
    """

    permission_classes = [IsAuthenticated, CanViewZatca]

    def get(self, request, pk):
        invoice = _get_invoice(pk)
        if not invoice:
            return Response({"error": "NOT_FOUND", "message": "Invoice not found."}, status=404)
        return Response(verify_document_hash(invoice))


class CreditNoteZatcaVerifyAPI(APIView):
    """
    GET /sales/credit-notes/<uuid>/zatca/verify/
    """

    permission_classes = [IsAuthenticated, CanViewZatca]

    def get(self, request, pk):
        note = _get_credit_note(pk)
        if not note:
            return Response({"error": "NOT_FOUND", "message": "Credit note not found."}, status=404)
        return Response(verify_document_hash(note))

