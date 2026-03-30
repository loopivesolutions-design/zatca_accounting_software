from .base import BaseXMLValidator


class StructuralValidator(BaseXMLValidator):
    def validate(self) -> list[dict]:
        required_nodes = [
            ("cbc:ProfileID", "ZATCA-STR-001", "Missing ProfileID"),
            ("cbc:UUID", "ZATCA-STR-002", "Missing UUID"),
            ("cbc:InvoiceTypeCode", "ZATCA-STR-003", "Missing InvoiceTypeCode"),
            ("cbc:IssueDate", "ZATCA-STR-004", "Missing IssueDate"),
            (".//cac:AccountingSupplierParty", "ZATCA-STR-005", "Missing AccountingSupplierParty"),
            (".//cac:TaxTotal", "ZATCA-STR-006", "Missing TaxTotal"),
            (".//cac:LegalMonetaryTotal", "ZATCA-STR-007", "Missing LegalMonetaryTotal"),
        ]
        for xpath, code, message in required_nodes:
            if self.root.find(xpath, namespaces=self.ns) is None:
                self.add_error(code, message, xpath=xpath)
        return self.errors

