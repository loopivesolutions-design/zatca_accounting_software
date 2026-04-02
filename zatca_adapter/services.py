"""
ZATCA adapter services.

This module must remain accounting-agnostic:
- No imports from `accounting.*`
- No period lock checks here (enforced by callers)
"""

import base64
import hashlib
import json
import os
import re
import subprocess
import uuid
from datetime import timezone as dt_timezone
from decimal import Decimal
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError
from xml.sax.saxutils import escape

from django.db import transaction
from django.utils import timezone

from main.models import CompanySettings
from sales.models import (
    CustomerCreditNote,
    Invoice,
    ZatcaCertificate,
    ZatcaControlSequence,
    ZatcaEvidenceBundle,
    ZatcaSubmissionStatusLog,
)
from zatca_adapter.state_machine import (
    TERMINAL_ZATCA_SUBMISSION_STATUSES,
    assert_zatca_submission_transition,
    normalize_zatca_submission_status,
)
from zatca_adapter.zatca_actor_context import get_current_zatca_actor
from zatca_adapter.validators import ZATCAValidator


class ZatcaValidationError(ValueError):
    def __init__(self, errors: list[dict]):
        super().__init__("ZATCA validation failed.")
        self.errors = errors


class ZatcaSubmissionTransportError(ValueError):
    def __init__(
        self,
        *,
        message: str,
        retryable: bool,
        http_status: int | None = None,
        parsed_body: dict | None = None,
        raw_body: str = "",
        response_headers: dict | None = None,
    ):
        super().__init__(message)
        self.retryable = retryable
        self.http_status = http_status
        self.parsed_body = parsed_body or {}
        self.raw_body = raw_body
        self.response_headers = response_headers or {}


def _hash_chain_legacy_mode() -> bool:
    """When true, accept legacy invoice hash = sha256(canonical_xml) without previous-hash chaining."""
    return _to_bool(os.getenv("ZATCA_HASH_CHAIN_LEGACY", "false"))


def _previous_hash_digest_bytes(previous_hash: str) -> bytes:
    """
    Normalize previous invoice hash for chaining.
    64-char hex => raw 32 bytes; empty => empty bytes; otherwise UTF-8 of string.
    """
    s = (previous_hash or "").strip()
    if not s:
        return b""
    if re.fullmatch(r"[A-Fa-f0-9]{64}", s):
        return bytes.fromhex(s)
    return s.encode("utf-8")


def compute_zatca_chain_invoice_hash(*, previous_hash: str, canonical_xml: str) -> str:
    """
    Tamper-evident chain: SHA256(previous_hash_digest || UTF-8(canonical_xml)).
    Genesis (no previous): SHA256(b"" || UTF-8(canonical_xml)).
    """
    prev_b = _previous_hash_digest_bytes(previous_hash)
    body_b = (canonical_xml or "").encode("utf-8")
    return hashlib.sha256(prev_b + body_b).hexdigest()


def apply_document_zatca_submission_status(document, new_status: str, *, actor=None) -> None:
    """Apply a legal ZATCA submission status transition; mutates document field only."""
    old = normalize_zatca_submission_status(getattr(document, "zatca_submission_status", "") or "not_submitted")
    new = normalize_zatca_submission_status(new_status)
    assert_zatca_submission_transition(old_status=old, new_status=new)
    effective_actor = actor if actor is not None else get_current_zatca_actor()
    if old != new:
        doc_type = "credit_note" if hasattr(document, "credit_note_number") else "invoice"
        ZatcaSubmissionStatusLog.objects.create(
            document_type=doc_type,
            document_id=document.id,
            from_status=old,
            to_status=new,
            actor=effective_actor if getattr(effective_actor, "pk", None) else None,
        )
    document.zatca_submission_status = new


def _to_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _get_signature_policy_config() -> dict[str, str | bool]:
    """
    Optional XAdES SignaturePolicyIdentifier configuration.
    Keep disabled by default for compatibility, enable when strict validators require it.
    """
    policy_id = os.getenv("ZATCA_XADES_POLICY_ID", "").strip()
    policy_name = os.getenv("ZATCA_XADES_POLICY_NAME", "").strip()
    policy_hash = os.getenv("ZATCA_XADES_POLICY_HASH", "").strip()
    policy_hash_algo = os.getenv(
        "ZATCA_XADES_POLICY_HASH_ALGO",
        "http://www.w3.org/2001/04/xmlenc#sha256",
    ).strip() or "http://www.w3.org/2001/04/xmlenc#sha256"
    require_policy = _to_bool(os.getenv("ZATCA_REQUIRE_SIGNATURE_POLICY", "false"))
    enabled = bool(policy_id and policy_hash)
    return {
        "id": policy_id,
        "name": policy_name,
        "hash": policy_hash,
        "hash_algo": policy_hash_algo,
        "enabled": enabled,
        "required": require_policy,
    }


def _get_signing_certificate_mode() -> str:
    """
    XAdES signing certificate block mode.
    Allowed: v1 | v2 | both. Defaults to both for interoperability.
    """
    mode = os.getenv("ZATCA_XADES_SIGNING_CERT_MODE", "both").strip().lower()
    if mode not in {"v1", "v2", "both"}:
        return "both"
    return mode


def _require_keyinfo_reference() -> bool:
    """
    Enforce KeyInfo reference in SignedInfo for validators that require it.
    """
    return _to_bool(os.getenv("ZATCA_REQUIRE_KEYINFO_REFERENCE", "false"))


def _strict_profile_mode() -> bool:
    """
    Enforce stricter ZATCA/XAdES profile checks.
    """
    return _to_bool(os.getenv("ZATCA_STRICT_PROFILE_MODE", "false"))


def _split_pem_chain(pem_bundle: str) -> list[str]:
    """
    Split a PEM bundle into individual certificate PEM blocks.
    """
    text = (pem_bundle or "").strip()
    if not text:
        return []
    blocks: list[str] = []
    marker_begin = "-----BEGIN CERTIFICATE-----"
    marker_end = "-----END CERTIFICATE-----"
    start = 0
    while True:
        b = text.find(marker_begin, start)
        if b < 0:
            break
        e = text.find(marker_end, b)
        if e < 0:
            break
        e += len(marker_end)
        block = text[b:e].strip()
        if block:
            blocks.append(block + "\n")
        start = e
    return blocks


def _assert_minimum_cert_chain(cert_chain: list[str]) -> None:
    """
    Enforce ZATCA certificate chain minimum: leaf + intermediate.
    """
    if len(cert_chain) < 2:
        raise ValueError("Certificate chain is incomplete. ZATCA requires at least leaf + intermediate certificates.")


def _cert_fingerprint_sha256_from_pem(pem_text: str) -> str:
    try:
        from cryptography import x509  # type: ignore
        from cryptography.hazmat.primitives import hashes, serialization  # type: ignore
    except Exception as exc:
        raise ValueError(f"Certificate parsing dependencies missing: {exc}")
    cert_obj = x509.load_pem_x509_certificate((pem_text or "").encode("utf-8"))
    der = cert_obj.public_bytes(encoding=serialization.Encoding.DER)
    h = hashes.Hash(hashes.SHA256())
    h.update(der)
    return h.finalize().hex()


def validate_xml_against_xsd(xml: str, *, xsd_paths: list[str]) -> None:
    """
    Validate XML against one or more XSDs.
    Raises ValueError with a readable message if invalid.
    """
    try:
        from lxml import etree  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise ValueError(f"XML validation requires lxml. Install dependency. {exc}")

    if not xml:
        raise ValueError("XML is empty.")
    if not xsd_paths:
        raise ValueError("No XSD paths configured.")

    doc = etree.fromstring(xml.encode("utf-8"))
    errors: list[str] = []

    for xsd_path in xsd_paths:
        xsd_path = (xsd_path or "").strip()
        if not xsd_path:
            continue
        if not os.path.exists(xsd_path):
            errors.append(f"XSD not found: {xsd_path}")
            continue

        try:
            schema_doc = etree.parse(xsd_path)  # includes/imports resolve relative to this path
            schema = etree.XMLSchema(schema_doc)
            if not schema.validate(doc):
                for e in schema.error_log[:25]:
                    errors.append(f"{os.path.basename(xsd_path)}: line {e.line}: {e.message}")
        except Exception as exc:
            errors.append(f"{os.path.basename(xsd_path)}: schema parse/validate error: {exc}")

    if errors:
        raise ValueError("XML does not validate against XSD(s): " + " | ".join(errors))


def _encode_tlv(tag: int, value: str) -> bytes:
    raw = (value or "").encode("utf-8")
    return bytes([tag, len(raw)]) + raw


def _build_qr_tlv_base64(
    *,
    seller_name: str,
    vat_number: str,
    timestamp: str,
    total_amount: Decimal,
    vat_amount: Decimal,
    tag6_cryptographic_stamp_hex: str = "",
    tag7_ecdsa_signature_hex: str = "",
    tag8_ecdsa_public_key_der_b64: str = "",
    tag9_ecdsa_public_key_sha256_hex: str = "",
) -> str:
    """
    ZATCA Phase 1 TLV order (UTF-8 values, tag-length-value):
    1 seller name, 2 VAT number, 3 timestamp, 4 invoice total, 5 VAT total,
    6 hash of the invoice XML (ZATCA QR guidance: “Hash of XML Invoice”).

    This implementation uses the Phase-2-style **chained** digest
    SHA256(previous_hash_bytes ‖ UTF-8(canonical_xml)) as a 64-char hex string,
    where canonical_xml excludes the QR AdditionalDocumentReference so the TLV
    can embed tag 6 without circularity. Tags 7–9 (ECDSA fields in newer QR specs)
    are not embedded here because they would require either signing after final QR
    bytes or excluding QR from the signed XML scope end-to-end.
    """
    parts = [
        _encode_tlv(1, seller_name),
        _encode_tlv(2, vat_number),
        _encode_tlv(3, timestamp),
        _encode_tlv(4, f"{total_amount:.2f}"),
        _encode_tlv(5, f"{vat_amount:.2f}"),
    ]
    t6 = (tag6_cryptographic_stamp_hex or "").strip()
    if t6:
        parts.append(_encode_tlv(6, t6.lower()))
    for tag, val in (
        (7, (tag7_ecdsa_signature_hex or "").strip()),
        (8, (tag8_ecdsa_public_key_der_b64 or "").strip()),
        (9, (tag9_ecdsa_public_key_sha256_hex or "").strip()),
    ):
        if val:
            parts.append(_encode_tlv(tag, val if tag != 7 else val.lower()))
    return base64.b64encode(b"".join(parts)).decode("utf-8")


def _decode_tlv_base64(qr_b64: str) -> dict[int, str]:
    raw = base64.b64decode((qr_b64 or "").encode("utf-8"))
    i = 0
    out: dict[int, str] = {}
    while i < len(raw):
        if i + 2 > len(raw):
            raise ValueError("Invalid TLV encoding (truncated header).")
        tag = raw[i]
        length = raw[i + 1]
        i += 2
        if i + length > len(raw):
            raise ValueError("Invalid TLV encoding (truncated value).")
        value = raw[i : i + length].decode("utf-8")
        out[tag] = value
        i += length
    return out


def _latest_hash() -> str:
    inv = Invoice.objects.filter(status="posted", is_deleted=False).exclude(zatca_invoice_hash="").order_by("-posted_at", "-created_at").first()
    crn = CustomerCreditNote.objects.filter(status="posted", is_deleted=False).exclude(zatca_invoice_hash="").order_by("-posted_at", "-created_at").first()
    candidates = [x for x in [inv, crn] if x and x.posted_at]
    if not candidates:
        return ""
    latest = sorted(candidates, key=lambda x: x.posted_at, reverse=True)[0]
    return latest.zatca_invoice_hash or ""


def _resolve_profile_id(*, is_credit_note: bool) -> str:
    specific = (
        os.getenv("ZATCA_PROFILE_ID_CREDIT_NOTE", "").strip()
        if is_credit_note
        else os.getenv("ZATCA_PROFILE_ID_INVOICE", "").strip()
    )
    generic = os.getenv("ZATCA_PROFILE_ID", "").strip()
    chosen = specific or generic or "reporting:1.0"
    if _strict_profile_mode() and not (specific or generic):
        raise ValueError(
            "Strict mode requires explicit profile id env vars: "
            "ZATCA_PROFILE_ID_INVOICE / ZATCA_PROFILE_ID_CREDIT_NOTE (or ZATCA_PROFILE_ID)."
        )
    return chosen


def _resolve_invoice_type_name(*, profile_id: str, is_credit_note: bool) -> str:
    # Standard/Clearance => 0100000, Simplified/Reporting => 0200000
    pid = (profile_id or "").strip().lower()
    if pid == "reporting:1.0":
        return "0200000"
    return "0100000"


def _resolve_credit_note_reference(document) -> str:
    # Prefer explicit attribute if caller/model supplies it.
    explicit = str(getattr(document, "original_invoice_number", "") or "").strip()
    if explicit:
        return explicit
    # Fallback: try extracting an invoice-like token from note text.
    note = str(getattr(document, "note", "") or "")
    m = re.search(r"\b([A-Za-z]{2,6}[-_/]?\d{2,})\b", note)
    return (m.group(1) if m else "").strip()


def _next_icv_value() -> str:
    """
    Generate next numeric Internal Control Value (ICV) using a persistent,
    transaction-safe sequence.
    """
    scope = "invoice_icv"
    with transaction.atomic():
        seq = (
            ZatcaControlSequence.objects.select_for_update()
            .filter(scope=scope, is_deleted=False)
            .first()
        )
        if not seq:
            seq = ZatcaControlSequence.objects.create(scope=scope, next_value=2)
            return "1"
        current = int(seq.next_value or 1)
        seq.next_value = current + 1
        seq.save(update_fields=["next_value", "updated_at"])
        return str(current)


def _derive_icv_value(document, *, is_credit_note: bool) -> str:
    """
    Derive numeric ICV from document number when available; fallback to monotonic sequence.
    """
    raw_doc_no = (
        str(getattr(document, "credit_note_number", "") or "")
        if is_credit_note
        else str(getattr(document, "invoice_number", "") or "")
    )
    digits = "".join(ch for ch in raw_doc_no if ch.isdigit())
    if digits:
        return digits
    return _next_icv_value()


