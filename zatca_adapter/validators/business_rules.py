import re
import uuid

from .base import BaseXMLValidator


class BusinessRulesValidator(BaseXMLValidator):
    def validate(self) -> list[dict]:
        uuid_val = (self.root.findtext("cbc:UUID", namespaces=self.ns) or "").strip()
        if not uuid_val:
            self.add_error("ZATCA-BR-001", "UUID is required", xpath="cbc:UUID")
        else:
            try:
                uuid.UUID(uuid_val)
            except Exception:
                self.add_error("ZATCA-BR-002", "UUID must be valid", xpath="cbc:UUID")

        inv_type = self.root.find("cbc:InvoiceTypeCode", namespaces=self.ns)
        if inv_type is None or not (inv_type.get("name") or "").strip():
            self.add_error("ZATCA-BR-003", "InvoiceTypeCode must include name attribute", xpath="cbc:InvoiceTypeCode")

        seller_vat_node = self.root.find(
            ".//cac:AccountingSupplierParty/cac:Party/cac:PartyTaxScheme/cbc:CompanyID",
            namespaces=self.ns,
        )
        seller_vat = (seller_vat_node.text or "").strip() if seller_vat_node is not None else ""
        if seller_vat_node is None:
            self.add_error(
                "ZATCA-BR-004",
                "Seller VAT CompanyID is required",
                xpath=".//cac:AccountingSupplierParty/cac:Party/cac:PartyTaxScheme/cbc:CompanyID",
            )
        else:
            if (seller_vat_node.get("schemeID") or "").strip() != "VAT":
                self.add_error("ZATCA-BR-005", "Seller VAT must use schemeID='VAT'", xpath=".//cbc:CompanyID")
            if not re.fullmatch(r"[0-9]{15}", seller_vat):
                self.add_error("ZATCA-BR-006", "Seller VAT must be 15 digits", xpath=".//cbc:CompanyID")

        tax_categories = self.root.xpath(".//cac:TaxCategory", namespaces=self.ns)
        for idx, tc in enumerate(tax_categories, start=1):
            pct = (tc.findtext("cbc:Percent", namespaces=self.ns) or "").strip()
            if not pct:
                self.add_error(
                    "ZATCA-BR-007",
                    "TaxCategory must include Percent",
                    xpath=f".//cac:TaxCategory[{idx}]/cbc:Percent",
                )

        return self.errors

