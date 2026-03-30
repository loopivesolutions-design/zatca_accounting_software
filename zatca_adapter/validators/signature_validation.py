from .base import BaseXMLValidator


class SignatureValidator(BaseXMLValidator):
    def validate(self) -> list[dict]:
        if self.root.find(".//ds:Signature", namespaces=self.ns) is None:
            self.add_error("ZATCA-SIG-001", "Missing ds:Signature", xpath=".//ds:Signature")

        pih = self.root.find(
            ".//cac:AdditionalDocumentReference[cbc:ID='PIH']/cac:Attachment/cbc:EmbeddedDocumentBinaryObject",
            namespaces=self.ns,
        )
        if pih is None or not (pih.text or "").strip():
            self.add_error(
                "ZATCA-SIG-002",
                "Missing PIH embedded hash reference",
                xpath=".//cac:AdditionalDocumentReference[cbc:ID='PIH']",
            )

        icv = self.root.find(
            ".//cac:AdditionalDocumentReference[cbc:ID='ICV']/cbc:UUID",
            namespaces=self.ns,
        )
        if icv is None or not (icv.text or "").strip():
            self.add_error(
                "ZATCA-SIG-003",
                "Missing ICV reference",
                xpath=".//cac:AdditionalDocumentReference[cbc:ID='ICV']/cbc:UUID",
            )
        return self.errors