def _build_invoice_xml(document, is_credit_note: bool) -> str:
    from lxml import etree  # type: ignore

    def fmt_amount(value) -> str:
        val = value if isinstance(value, Decimal) else Decimal(str(value or "0"))
        return f"{val.quantize(Decimal('0.01'))}"

    company = CompanySettings.objects.first()
    seller_name = company.company_name if company and company.company_name else ""
    seller_vat = company.vat_registration_number if company and company.vat_registration_number else ""
    customer_name = getattr(getattr(document, "customer", None), "company_name", "") or ""

    ksa_tz = timezone.get_fixed_timezone(180)
    issue_date_obj = getattr(document, "date", None) or timezone.localdate()
    issue_date = issue_date_obj.isoformat()
    issue_time_src = getattr(document, "posted_at", None) or getattr(document, "created_at", None) or timezone.now()
    issue_time_ksa = timezone.localtime(issue_time_src, ksa_tz)
    ts = issue_time_ksa.strftime("%H:%M:%S")

    doc_tag = "CreditNote" if is_credit_note else "Invoice"
    line_tag = "CreditNoteLine" if is_credit_note else "InvoiceLine"
    qty_tag = "CreditedQuantity" if is_credit_note else "InvoicedQuantity"
    invoice_type_code = "381" if is_credit_note else "388"  # ZATCA/UBL code list
    doc_id = getattr(document, "credit_note_number", "") if is_credit_note else getattr(document, "invoice_number", "")

    ns_ubl = f"urn:oasis:names:specification:ubl:schema:xsd:{doc_tag}-2"
    ns_cac = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
    ns_cbc = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
    ns_ext = "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"
    nsmap = {None: ns_ubl, "cac": ns_cac, "cbc": ns_cbc, "ext": ns_ext}
    currency = (os.getenv("ZATCA_DOCUMENT_CURRENCY", "SAR") or "SAR").strip().upper()
    profile_id = _resolve_profile_id(is_credit_note=is_credit_note)

    root = etree.Element(f"{{{ns_ubl}}}{doc_tag}", nsmap=nsmap)

    # UBL strict signature container: ext:UBLExtensions/ext:UBLExtension/ext:ExtensionContent
    # ds:Signature is placed here during signing.
    ubl_extensions = etree.SubElement(root, f"{{{ns_ext}}}UBLExtensions")
    ubl_extension = etree.SubElement(ubl_extensions, f"{{{ns_ext}}}UBLExtension")
    etree.SubElement(ubl_extension, f"{{{ns_ext}}}ExtensionContent")

    etree.SubElement(root, f"{{{ns_cbc}}}CustomizationID").text = "urn:zatca:sa:einvoicing:1.0"
    etree.SubElement(root, f"{{{ns_cbc}}}ProfileID").text = profile_id
    etree.SubElement(root, f"{{{ns_cbc}}}ID").text = str(doc_id or "")
    etree.SubElement(root, f"{{{ns_cbc}}}IssueDate").text = issue_date
    etree.SubElement(root, f"{{{ns_cbc}}}IssueTime").text = ts
    invoice_type_name = _resolve_invoice_type_name(profile_id=profile_id, is_credit_note=is_credit_note)
    etree.SubElement(root, f"{{{ns_cbc}}}InvoiceTypeCode", name=invoice_type_name).text = invoice_type_code
    etree.SubElement(root, f"{{{ns_cbc}}}DocumentCurrencyCode").text = currency
    etree.SubElement(root, f"{{{ns_cbc}}}TaxCurrencyCode").text = currency
    etree.SubElement(root, f"{{{ns_cbc}}}UUID").text = str(getattr(document, "zatca_uuid", "") or "")

    # ZATCA business references
    pih_ref = etree.SubElement(root, f"{{{ns_cac}}}AdditionalDocumentReference")
    etree.SubElement(pih_ref, f"{{{ns_cbc}}}ID").text = "PIH"
    pih_attachment = etree.SubElement(pih_ref, f"{{{ns_cac}}}Attachment")
    pih_value = str(getattr(document, "zatca_previous_hash", "") or "")
    # If previous hash is a 64-char hex digest, convert to base64 for PIH payload compatibility.
    if re.fullmatch(r"[A-Fa-f0-9]{64}", pih_value):
        pih_value = base64.b64encode(bytes.fromhex(pih_value)).decode("utf-8")
    etree.SubElement(
        pih_attachment,
        f"{{{ns_cbc}}}EmbeddedDocumentBinaryObject",
        mimeCode="text/plain",
    ).text = pih_value

    icv_ref = etree.SubElement(root, f"{{{ns_cac}}}AdditionalDocumentReference")
    etree.SubElement(icv_ref, f"{{{ns_cbc}}}ID").text = "ICV"
    etree.SubElement(icv_ref, f"{{{ns_cbc}}}UUID").text = _derive_icv_value(document, is_credit_note=is_credit_note)

    qr_ref = etree.SubElement(root, f"{{{ns_cac}}}AdditionalDocumentReference")
    etree.SubElement(qr_ref, f"{{{ns_cbc}}}ID").text = "QR"
    qr_attachment = etree.SubElement(qr_ref, f"{{{ns_cac}}}Attachment")
    etree.SubElement(
        qr_attachment,
        f"{{{ns_cbc}}}EmbeddedDocumentBinaryObject",
        mimeCode="text/plain",
    ).text = str(getattr(document, "qr_code_text", "") or "")

    # UBL signature placeholder must be present before signing. The final XML must not be
    # mutated after XMLDSig is generated.
    ubl_sig = etree.SubElement(root, f"{{{ns_cac}}}Signature")
    etree.SubElement(ubl_sig, f"{{{ns_cbc}}}ID").text = "urn:oasis:names:specification:ubl:signature:Invoice"
    etree.SubElement(ubl_sig, f"{{{ns_cbc}}}SignatureMethod").text = "urn:ietf:rfc:4051#rsa-sha256"

    if is_credit_note:
        original_invoice_id = _resolve_credit_note_reference(document)
        billing_ref = etree.SubElement(root, f"{{{ns_cac}}}BillingReference")
        inv_doc_ref = etree.SubElement(billing_ref, f"{{{ns_cac}}}InvoiceDocumentReference")
        etree.SubElement(inv_doc_ref, f"{{{ns_cbc}}}ID").text = original_invoice_id

    supplier_party = etree.SubElement(root, f"{{{ns_cac}}}AccountingSupplierParty")
    sp = etree.SubElement(supplier_party, f"{{{ns_cac}}}Party")
    sp_name = etree.SubElement(etree.SubElement(sp, f"{{{ns_cac}}}PartyName"), f"{{{ns_cbc}}}Name")
    sp_name.text = seller_name
    company_country_code = (
        getattr(getattr(company, "country", None), "iso2", None)
        or os.getenv("ZATCA_COUNTRY_CODE", "SA")
    )
    sp_addr = etree.SubElement(sp, f"{{{ns_cac}}}PostalAddress")
    etree.SubElement(sp_addr, f"{{{ns_cbc}}}StreetName").text = str(getattr(company, "street_address", "") or "")
    etree.SubElement(sp_addr, f"{{{ns_cbc}}}BuildingNumber").text = str(getattr(company, "building_number", "") or "")
    etree.SubElement(sp_addr, f"{{{ns_cbc}}}CountrySubentity").text = str(getattr(company, "district", "") or "")
    etree.SubElement(sp_addr, f"{{{ns_cbc}}}CitySubdivisionName").text = str(getattr(company, "district", "") or "")
    etree.SubElement(sp_addr, f"{{{ns_cbc}}}CityName").text = str(getattr(company, "city", "") or "")
    etree.SubElement(sp_addr, f"{{{ns_cbc}}}PostalZone").text = str(getattr(company, "postal_code", "") or "")
    sp_country = etree.SubElement(sp_addr, f"{{{ns_cac}}}Country")
    etree.SubElement(sp_country, f"{{{ns_cbc}}}IdentificationCode").text = str(company_country_code or "SA")
    sp_tax = etree.SubElement(sp, f"{{{ns_cac}}}PartyTaxScheme")
    etree.SubElement(sp_tax, f"{{{ns_cbc}}}CompanyID", schemeID="VAT").text = seller_vat
    etree.SubElement(etree.SubElement(sp_tax, f"{{{ns_cac}}}TaxScheme"), f"{{{ns_cbc}}}ID").text = "VAT"
    sp_legal = etree.SubElement(sp, f"{{{ns_cac}}}PartyLegalEntity")
    etree.SubElement(sp_legal, f"{{{ns_cbc}}}RegistrationName").text = seller_name
    etree.SubElement(sp_legal, f"{{{ns_cbc}}}CompanyID").text = str(getattr(company, "cr_number", "") or seller_vat)

    customer_party = etree.SubElement(root, f"{{{ns_cac}}}AccountingCustomerParty")
    cp = etree.SubElement(customer_party, f"{{{ns_cac}}}Party")
    cp_name = etree.SubElement(etree.SubElement(cp, f"{{{ns_cac}}}PartyName"), f"{{{ns_cbc}}}Name")
    cp_name.text = customer_name
    customer = getattr(document, "customer", None)
    customer_country_code = (
        getattr(getattr(customer, "country", None), "iso2", None)
        or os.getenv("ZATCA_COUNTRY_CODE", "SA")
    )
    cp_addr = etree.SubElement(cp, f"{{{ns_cac}}}PostalAddress")
    etree.SubElement(cp_addr, f"{{{ns_cbc}}}StreetName").text = str(getattr(customer, "street_address", "") or "")
    etree.SubElement(cp_addr, f"{{{ns_cbc}}}BuildingNumber").text = str(getattr(customer, "building_number", "") or "")
    etree.SubElement(cp_addr, f"{{{ns_cbc}}}CountrySubentity").text = str(getattr(customer, "district", "") or "")
    etree.SubElement(cp_addr, f"{{{ns_cbc}}}CitySubdivisionName").text = str(getattr(customer, "district", "") or "")
    etree.SubElement(cp_addr, f"{{{ns_cbc}}}CityName").text = str(getattr(customer, "city", "") or "")
    etree.SubElement(cp_addr, f"{{{ns_cbc}}}PostalZone").text = str(getattr(customer, "postal_code", "") or "")
    cp_country = etree.SubElement(cp_addr, f"{{{ns_cac}}}Country")
    etree.SubElement(cp_country, f"{{{ns_cbc}}}IdentificationCode").text = str(customer_country_code or "SA")
    buyer_vat = str(getattr(customer, "tax_registration_number", "") or "").strip()
    if buyer_vat:
        cp_tax = etree.SubElement(cp, f"{{{ns_cac}}}PartyTaxScheme")
        etree.SubElement(cp_tax, f"{{{ns_cbc}}}CompanyID", schemeID="VAT").text = buyer_vat
        etree.SubElement(etree.SubElement(cp_tax, f"{{{ns_cac}}}TaxScheme"), f"{{{ns_cbc}}}ID").text = "VAT"
    else:
        # Simplified/B2C fallback identifier expected by some validators.
        cp_ident = etree.SubElement(cp, f"{{{ns_cac}}}PartyIdentification")
        etree.SubElement(cp_ident, f"{{{ns_cbc}}}ID").text = "0000000000"
    cp_legal = etree.SubElement(cp, f"{{{ns_cac}}}PartyLegalEntity")
    etree.SubElement(cp_legal, f"{{{ns_cbc}}}RegistrationName").text = customer_name
    if buyer_vat:
        etree.SubElement(cp_legal, f"{{{ns_cbc}}}CompanyID").text = buyer_vat

    lines = list(document.lines.filter(is_deleted=False).select_related("tax_rate"))
    tax_groups: dict[tuple[str, str], dict[str, Decimal]] = {}
    discount_total = Decimal("0")

    for i, line in enumerate(lines, start=1):
        rate = Decimal(str(line.tax_rate.rate if line.tax_rate else "0"))
        cat = (line.tax_rate.zatca_category if line.tax_rate else "O") or "O"
        taxable = Decimal(str(line.subtotal()))
        tax_amt = Decimal(str(line.tax_amount()))
        discount_total += Decimal(str(line.discount_amount() if hasattr(line, "discount_amount") else "0"))
        key = (cat, str(rate))
        bucket = tax_groups.setdefault(key, {"taxable": Decimal("0"), "tax": Decimal("0")})
        bucket["taxable"] += taxable
        bucket["tax"] += tax_amt

        line_el = etree.SubElement(root, f"{{{ns_cac}}}{line_tag}")
        etree.SubElement(line_el, f"{{{ns_cbc}}}ID").text = str(i)
        etree.SubElement(line_el, f"{{{ns_cbc}}}{qty_tag}").text = str(line.quantity)
        etree.SubElement(line_el, f"{{{ns_cbc}}}LineExtensionAmount", currencyID=currency).text = fmt_amount(taxable)

        line_tax_total = etree.SubElement(line_el, f"{{{ns_cac}}}TaxTotal")
        etree.SubElement(line_tax_total, f"{{{ns_cbc}}}TaxAmount", currencyID=currency).text = fmt_amount(tax_amt)
        line_tax_sub = etree.SubElement(line_tax_total, f"{{{ns_cac}}}TaxSubtotal")
        etree.SubElement(line_tax_sub, f"{{{ns_cbc}}}TaxableAmount", currencyID=currency).text = fmt_amount(taxable)
        etree.SubElement(line_tax_sub, f"{{{ns_cbc}}}TaxAmount", currencyID=currency).text = fmt_amount(tax_amt)
        line_tax_cat = etree.SubElement(line_tax_sub, f"{{{ns_cac}}}TaxCategory")
        etree.SubElement(line_tax_cat, f"{{{ns_cbc}}}ID").text = cat
        etree.SubElement(line_tax_cat, f"{{{ns_cbc}}}Percent").text = str(rate.quantize(Decimal("0.01")))
        etree.SubElement(etree.SubElement(line_tax_cat, f"{{{ns_cac}}}TaxScheme"), f"{{{ns_cbc}}}ID").text = "VAT"

        item = etree.SubElement(line_el, f"{{{ns_cac}}}Item")
        etree.SubElement(item, f"{{{ns_cbc}}}Name").text = str(line.description or "")
        price = etree.SubElement(line_el, f"{{{ns_cac}}}Price")
        etree.SubElement(price, f"{{{ns_cbc}}}PriceAmount", currencyID=currency).text = fmt_amount(line.unit_price)

    tax_total = etree.SubElement(root, f"{{{ns_cac}}}TaxTotal")
    etree.SubElement(tax_total, f"{{{ns_cbc}}}TaxAmount", currencyID=currency).text = fmt_amount(getattr(document, "total_vat", Decimal("0")))
    for (cat, rate), totals in tax_groups.items():
        sub = etree.SubElement(tax_total, f"{{{ns_cac}}}TaxSubtotal")
        etree.SubElement(sub, f"{{{ns_cbc}}}TaxableAmount", currencyID=currency).text = fmt_amount(totals["taxable"])
        etree.SubElement(sub, f"{{{ns_cbc}}}TaxAmount", currencyID=currency).text = fmt_amount(totals["tax"])
        tax_cat = etree.SubElement(sub, f"{{{ns_cac}}}TaxCategory")
        etree.SubElement(tax_cat, f"{{{ns_cbc}}}ID").text = cat
        etree.SubElement(tax_cat, f"{{{ns_cbc}}}Percent").text = str(Decimal(rate).quantize(Decimal("0.01")))
        etree.SubElement(etree.SubElement(tax_cat, f"{{{ns_cac}}}TaxScheme"), f"{{{ns_cbc}}}ID").text = "VAT"

    monetary = etree.SubElement(root, f"{{{ns_cac}}}LegalMonetaryTotal")
    etree.SubElement(monetary, f"{{{ns_cbc}}}LineExtensionAmount", currencyID=currency).text = fmt_amount(getattr(document, "subtotal", Decimal("0")))
    etree.SubElement(monetary, f"{{{ns_cbc}}}TaxExclusiveAmount", currencyID=currency).text = fmt_amount(getattr(document, "subtotal", Decimal("0")))
    etree.SubElement(monetary, f"{{{ns_cbc}}}TaxInclusiveAmount", currencyID=currency).text = fmt_amount(getattr(document, "total_amount", Decimal("0")))
    etree.SubElement(monetary, f"{{{ns_cbc}}}AllowanceTotalAmount", currencyID=currency).text = fmt_amount(discount_total)
    etree.SubElement(monetary, f"{{{ns_cbc}}}PayableAmount", currencyID=currency).text = fmt_amount(getattr(document, "total_amount", Decimal("0")))

    if discount_total > Decimal("0"):
        allowance = etree.SubElement(root, f"{{{ns_cac}}}AllowanceCharge")
        etree.SubElement(allowance, f"{{{ns_cbc}}}ChargeIndicator").text = "false"
        etree.SubElement(allowance, f"{{{ns_cbc}}}Amount", currencyID=currency).text = fmt_amount(discount_total)

    return etree.tostring(root, encoding="utf-8", xml_declaration=False).decode("utf-8")


