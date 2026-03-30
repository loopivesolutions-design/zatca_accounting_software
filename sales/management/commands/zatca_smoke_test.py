import os
import tempfile
import uuid

from django.core.management.base import BaseCommand, CommandError

from sales.models import CustomerCreditNote, Invoice
from sales.zatca_services import ZatcaValidationError, prepare_zatca_artifacts, validate_zatca_document


class Command(BaseCommand):
    help = "Generate signed XML for one document, run local ZATCA precheck, and optionally delete temp XML file."

    def add_arguments(self, parser):
        parser.add_argument("--invoice-id", type=str, help="Invoice UUID to smoke test.")
        parser.add_argument("--credit-note-id", type=str, help="Credit note UUID to smoke test.")
        parser.add_argument(
            "--keep-file",
            action="store_true",
            help="Keep generated signed XML file. Default behavior deletes file after successful test.",
        )

    def handle(self, *args, **options):
        invoice_id = (options.get("invoice_id") or "").strip()
        credit_note_id = (options.get("credit_note_id") or "").strip()
        keep_file = bool(options.get("keep_file"))

        def _normalize_uuid(value: str, field_name: str) -> str:
            if not value:
                return ""
            try:
                return str(uuid.UUID(value))
            except Exception:
                raise CommandError(
                    f"{field_name} must be a valid UUID. "
                    f"You passed: {value!r}. "
                    f"Example usage: python manage.py zatca_smoke_test --invoice-id 8409043d-ff23-494b-9f1d-95aa36cf2a2b"
                )

        invoice_id = _normalize_uuid(invoice_id, "--invoice-id")
        credit_note_id = _normalize_uuid(credit_note_id, "--credit-note-id")

        if invoice_id and credit_note_id:
            raise CommandError("Provide either --invoice-id or --credit-note-id, not both.")

        doc = None
        is_credit_note = False
        if invoice_id:
            doc = Invoice.objects.filter(pk=invoice_id, is_deleted=False).first()
            if not doc:
                raise CommandError("Invoice not found.")
        elif credit_note_id:
            doc = CustomerCreditNote.objects.filter(pk=credit_note_id, is_deleted=False).first()
            is_credit_note = True
            if not doc:
                raise CommandError("Credit note not found.")
        else:
            doc = Invoice.objects.filter(is_deleted=False).order_by("-created_at").first()
            if doc:
                self.stdout.write(self.style.WARNING(f"No id provided; using latest invoice {doc.id}."))
            else:
                doc = CustomerCreditNote.objects.filter(is_deleted=False).order_by("-created_at").first()
                is_credit_note = True
                if doc:
                    self.stdout.write(self.style.WARNING(f"No id provided; using latest credit note {doc.id}."))
            if not doc:
                raise CommandError("No invoice or credit note found to test.")

        signed_xml_path = ""
        try:
            prepare_zatca_artifacts(doc, is_credit_note=is_credit_note)
            validate_zatca_document(doc)

            signed_xml = getattr(doc, "zatca_signed_xml", "") or ""
            if not signed_xml:
                raise CommandError("Smoke test failed: signed XML was not generated.")

            tmp = tempfile.NamedTemporaryFile(prefix="zatca-signed-", suffix=".xml", delete=False)
            tmp.write(signed_xml.encode("utf-8"))
            tmp.flush()
            tmp.close()
            signed_xml_path = tmp.name

            self.stdout.write(self.style.SUCCESS("ZATCA smoke test passed."))
            self.stdout.write(f"Document type: {'credit_note' if is_credit_note else 'invoice'}")
            self.stdout.write(f"Document id: {doc.id}")
            self.stdout.write(f"Signed XML temp file: {signed_xml_path}")

            if keep_file:
                self.stdout.write(self.style.WARNING("--keep-file enabled: temp XML file retained."))
            else:
                os.remove(signed_xml_path)
                self.stdout.write(self.style.SUCCESS("Temp XML file deleted after successful test."))
        except ZatcaValidationError as exc:
            raise CommandError(f"ZATCA validation failed: {exc.errors}")
        except Exception as exc:
            raise CommandError(f"Smoke test failed: {exc}")
