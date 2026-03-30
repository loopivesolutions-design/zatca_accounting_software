from django.core.management.base import BaseCommand
from django.utils import timezone

from sales.models import CustomerCreditNote, Invoice, ZatcaHashChainAnchor
from sales.zatca_services import verify_document_hash


class Command(BaseCommand):
    help = "Nightly-style check: recompute chained ZATCA invoice hash vs stored (and optional legacy mode)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fail-on-error",
            action="store_true",
            help="Exit with code 1 if any posted document fails hash verification.",
        )

    def handle(self, *args, **options):
        fail = bool(options.get("fail_on_error"))
        errors = 0
        checked = 0
        for Model in (Invoice, CustomerCreditNote):
            qs = Model.objects.filter(is_deleted=False, status="posted").exclude(zatca_invoice_hash="")
            for doc in qs.iterator():
                checked += 1
                result = verify_document_hash(doc)
                if not result.get("is_valid"):
                    errors += 1
                    self.stdout.write(
                        self.style.WARNING(
                            f"{Model.__name__} {doc.pk}: expected={result.get('computed_hash')} "
                            f"stored={result.get('stored_hash')}"
                        )
                    )
        self.stdout.write(f"Checked: {checked}, mismatches: {errors}")

        tip_hash = ""
        latest_row = None  # (posted_at, hash)
        for Model in (Invoice, CustomerCreditNote):
            row = (
                Model.objects.filter(is_deleted=False, status="posted")
                .exclude(zatca_invoice_hash="")
                .order_by("-posted_at")
                .values_list("posted_at", "zatca_invoice_hash")
                .first()
            )
            if not row or not row[1]:
                continue
            posted_at, h = row
            if latest_row is None or (posted_at and latest_row[0] and posted_at > latest_row[0]):
                latest_row = (posted_at, h)
        if latest_row:
            tip_hash = latest_row[1]

        ok = errors == 0
        anchor = ZatcaHashChainAnchor.get_solo()
        anchor.chain_integrity_ok = ok
        anchor.verified_at = timezone.now()
        anchor.last_verified_hash = (tip_hash or "")[:128]
        anchor.last_run_summary = f"checked={checked} mismatches={errors}"
        anchor.save(
            update_fields=[
                "chain_integrity_ok",
                "verified_at",
                "last_verified_hash",
                "last_run_summary",
                "updated_at",
            ]
        )

        if errors and fail:
            raise SystemExit(1)
        if errors == 0:
            self.stdout.write(self.style.SUCCESS("ZATCA hash chain verification passed."))