def prepare_zatca_artifacts(document, *, is_credit_note: bool = False, force_sign: bool = False) -> None:
    company = CompanySettings.objects.first()
    now_ts = timezone.now()
    timestamp = now_ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    seller_name = company.company_name if company and company.company_name else ""
    seller_vat = company.vat_registration_number if company and company.vat_registration_number else ""

    document.zatca_uuid = document.zatca_uuid or str(uuid.uuid4())
    document.zatca_previous_hash = document.zatca_previous_hash or _latest_hash()
    # TLV tags 1–5 first; tag 6 (chained hash) added after hash anchor is computed.
    document.qr_code_text = _build_qr_tlv_base64(
        seller_name=seller_name,
        vat_number=seller_vat,
        timestamp=timestamp,
        total_amount=document.total_amount or Decimal("0"),
        vat_amount=document.total_vat or Decimal("0"),
        tag6_cryptographic_stamp_hex="",
    )

    unsigned_xml = _build_invoice_xml(document, is_credit_note=is_credit_note)
    canonical_xml = _canonicalize_for_zatca_hash(unsigned_xml)
    invoice_hash = compute_zatca_chain_invoice_hash(
        previous_hash=str(getattr(document, "zatca_previous_hash", "") or ""),
        canonical_xml=canonical_xml,
    )

    document.qr_code_text = _build_qr_tlv_base64(
        seller_name=seller_name,
        vat_number=seller_vat,
        timestamp=timestamp,
        total_amount=document.total_amount or Decimal("0"),
        vat_amount=document.total_vat or Decimal("0"),
        tag6_cryptographic_stamp_hex=invoice_hash,
    )
    unsigned_xml = _build_invoice_xml(document, is_credit_note=is_credit_note)
    preflight = ZATCAValidator(unsigned_xml).validate(include_signature=False)
    if not preflight["valid"]:
        raise ZatcaValidationError(
            [
                {
                    "field": e.get("xpath", ""),
                    "code": e.get("code", "ZATCA-VAL-UNKNOWN"),
                    "message": e.get("message", "Validation failed."),
                }
                for e in preflight.get("errors", [])
            ]
        )

    # ── Signing gate ────────────────────────────────────────────────────────
    # When ZATCA_SIGNING_ENABLED is False (the default), invoice posting stores
    # the unsigned artifacts and returns.  Actual signing + submission only
    # happens when the user clicks "Report to Fatoora" (a separate API call).
    try:
        from django.conf import settings as _dj_settings
        _signing_enabled = bool(getattr(_dj_settings, "ZATCA_SIGNING_ENABLED", False))
    except Exception:
        _signing_enabled = False

    if not _signing_enabled and not force_sign:
        document.zatca_xml = unsigned_xml
        document.zatca_canonical_xml = canonical_xml
        document.zatca_invoice_hash = invoice_hash
        document.zatca_signed_hash = ""
        document.zatca_signature_value = ""
        document.zatca_signed_xml = ""
        document.zatca_certificate = None
        document.zatca_submission_status = "not_submitted"
        document.zatca_submission_error = ""
        return
    # ────────────────────────────────────────────────────────────────────────

    cert = _get_active_certificate()
    if not (cert.certificate_pem or "").strip():
        raise ValueError("Active ZATCA certificate is missing certificate_pem.")
    signed_xml = _sign_xml_enveloped(unsigned_xml, key_path=cert.private_key_path, cert_pem_bundle=cert.certificate_pem)
    signature_value = _extract_xmlsig_signature_value(signed_xml)
    _verify_signed_xml_local(signed_xml, cert_pem_bundle=cert.certificate_pem)

    try:
        from django.conf import settings as dj_settings
    except Exception:  # pragma: no cover
        dj_settings = None

    if dj_settings is not None and getattr(dj_settings, "ZATCA_QR_INCLUDE_ECDSA", False):
        tag7 = tag8 = tag9 = ""
        try:
            sig_raw = base64.b64decode(signature_value) if signature_value else b""
            tag7 = sig_raw.hex()
        except Exception:
            tag7 = (signature_value or "")[:512]
        try:
            from cryptography import x509  # type: ignore
            from cryptography.hazmat.primitives import serialization  # type: ignore

            cert_obj = x509.load_pem_x509_certificate((cert.certificate_pem or "").encode("utf-8"))
            pub = cert_obj.public_key()
            spki = pub.public_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            tag8 = base64.b64encode(spki).decode("ascii")
            tag9 = hashlib.sha256(spki).hexdigest()
        except Exception:
            pass
        document.qr_code_text = _build_qr_tlv_base64(
            seller_name=seller_name,
            vat_number=seller_vat,
            timestamp=timestamp,
            total_amount=document.total_amount or Decimal("0"),
            vat_amount=document.total_vat or Decimal("0"),
            tag6_cryptographic_stamp_hex=invoice_hash,
            tag7_ecdsa_signature_hex=tag7,
            tag8_ecdsa_public_key_der_b64=tag8,
            tag9_ecdsa_public_key_sha256_hex=tag9,
        )
        unsigned_xml = _build_invoice_xml(document, is_credit_note=is_credit_note)
        canonical_check = _canonicalize_for_zatca_hash(unsigned_xml)
        if canonical_check != canonical_xml:
            raise ValueError(
                "ZATCA_QR_INCLUDE_ECDSA: canonical hash input changed after QR extension; refusing to re-sign."
            )
        signed_xml = _sign_xml_enveloped(unsigned_xml, key_path=cert.private_key_path, cert_pem_bundle=cert.certificate_pem)
        signature_value = _extract_xmlsig_signature_value(signed_xml)
        _verify_signed_xml_local(signed_xml, cert_pem_bundle=cert.certificate_pem)

    document.zatca_xml = unsigned_xml
    document.zatca_canonical_xml = canonical_xml
    document.zatca_invoice_hash = invoice_hash
    try:
        sig_bytes = base64.b64decode(signature_value) if signature_value else b""
        document.zatca_signed_hash = hashlib.sha256(sig_bytes).hexdigest()
    except Exception:
        document.zatca_signed_hash = hashlib.sha256((signature_value or "").encode("utf-8")).hexdigest()
    document.zatca_signature_value = signature_value
    document.zatca_signed_xml = signed_xml
    document.zatca_certificate = cert
    st = normalize_zatca_submission_status(getattr(document, "zatca_submission_status", "") or "not_submitted")
    if st == "not_submitted":
        apply_document_zatca_submission_status(document, "signed")
    document.zatca_submission_error = ""


def submit_to_zatca(document, *, submission_type: str, idempotency_key: str = "") -> None:
    from django.conf import settings as django_settings

    # Governance (period locks, approvals) is handled by callers.
    document.zatca_submission_type = submission_type
    document.zatca_submitted_at = timezone.now()
    document.zatca_submission_error = ""

    simulation_mode = bool(getattr(django_settings, "ZATCA_SIMULATION_MODE", False))
    st = normalize_zatca_submission_status(getattr(document, "zatca_submission_status", "") or "not_submitted")

    if simulation_mode:
        if st in ("cleared", "reported"):
            return
        if st == "not_submitted":
            raise ValueError("ZATCA artifacts must be prepared (signed) before submission.")
        if st == "signed":
            apply_document_zatca_submission_status(document, "submitted")
        elif st == "retrying":
            apply_document_zatca_submission_status(document, "submitted")
        document.zatca_submission_reference = f"SIM-{uuid.uuid4().hex[:16].upper()}"
        apply_document_zatca_submission_status(document, "cleared" if submission_type == "clearance" else "reported")
        document.zatca_cleared_at = timezone.now()
        return

    if st in ("cleared", "reported"):
        return
    if st in ("rejected", "failed_final"):
        raise ValueError("ZATCA submission is in a terminal failure state; create a correction document instead.")
    if st == "not_submitted":
        raise ValueError("ZATCA artifacts must be prepared (signed) before submission.")
    if st in ("signed", "retrying"):
        apply_document_zatca_submission_status(document, "submitted")

    validate_zatca_document(document)
    _submit_live(document, submission_type=submission_type, idempotency_key=idempotency_key)


def verify_document_hash(document) -> dict:
    source_xml = (document.zatca_xml or "").strip()
    canonical_for_hash = _canonicalize_for_zatca_hash(source_xml) if source_xml else (document.zatca_canonical_xml or "")
    expected_chain = (
        compute_zatca_chain_invoice_hash(
            previous_hash=str(getattr(document, "zatca_previous_hash", "") or ""),
            canonical_xml=canonical_for_hash,
        )
        if canonical_for_hash
        else ""
    )
    legacy_plain = hashlib.sha256(canonical_for_hash.encode("utf-8")).hexdigest() if canonical_for_hash else ""
    stored = (document.zatca_invoice_hash or "").strip().lower()
    is_valid = bool(stored) and stored == expected_chain.lower()
    if not is_valid and _hash_chain_legacy_mode() and canonical_for_hash:
        is_valid = stored == legacy_plain.lower()
    return {
        "stored_hash": document.zatca_invoice_hash or "",
        "computed_hash": expected_chain,
        "legacy_plain_hash": legacy_plain,
        "is_valid": is_valid,
    }


