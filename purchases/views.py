import json
import uuid

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django.db.models import Q, Sum
from django.db import transaction
from django.db import DatabaseError
from rest_framework.pagination import PageNumberPagination
from decimal import Decimal

from main.approvals import create_approval_request, maker_checker_enabled

from accounting.models import JournalEntry, JournalEntryLine, Account
from main.management.commands.create_groups_and_permissions import IsAdmin
from main.idempotency import begin_idempotent, finalize_idempotent_failure, finalize_idempotent_success
from accounting.permissions import CanPostPurchases
from accounting.services.posting import post_bill_journal, post_debit_note_journal, post_supplier_payment_journal
from purchases.supplier_payment_posting import apply_supplier_payment_allocations, sync_bill_payment_status
from purchases.supplier_refund_posting import (
    apply_supplier_refund_allocations,
    rollback_supplier_refund_allocations,
    create_supplier_refund_from_payload,
)
from .models import (
    Supplier,
    PAYMENT_TERMS_CHOICES,
    VAT_TREATMENT_CHOICES,
    OPENING_BALANCE_CHOICES,
    Bill,
    SupplierPayment,
    SupplierPaymentAllocation,
    SUPPLIER_PAYMENT_TYPE_CHOICES,
    DebitNote,
    SupplierRefund,
)
from .serializers import (
    SupplierSerializer,
    BillSerializer,
    BillPostSerializer,
    BillListSerializer,
    SupplierPaymentSerializer,
    DebitNoteSerializer,
    SupplierRefundSerializer,
)


class SupplierPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 200


def _get_supplier(pk):
    try:
        return Supplier.objects.get(pk=pk, is_deleted=False)
    except Supplier.DoesNotExist:
        return None


class SupplierListCreateAPI(APIView):
    """
    GET  /purchases/suppliers/
    POST /purchases/suppliers/
    """

    permission_classes = [IsAuthenticated]
    pagination_class = SupplierPagination

    def get(self, request):
        qs = Supplier.objects.filter(is_deleted=False).select_related("country", "opening_balance_account")

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
        return paginator.get_paginated_response(SupplierSerializer(page, many=True).data)

    def post(self, request):
        serializer = SupplierSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        supplier = serializer.save(creator=request.user)
        return Response(SupplierSerializer(supplier).data, status=status.HTTP_201_CREATED)


class SupplierDetailAPI(APIView):
    """
    GET/PATCH/DELETE /purchases/suppliers/<uuid>/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        supplier = _get_supplier(pk)
        if not supplier:
            return Response({"error": "NOT_FOUND", "message": "Supplier not found."}, status=404)
        return Response(SupplierSerializer(supplier).data)

    def patch(self, request, pk):
        supplier = _get_supplier(pk)
        if not supplier:
            return Response({"error": "NOT_FOUND", "message": "Supplier not found."}, status=404)
        serializer = SupplierSerializer(supplier, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        supplier = serializer.save(updator=request.user)
        return Response(SupplierSerializer(supplier).data)

    def delete(self, request, pk):
        supplier = _get_supplier(pk)
        if not supplier:
            return Response({"error": "NOT_FOUND", "message": "Supplier not found."}, status=404)

        # Future: block delete if used in bills/payments. For now allow soft delete.
        supplier.is_deleted = True
        supplier.save(update_fields=["is_deleted", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class SupplierChoicesAPI(APIView):
    """
    GET /purchases/suppliers/choices/
    Returns dropdown choices for the Add Supplier form.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            {
                "payment_terms": [{"id": k, "label": v} for k, v in PAYMENT_TERMS_CHOICES],
                "vat_treatments": [{"id": k, "label": v} for k, v in VAT_TREATMENT_CHOICES],
                "opening_balance_types": [{"id": k, "label": v} for k, v in OPENING_BALANCE_CHOICES],
            }
        )


class BillPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 200


def _get_bill(pk):
    try:
        return Bill.objects.get(pk=pk, is_deleted=False)
    except Bill.DoesNotExist:
        return None


