"""ZATCA evidence bundle completeness checks (live submit audit trail)."""


class ZatcaEvidenceIncompleteError(RuntimeError):
    """Raised when a persisted evidence bundle is missing mandatory audit fields."""


def assert_zatca_evidence_bundle_complete(bundle) -> None:
    if bundle.is_complete():
        return
    raise ZatcaEvidenceIncompleteError(
        "ZATCA evidence bundle is incomplete (require request/response payloads, http_status, "
        "certificate_sha256, and API request headers)."
    )