def validate_zatca_document(document) -> None:
    """
    Strict pre-submission validation gate.
    Raises ZatcaValidationError with structured error list.
    """
    errors: list[dict] = []

    company = CompanySettings.objects.first()
    seller_name = company.company_name if company and company.company_name else ""
    seller_vat = company.vat_registration_number if company and company.vat_registration_number else ""

    if not seller_name:
        errors.append({"field": "company_settings.company_name", "code": "MISSING_SELLER_NAME", "message": "Seller name is required."})
    if not seller_vat:
        errors.append({"field": "company_settings.vat_registration_number", "code": "MISSING_SELLER_VAT", "message": "Seller VAT number is required for ZATCA."})

    if not getattr(document, "zatca_uuid", ""):
        errors.append({"field": "zatca_uuid", "code": "MISSING_UUID", "message": "ZATCA UUID is required."})
    else:
        z_uuid = str(getattr(document, "zatca_uuid", "") or "").strip()
        if z_uuid:
            inv_dup = Invoice.objects.filter(is_deleted=False, zatca_uuid=z_uuid).exclude(pk=getattr(document, "id", None)).exists()
            crn_dup = CustomerCreditNote.objects.filter(is_deleted=False, zatca_uuid=z_uuid).exclude(pk=getattr(document, "id", None)).exists()
            if inv_dup or crn_dup:
                errors.append({"field": "zatca_uuid", "code": "DUPLICATE_UUID", "message": "ZATCA UUID must be unique across invoices and credit notes."})
    if not getattr(document, "zatca_xml", ""):
        errors.append({"field": "zatca_xml", "code": "MISSING_XML", "message": "ZATCA XML is required."})
    if not getattr(document, "zatca_signed_xml", ""):
        errors.append({"field": "zatca_signed_xml", "code": "MISSING_SIGNED_XML", "message": "Signed XML is required."})
    if not getattr(document, "zatca_signature_value", ""):
        errors.append({"field": "zatca_signature_value", "code": "MISSING_SIGNATURE", "message": "Signature value is required."})
    if not getattr(document, "zatca_certificate_id", None):
        errors.append({"field": "zatca_certificate", "code": "MISSING_CERTIFICATE", "message": "Certificate used must be recorded."})
    if not getattr(document, "zatca_invoice_hash", ""):
        errors.append({"field": "zatca_invoice_hash", "code": "MISSING_HASH", "message": "Invoice hash is required."})
    if getattr(document, "zatca_signed_xml", "") and "<ds:Signature" not in (document.zatca_signed_xml or "") and "Signature" not in (document.zatca_signed_xml or ""):
        errors.append({"field": "zatca_signed_xml", "code": "MISSING_XMLDSIG", "message": "Signed XML must include an XMLDSig Signature block."})
    if getattr(document, "zatca_signed_xml", "") and "SignedProperties" not in (document.zatca_signed_xml or ""):
        errors.append({"field": "zatca_signed_xml", "code": "MISSING_XADES", "message": "Signed XML must include XAdES SignedProperties."})
    if getattr(document, "zatca_signed_xml", ""):
        errors.extend(_validate_xades_structure(document.zatca_signed_xml))

    if getattr(document, "zatca_xml", "") and getattr(document, "zatca_invoice_hash", ""):
        try:
            hash_input_xml = _canonicalize_for_zatca_hash(document.zatca_xml or "")
            computed_chain = compute_zatca_chain_invoice_hash(
                previous_hash=str(getattr(document, "zatca_previous_hash", "") or ""),
                canonical_xml=hash_input_xml,
            )
            computed_legacy = hashlib.sha256(hash_input_xml.encode("utf-8")).hexdigest()
            stored_h = (document.zatca_invoice_hash or "").strip().lower()
            ok = stored_h == computed_chain.lower()
            if not ok and _hash_chain_legacy_mode():
                ok = stored_h == computed_legacy.lower()
            if not ok:
                errors.append(
                    {
                        "field": "zatca_invoice_hash",
                        "code": "HASH_MISMATCH",
                        "message": "Stored hash does not match chained hash (SHA256(previous||canonical)) or legacy plain canonical hash.",
                    }
                )
        except Exception as exc:
            errors.append({"field": "zatca_xml", "code": "HASH_INPUT_BUILD_FAILED", "message": f"Unable to build ZATCA hash-input XML: {exc}"})

    # Profile-level structural checks (beyond XSD)
    if getattr(document, "zatca_signed_xml", ""):
        try:
            from lxml import etree  # type: ignore

            root = etree.fromstring((document.zatca_signed_xml or "").encode("utf-8"))
            ns = {
                "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
                "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
            }
            inv_type = root.findtext(".//cbc:InvoiceTypeCode", namespaces=ns)
            expected_code = "381" if isinstance(document, CustomerCreditNote) else "388"
            if (inv_type or "").strip() != expected_code:
                errors.append(
                    {
                        "field": "InvoiceTypeCode",
                        "code": "INVALID_INVOICE_TYPE_CODE",
                        "message": f"InvoiceTypeCode must be {expected_code} for this document.",
                    }
                )
            inv_type_node = root.find(".//cbc:InvoiceTypeCode", namespaces=ns)
            inv_type_name = (inv_type_node.get("name") or "").strip() if inv_type_node is not None else ""
            if not inv_type_name:
                errors.append(
                    {
                        "field": "InvoiceTypeCode@name",
                        "code": "MISSING_INVOICE_TYPE_NAME",
                        "message": "InvoiceTypeCode must include required 'name' attribute.",
                    }
                )
            else:
                expected_type_name = _resolve_invoice_type_name(
                    profile_id=(root.findtext(".//cbc:ProfileID", namespaces=ns) or "").strip(),
                    is_credit_note=isinstance(document, CustomerCreditNote),
                )
                if inv_type_name != expected_type_name:
                    errors.append(
                        {
                            "field": "InvoiceTypeCode@name",
                            "code": "INVALID_INVOICE_TYPE_NAME",
                            "message": f"InvoiceTypeCode@name must be '{expected_type_name}' for this profile/document type.",
                        }
                    )
            # Core party/address completeness checks (common ZATCA profile rejection area).
            profile_id_xml = root.findtext(".//cbc:ProfileID", namespaces=ns)
            if not (profile_id_xml or "").strip():
                errors.append(
                    {
                        "field": "ProfileID",
                        "code": "MISSING_PROFILE_ID",
                        "message": "ProfileID is required.",
                    }
                )
            else:
                if (profile_id_xml or "").strip() not in {"reporting:1.0", "clearance:1.0"}:
                    errors.append(
                        {
                            "field": "ProfileID",
                            "code": "INVALID_PROFILE_ID",
                            "message": "ProfileID must be one of: reporting:1.0, clearance:1.0.",
                        }
                    )
                expected_profile = _resolve_profile_id(is_credit_note=isinstance(document, CustomerCreditNote))
                if _strict_profile_mode() and (profile_id_xml or "").strip() != expected_profile:
                    errors.append(
                        {
                            "field": "ProfileID",
                            "code": "PROFILE_ID_MISMATCH",
                            "message": f"Strict mode requires ProfileID='{expected_profile}'.",
                        }
                    )
            uuid_xml = (root.findtext(".//cbc:UUID", namespaces=ns) or "").strip()
            if not uuid_xml:
                errors.append(
                    {
                        "field": "UUID",
                        "code": "MISSING_UUID_XML",
                        "message": "cbc:UUID is required in XML.",
                    }
                )
            else:
                try:
                    uuid.UUID(uuid_xml)
                except Exception:
                    errors.append(
                        {
                            "field": "UUID",
                            "code": "INVALID_UUID_XML",
                            "message": "cbc:UUID must be a valid UUID value.",
                        }
                    )
            icv_xml = root.findtext(".//cac:AdditionalDocumentReference[cbc:ID='ICV']/cbc:UUID", namespaces=ns)
            if not (icv_xml or "").strip():
                errors.append(
                    {
                        "field": "AdditionalDocumentReference(ICV).UUID",
                        "code": "MISSING_ICV",
                        "message": "ICV reference UUID is required.",
                    }
                )
            elif not str(icv_xml).strip().isdigit():
                errors.append(
                    {
                        "field": "AdditionalDocumentReference(ICV).UUID",
                        "code": "INVALID_ICV_FORMAT",
                        "message": "ICV UUID must be numeric.",
                    }
                )
            pih_xml = (
                root.findtext(
                    ".//cac:AdditionalDocumentReference[cbc:ID='PIH']/cac:Attachment/cbc:EmbeddedDocumentBinaryObject",
                    namespaces=ns,
                )
                or ""
            ).strip()
            if not pih_xml:
                errors.append(
                    {
                        "field": "AdditionalDocumentReference(PIH).EmbeddedDocumentBinaryObject",
                        "code": "MISSING_PIH",
                        "message": "PIH embedded hash is required.",
                    }
                )
            else:
                expected_pih = str(getattr(document, "zatca_previous_hash", "") or "").strip()
                if re.fullmatch(r"[A-Fa-f0-9]{64}", expected_pih):
                    expected_pih = base64.b64encode(bytes.fromhex(expected_pih)).decode("utf-8")
                if expected_pih and pih_xml != expected_pih:
                    errors.append(
                        {
                            "field": "AdditionalDocumentReference(PIH).EmbeddedDocumentBinaryObject",
                            "code": "PIH_MISMATCH",
                            "message": "PIH payload does not match expected previous hash representation.",
                        }
                    )
            seller_vat_xml = root.findtext(".//cac:AccountingSupplierParty/cac:Party/cac:PartyTaxScheme/cbc:CompanyID", namespaces=ns)
            if not (seller_vat_xml or "").strip():
                errors.append(
                    {
                        "field": "AccountingSupplierParty.PartyTaxScheme.CompanyID",
                        "code": "MISSING_SELLER_VAT_XML",
                        "message": "Supplier VAT (CompanyID) is required in XML.",
                    }
                )
            seller_vat_node = root.find(".//cac:AccountingSupplierParty/cac:Party/cac:PartyTaxScheme/cbc:CompanyID", namespaces=ns)
            if seller_vat_node is not None and (seller_vat_node.get("schemeID") or "").strip() != "VAT":
                errors.append(
                    {
                        "field": "AccountingSupplierParty.PartyTaxScheme.CompanyID@schemeID",
                        "code": "INVALID_SELLER_VAT_SCHEME",
                        "message": "Seller VAT CompanyID must include schemeID='VAT'.",
                    }
                )
            seller_legal_name = root.findtext(".//cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity/cbc:RegistrationName", namespaces=ns)
            if not (seller_legal_name or "").strip():
                errors.append(
                    {
                        "field": "AccountingSupplierParty.PartyLegalEntity.RegistrationName",
                        "code": "MISSING_SELLER_LEGAL_NAME",
                        "message": "Supplier legal registration name is required in XML.",
                    }
                )
            for field_path, field_label in [
                (".//cac:AccountingSupplierParty/cac:Party/cac:PostalAddress/cbc:StreetName", "supplier street"),
                (".//cac:AccountingSupplierParty/cac:Party/cac:PostalAddress/cbc:CityName", "supplier city"),
                (".//cac:AccountingSupplierParty/cac:Party/cac:PostalAddress/cbc:PostalZone", "supplier postal code"),
                (".//cac:AccountingSupplierParty/cac:Party/cac:PostalAddress/cbc:CountrySubentity", "supplier country subentity"),
                (".//cac:AccountingSupplierParty/cac:Party/cac:PostalAddress/cac:Country/cbc:IdentificationCode", "supplier country code"),
                (".//cac:AccountingCustomerParty/cac:Party/cac:PostalAddress/cbc:StreetName", "buyer street"),
                (".//cac:AccountingCustomerParty/cac:Party/cac:PostalAddress/cbc:CityName", "buyer city"),
                (".//cac:AccountingCustomerParty/cac:Party/cac:PostalAddress/cbc:PostalZone", "buyer postal code"),
                (".//cac:AccountingCustomerParty/cac:Party/cac:PostalAddress/cbc:CountrySubentity", "buyer country subentity"),
                (".//cac:AccountingCustomerParty/cac:Party/cac:PostalAddress/cac:Country/cbc:IdentificationCode", "buyer country code"),
            ]:
                if not (root.findtext(field_path, namespaces=ns) or "").strip():
                    errors.append(
                        {
                            "field": field_path,
                            "code": "MISSING_ADDRESS_FIELD",
                            "message": f"Missing required {field_label} in XML.",
                        }
                    )
            # Basic format checks to catch profile-level rejects earlier.
            country_code_paths = [
                ".//cac:AccountingSupplierParty/cac:Party/cac:PostalAddress/cac:Country/cbc:IdentificationCode",
                ".//cac:AccountingCustomerParty/cac:Party/cac:PostalAddress/cac:Country/cbc:IdentificationCode",
            ]
            for p in country_code_paths:
                code_val = (root.findtext(p, namespaces=ns) or "").strip()
                if code_val and not re.fullmatch(r"[A-Z]{2}", code_val):
                    errors.append(
                        {
                            "field": p,
                            "code": "INVALID_COUNTRY_CODE",
                            "message": "Country IdentificationCode must be ISO alpha-2 uppercase (e.g., SA).",
                        }
                    )

            if (seller_vat_xml or "").strip() and not re.fullmatch(r"[0-9]{15}", (seller_vat_xml or "").strip()):
                errors.append(
                    {
                        "field": "AccountingSupplierParty.PartyTaxScheme.CompanyID",
                        "code": "INVALID_SELLER_VAT_FORMAT",
                        "message": "Seller VAT should be 15 numeric digits for KSA VAT registration numbers.",
                    }
                )

            # Buyer VAT is mandatory for KSA VAT-registered B2B customers.
            customer = getattr(document, "customer", None)
            if customer and str(getattr(customer, "vat_treatment", "")).strip() == "vat_registered_ksa":
                buyer_vat_xml = root.findtext(".//cac:AccountingCustomerParty/cac:Party/cac:PartyTaxScheme/cbc:CompanyID", namespaces=ns)
                if not (buyer_vat_xml or "").strip():
                    errors.append(
                        {
                            "field": "AccountingCustomerParty.PartyTaxScheme.CompanyID",
                            "code": "MISSING_BUYER_VAT_XML",
                            "message": "Buyer VAT is required for VAT-registered KSA customers.",
                        }
                    )
                elif not re.fullmatch(r"[0-9]{15}", (buyer_vat_xml or "").strip()):
                    errors.append(
                        {
                            "field": "AccountingCustomerParty.PartyTaxScheme.CompanyID",
                            "code": "INVALID_BUYER_VAT_FORMAT",
                            "message": "Buyer VAT should be 15 numeric digits for KSA VAT registration numbers.",
                        }
                    )
                buyer_vat_node = root.find(".//cac:AccountingCustomerParty/cac:Party/cac:PartyTaxScheme/cbc:CompanyID", namespaces=ns)
                if buyer_vat_node is not None and (buyer_vat_node.get("schemeID") or "").strip() != "VAT":
                    errors.append(
                        {
                            "field": "AccountingCustomerParty.PartyTaxScheme.CompanyID@schemeID",
                            "code": "INVALID_BUYER_VAT_SCHEME",
                            "message": "Buyer VAT CompanyID must include schemeID='VAT'.",
                        }
                    )
            buyer_legal_name = root.findtext(".//cac:AccountingCustomerParty/cac:Party/cac:PartyLegalEntity/cbc:RegistrationName", namespaces=ns)
            if not (buyer_legal_name or "").strip():
                errors.append(
                    {
                        "field": "AccountingCustomerParty.PartyLegalEntity.RegistrationName",
                        "code": "MISSING_BUYER_LEGAL_NAME",
                        "message": "Buyer legal registration name is required in XML.",
                    }
                )
            if isinstance(document, CustomerCreditNote):
                ref_id = root.findtext(".//cac:BillingReference/cac:InvoiceDocumentReference/cbc:ID", namespaces=ns)
                if not (ref_id or "").strip():
                    errors.append(
                        {
                            "field": "BillingReference.InvoiceDocumentReference.ID",
                            "code": "MISSING_BILLING_REFERENCE",
                            "message": "Credit note must include BillingReference to original invoice ID.",
                        }
                    )

            tax_sub_count = len(root.findall(".//cac:TaxTotal/cac:TaxSubtotal", namespaces=ns))
            if tax_sub_count == 0:
                errors.append(
                    {
                        "field": "TaxSubtotal",
                        "code": "MISSING_TAX_SUBTOTAL",
                        "message": "At least one TaxSubtotal is required in TaxTotal.",
                    }
                )
            discount_total_xml = (
                root.findtext(".//cac:LegalMonetaryTotal/cbc:AllowanceTotalAmount", namespaces=ns) or "0"
            ).strip()
            try:
                discount_total_dec = Decimal(discount_total_xml).quantize(Decimal("0.01"))
            except Exception:
                discount_total_dec = Decimal("0.00")
            if discount_total_dec > Decimal("0.00"):
                allowance_node = root.find(".//cac:AllowanceCharge", namespaces=ns)
                if allowance_node is None:
                    errors.append(
                        {
                            "field": "AllowanceCharge",
                            "code": "MISSING_ALLOWANCE_CHARGE",
                            "message": "AllowanceCharge is required when AllowanceTotalAmount is greater than zero.",
                        }
                    )
                else:
                    charge_indicator = (allowance_node.findtext("./cbc:ChargeIndicator", namespaces=ns) or "").strip().lower()
                    if charge_indicator != "false":
                        errors.append(
                            {
                                "field": "AllowanceCharge.ChargeIndicator",
                                "code": "INVALID_ALLOWANCE_CHARGE_INDICATOR",
                                "message": "AllowanceCharge ChargeIndicator must be false for discounts.",
                            }
                        )
                    allowance_amount = (allowance_node.findtext("./cbc:Amount", namespaces=ns) or "").strip()
                    try:
                        allowance_amount_dec = Decimal(allowance_amount).quantize(Decimal("0.01"))
                        if allowance_amount_dec != discount_total_dec:
                            errors.append(
                                {
                                    "field": "AllowanceCharge.Amount",
                                    "code": "ALLOWANCE_AMOUNT_MISMATCH",
                                    "message": "AllowanceCharge Amount must match LegalMonetaryTotal.AllowanceTotalAmount.",
                                }
                            )
                    except Exception:
                        errors.append(
                            {
                                "field": "AllowanceCharge.Amount",
                                "code": "INVALID_ALLOWANCE_AMOUNT",
                                "message": "AllowanceCharge Amount must be a valid decimal amount.",
                            }
                        )
            monetary = root.find(".//cac:LegalMonetaryTotal", namespaces=ns)
            if monetary is None:
                errors.append(
                    {
                        "field": "LegalMonetaryTotal",
                        "code": "MISSING_MONETARY_TOTAL",
                        "message": "LegalMonetaryTotal section is required.",
                    }
                )
            else:
                for tag in ["LineExtensionAmount", "TaxExclusiveAmount", "TaxInclusiveAmount", "PayableAmount"]:
                    if monetary.find(f"cbc:{tag}", namespaces=ns) is None:
                        errors.append(
                            {
                                "field": f"LegalMonetaryTotal.{tag}",
                                "code": "MISSING_MONETARY_FIELD",
                                "message": f"{tag} is required in LegalMonetaryTotal.",
                            }
                        )
                # Strict UBL math checks (high-impact ZATCA rejection area).
                def _money_at(path: str, field_name: str) -> Decimal | None:
                    node = monetary.find(path, namespaces=ns)
                    if node is None or not (node.text or "").strip():
                        return None
                    if not (node.get("currencyID") or "").strip():
                        errors.append(
                            {
                                "field": field_name,
                                "code": "MISSING_CURRENCY_ID",
                                "message": f"{field_name} must include currencyID attribute.",
                            }
                        )
                    try:
                        return Decimal((node.text or "0").strip()).quantize(Decimal("0.01"))
                    except Exception:
                        errors.append(
                            {
                                "field": field_name,
                                "code": "INVALID_MONEY_VALUE",
                                "message": f"{field_name} must be a valid decimal amount.",
                            }
                        )
                        return None

                line_ext = _money_at("cbc:LineExtensionAmount", "LegalMonetaryTotal.LineExtensionAmount")
                tax_excl = _money_at("cbc:TaxExclusiveAmount", "LegalMonetaryTotal.TaxExclusiveAmount")
                tax_incl = _money_at("cbc:TaxInclusiveAmount", "LegalMonetaryTotal.TaxInclusiveAmount")
                payable = _money_at("cbc:PayableAmount", "LegalMonetaryTotal.PayableAmount")

                top_tax_amt_text = root.findtext("./cac:TaxTotal/cbc:TaxAmount", namespaces=ns)
                top_tax_amt_node = root.find("./cac:TaxTotal/cbc:TaxAmount", namespaces=ns)
                top_tax_amt: Decimal | None = None
                if top_tax_amt_node is not None and not (top_tax_amt_node.get("currencyID") or "").strip():
                    errors.append(
                        {
                            "field": "TaxTotal.TaxAmount",
                            "code": "MISSING_CURRENCY_ID",
                            "message": "TaxTotal.TaxAmount must include currencyID attribute.",
                        }
                    )
                if (top_tax_amt_text or "").strip():
                    try:
                        top_tax_amt = Decimal(top_tax_amt_text.strip()).quantize(Decimal("0.01"))
                    except Exception:
                        errors.append(
                            {
                                "field": "TaxTotal.TaxAmount",
                                "code": "INVALID_TAX_TOTAL",
                                "message": "Top-level TaxTotal/TaxAmount must be a valid decimal amount.",
                            }
                        )
                else:
                    errors.append(
                        {
                            "field": "TaxTotal.TaxAmount",
                            "code": "MISSING_TAX_TOTAL",
                            "message": "Top-level TaxTotal/TaxAmount is required.",
                        }
                    )

                subtotal_taxes: list[Decimal] = []
                for idx, sub_tax_node in enumerate(root.findall("./cac:TaxTotal/cac:TaxSubtotal/cbc:TaxAmount", namespaces=ns), start=1):
                    raw = (sub_tax_node.text or "").strip()
                    if not (sub_tax_node.get("currencyID") or "").strip():
                        errors.append(
                            {
                                "field": f"TaxTotal.TaxSubtotal[{idx}].TaxAmount",
                                "code": "MISSING_CURRENCY_ID",
                                "message": "TaxSubtotal TaxAmount must include currencyID attribute.",
                            }
                        )
                    if not raw:
                        errors.append(
                            {
                                "field": f"TaxTotal.TaxSubtotal[{idx}].TaxAmount",
                                "code": "MISSING_TAX_SUBTOTAL_AMOUNT",
                                "message": "Each TaxSubtotal must include TaxAmount.",
                            }
                        )
                        continue
                    try:
                        subtotal_taxes.append(Decimal(raw).quantize(Decimal("0.01")))
                    except Exception:
                        errors.append(
                            {
                                "field": f"TaxTotal.TaxSubtotal[{idx}].TaxAmount",
                                "code": "INVALID_TAX_SUBTOTAL_AMOUNT",
                                "message": "TaxSubtotal TaxAmount must be a valid decimal amount.",
                            }
                        )
                if subtotal_taxes and top_tax_amt is not None:
                    sub_sum = sum(subtotal_taxes, Decimal("0.00")).quantize(Decimal("0.01"))
                    if sub_sum != top_tax_amt:
                        errors.append(
                            {
                                "field": "TaxTotal",
                                "code": "TAX_TOTAL_MISMATCH",
                                "message": f"TaxTotal ({top_tax_amt}) must equal sum(TaxSubtotal.TaxAmount) ({sub_sum}).",
                            }
                        )

                if tax_excl is not None and top_tax_amt is not None and tax_incl is not None:
                    expected_incl = (tax_excl + top_tax_amt).quantize(Decimal("0.01"))
                    if expected_incl != tax_incl:
                        errors.append(
                            {
                                "field": "LegalMonetaryTotal.TaxInclusiveAmount",
                                "code": "TAX_INCLUSIVE_MISMATCH",
                                "message": f"TaxInclusiveAmount ({tax_incl}) must equal TaxExclusiveAmount + TaxTotal ({expected_incl}).",
                            }
                        )

                if payable is not None and tax_incl is not None and payable != tax_incl:
                    errors.append(
                        {
                            "field": "LegalMonetaryTotal.PayableAmount",
                            "code": "PAYABLE_MISMATCH",
                            "message": f"PayableAmount ({payable}) must equal TaxInclusiveAmount ({tax_incl}).",
                        }
                    )

                if _strict_profile_mode() and line_ext is not None and tax_excl is not None and line_ext != tax_excl:
                    errors.append(
                        {
                            "field": "LegalMonetaryTotal",
                            "code": "LINE_EXT_TAX_EXCL_MISMATCH",
                            "message": "Strict mode expects LineExtensionAmount to equal TaxExclusiveAmount.",
                        }
                    )
        except Exception as exc:
            errors.append({"field": "zatca_signed_xml", "code": "XML_PARSE_ERROR", "message": f"Unable to parse signed XML for profile checks: {exc}"})

    # UBL 2.1 + ZATCA profile validation gate (blocks live submission)
    xsd_paths: list[str] = []
    if isinstance(document, CustomerCreditNote):
        xsd_paths.append(os.getenv("ZATCA_UBL_CREDIT_NOTE_XSD_PATH", "").strip())
    else:
        xsd_paths.append(os.getenv("ZATCA_UBL_INVOICE_XSD_PATH", "").strip())
    profile_paths = [p.strip() for p in os.getenv("ZATCA_PROFILE_XSD_PATHS", "").split(",") if p.strip()]
    xsd_paths.extend(profile_paths)

    if not any(xsd_paths):
        errors.append(
            {
                "field": "zatca_signed_xml",
                "code": "XSD_NOT_CONFIGURED",
                "message": "UBL/ZATCA XSD paths are not configured (set ZATCA_UBL_INVOICE_XSD_PATH / ZATCA_UBL_CREDIT_NOTE_XSD_PATH and optional ZATCA_PROFILE_XSD_PATHS).",
            }
        )
    else:
        try:
            validate_xml_against_xsd(document.zatca_signed_xml, xsd_paths=[p for p in xsd_paths if p])
        except ValueError as exc:
            errors.append({"field": "zatca_signed_xml", "code": "XSD_VALIDATION_FAILED", "message": str(exc)})

    # QR TLV compliance (Phase 1 baseline)
    if not getattr(document, "qr_code_text", ""):
        errors.append({"field": "qr_code_text", "code": "MISSING_QR", "message": "QR TLV payload is required."})
    else:
        try:
            tlv = _decode_tlv_base64(document.qr_code_text)
            for required_tag, label in [
                (1, "Seller name"),
                (2, "VAT number"),
                (3, "Timestamp"),
                (4, "Invoice total"),
                (5, "VAT total"),
                (6, "Cryptographic stamp (chained invoice hash)"),
            ]:
                if required_tag not in tlv or not tlv[required_tag]:
                    errors.append({"field": "qr_code_text", "code": "TLV_MISSING_TAG", "message": f"QR TLV missing required tag {required_tag} ({label})."})
            if 2 in tlv and seller_vat and tlv[2] != seller_vat:
                errors.append({"field": "qr_code_text", "code": "TLV_VAT_MISMATCH", "message": "QR TLV VAT number does not match Company VAT number."})
            expected_total = f"{Decimal(str(getattr(document, 'total_amount', Decimal('0')))).quantize(Decimal('0.01'))}"
            expected_vat = f"{Decimal(str(getattr(document, 'total_vat', Decimal('0')))).quantize(Decimal('0.01'))}"
            if 4 in tlv and tlv[4] != expected_total:
                errors.append({"field": "qr_code_text", "code": "TLV_TOTAL_MISMATCH", "message": "QR TLV total (tag 4) does not match document total."})
            if 5 in tlv and tlv[5] != expected_vat:
                errors.append({"field": "qr_code_text", "code": "TLV_VAT_TOTAL_MISMATCH", "message": "QR TLV VAT total (tag 5) does not match document VAT total."})
            inv_hash = (getattr(document, "zatca_invoice_hash", "") or "").strip().lower()
            if 6 in tlv and inv_hash and tlv[6].strip().lower() != inv_hash:
                errors.append(
                    {
                        "field": "qr_code_text",
                        "code": "TLV_HASH_MISMATCH",
                        "message": "QR TLV tag 6 must equal the document chained invoice hash.",
                    }
                )
        except Exception as exc:
            errors.append({"field": "qr_code_text", "code": "TLV_INVALID", "message": f"Invalid QR TLV payload: {exc}"})

    # Line-level tax categories must exist when tax rate exists
    lines = getattr(document, "lines", None)
    if lines is not None:
        for idx, line in enumerate(document.lines.filter(is_deleted=False).select_related("tax_rate"), start=1):
            if line.tax_rate and not (line.tax_rate.zatca_category or "").strip():
                errors.append({"field": f"lines[{idx}].tax_rate.zatca_category", "code": "MISSING_TAX_CATEGORY", "message": "Tax rate ZATCA category is required for taxed lines."})

    if errors:
        raise ZatcaValidationError(errors)


