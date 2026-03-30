import json
import os
import zipfile
from pathlib import Path

from django.core.management.base import BaseCommand
from django.utils import timezone

from sales.models import CustomerCreditNote, Invoice, ZatcaEvidenceBundle


def _safe_json_loads(raw: str):
    try:
        return json.loads(raw) if raw else {}
    except Exception:
        return {"_raw": raw or ""}


class Command(BaseCommand):
    help = "Export an auditor-ready ZATCA evidence package to disk (folder + optional zip)."

    def add_arguments(self, parser):
        parser.add_argument("--document-type", required=True, choices=["invoice", "credit_note"])
        parser.add_argument("--id", required=True, help="Document UUID.")
        parser.add_argument("--out-dir", required=True, help="Output base directory.")
        parser.add_argument("--zip", action="store_true", help="Also create a zip archive.")

    def handle(self, *args, **options):
        doc_type = options["document_type"]
        doc_id = options["id"]
        out_dir = Path(options["out_dir"]).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        if doc_type == "invoice":
            doc = Invoice.objects.filter(pk=doc_id, is_deleted=False).select_related("zatca_certificate").first()
        else:
            doc = CustomerCreditNote.objects.filter(pk=doc_id, is_deleted=False).select_related("zatca_certificate").first()
        if not doc:
            raise SystemExit(f"{doc_type} not found: {doc_id}")

        folder = out_dir / f"{doc_type}_{doc.id}"
        folder.mkdir(parents=True, exist_ok=True)

        # Best-effort: latest evidence bundle for this document
        evidence = (
            ZatcaEvidenceBundle.objects.filter(is_deleted=False, document_type=doc_type, document_id=doc.id)
            .order_by("-created_at")
            .first()
        )

        original_payload = _safe_json_loads(getattr(evidence, "request_payload", "") or "")
        response_payload = _safe_json_loads(getattr(evidence, "response_payload", "") or "")

        timestamps = {
            "generated_at": timezone.now().isoformat(),
            "document_created_at": getattr(doc, "created_at", None).isoformat() if getattr(doc, "created_at", None) else None,
            "document_posted_at": getattr(doc, "posted_at", None).isoformat() if getattr(doc, "posted_at", None) else None,
            "zatca_submitted_at": getattr(doc, "zatca_submitted_at", None).isoformat() if getattr(doc, "zatca_submitted_at", None) else None,
            "zatca_cleared_at": getattr(doc, "zatca_cleared_at", None).isoformat() if getattr(doc, "zatca_cleared_at", None) else None,
            "evidence_created_at": getattr(evidence, "created_at", None).isoformat() if evidence and getattr(evidence, "created_at", None) else None,
        }

        cert = getattr(doc, "zatca_certificate", None)
        cert_meta = {
            "id": str(getattr(cert, "id", "")) if cert else None,
            "name": getattr(cert, "name", "") if cert else "",
            "activated_at": getattr(cert, "activated_at", None).isoformat() if cert and cert.activated_at else None,
            "revoked_at": getattr(cert, "revoked_at", None).isoformat() if cert and cert.revoked_at else None,
        }

        # Required files auditors ask for
        (folder / "original_payload.json").write_text(json.dumps(original_payload, indent=2, ensure_ascii=False), encoding="utf-8")
        (folder / "canonical_xml.xml").write_text(getattr(doc, "zatca_canonical_xml", "") or "", encoding="utf-8")
        (folder / "signed_xml.xml").write_text(getattr(doc, "zatca_signed_xml", "") or "", encoding="utf-8")
        (folder / "hash.txt").write_text(getattr(doc, "zatca_invoice_hash", "") or "", encoding="utf-8")
        (folder / "qr.txt").write_text(getattr(doc, "qr_code_text", "") or "", encoding="utf-8")
        (folder / "zatca_response.json").write_text(json.dumps(response_payload, indent=2, ensure_ascii=False), encoding="utf-8")
        (folder / "timestamps.json").write_text(json.dumps(timestamps, indent=2, ensure_ascii=False), encoding="utf-8")

        # Extra artifacts (helpful in disputes)
        (folder / "unsigned_xml.xml").write_text(getattr(doc, "zatca_xml", "") or "", encoding="utf-8")
        (folder / "signature_value.txt").write_text(getattr(doc, "zatca_signature_value", "") or "", encoding="utf-8")
        (folder / "certificate.json").write_text(json.dumps(cert_meta, indent=2, ensure_ascii=False), encoding="utf-8")

        if options["zip"]:
            zip_path = out_dir / f"{doc_type}_{doc.id}.zip"
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for p in folder.rglob("*"):
                    if p.is_file():
                        zf.write(p, arcname=str(p.relative_to(folder)))
            self.stdout.write(self.style.SUCCESS(f"Zip created: {zip_path}"))

        self.stdout.write(self.style.SUCCESS(f"Evidence package exported to: {folder}"))

