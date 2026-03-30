import os
from datetime import datetime, timezone
from pathlib import Path

from django.core.management.base import BaseCommand

from sales.models import ZatcaCertificate, ZatcaControlSequence


def _looks_like_cert_chain(pem_text: str) -> bool:
    return pem_text.count("BEGIN CERTIFICATE") >= 2


class Command(BaseCommand):
    help = "Validate ZATCA go-live readiness controls."

    def add_arguments(self, parser):
        parser.add_argument("--strict", action="store_true", help="Fail with non-zero exit when any check fails.")
        parser.add_argument("--max-clock-skew-seconds", type=int, default=300)

    def handle(self, *args, **options):
        strict = bool(options.get("strict"))
        max_skew = int(options.get("max_clock_skew_seconds") or 300)

        errors: list[str] = []
        warnings: list[str] = []

        cert = ZatcaCertificate.objects.filter(is_deleted=False, is_active=True).order_by("-created_at").first()
        if not cert:
            errors.append("No active ZATCA certificate configured.")
        else:
            if not cert.private_key_path:
                errors.append("Active certificate missing private_key_path.")
            else:
                key_path = Path(cert.private_key_path)
                if not key_path.exists():
                    errors.append(f"Private key path does not exist: {key_path}")
                elif not os.access(key_path, os.R_OK):
                    errors.append(f"Private key path is not readable: {key_path}")
            if not cert.certificate_pem.strip():
                errors.append("Active certificate has empty certificate_pem.")
            elif not _looks_like_cert_chain(cert.certificate_pem):
                errors.append("Active certificate_pem does not include full chain (leaf + intermediate).")

        invoice_xsd = (os.getenv("ZATCA_UBL_INVOICE_XSD_PATH", "") or "").strip()
        credit_xsd = (os.getenv("ZATCA_UBL_CREDIT_NOTE_XSD_PATH", "") or "").strip()
        profile_xsds = [p.strip() for p in (os.getenv("ZATCA_PROFILE_XSD_PATHS", "") or "").split(",") if p.strip()]
        for label, xsd in [("invoice", invoice_xsd), ("credit_note", credit_xsd)]:
            if not xsd:
                errors.append(f"Missing ZATCA {label} XSD path env.")
            elif not Path(xsd).exists():
                errors.append(f"Configured ZATCA {label} XSD path not found: {xsd}")
        if not profile_xsds:
            warnings.append("ZATCA_PROFILE_XSD_PATHS is empty.")
        for path in profile_xsds:
            if not Path(path).exists():
                errors.append(f"Configured profile XSD path not found: {path}")

        strict_mode = str(os.getenv("ZATCA_STRICT_PROFILE_MODE", "false")).strip().lower() in {"1", "true", "yes", "on"}
        require_policy = str(os.getenv("ZATCA_REQUIRE_SIGNATURE_POLICY", "false")).strip().lower() in {"1", "true", "yes", "on"}
        if strict_mode and require_policy:
            for key in ("ZATCA_XADES_POLICY_ID", "ZATCA_XADES_POLICY_HASH", "ZATCA_XADES_POLICY_HASH_ALGO"):
                if not (os.getenv(key, "") or "").strip():
                    errors.append(f"Strict mode requires {key}.")

        icv = ZatcaControlSequence.objects.filter(is_deleted=False, scope="invoice_icv").first()
        if not icv:
            errors.append("ZATCA ICV sequence not initialized (scope=invoice_icv).")
        elif int(icv.next_value or 0) <= 1:
            warnings.append("ICV next_value is at default/low value; verify bootstrap above historical sequence.")

        now = datetime.now(timezone.utc)
        skew = abs((datetime.utcnow().replace(tzinfo=timezone.utc) - now).total_seconds())
        if skew > max_skew:
            errors.append(f"System time skew appears too high: {skew:.1f}s > {max_skew}s")

        for w in warnings:
            self.stdout.write(self.style.WARNING(f"WARNING: {w}"))
        for err in errors:
            self.stdout.write(self.style.ERROR(f"ERROR: {err}"))

        if errors:
            msg = f"ZATCA readiness checks failed ({len(errors)} error(s))."
            if strict:
                raise SystemExit(msg)
            self.stdout.write(self.style.WARNING(msg))
            return

        self.stdout.write(self.style.SUCCESS("ZATCA readiness checks passed."))