def _validate_xades_structure(signed_xml: str) -> list[dict]:
    """
    Strict structural checks for XAdES-BES baseline paths and references.
    Returns a list of structured errors.
    """
    errs: list[dict] = []
    try:
        from lxml import etree  # type: ignore
    except Exception as exc:
        return [{"field": "zatca_signed_xml", "code": "XML_PARSER_MISSING", "message": f"XML parser dependency missing: {exc}"}]

    ns = {
        "ds": "http://www.w3.org/2000/09/xmldsig#",
        "xades": "http://uri.etsi.org/01903/v1.3.2#",
    }

    try:
        root = etree.fromstring((signed_xml or "").encode("utf-8"))
    except Exception as exc:
        return [{"field": "zatca_signed_xml", "code": "XML_PARSE_ERROR", "message": f"Unable to parse signed XML: {exc}"}]

    sig = root.find(".//ds:Signature", namespaces=ns)
    if sig is None:
        return [{"field": "XAdES.Signature", "code": "MISSING_SIGNATURE", "message": "ds:Signature is required."}]

    ext_ns = "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"
    ext_content = root.find(f".//{{{ext_ns}}}UBLExtensions/{{{ext_ns}}}UBLExtension/{{{ext_ns}}}ExtensionContent")
    if ext_content is None:
        errs.append(
            {
                "field": "UBL.UBLExtensions",
                "code": "MISSING_UBL_EXTENSIONS",
                "message": "ext:UBLExtensions/ext:UBLExtension/ext:ExtensionContent is required for ds:Signature placement.",
            }
        )
    else:
        ext_sig = ext_content.find("./ds:Signature", namespaces=ns)
        if ext_sig is None:
            errs.append(
                {
                    "field": "UBL.ExtensionContent.Signature",
                    "code": "MISSING_SIGNATURE_IN_EXTENSION_CONTENT",
                    "message": "ds:Signature must be placed inside ext:ExtensionContent.",
                }
            )
        outside_sigs = root.xpath(
            "//ds:Signature[not(ancestor::ext:ExtensionContent)]",
            namespaces={"ds": ns["ds"], "ext": ext_ns},
        )
        if outside_sigs:
            errs.append(
                {
                    "field": "UBL.SignaturePlacement",
                    "code": "SIGNATURE_OUTSIDE_EXTENSION_CONTENT",
                    "message": "ds:Signature must not be outside ext:ExtensionContent.",
                }
            )

    sig_id = (sig.get("Id") or "").strip()
    if not sig_id:
        errs.append({"field": "XAdES.Signature@Id", "code": "MISSING_SIGNATURE_ID", "message": "ds:Signature must have an Id attribute."})

    qprops = sig.find(".//xades:QualifyingProperties", namespaces=ns)
    if qprops is None:
        errs.append({"field": "XAdES.QualifyingProperties", "code": "MISSING_QUALIFYING_PROPERTIES", "message": "xades:QualifyingProperties is required."})
        return errs

    target = (qprops.get("Target") or "").strip()
    if not target:
        errs.append({"field": "XAdES.QualifyingProperties@Target", "code": "MISSING_TARGET", "message": "QualifyingProperties@Target is required."})
    elif sig_id and target != f"#{sig_id}":
        errs.append(
            {
                "field": "XAdES.QualifyingProperties@Target",
                "code": "TARGET_MISMATCH",
                "message": f"QualifyingProperties@Target must reference ds:Signature Id '#{sig_id}'.",
            }
        )

    signed_props = qprops.find("./xades:SignedProperties", namespaces=ns)
    if signed_props is None:
        errs.append({"field": "XAdES.SignedProperties", "code": "MISSING_SIGNED_PROPERTIES", "message": "xades:SignedProperties is required."})
        return errs

    signed_props_id = (signed_props.get("Id") or "").strip()
    if not signed_props_id:
        errs.append({"field": "XAdES.SignedProperties@Id", "code": "MISSING_SIGNED_PROPERTIES_ID", "message": "SignedProperties must have Id."})

    ssp = signed_props.find("./xades:SignedSignatureProperties", namespaces=ns)
    if ssp is None:
        errs.append({"field": "XAdES.SignedSignatureProperties", "code": "MISSING_SIGNED_SIGNATURE_PROPERTIES", "message": "xades:SignedSignatureProperties is required."})
        return errs

    if ssp.find("./xades:SigningTime", namespaces=ns) is None:
        errs.append({"field": "XAdES.SigningTime", "code": "MISSING_SIGNING_TIME", "message": "xades:SigningTime is required."})

    signing_cert_mode = _get_signing_certificate_mode()
    signing_cert_v1 = ssp.find("./xades:SigningCertificate", namespaces=ns)
    signing_cert_v2 = ssp.find("./xades:SigningCertificateV2", namespaces=ns)
    if signing_cert_mode == "v1":
        required_blocks = [("SigningCertificate", signing_cert_v1)]
    elif signing_cert_mode == "v2":
        required_blocks = [("SigningCertificateV2", signing_cert_v2)]
    else:
        required_blocks = []
        if signing_cert_v1 is None and signing_cert_v2 is None:
            errs.append(
                {
                    "field": "XAdES.SigningCertificate",
                    "code": "MISSING_SIGNING_CERTIFICATE_BLOCK",
                    "message": "At least one of xades:SigningCertificate or xades:SigningCertificateV2 is required.",
                }
            )
        if signing_cert_v1 is not None:
            required_blocks.append(("SigningCertificate", signing_cert_v1))
        if signing_cert_v2 is not None:
            required_blocks.append(("SigningCertificateV2", signing_cert_v2))

    for block_name, block in required_blocks:
        if block is None:
            errs.append(
                {
                    "field": f"XAdES.{block_name}",
                    "code": "MISSING_SIGNING_CERTIFICATE",
                    "message": f"xades:{block_name} is required by configuration.",
                }
            )
            continue
        cert_el = block.find("./xades:Cert", namespaces=ns)
        if cert_el is None:
            errs.append(
                {
                    "field": f"XAdES.{block_name}.Cert",
                    "code": "MISSING_CERT_NODE",
                    "message": f"xades:Cert is required under xades:{block_name}.",
                }
            )
            continue
        if cert_el.find("./xades:CertDigest/ds:DigestMethod", namespaces=ns) is None:
            errs.append(
                {
                    "field": f"XAdES.{block_name}.CertDigest.DigestMethod",
                    "code": "MISSING_CERT_DIGEST_METHOD",
                    "message": f"CertDigest/DigestMethod is required in xades:{block_name}.",
                }
            )
        if cert_el.find("./xades:CertDigest/ds:DigestValue", namespaces=ns) is None:
            errs.append(
                {
                    "field": f"XAdES.{block_name}.CertDigest.DigestValue",
                    "code": "MISSING_CERT_DIGEST_VALUE",
                    "message": f"CertDigest/DigestValue is required in xades:{block_name}.",
                }
            )
        issuer_node_name = "IssuerSerialV2" if block_name == "SigningCertificateV2" else "IssuerSerial"
        if cert_el.find(f"./xades:{issuer_node_name}/ds:X509IssuerName", namespaces=ns) is None:
            errs.append(
                {
                    "field": f"XAdES.{block_name}.{issuer_node_name}.X509IssuerName",
                    "code": "MISSING_ISSUER_NAME",
                    "message": f"{issuer_node_name}/X509IssuerName is required in xades:{block_name}.",
                }
            )
        if cert_el.find(f"./xades:{issuer_node_name}/ds:X509SerialNumber", namespaces=ns) is None:
            errs.append(
                {
                    "field": f"XAdES.{block_name}.{issuer_node_name}.X509SerialNumber",
                    "code": "MISSING_SERIAL_NUMBER",
                    "message": f"{issuer_node_name}/X509SerialNumber is required in xades:{block_name}.",
                }
            )

    policy_cfg = _get_signature_policy_config()
    sig_policy = ssp.find("./xades:SignaturePolicyIdentifier/xades:SignaturePolicyId", namespaces=ns)
    if policy_cfg["required"] and sig_policy is None:
        errs.append(
            {
                "field": "XAdES.SignaturePolicyIdentifier",
                "code": "MISSING_SIGNATURE_POLICY",
                "message": "xades:SignaturePolicyIdentifier is required by configuration.",
            }
        )
    if sig_policy is not None:
        policy_identifier = sig_policy.find("./xades:SigPolicyId/xades:Identifier", namespaces=ns)
        if policy_identifier is None or not (policy_identifier.text or "").strip():
            errs.append(
                {
                    "field": "XAdES.SignaturePolicyIdentifier.Identifier",
                    "code": "MISSING_SIGNATURE_POLICY_ID",
                    "message": "SignaturePolicyIdentifier must include SigPolicyId/Identifier.",
                }
            )
        policy_digest_method = sig_policy.find("./xades:SigPolicyHash/ds:DigestMethod", namespaces=ns)
        policy_digest_value = sig_policy.find("./xades:SigPolicyHash/ds:DigestValue", namespaces=ns)
        if policy_digest_method is None:
            errs.append(
                {
                    "field": "XAdES.SignaturePolicyIdentifier.DigestMethod",
                    "code": "MISSING_SIGNATURE_POLICY_DIGEST_METHOD",
                    "message": "SignaturePolicyIdentifier must include SigPolicyHash/DigestMethod.",
                }
            )
        if policy_digest_value is None or not (policy_digest_value.text or "").strip():
            errs.append(
                {
                    "field": "XAdES.SignaturePolicyIdentifier.DigestValue",
                    "code": "MISSING_SIGNATURE_POLICY_DIGEST_VALUE",
                    "message": "SignaturePolicyIdentifier must include SigPolicyHash/DigestValue.",
                }
            )
        if policy_cfg["enabled"]:
            expected_id = str(policy_cfg["id"]).strip()
            expected_hash = str(policy_cfg["hash"]).strip()
            actual_id = (policy_identifier.text or "").strip() if policy_identifier is not None else ""
            actual_hash = (policy_digest_value.text or "").strip() if policy_digest_value is not None else ""
            if expected_id and actual_id and actual_id != expected_id:
                errs.append(
                    {
                        "field": "XAdES.SignaturePolicyIdentifier.Identifier",
                        "code": "SIGNATURE_POLICY_ID_MISMATCH",
                        "message": "Signature policy identifier does not match configured value.",
                    }
                )
            if expected_hash and actual_hash and actual_hash != expected_hash:
                errs.append(
                    {
                        "field": "XAdES.SignaturePolicyIdentifier.DigestValue",
                        "code": "SIGNATURE_POLICY_HASH_MISMATCH",
                        "message": "Signature policy digest does not match configured value.",
                    }
                )

    # SignedProperties reference in ds:SignedInfo
    signed_info = sig.find("./ds:SignedInfo", namespaces=ns)
    if signed_info is None:
        errs.append({"field": "XAdES.SignedInfo", "code": "MISSING_SIGNED_INFO", "message": "ds:SignedInfo is required."})
    else:
        c14n_method = signed_info.find("./ds:CanonicalizationMethod", namespaces=ns)
        c14n_algo = (c14n_method.get("Algorithm") if c14n_method is not None else "").strip()
        if c14n_algo != "http://www.w3.org/2001/10/xml-exc-c14n#":
            errs.append(
                {
                    "field": "XAdES.SignedInfo.CanonicalizationMethod",
                    "code": "INVALID_C14N_ALGORITHM",
                    "message": "SignedInfo CanonicalizationMethod must be exclusive C14N (http://www.w3.org/2001/10/xml-exc-c14n#).",
                }
            )

        ref_nodes = signed_info.findall("./ds:Reference", namespaces=ns)
        if len(ref_nodes) < 2:
            errs.append(
                {
                    "field": "XAdES.SignedInfo.Reference",
                    "code": "INSUFFICIENT_REFERENCES",
                    "message": "SignedInfo must include at least document and SignedProperties references.",
                }
            )
        sp_ref = None
        key_info_ref = None
        document_ref = None

        def _resolve_target(uri: str):
            if uri == "":
                return root
            if not uri.startswith("#"):
                return None
            target_id = uri[1:]
            if _strict_profile_mode():
                targets = root.xpath(f"//*[@Id='{target_id}']")
            else:
                targets = root.xpath(
                    f"//*[@Id='{target_id}' or @id='{target_id}' or @xml:id='{target_id}']",
                    namespaces={"xml": "http://www.w3.org/XML/1998/namespace"},
                )
            return targets[0] if targets else None

        def _reference_c14n_bytes(ref_node):
            uri = (ref_node.get("URI") or "").strip()
            target = _resolve_target(uri)
            if target is None:
                return None

            target_copy = etree.fromstring(etree.tostring(target))
            transform_algos = [(t.get("Algorithm") or "").strip() for t in ref_node.findall("./ds:Transforms/ds:Transform", namespaces=ns)]
            if "http://www.w3.org/2000/09/xmldsig#enveloped-signature" in transform_algos:
                for sig_node in target_copy.xpath(".//ds:Signature", namespaces=ns):
                    parent = sig_node.getparent()
                    if parent is not None:
                        parent.remove(sig_node)
            # Required canonicalization for reference digesting.
            return etree.tostring(target_copy, method="c14n", exclusive=True, with_comments=False)

        for r in ref_nodes:
            ref_type = (r.get("Type") or "").strip()
            uri = (r.get("URI") or "").strip()
            if ref_type == "http://uri.etsi.org/01903#SignedProperties":
                sp_ref = r
            elif ref_type == "http://www.w3.org/2000/09/xmldsig#KeyInfo":
                key_info_ref = r
            elif not ref_type and uri == "":
                document_ref = r

            digest_method = r.find("./ds:DigestMethod", namespaces=ns)
            digest_algo = (digest_method.get("Algorithm") if digest_method is not None else "").strip()
            if digest_algo != "http://www.w3.org/2001/04/xmlenc#sha256":
                errs.append(
                    {
                        "field": "XAdES.Reference.DigestMethod",
                        "code": "INVALID_DIGEST_ALGORITHM",
                        "message": "All signature references must use SHA-256 digest.",
                    }
                )

            for tr in r.findall("./ds:Transforms/ds:Transform", namespaces=ns):
                algo = (tr.get("Algorithm") or "").strip()
                allowed = {
                    "http://www.w3.org/2000/09/xmldsig#enveloped-signature",
                    "http://www.w3.org/2001/10/xml-exc-c14n#",
                }
                if algo and algo not in allowed:
                    errs.append(
                        {
                            "field": "XAdES.Reference.Transform",
                            "code": "INVALID_TRANSFORM_ALGORITHM",
                            "message": f"Unsupported transform algorithm in reference: {algo}",
                        }
                    )

            target = _resolve_target(uri)
            if uri.startswith("#") and target is None:
                errs.append(
                    {
                        "field": "XAdES.Reference.URI",
                        "code": "REFERENCE_TARGET_NOT_FOUND",
                        "message": f"Reference target '{uri}' was not found by Id.",
                    }
                )

            ref_digest_node = r.find("./ds:DigestValue", namespaces=ns)
            ref_digest_value = (ref_digest_node.text or "").strip() if ref_digest_node is not None else ""
            if not ref_digest_value:
                errs.append(
                    {
                        "field": "XAdES.Reference.DigestValue",
                        "code": "MISSING_REFERENCE_DIGEST_VALUE",
                        "message": f"Reference for URI '{uri}' must include non-empty DigestValue.",
                    }
                )
            ref_c14n = _reference_c14n_bytes(r)
            if ref_c14n is None:
                errs.append(
                    {
                        "field": "XAdES.Reference.URI",
                        "code": "REFERENCE_TARGET_UNRESOLVED",
                        "message": f"Unable to resolve reference target for URI '{uri}'.",
                    }
                )
            elif ref_digest_value:
                computed_ref_digest = base64.b64encode(hashlib.sha256(ref_c14n).digest()).decode("utf-8")
                if computed_ref_digest != ref_digest_value:
                    errs.append(
                        {
                            "field": "XAdES.Reference.DigestValue",
                            "code": "REFERENCE_DIGEST_MISMATCH",
                            "message": f"Reference digest mismatch for URI '{uri}'.",
                        }
                    )

        if document_ref is None:
            errs.append(
                {
                    "field": "XAdES.DocumentReference",
                    "code": "MISSING_DOCUMENT_REFERENCE",
                    "message": "SignedInfo must include a document reference with URI=''.",
                }
            )

        if sp_ref is None:
            errs.append({"field": "XAdES.SignedPropertiesReference", "code": "MISSING_SIGNED_PROPERTIES_REFERENCE", "message": "SignedInfo must include a Reference with Type=...#SignedProperties."})
        else:
            uri = (sp_ref.get("URI") or "").strip()
            if signed_props_id and uri != f"#{signed_props_id}":
                errs.append({"field": "XAdES.SignedPropertiesReference.URI", "code": "SIGNED_PROPERTIES_URI_MISMATCH", "message": f"SignedProperties reference URI must be '#{signed_props_id}'."})
            if sp_ref.find("./ds:DigestMethod", namespaces=ns) is None:
                errs.append({"field": "XAdES.SignedPropertiesReference.DigestMethod", "code": "MISSING_SP_REF_DIGEST_METHOD", "message": "SignedProperties reference must include DigestMethod."})
            if sp_ref.find("./ds:DigestValue", namespaces=ns) is None:
                errs.append({"field": "XAdES.SignedPropertiesReference.DigestValue", "code": "MISSING_SP_REF_DIGEST_VALUE", "message": "SignedProperties reference must include DigestValue."})

        if _strict_profile_mode():
            expected_ref_count = 3 if (_require_keyinfo_reference() or key_info_ref is not None) else 2
            if len(ref_nodes) != expected_ref_count:
                errs.append(
                    {
                        "field": "XAdES.SignedInfo.Reference",
                        "code": "INVALID_REFERENCE_COUNT",
                        "message": f"Strict mode requires exactly {expected_ref_count} references in SignedInfo.",
                    }
                )
            if document_ref is not None:
                doc_transforms = {
                    (t.get("Algorithm") or "").strip()
                    for t in document_ref.findall("./ds:Transforms/ds:Transform", namespaces=ns)
                }
                if doc_transforms != {
                    "http://www.w3.org/2000/09/xmldsig#enveloped-signature",
                    "http://www.w3.org/2001/10/xml-exc-c14n#",
                }:
                    errs.append(
                        {
                            "field": "XAdES.DocumentReference.Transforms",
                            "code": "INVALID_DOCUMENT_REFERENCE_TRANSFORMS",
                            "message": "Strict mode requires document reference transforms: enveloped-signature + exclusive c14n.",
                        }
                    )
            if sp_ref is not None:
                sp_transforms = {
                    (t.get("Algorithm") or "").strip()
                    for t in sp_ref.findall("./ds:Transforms/ds:Transform", namespaces=ns)
                }
                if sp_transforms != {"http://www.w3.org/2001/10/xml-exc-c14n#"}:
                    errs.append(
                        {
                            "field": "XAdES.SignedPropertiesReference.Transforms",
                            "code": "INVALID_SP_REFERENCE_TRANSFORMS",
                            "message": "Strict mode requires SignedProperties reference transform: exclusive c14n only.",
                        }
                    )
            if key_info_ref is not None:
                ki_transforms = {
                    (t.get("Algorithm") or "").strip()
                    for t in key_info_ref.findall("./ds:Transforms/ds:Transform", namespaces=ns)
                }
                if ki_transforms != {"http://www.w3.org/2001/10/xml-exc-c14n#"}:
                    errs.append(
                        {
                            "field": "XAdES.KeyInfoReference.Transforms",
                            "code": "INVALID_KEYINFO_REFERENCE_TRANSFORMS",
                            "message": "Strict mode requires KeyInfo reference transform: exclusive c14n only.",
                        }
                    )
            # Strict reference order:
            # 1) Document URI=""
            # 2) SignedProperties Type=...#SignedProperties
            # 3) KeyInfo Type=...#KeyInfo (when present/required)
            if len(ref_nodes) >= 1:
                r0 = ref_nodes[0]
                if (r0.get("URI") or "").strip() != "" or (r0.get("Type") or "").strip():
                    errs.append(
                        {
                            "field": "XAdES.SignedInfo.Reference[0]",
                            "code": "INVALID_REFERENCE_ORDER_DOC",
                            "message": "Strict mode requires first reference to be Document reference with URI=''.",
                        }
                    )
            if len(ref_nodes) >= 2:
                r1 = ref_nodes[1]
                if (r1.get("Type") or "").strip() != "http://uri.etsi.org/01903#SignedProperties":
                    errs.append(
                        {
                            "field": "XAdES.SignedInfo.Reference[1]",
                            "code": "INVALID_REFERENCE_ORDER_SIGNED_PROPERTIES",
                            "message": "Strict mode requires second reference to be SignedProperties reference.",
                        }
                    )
            if expected_ref_count == 3 and len(ref_nodes) >= 3:
                r2 = ref_nodes[2]
                if (r2.get("Type") or "").strip() != "http://www.w3.org/2000/09/xmldsig#KeyInfo":
                    errs.append(
                        {
                            "field": "XAdES.SignedInfo.Reference[2]",
                            "code": "INVALID_REFERENCE_ORDER_KEYINFO",
                            "message": "Strict mode requires third reference to be KeyInfo reference.",
                        }
                    )

        if _require_keyinfo_reference():
            if key_info_ref is None:
                errs.append(
                    {
                        "field": "XAdES.KeyInfoReference",
                        "code": "MISSING_KEYINFO_REFERENCE",
                        "message": "SignedInfo must include a KeyInfo reference when ZATCA_REQUIRE_KEYINFO_REFERENCE=true.",
                    }
                )
            else:
                key_info_uri = (key_info_ref.get("URI") or "").strip()
                if not key_info_uri.startswith("#"):
                    errs.append(
                        {
                            "field": "XAdES.KeyInfoReference.URI",
                            "code": "INVALID_KEYINFO_REFERENCE_URI",
                            "message": "KeyInfo reference URI must point to a local KeyInfo Id (e.g., #KeyInfo-...).",
                        }
                    )

    return errs


