from django.conf import settings

from .base import BaseXMLValidator


class ProfileValidator(BaseXMLValidator):
    def validate(self) -> list[dict]:
        xml_profile = (self.root.findtext("cbc:ProfileID", namespaces=self.ns) or "").strip()
        expected = str(getattr(settings, "ZATCA_PROFILE_ID", "") or "").strip()
        if expected and xml_profile and xml_profile != expected:
            self.add_error(
                "ZATCA-PROFILE-001",
                f"ProfileID mismatch: expected '{expected}' got '{xml_profile}'",
                xpath="cbc:ProfileID",
            )
        return self.errors

