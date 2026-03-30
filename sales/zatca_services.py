"""
Backwards-compatibility shim.

ZATCA logic is now hosted in `zatca_adapter.services` to enforce a clean domain boundary:
- `zatca_adapter` MUST NOT import or depend on `accounting.*`
- period locks, approvals, and posting governance live in the orchestration layer (views/services)
"""

from zatca_adapter.services import (  # noqa: F401
    ZatcaSubmissionTransportError,
    ZatcaValidationError,
    compute_zatca_chain_invoice_hash,
    prepare_zatca_artifacts,
    submit_to_zatca,
    validate_xml_against_xsd,
    validate_zatca_document,
    verify_document_hash,
)