def _get_active_certificate() -> ZatcaCertificate:
    cert = ZatcaCertificate.objects.filter(is_deleted=False, is_active=True).order_by("-activated_at", "-created_at").first()
    if not cert:
        raise ValueError("No active ZATCA certificate configured.")
    if not cert.private_key_path:
        raise ValueError("Active ZATCA certificate is missing private_key_path.")
    return cert


def _canonicalize_xml(xml: str) -> str:
    """
    Canonicalize XML using XML C14N (deterministic hash input).
    Falls back to whitespace normalization if C14N fails.
    """
    try:
        from lxml import etree  # type: ignore

        parser = etree.XMLParser(remove_blank_text=True, resolve_entities=False, no_network=True, recover=False)
        root = etree.fromstring((xml or "").encode("utf-8"), parser=parser)
        c14n_bytes = etree.tostring(root, method="c14n", exclusive=True, with_comments=False)
        return c14n_bytes.decode("utf-8")
    except Exception as exc:
        raise ValueError(f"Unable to canonicalize XML (C14N): {exc}")


def _canonicalize_for_zatca_hash(xml: str) -> str:
    """
    Build hash-input XML with ZATCA exclusion rules, then exclusive C14N.
    Exclusions:
      - ext:UBLExtensions
      - cac:Signature (UBL placeholder)
      - cac:AdditionalDocumentReference where cbc:ID = QR (QR TLV may be updated after hash anchor)
    """
    try:
        from lxml import etree  # type: ignore

        parser = etree.XMLParser(remove_blank_text=True, resolve_entities=False, no_network=True, recover=False)
        root = etree.fromstring((xml or "").encode("utf-8"), parser=parser)
        ns = {
            "ext": "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2",
            "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
            "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
        }
        for node in root.xpath("//ext:UBLExtensions | //cac:Signature", namespaces=ns):
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)
        for node in root.xpath("//cac:AdditionalDocumentReference[cbc:ID/text()='QR']", namespaces=ns):
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)

        c14n_bytes = etree.tostring(root, method="c14n", exclusive=True, with_comments=False)
        return c14n_bytes.decode("utf-8")
    except Exception as exc:
        raise ValueError(f"Unable to canonicalize ZATCA hash input XML: {exc}")


