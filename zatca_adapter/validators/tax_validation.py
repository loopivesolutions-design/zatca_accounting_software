from decimal import Decimal

from .base import BaseXMLValidator


class TaxValidator(BaseXMLValidator):
    def validate(self) -> list[dict]:
        top_tax = self.root.findtext("./cac:TaxTotal/cbc:TaxAmount", namespaces=self.ns)
        if top_tax is None:
            self.add_error("ZATCA-TAX-001", "Missing top-level TaxTotal/TaxAmount", xpath="./cac:TaxTotal/cbc:TaxAmount")
            return self.errors

        try:
            top_tax_dec = self.money(top_tax)
        except Exception:
            self.add_error("ZATCA-TAX-002", "Invalid top-level TaxTotal/TaxAmount", xpath="./cac:TaxTotal/cbc:TaxAmount")
            return self.errors

        sub_tax_nodes = self.root.xpath("./cac:TaxTotal/cac:TaxSubtotal/cbc:TaxAmount", namespaces=self.ns)
        sub_total = Decimal("0.00")
        for idx, node in enumerate(sub_tax_nodes, start=1):
            try:
                sub_total += self.money((node.text or "").strip())
            except Exception:
                self.add_error(
                    "ZATCA-TAX-003",
                    "Invalid TaxSubtotal TaxAmount",
                    xpath=f"./cac:TaxTotal/cac:TaxSubtotal[{idx}]/cbc:TaxAmount",
                )
        sub_total = self.money(sub_total)
        if sub_total != top_tax_dec:
            self.add_error(
                "ZATCA-TAX-004",
                f"TaxTotal mismatch: top={top_tax_dec} subtotal_sum={sub_total}",
                xpath="./cac:TaxTotal",
            )

        tax_excl = self.root.findtext(".//cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount", namespaces=self.ns)
        tax_incl = self.root.findtext(".//cac:LegalMonetaryTotal/cbc:TaxInclusiveAmount", namespaces=self.ns)
        payable = self.root.findtext(".//cac:LegalMonetaryTotal/cbc:PayableAmount", namespaces=self.ns)
        try:
            excl_dec = self.money(tax_excl)
            incl_dec = self.money(tax_incl)
            pay_dec = self.money(payable)
            if self.money(excl_dec + top_tax_dec) != incl_dec:
                self.add_error(
                    "ZATCA-TAX-005",
                    "TaxInclusiveAmount must equal TaxExclusiveAmount + TaxTotal",
                    xpath=".//cac:LegalMonetaryTotal",
                )
            if pay_dec != incl_dec:
                self.add_error(
                    "ZATCA-TAX-006",
                    "PayableAmount must equal TaxInclusiveAmount",
                    xpath=".//cac:LegalMonetaryTotal/cbc:PayableAmount",
                )
        except Exception:
            self.add_error("ZATCA-TAX-007", "Invalid LegalMonetaryTotal amount fields", xpath=".//cac:LegalMonetaryTotal")

        # Currency consistency check across key monetary fields.
        currency_ids = []
        amount_nodes = self.root.xpath(
            ".//cbc:TaxAmount | .//cbc:TaxableAmount | .//cbc:LineExtensionAmount | .//cbc:TaxExclusiveAmount | .//cbc:TaxInclusiveAmount | .//cbc:PayableAmount",
            namespaces=self.ns,
        )
        for node in amount_nodes:
            cid = (node.get("currencyID") or "").strip()
            if cid:
                currency_ids.append(cid)
        if currency_ids and len(set(currency_ids)) != 1:
            self.add_error("ZATCA-TAX-008", "currencyID mismatch across monetary amounts", xpath=".//cbc:*[@currencyID]")

        return self.errors

