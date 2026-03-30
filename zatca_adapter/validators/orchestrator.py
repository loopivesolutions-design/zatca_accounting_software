from .business_rules import BusinessRulesValidator
from .profile_validation import ProfileValidator
from .signature_validation import SignatureValidator
from .structural import StructuralValidator
from .tax_validation import TaxValidator


class ZATCAValidator:
    def __init__(self, xml_string: str):
        self.xml_string = xml_string

    def validate(self, *, include_signature: bool = False) -> dict:
        try:
            from lxml import etree  # type: ignore
        except Exception as exc:
            return {
                "valid": False,
                "errors": [{"code": "ZATCA-VAL-000", "message": f"XML parser dependency missing: {exc}", "xpath": ""}],
            }

        try:
            root = etree.fromstring((self.xml_string or "").encode("utf-8"))
        except Exception as exc:
            return {
                "valid": False,
                "errors": [{"code": "ZATCA-VAL-001", "message": f"Invalid XML: {exc}", "xpath": ""}],
            }

        validators = [
            StructuralValidator(root),
            TaxValidator(root),
            BusinessRulesValidator(root),
            ProfileValidator(root),
        ]
        if include_signature:
            validators.append(SignatureValidator(root))

        errors: list[dict] = []
        for v in validators:
            errors.extend(v.validate())
        return {"valid": len(errors) == 0, "errors": errors}