class BillListCreateAPI(APIView):
    """
    GET  /purchases/bills/
    POST /purchases/bills/
    """

    permission_classes = [IsAuthenticated]
    pagination_class = BillPagination

    def get(self, request):
        qs = Bill.objects.filter(is_deleted=False).select_related("supplier")

        search = request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(bill_number__icontains=search)
                | Q(note__icontains=search)
                | Q(supplier__company_name__icontains=search)
            )

        status_param = request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)

        supplier = request.query_params.get("supplier")
        if supplier:
            qs = qs.filter(supplier_id=supplier)

        date_from = request.query_params.get("date_from")
        if date_from:
            qs = qs.filter(bill_date__gte=date_from)

        date_to = request.query_params.get("date_to")
        if date_to:
            qs = qs.filter(bill_date__lte=date_to)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs.order_by("-bill_date", "-created_at"), request)
        return paginator.get_paginated_response(BillListSerializer(page, many=True).data)

    def post(self, request):
        rec, early = begin_idempotent(request, scope="purchases.bill.create")
        if early:
            return early
        serializer = BillSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        bill = serializer.save()
        response = Response(BillSerializer(bill).data, status=status.HTTP_201_CREATED)
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response


class BillDetailAPI(APIView):
    """
    GET/PATCH/DELETE /purchases/bills/<uuid>/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        bill = _get_bill(pk)
        if not bill:
            return Response({"error": "NOT_FOUND", "message": "Bill not found."}, status=404)
        return Response(BillSerializer(bill).data)

    def patch(self, request, pk):
        rec, early = begin_idempotent(request, scope="purchases.bill.update")
        if early:
            return early
        with transaction.atomic():
            bill = Bill.objects.select_for_update().filter(pk=pk, is_deleted=False).first()
            if not bill:
                return finalize_idempotent_failure(rec, error="NOT_FOUND", message="Bill not found.", http_status=404)  # type: ignore[arg-type]
            serializer = BillSerializer(bill, data=request.data, partial=True, context={"request": request})
            serializer.is_valid(raise_exception=True)
            bill = serializer.save()
        response = Response(BillSerializer(bill).data)
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response

    def delete(self, request, pk):
        rec, early = begin_idempotent(request, scope="purchases.bill.delete")
        if early:
            return early
        with transaction.atomic():
            bill = Bill.objects.select_for_update().filter(pk=pk, is_deleted=False).first()
            if not bill:
                return finalize_idempotent_failure(rec, error="NOT_FOUND", message="Bill not found.", http_status=404)  # type: ignore[arg-type]
            if bill.status in ("posted", "partially_paid", "paid"):
                return finalize_idempotent_failure(
                    rec,  # type: ignore[arg-type]
                    error="BILL_POSTED",
                    message="Posted bill cannot be deleted.",
                    http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            bill.is_deleted = True
            bill.save(update_fields=["is_deleted", "updated_at"])
        response = Response(status=status.HTTP_204_NO_CONTENT)
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response


class BillPostAPI(APIView):
    """
    POST /purchases/bills/<uuid>/post/
    """

    permission_classes = [IsAuthenticated, CanPostPurchases]

    def post(self, request, pk):
        rec, early = begin_idempotent(request, scope="purchases.bill.post")
        if early:
            return early

        serializer = BillPostSerializer(data=request.data, context={"bill_id": str(pk)})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        with transaction.atomic():
            bill = Bill.objects.select_for_update().filter(pk=pk, is_deleted=False).first()
            if not bill:
                return finalize_idempotent_failure(  # type: ignore[arg-type]
                    rec, error="NOT_FOUND", message="Bill not found.", http_status=404
                )
            if bill.status in ("posted", "partially_paid", "paid"):
                response = Response(BillSerializer(bill).data, status=status.HTTP_200_OK)
                finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
                return response

            if maker_checker_enabled("purchases.bill.post"):
                payload = json.loads(json.dumps(data, default=str))
                approval = create_approval_request(
                    scope="purchases.bill.post",
                    object_type="purchases.Bill",
                    object_id=bill.id,
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
                je = post_bill_journal(
                    bill=bill,
                    user=request.user,
                    payable_account_id=data.get("payable_account"),
                    vat_account_id=data.get("vat_account"),
                    posting_date=data.get("posting_date"),
                    memo=data.get("memo", ""),
                )
                bill.journal_entry = je
                bill.save(update_fields=["journal_entry", "updated_at"])
            except ValueError as exc:
                return finalize_idempotent_failure(
                    rec,  # type: ignore[arg-type]
                    error="POST_VALIDATION_ERROR",
                    message=str(exc),
                    http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )

            bill.mark_posted(user=request.user)

        response = Response(BillSerializer(bill).data, status=status.HTTP_200_OK)
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response

class SupplierPaymentChoicesAPI(APIView):
    """
    GET /purchases/supplier-payments/choices/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        import re as _re
        last = SupplierPayment.objects.order_by("-created_at").values_list("payment_number", flat=True).first()
        next_number = "SP-0001"
        if last:
            m = _re.search(r"(\d+)$", last)
            if m:
                next_number = last[: last.rfind(m.group(1))] + str(int(m.group(1)) + 1).zfill(len(m.group(1)))
            else:
                next_number = last + "-1"
        return Response({"next_number": next_number})


class SupplierPaymentPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 200


def _get_payment(pk):
    try:
        return SupplierPayment.objects.get(pk=pk, is_deleted=False)
    except SupplierPayment.DoesNotExist:
        return None


class SupplierPaymentListCreateAPI(APIView):
    """
    GET  /purchases/supplier-payments/
    POST /purchases/supplier-payments/
    """

    permission_classes = [IsAuthenticated, CanPostPurchases]
    pagination_class = SupplierPaymentPagination

    def get(self, request):
        qs = SupplierPayment.objects.filter(is_deleted=False).select_related("supplier", "paid_through")
        search = request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(payment_number__icontains=search)
                | Q(supplier__company_name__icontains=search)
                | Q(description__icontains=search)
            )
        supplier = request.query_params.get("supplier")
        if supplier:
            qs = qs.filter(supplier_id=supplier)
        payment_type = request.query_params.get("payment_type")
        if payment_type:
            qs = qs.filter(payment_type=payment_type)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs.order_by("-payment_date", "-created_at"), request)
        return paginator.get_paginated_response(SupplierPaymentSerializer(page, many=True).data)

    def post(self, request):
        rec, early = begin_idempotent(request, scope="purchases.supplier_payment.create")
        if early:
            return early

        serializer = SupplierPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        allocations = request.data.get("allocations", [])

        if maker_checker_enabled("purchases.supplier_payment.create"):
            payload = json.loads(json.dumps(request.data, default=str))
            approval = create_approval_request(
                scope="purchases.supplier_payment.create",
                object_type="purchases.SupplierPayment.create",
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
            with transaction.atomic():
                payment = serializer.save(creator=request.user)
                apply_supplier_payment_allocations(payment, allocations, user=request.user)
                je = post_supplier_payment_journal(payment=payment, user=request.user)
                payment.journal_entry = je
                payment.save(update_fields=["journal_entry", "updated_at"])
                payment.refresh_from_db()
        except (ValueError, DatabaseError) as exc:
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error="VALIDATION_ERROR",
                message=str(exc),
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        response = Response(SupplierPaymentSerializer(payment).data, status=status.HTTP_201_CREATED)
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response

    def _replace_allocations(self, payment: SupplierPayment, allocations, user):
        apply_supplier_payment_allocations(payment, allocations, user)


class SupplierPaymentDetailAPI(APIView):
    """
    GET/PATCH/DELETE /purchases/supplier-payments/<uuid>/
    """

    permission_classes = [IsAuthenticated, CanPostPurchases]

    def get(self, request, pk):
        payment = _get_payment(pk)
        if not payment:
            return Response({"error": "NOT_FOUND", "message": "Supplier payment not found."}, status=404)
        return Response(SupplierPaymentSerializer(payment).data)

    def patch(self, request, pk):
        rec, early = begin_idempotent(request, scope="purchases.supplier_payment.update")
        if early:
            return early

        allocations = request.data.get("allocations", None)
        try:
            with transaction.atomic():
                payment = SupplierPayment.objects.select_for_update().filter(pk=pk, is_deleted=False).first()
                if not payment:
                    return finalize_idempotent_failure(
                        rec, error="NOT_FOUND", message="Supplier payment not found.", http_status=404  # type: ignore[arg-type]
                    )
                if payment.is_posted:
                    return finalize_idempotent_failure(
                        rec,  # type: ignore[arg-type]
                        error="PAYMENT_POSTED",
                        message="Posted payment cannot be edited.",
                        http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    )

                serializer = SupplierPaymentSerializer(payment, data=request.data, partial=True)
                serializer.is_valid(raise_exception=True)
                self._rollback_allocations(payment)
                payment = serializer.save(updator=request.user)
                if allocations is not None:
                    self._replace_allocations(payment, allocations, user=request.user)
                payment.refresh_from_db()
        except (ValueError, DatabaseError) as exc:
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error="VALIDATION_ERROR",
                message=str(exc),
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        response = Response(SupplierPaymentSerializer(payment).data)
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response

    def delete(self, request, pk):
        rec, early = begin_idempotent(request, scope="purchases.supplier_payment.delete")
        if early:
            return early

        with transaction.atomic():
            payment = SupplierPayment.objects.select_for_update().filter(pk=pk, is_deleted=False).first()
            if not payment:
                return finalize_idempotent_failure(
                    rec, error="NOT_FOUND", message="Supplier payment not found.", http_status=404  # type: ignore[arg-type]
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

    def _rollback_allocations(self, payment: SupplierPayment):
        affected_bill_ids = list(
            payment.allocations.filter(is_deleted=False).values_list("bill_id", flat=True)
        )
        payment.allocations.filter(is_deleted=False).update(is_deleted=True)
        for bill_id in affected_bill_ids:
            bill = Bill.objects.select_for_update().get(pk=bill_id)
            sync_bill_payment_status(bill)

    def _replace_allocations(self, payment: SupplierPayment, allocations, user):
        return SupplierPaymentListCreateAPI()._replace_allocations(payment, allocations, user)


class SupplierOutstandingBillsAPI(APIView):
    """
    GET /purchases/supplier-payments/outstanding-bills/?supplier=<uuid>
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        supplier_id = request.query_params.get("supplier")
        if not supplier_id:
            return Response(
                {"error": "SUPPLIER_REQUIRED", "message": "supplier query param is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        bills = Bill.objects.filter(
            is_deleted=False,
            status__in=("posted", "partially_paid"),
            supplier_id=supplier_id,
        ).order_by("bill_date", "created_at")

        results = []
        for bill in bills:
            balance = bill.balance_amount
            if balance <= 0:
                continue
            results.append(
                {
                    "id": str(bill.id),
                    "bill_number": bill.bill_number,
                    "bill_date": bill.bill_date,
                    "total_amount": str(bill.total_amount),
                    "paid_amount": str(bill.paid_amount),
                    "balance_amount": str(balance),
                }
            )
        return Response({"results": results, "payment_types": [{"id": k, "label": v} for k, v in SUPPLIER_PAYMENT_TYPE_CHOICES]})


class DebitNotePagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 200


def _get_debit_note(pk):
    try:
        return DebitNote.objects.get(pk=pk, is_deleted=False)
    except DebitNote.DoesNotExist:
        return None


class DebitNoteListCreateAPI(APIView):
    """
    GET  /purchases/debit-notes/
    POST /purchases/debit-notes/
    """

    permission_classes = [IsAuthenticated]
    pagination_class = DebitNotePagination

    def get(self, request):
        qs = DebitNote.objects.filter(is_deleted=False).select_related("supplier")
        search = request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(debit_note_number__icontains=search)
                | Q(note__icontains=search)
                | Q(supplier__company_name__icontains=search)
            )
        supplier = request.query_params.get("supplier")
        if supplier:
            qs = qs.filter(supplier_id=supplier)
        status_param = request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        date_from = request.query_params.get("date_from")
        if date_from:
            qs = qs.filter(date__gte=date_from)
        date_to = request.query_params.get("date_to")
        if date_to:
            qs = qs.filter(date__lte=date_to)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs.order_by("-date", "-created_at"), request)
        return paginator.get_paginated_response(DebitNoteSerializer(page, many=True).data)

    def post(self, request):
        serializer = DebitNoteSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        note = serializer.save()
        return Response(DebitNoteSerializer(note).data, status=status.HTTP_201_CREATED)


class DebitNoteDetailAPI(APIView):
    """
    GET/PATCH/DELETE /purchases/debit-notes/<uuid>/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        note = _get_debit_note(pk)
        if not note:
            return Response({"error": "NOT_FOUND", "message": "Debit note not found."}, status=404)
        return Response(DebitNoteSerializer(note).data)

    def patch(self, request, pk):
        note = _get_debit_note(pk)
        if not note:
            return Response({"error": "NOT_FOUND", "message": "Debit note not found."}, status=404)
        serializer = DebitNoteSerializer(note, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        note = serializer.save()
        return Response(DebitNoteSerializer(note).data)

    def delete(self, request, pk):
        note = _get_debit_note(pk)
        if not note:
            return Response({"error": "NOT_FOUND", "message": "Debit note not found."}, status=404)
        if note.status == "posted":
            return Response(
                {"error": "DEBIT_NOTE_POSTED", "message": "Posted debit note cannot be deleted."},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        note.is_deleted = True
        note.save(update_fields=["is_deleted", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class DebitNotePostAPI(APIView):
    """
    POST /purchases/debit-notes/<uuid>/post/
    Transitions a draft debit note to posted and creates its journal entry.
    """

    permission_classes = [IsAuthenticated, CanPostPurchases]

    def post(self, request, pk):
        rec, early = begin_idempotent(request, scope="purchases.debit_note.post")
        if early:
            return early

        with transaction.atomic():
            note = DebitNote.objects.select_for_update().filter(pk=pk, is_deleted=False).first()
            if not note:
                return finalize_idempotent_failure(  # type: ignore[arg-type]
                    rec, error="NOT_FOUND", message="Debit note not found.", http_status=404
                )
            if note.status == "posted":
                response = Response(DebitNoteSerializer(note).data, status=status.HTTP_200_OK)
                finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
                return response

            try:
                je = post_debit_note_journal(debit_note=note, user=request.user)
            except ValueError as exc:
                return finalize_idempotent_failure(  # type: ignore[arg-type]
                    rec,
                    error="POST_VALIDATION_ERROR",
                    message=str(exc),
                    http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )

            note.journal_entry = je
            note.mark_posted(user=request.user)

        response = Response(DebitNoteSerializer(note).data, status=status.HTTP_200_OK)
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response


# ── Supplier Refunds ──────────────────────────────────────────────────────────

class SupplierRefundChoicesAPI(APIView):
    """
    GET /purchases/supplier-refunds/choices/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        import re as _re
        last = SupplierRefund.objects.order_by("-created_at").values_list("refund_number", flat=True).first()
        next_number = "SRF-0001"
        if last:
            m = _re.search(r"(\d+)$", last)
            if m:
                next_number = last[: last.rfind(m.group(1))] + str(int(m.group(1)) + 1).zfill(len(m.group(1)))
            else:
                next_number = last + "-1"
        return Response({"next_number": next_number})


class SupplierRefundPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 200


def _get_supplier_refund(pk):
    try:
        return SupplierRefund.objects.get(pk=pk, is_deleted=False)
    except SupplierRefund.DoesNotExist:
        return None


class SupplierRefundListCreateAPI(APIView):
    """
    GET  /purchases/supplier-refunds/
    POST /purchases/supplier-refunds/
    """

    permission_classes = [IsAuthenticated, CanPostPurchases]
    pagination_class = SupplierRefundPagination

    def get(self, request):
        qs = SupplierRefund.objects.filter(is_deleted=False).select_related("supplier", "paid_through")
        search = request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(refund_number__icontains=search)
                | Q(supplier__company_name__icontains=search)
                | Q(description__icontains=search)
            )
        supplier_id = request.query_params.get("supplier")
        if supplier_id:
            qs = qs.filter(supplier_id=supplier_id)
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request)
        return paginator.get_paginated_response(SupplierRefundSerializer(page, many=True).data)

    def post(self, request):
        rec, early = begin_idempotent(request, scope="purchases.supplier_refund.create")
        if early:
            return early

        allocations = request.data.get("allocations", [])

        serializer = SupplierRefundSerializer(data={k: v for k, v in request.data.items() if k != "allocations"})
        serializer.is_valid(raise_exception=True)

        try:
            with transaction.atomic():
                refund = serializer.save(creator=request.user)
                apply_supplier_refund_allocations(refund, allocations, user=request.user)
                from accounting.services.posting import post_supplier_refund_journal
                je = post_supplier_refund_journal(refund=refund, user=request.user)
                refund.journal_entry = je
                refund.save(update_fields=["journal_entry", "updated_at"])
                refund.refresh_from_db()
        except (ValueError, DatabaseError) as exc:
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error="VALIDATION_ERROR",
                message=str(exc),
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        response = Response(SupplierRefundSerializer(refund).data, status=status.HTTP_201_CREATED)
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response


class SupplierRefundDetailAPI(APIView):
    """
    GET/PATCH/DELETE /purchases/supplier-refunds/<uuid>/
    """

    permission_classes = [IsAuthenticated, CanPostPurchases]

    def get(self, request, pk):
        refund = _get_supplier_refund(pk)
        if not refund:
            return Response({"error": "NOT_FOUND", "message": "Supplier refund not found."}, status=404)
        return Response(SupplierRefundSerializer(refund).data)

    def patch(self, request, pk):
        rec, early = begin_idempotent(request, scope="purchases.supplier_refund.update")
        if early:
            return early

        allocations = request.data.get("allocations", None)
        try:
            with transaction.atomic():
                refund = SupplierRefund.objects.select_for_update().filter(pk=pk, is_deleted=False).first()
                if not refund:
                    return finalize_idempotent_failure(
                        rec, error="NOT_FOUND", message="Supplier refund not found.", http_status=404  # type: ignore[arg-type]
                    )
                if refund.is_posted:
                    return finalize_idempotent_failure(
                        rec,  # type: ignore[arg-type]
                        error="REFUND_POSTED",
                        message="Posted refund cannot be edited.",
                        http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    )
                serializer = SupplierRefundSerializer(refund, data=request.data, partial=True)
                serializer.is_valid(raise_exception=True)
                rollback_supplier_refund_allocations(refund)
                refund = serializer.save(updator=request.user)
                if allocations is not None:
                    apply_supplier_refund_allocations(refund, allocations, user=request.user)
                refund.refresh_from_db()
        except (ValueError, DatabaseError) as exc:
            return finalize_idempotent_failure(
                rec,  # type: ignore[arg-type]
                error="VALIDATION_ERROR",
                message=str(exc),
                http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        response = Response(SupplierRefundSerializer(refund).data)
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response

    def delete(self, request, pk):
        rec, early = begin_idempotent(request, scope="purchases.supplier_refund.delete")
        if early:
            return early

        with transaction.atomic():
            refund = SupplierRefund.objects.select_for_update().filter(pk=pk, is_deleted=False).first()
            if not refund:
                return finalize_idempotent_failure(
                    rec, error="NOT_FOUND", message="Supplier refund not found.", http_status=404  # type: ignore[arg-type]
                )
            if refund.is_posted:
                return finalize_idempotent_failure(
                    rec,  # type: ignore[arg-type]
                    error="REFUND_POSTED",
                    message="Posted refund cannot be deleted.",
                    http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            rollback_supplier_refund_allocations(refund)
            refund.is_deleted = True
            refund.save(update_fields=["is_deleted", "updated_at"])

        response = Response(status=status.HTTP_204_NO_CONTENT)
        finalize_idempotent_success(rec, response)  # type: ignore[arg-type]
        return response


class SupplierOutstandingDebitNotesAPI(APIView):
    """
    GET /purchases/supplier-refunds/outstanding-debit-notes/?supplier=<uuid>
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        supplier_id = request.query_params.get("supplier")
        if not supplier_id:
            return Response(
                {"error": "SUPPLIER_REQUIRED", "message": "supplier query param is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        notes = DebitNote.objects.filter(
            is_deleted=False,
            status="posted",
            supplier_id=supplier_id,
        ).order_by("date", "created_at")

        results = []
        for note in notes:
            balance = note.balance_amount
            if balance <= 0:
                continue
            results.append({
                "id": str(note.id),
                "debit_note_number": note.debit_note_number,
                "date": note.date,
                "total_amount": str(note.total_amount),
                "refunded_amount": str(note.refunded_amount),
                "balance_amount": str(balance),
            })
        return Response({"results": results})