def _sign_bytes_with_key(data: bytes, key_path: str) -> str:
    try:
        proc = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", key_path, "-binary"],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        return base64.b64encode(proc.stdout).decode("utf-8")
    except Exception as exc:
        raise ValueError(f"Unable to sign with private key: {exc}")


def _embed_signature(unsigned_xml: str, signature_value_b64: str, cert: ZatcaCertificate) -> str:
    # Deprecated: kept for compatibility with earlier exports
    return _embed_signature_note(unsigned_xml, signature_value_b64, cert)


def _embed_signature_note(xml: str, signature_value_b64: str, cert: ZatcaCertificate) -> str:
    """
    Adds a UBL `cac:Signature` note carrying the signed hash value.
    XMLDSig is produced separately via `_sign_xml_enveloped`.
    """
    cert_name = escape(cert.name or "active-cert")
    sig = (
        f"<cac:Signature>"
        f"<cbc:ID>{cert_name}</cbc:ID>"
        f"<cbc:Note>SignedHashBase64:{escape(signature_value_b64)}</cbc:Note>"
        f"</cac:Signature>"
    )
    if xml and xml.endswith("</ubl:Invoice>"):
        return xml.replace("</ubl:Invoice>", f"{sig}</ubl:Invoice>")
    if xml and xml.endswith("</ubl:CreditNote>"):
        return xml.replace("</ubl:CreditNote>", f"{sig}</ubl:CreditNote>")
    return xml + sig


def _sign_xml_enveloped(unsigned_xml: str, *, key_path: str, cert_pem_bundle: str) -> str:
    """
    Produce an enveloped XMLDSig signature over the document.
    This is the baseline required structure for Phase 2; ZATCA profile may require
    additional XAdES properties beyond this.
    """
    try:
        from lxml import etree  # type: ignore
        from signxml import XMLSigner, methods  # type: ignore
        from signxml.util import namespaces  # type: ignore
        from cryptography import x509  # type: ignore
        from cryptography.hazmat.primitives import hashes, serialization  # type: ignore
    except Exception as exc:
        raise ValueError(f"XML signing dependencies missing: {exc}")

    parser = etree.XMLParser(remove_blank_text=True, resolve_entities=False, no_network=True, recover=False)
    root = etree.fromstring((unsigned_xml or "").encode("utf-8"), parser=parser)

    # Mandatory UBL placement: signature must live inside ext:ExtensionContent.
    ext_ns = "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"
    ds_ns = "http://www.w3.org/2000/09/xmldsig#"
    ext_content = root.find(f".//{{{ext_ns}}}UBLExtensions/{{{ext_ns}}}UBLExtension/{{{ext_ns}}}ExtensionContent")
    if ext_content is None:
        raise ValueError("Missing ext:ExtensionContent in unsigned XML for signature placement.")
    # Pre-create ds:Signature placeholder before signing; never move signature after signing.
    if ext_content.find(f"./{{{ds_ns}}}Signature") is None:
        etree.SubElement(ext_content, f"{{{ds_ns}}}Signature")

    with open(key_path, "rb") as f:
        key_bytes = f.read()
    cert_chain = _split_pem_chain(cert_pem_bundle)
    if not cert_chain:
        raise ValueError("Certificate PEM bundle is empty.")
    _assert_minimum_cert_chain(cert_chain)
    cert_bytes = cert_chain[0].encode("utf-8")

    policy_cfg = _get_signature_policy_config()
    signing_cert_mode = _get_signing_certificate_mode()
    if policy_cfg["required"] and not policy_cfg["enabled"]:
        raise ValueError(
            "Signature policy is required but not configured. Set ZATCA_XADES_POLICY_ID and ZATCA_XADES_POLICY_HASH."
        )

    # Build XAdES SignedProperties (XAdES-BES baseline) and ensure it is referenced from SignedInfo.
    def _xades_annotator(sig_root, signing_settings):  # signxml calls annotators before finalizing digests
        ds_ns = namespaces.ds
        xades_ns = "http://uri.etsi.org/01903/v1.3.2#"
        nsmap = {"ds": ds_ns, "xades": xades_ns}
        sig_id = f"Signature-{uuid.uuid4().hex}"
        sig_root.set("Id", sig_id)

        # Parse certificate and compute SHA-256 digest of DER bytes
        cert_obj = x509.load_pem_x509_certificate(cert_bytes)
        der = cert_obj.public_bytes(encoding=serialization.Encoding.DER)
        h = hashes.Hash(hashes.SHA256())
        h.update(der)
        cert_digest_b64 = base64.b64encode(h.finalize()).decode("utf-8")

        # ds:Object/xades:QualifyingProperties/xades:SignedProperties
        signed_props_id = f"xades-{uuid.uuid4().hex}"
        obj = etree.SubElement(sig_root, f"{{{ds_ns}}}Object")
        qp = etree.SubElement(obj, f"{{{xades_ns}}}QualifyingProperties", nsmap=nsmap)
        qp.set("Target", f"#{sig_id}")

        sp = etree.SubElement(qp, f"{{{xades_ns}}}SignedProperties")
        sp.set("Id", signed_props_id)

        ssp = etree.SubElement(sp, f"{{{xades_ns}}}SignedSignatureProperties")
        st = etree.SubElement(ssp, f"{{{xades_ns}}}SigningTime")
        st.text = timezone.now().astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        def _add_signing_certificate_v1() -> None:
            sc = etree.SubElement(ssp, f"{{{xades_ns}}}SigningCertificate")
            cert_el = etree.SubElement(sc, f"{{{xades_ns}}}Cert")
            cd = etree.SubElement(cert_el, f"{{{xades_ns}}}CertDigest")
            dm = etree.SubElement(cd, f"{{{ds_ns}}}DigestMethod")
            dm.set("Algorithm", "http://www.w3.org/2001/04/xmlenc#sha256")
            dv = etree.SubElement(cd, f"{{{ds_ns}}}DigestValue")
            dv.text = cert_digest_b64

            iser = etree.SubElement(cert_el, f"{{{xades_ns}}}IssuerSerial")
            iname = etree.SubElement(iser, f"{{{ds_ns}}}X509IssuerName")
            iname.text = cert_obj.issuer.rfc4514_string()
            sn = etree.SubElement(iser, f"{{{ds_ns}}}X509SerialNumber")
            sn.text = str(cert_obj.serial_number)

        def _add_signing_certificate_v2() -> None:
            sc = etree.SubElement(ssp, f"{{{xades_ns}}}SigningCertificateV2")
            cert_el = etree.SubElement(sc, f"{{{xades_ns}}}Cert")
            cd = etree.SubElement(cert_el, f"{{{xades_ns}}}CertDigest")
            dm = etree.SubElement(cd, f"{{{ds_ns}}}DigestMethod")
            dm.set("Algorithm", "http://www.w3.org/2001/04/xmlenc#sha256")
            dv = etree.SubElement(cd, f"{{{ds_ns}}}DigestValue")
            dv.text = cert_digest_b64

            iser_v2 = etree.SubElement(cert_el, f"{{{xades_ns}}}IssuerSerialV2")
            iname = etree.SubElement(iser_v2, f"{{{ds_ns}}}X509IssuerName")
            iname.text = cert_obj.issuer.rfc4514_string()
            sn = etree.SubElement(iser_v2, f"{{{ds_ns}}}X509SerialNumber")
            sn.text = str(cert_obj.serial_number)

        if signing_cert_mode in {"v1", "both"}:
            _add_signing_certificate_v1()
        if signing_cert_mode in {"v2", "both"}:
            _add_signing_certificate_v2()

        # Optional SignaturePolicyIdentifier for validators that enforce explicit policy linkage.
        if policy_cfg["enabled"]:
            spi = etree.SubElement(ssp, f"{{{xades_ns}}}SignaturePolicyIdentifier")
            spid = etree.SubElement(spi, f"{{{xades_ns}}}SignaturePolicyId")
            sig_policy_id = etree.SubElement(spid, f"{{{xades_ns}}}SigPolicyId")
            identifier = etree.SubElement(sig_policy_id, f"{{{xades_ns}}}Identifier")
            identifier.text = str(policy_cfg["id"])
            if str(policy_cfg["name"]).strip():
                description = etree.SubElement(sig_policy_id, f"{{{xades_ns}}}Description")
                description.text = str(policy_cfg["name"]).strip()
            sph = etree.SubElement(spid, f"{{{xades_ns}}}SigPolicyHash")
            dm_policy = etree.SubElement(sph, f"{{{ds_ns}}}DigestMethod")
            dm_policy.set("Algorithm", str(policy_cfg["hash_algo"]))
            dv_policy = etree.SubElement(sph, f"{{{ds_ns}}}DigestValue")
            dv_policy.text = str(policy_cfg["hash"])

        # Add a reference to SignedProperties into ds:SignedInfo with required Type
        signed_info = sig_root.find(f".//{{{ds_ns}}}SignedInfo")
        if signed_info is None:
            raise ValueError("XMLDSig SignedInfo not found for XAdES reference insertion.")
        ref = etree.SubElement(signed_info, f"{{{ds_ns}}}Reference")
        ref.set("URI", f"#{signed_props_id}")
        ref.set("Type", "http://uri.etsi.org/01903#SignedProperties")
        transforms = etree.SubElement(ref, f"{{{ds_ns}}}Transforms")
        t = etree.SubElement(transforms, f"{{{ds_ns}}}Transform")
        t.set("Algorithm", "http://www.w3.org/2001/10/xml-exc-c14n#")
        dm2 = etree.SubElement(ref, f"{{{ds_ns}}}DigestMethod")
        dm2.set("Algorithm", "http://www.w3.org/2001/04/xmlenc#sha256")
        dv2 = etree.SubElement(ref, f"{{{ds_ns}}}DigestValue")

        # Explicitly compute SignedProperties digest to avoid relying on library internals.
        sp_c14n = etree.tostring(sp, method="c14n", exclusive=True, with_comments=False)
        h2 = hashes.Hash(hashes.SHA256())
        h2.update(sp_c14n)
        dv2.text = base64.b64encode(h2.finalize()).decode("utf-8")

        # Optional: reference KeyInfo from SignedInfo for strict validators.
        key_info = sig_root.find(f".//{{{ds_ns}}}KeyInfo")
        if key_info is not None:
            key_info_id = (key_info.get("Id") or "").strip()
            if not key_info_id:
                key_info_id = f"KeyInfo-{uuid.uuid4().hex}"
                key_info.set("Id", key_info_id)

            existing_key_ref = None
            for r in signed_info.findall(f"./{{{ds_ns}}}Reference"):
                if (r.get("Type") or "").strip() == "http://www.w3.org/2000/09/xmldsig#KeyInfo":
                    existing_key_ref = r
                    break

            if existing_key_ref is None and _require_keyinfo_reference():
                key_ref = etree.SubElement(signed_info, f"{{{ds_ns}}}Reference")
                key_ref.set("URI", f"#{key_info_id}")
                key_ref.set("Type", "http://www.w3.org/2000/09/xmldsig#KeyInfo")
                key_ref_transforms = etree.SubElement(key_ref, f"{{{ds_ns}}}Transforms")
                key_ref_t = etree.SubElement(key_ref_transforms, f"{{{ds_ns}}}Transform")
                key_ref_t.set("Algorithm", "http://www.w3.org/2001/10/xml-exc-c14n#")
                key_ref_dm = etree.SubElement(key_ref, f"{{{ds_ns}}}DigestMethod")
                key_ref_dm.set("Algorithm", "http://www.w3.org/2001/04/xmlenc#sha256")
                key_ref_dv = etree.SubElement(key_ref, f"{{{ds_ns}}}DigestValue")

                key_info_c14n = etree.tostring(key_info, method="c14n", exclusive=True, with_comments=False)
                h3 = hashes.Hash(hashes.SHA256())
                h3.update(key_info_c14n)
                key_ref_dv.text = base64.b64encode(h3.finalize()).decode("utf-8")
        elif _require_keyinfo_reference():
            raise ValueError("KeyInfo is missing from signature; cannot create required KeyInfo reference.")

    signer = XMLSigner(method=methods.enveloped, signature_algorithm="rsa-sha256", digest_algorithm="sha256", c14n_algorithm="http://www.w3.org/2001/10/xml-exc-c14n#")
    signer.signature_annotators.insert(0, _xades_annotator)
    signed_root = signer.sign(root, key=key_bytes, cert=cert_chain, always_add_key_value=False)

    # Strict guard: do not mutate after signing; fail if signer placed signature incorrectly.
    ext_content_signed = signed_root.find(f".//{{{ext_ns}}}UBLExtensions/{{{ext_ns}}}UBLExtension/{{{ext_ns}}}ExtensionContent")
    sig_in_ext = ext_content_signed.find(f"./{{{ds_ns}}}Signature") if ext_content_signed is not None else None
    if sig_in_ext is None:
        raise ValueError("Signer did not place ds:Signature inside ext:ExtensionContent; refusing post-sign mutation.")
    outside_sigs = signed_root.xpath(
        "//ds:Signature[not(ancestor::ext:ExtensionContent)]",
        namespaces={"ds": ds_ns, "ext": ext_ns},
    )
    if outside_sigs:
        raise ValueError("Signer produced ds:Signature outside ext:ExtensionContent; refusing post-sign mutation.")

    return etree.tostring(signed_root, encoding="utf-8", xml_declaration=False).decode("utf-8")


def _extract_xmlsig_signature_value(signed_xml: str) -> str:
    try:
        from lxml import etree  # type: ignore
    except Exception as exc:
        raise ValueError(f"XML parser dependency missing: {exc}")
    root = etree.fromstring((signed_xml or "").encode("utf-8"))
    ns = {"ds": "http://www.w3.org/2000/09/xmldsig#"}
    value = root.findtext(".//ds:SignatureValue", namespaces=ns)
    if not value:
        raise ValueError("XMLDSig SignatureValue is missing.")
    return value.strip()


def _zatca_precheck_signed_xml(signed_xml: str, *, cert_pem_bundle: str) -> None:
    """
    ZATCA-oriented pre-submission gate on signed XML:
    - cryptographic verify
    - exactly one signature
    - signature placement under ext:ExtensionContent
    - strict XAdES/reference checks
    - certificate chain presence in KeyInfo
    """
    try:
        from signxml import XMLVerifier  # type: ignore
        from lxml import etree  # type: ignore
    except Exception as exc:
        raise ValueError(f"Signature verification dependencies missing: {exc}")

    root = etree.fromstring((signed_xml or "").encode("utf-8"))
    preflight = ZATCAValidator(signed_xml).validate(include_signature=True)
    if not preflight["valid"]:
        first = (preflight.get("errors") or [{}])[0]
        raise ValueError(f"Preflight validator failed: {first.get('code')} - {first.get('message')}")
    cert_chain = _split_pem_chain(cert_pem_bundle)
    if not cert_chain:
        raise ValueError("Certificate PEM bundle is empty.")
    _assert_minimum_cert_chain(cert_chain)

    # 1) Cryptographic integrity (XMLDSig).
    XMLVerifier().verify(root, x509_cert=cert_chain[0])

    # 2) Exactly one ds:Signature.
    ds_ns = "http://www.w3.org/2000/09/xmldsig#"
    ext_ns = "urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2"
    sigs = root.xpath("//ds:Signature", namespaces={"ds": ds_ns})
    if len(sigs) != 1:
        raise ValueError(f"Signed XML must contain exactly one ds:Signature. Found: {len(sigs)}")

    # 3) Placement: ext:UBLExtensions/ext:UBLExtension/ext:ExtensionContent/ds:Signature
    sig_in_ext = root.xpath(
        "//ext:UBLExtensions/ext:UBLExtension/ext:ExtensionContent/ds:Signature",
        namespaces={"ext": ext_ns, "ds": ds_ns},
    )
    if len(sig_in_ext) != 1:
        raise ValueError("ds:Signature must be placed under ext:UBLExtensions/ext:UBLExtension/ext:ExtensionContent.")

    # 4) Structural/profile/reference checks (includes strict digest/URI checks).
    xades_errors = _validate_xades_structure(signed_xml)
    if xades_errors:
        first = xades_errors[0]
        raise ValueError(f"XAdES precheck failed: {first.get('code')} - {first.get('message')}")

    # 5) Certificate chain embedding check in KeyInfo.
    keyinfo_certs = root.xpath(
        "//ds:KeyInfo/ds:X509Data/ds:X509Certificate",
        namespaces={"ds": ds_ns},
    )
    if len(keyinfo_certs) < 2:
        raise ValueError("Signature KeyInfo must embed at least leaf + intermediate X509Certificate entries.")

    # 6) Chain identity match: embedded leaf/intermediate must match configured chain.
    try:
        configured_fps = [_cert_fingerprint_sha256_from_pem(pem) for pem in cert_chain]
        embedded_fps: list[str] = []
        for cert_el in keyinfo_certs:
            cert_b64 = (cert_el.text or "").strip()
            if not cert_b64:
                continue
            pem = "-----BEGIN CERTIFICATE-----\n" + cert_b64 + "\n-----END CERTIFICATE-----\n"
            embedded_fps.append(_cert_fingerprint_sha256_from_pem(pem))
        if len(embedded_fps) < 2:
            raise ValueError("Signature KeyInfo does not contain parseable leaf + intermediate certificates.")
        if embedded_fps[0] != configured_fps[0] or embedded_fps[1] != configured_fps[1]:
            raise ValueError("Embedded KeyInfo certificate chain does not match configured active certificate chain.")
        if _strict_profile_mode():
            if len(embedded_fps) != len(configured_fps):
                raise ValueError("Strict mode requires embedded KeyInfo certificate chain length to match configured chain.")
            if embedded_fps != configured_fps:
                raise ValueError("Strict mode requires embedded KeyInfo certificate chain order/content to match configured chain.")
    except Exception as exc:
        raise ValueError(f"Certificate chain identity validation failed: {exc}")


def _verify_signed_xml_local(signed_xml: str, *, cert_pem_bundle: str) -> None:
    """
    Local ZATCA-oriented verification before transport.
    """
    try:
        _zatca_precheck_signed_xml(signed_xml, cert_pem_bundle=cert_pem_bundle)
    except Exception as exc:
        raise ValueError(f"Local XML signature verification failed: {exc}")


def _leaf_cert_sha256_fingerprint(cert_pem_bundle: str) -> str:
    try:
        from cryptography import x509  # type: ignore
        from cryptography.hazmat.primitives import serialization  # type: ignore

        chain = _split_pem_chain(cert_pem_bundle)
        if not chain:
            return ""
        cert = x509.load_pem_x509_certificate(chain[0].encode("utf-8"))
        der = cert.public_bytes(serialization.Encoding.DER)
        return hashlib.sha256(der).hexdigest()
    except Exception:
        return ""


def _submit_live(document, *, submission_type: str, idempotency_key: str = "") -> None:
    base_url = os.getenv("ZATCA_API_BASE_URL", "").strip()
    token = os.getenv("ZATCA_API_TOKEN", "").strip()
    if not base_url or not token:
        raise ValueError("ZATCA_API_BASE_URL and ZATCA_API_TOKEN are required for live submission.")
    endpoint = "/clearance" if submission_type == "clearance" else "/reporting"
    url = f"{base_url.rstrip('/')}{endpoint}"
    final_xml = document.zatca_signed_xml or ""
    if not final_xml:
        raise ValueError("Signed XML is required for live submission.")
    cert_pem = getattr(getattr(document, "zatca_certificate", None), "certificate_pem", "") or ""
    cert_chain = _split_pem_chain(cert_pem)
    if not cert_chain:
        raise ValueError("Certificate PEM is required to verify final signed XML before submission.")
    _assert_minimum_cert_chain(cert_chain)

    # Mandatory: verify the exact final payload string that will be submitted.
    _verify_signed_xml_local(final_xml, cert_pem_bundle=cert_pem)
    final_xml_b64 = base64.b64encode(final_xml.encode("utf-8")).decode("utf-8")
    # Defensive roundtrip check to guarantee submitted bytes match verified bytes.
    if base64.b64decode(final_xml_b64.encode("utf-8")).decode("utf-8") != final_xml:
        raise ValueError("Final XML base64 roundtrip mismatch detected; submission aborted.")

    payload = {
        "uuid": document.zatca_uuid,
        "invoiceHash": document.zatca_invoice_hash,
        "signedHash": document.zatca_signature_value,
        "invoice": final_xml_b64,
        "certificateName": getattr(document.zatca_certificate, "name", "") if hasattr(document, "zatca_certificate") else "",
    }
    req = urllib_request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    http_status = None
    response_raw = ""
    parsed_body: dict = {}
    response_headers: dict[str, str] = {}

    def _norm_headers(headers) -> dict[str, str]:
        out: dict[str, str] = {}
        if not headers:
            return out
        try:
            items = headers.items()
        except Exception:
            return out
        for k, v in items:
            out[str(k).lower()] = str(v)
        return out

    def _safe_json(raw: str) -> dict:
        try:
            return json.loads(raw) if raw else {}
        except Exception:
            return {}

    def _extract_value(body: dict, *keys: str) -> str:
        for key in keys:
            val = body.get(key)
            if val is not None and str(val).strip():
                return str(val).strip()
        return ""

    def _classify_result(*, status_code: int | None, body: dict, headers: dict[str, str], sub_type: str):
        provider_request_id = _extract_value(
            body,
            "requestId",
            "requestID",
            "request_id",
            "invoiceRequestId",
            "clearanceRequestId",
            "reportingRequestId",
        )
        provider_correlation_id = (
            headers.get("x-correlation-id")
            or headers.get("correlation-id")
            or headers.get("x-request-id")
            or provider_request_id
            or ""
        )
        provider_status = (
            _extract_value(
                body,
                "status",
                "submissionStatus",
                "clearanceStatus",
                "reportingStatus",
                "processingStatus",
                "result",
            )
            or ("http_" + str(status_code) if status_code is not None else "")
        )
        provider_status_l = provider_status.lower()

        if status_code is None:
            normalized = "failed_retryable"
            retryable = True
        elif status_code >= 500 or status_code == 429:
            normalized = "failed_retryable"
            retryable = True
        elif 400 <= status_code < 500:
            if status_code in {408, 409}:
                normalized = "failed_retryable"
                retryable = True
            else:
                normalized = "rejected"
                retryable = False
        elif provider_status_l in {"rejected", "failed", "error", "invalid"}:
            normalized = "rejected"
            retryable = False
        elif provider_status_l in {"accepted", "queued", "processing", "pending"}:
            normalized = "accepted"
            retryable = False
        elif sub_type == "clearance" and provider_status_l in {"cleared", "clearance_success"}:
            normalized = "cleared"
            retryable = False
        elif sub_type == "reporting" and provider_status_l in {"reported", "reporting_success"}:
            normalized = "reported"
            retryable = False
        elif status_code in {200, 201, 202}:
            normalized = "cleared" if sub_type == "clearance" else "reported"
            retryable = False
        else:
            normalized = "accepted"
            retryable = False

        return {
            "provider_request_id": provider_request_id,
            "provider_correlation_id": provider_correlation_id,
            "provider_status": provider_status,
            "normalized_status": normalized,
            "retryable": retryable,
        }

    try:
        with urllib_request.urlopen(req, timeout=30) as resp:
            http_status = getattr(resp, "status", None)
            response_headers = _norm_headers(getattr(resp, "headers", {}))
            response_raw = resp.read().decode("utf-8")
        parsed_body = _safe_json(response_raw)
    except HTTPError as exc:
        http_status = exc.code
        response_headers = _norm_headers(getattr(exc, "headers", {}))
        detail = exc.read().decode("utf-8") if hasattr(exc, "read") else str(exc)
        response_raw = detail
        parsed_body = _safe_json(detail)
        mapped = _classify_result(
            status_code=http_status, body=parsed_body, headers=response_headers, sub_type=submission_type
        )
        setattr(document, "_zatca_provider_request_id", mapped["provider_request_id"])
        setattr(document, "_zatca_provider_correlation_id", mapped["provider_correlation_id"])
        setattr(document, "_zatca_provider_status", mapped["provider_status"])
        setattr(document, "_zatca_http_status", http_status)
        raise ZatcaSubmissionTransportError(
            message=f"ZATCA HTTP error {exc.code}: {detail}",
            retryable=bool(mapped["retryable"]),
            http_status=http_status,
            parsed_body=parsed_body,
            raw_body=detail,
            response_headers=response_headers,
        )
    except URLError as exc:
        setattr(document, "_zatca_provider_request_id", "")
        setattr(document, "_zatca_provider_correlation_id", "")
        setattr(document, "_zatca_provider_status", "connection_error")
        setattr(document, "_zatca_http_status", http_status)
        raise ZatcaSubmissionTransportError(
            message=f"ZATCA connection error: {exc}",
            retryable=True,
            http_status=http_status,
            parsed_body=parsed_body,
            raw_body=response_raw,
            response_headers=response_headers,
        )
    finally:
        from django.conf import settings as dj_settings

        from sales.zatca_evidence import ZatcaEvidenceIncompleteError, assert_zatca_evidence_bundle_complete

        try:
            cert = getattr(document, "zatca_certificate", None)
            cert_meta = {
                "id": str(getattr(cert, "id", "")) if cert else None,
                "name": getattr(cert, "name", "") if cert else "",
                "activated_at": getattr(cert, "activated_at", None).isoformat() if cert and getattr(cert, "activated_at", None) else None,
                "revoked_at": getattr(cert, "revoked_at", None).isoformat() if cert and getattr(cert, "revoked_at", None) else None,
            }
            cert_pem = (getattr(cert, "certificate_pem", None) or "") if cert else ""
            bundle_kwargs = dict(
                document_type="credit_note" if hasattr(document, "credit_note_number") else "invoice",
                document_id=document.id,
                submission_type=submission_type,
                idempotency_key=idempotency_key or "",
                request_payload=json.dumps(
                    {
                        "request": payload,
                        "artifacts": {
                            "zatca_uuid": getattr(document, "zatca_uuid", ""),
                            "previous_hash": getattr(document, "zatca_previous_hash", ""),
                            "invoice_hash": getattr(document, "zatca_invoice_hash", ""),
                            "qr": getattr(document, "qr_code_text", ""),
                            "unsigned_ubl_xml": getattr(document, "zatca_xml", ""),
                            "canonical_xml": getattr(document, "zatca_canonical_xml", ""),
                            "signed_xml": getattr(document, "zatca_signed_xml", ""),
                            "signature_value": getattr(document, "zatca_signature_value", ""),
                            "certificate": cert_meta,
                        },
                        "timestamps": {"submitted_at": timezone.now().isoformat()},
                    }
                ),
                response_payload=json.dumps({"raw": response_raw, "parsed": parsed_body}),
                http_status=http_status,
                reference=str(
                    parsed_body.get("requestId")
                    or parsed_body.get("requestID")
                    or parsed_body.get("request_id")
                    or ""
                ),
            )
            if cert_pem:
                bundle_kwargs["certificate_sha256"] = _leaf_cert_sha256_fingerprint(cert_pem)
            bundle_kwargs["api_request_headers"] = {"Content-Type": "application/json", "Accept": "application/json"}
            bundle_kwargs["api_response_headers"] = dict(response_headers or {})
            bundle = ZatcaEvidenceBundle.objects.create(**bundle_kwargs)
            strict = getattr(dj_settings, "ZATCA_STRICT_EVIDENCE_COMPLETENESS", not dj_settings.DEBUG)
            if strict and http_status is not None and 200 <= int(http_status) < 300:
                assert_zatca_evidence_bundle_complete(bundle)
        except ZatcaEvidenceIncompleteError:
            raise
        except Exception:
            pass

    mapped = _classify_result(
        status_code=http_status, body=parsed_body, headers=response_headers, sub_type=submission_type
    )
    # Persist parsed transport metadata for caller/outbox log mapping.
    setattr(document, "_zatca_provider_request_id", mapped["provider_request_id"])
    setattr(document, "_zatca_provider_correlation_id", mapped["provider_correlation_id"])
    setattr(document, "_zatca_provider_status", mapped["provider_status"])
    setattr(document, "_zatca_http_status", http_status)
    document.zatca_submission_reference = (
        mapped["provider_request_id"] or mapped["provider_correlation_id"] or ""
    )
    if mapped["normalized_status"] in {"cleared", "reported"}:
        apply_document_zatca_submission_status(document, "cleared" if submission_type == "clearance" else "reported")
        document.zatca_cleared_at = timezone.now()
    elif mapped["normalized_status"] == "rejected":
        apply_document_zatca_submission_status(document, "rejected")
        document.zatca_submission_error = (
            _extract_value(parsed_body, "message", "errorMessage", "error", "details") or "Rejected by ZATCA"
        )
        raise ZatcaSubmissionTransportError(
            message=document.zatca_submission_error,
            retryable=False,
            http_status=http_status,
            parsed_body=parsed_body,
            raw_body=response_raw,
            response_headers=response_headers,
        )
    else:
        apply_document_zatca_submission_status(document, "submitted")

