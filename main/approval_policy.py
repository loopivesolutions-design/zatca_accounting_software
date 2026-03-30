from dataclasses import dataclass


@dataclass(frozen=True)
class ApprovalPolicy:
    operation: str
    requires_approval: bool
    min_approvers: int = 1


DEFAULT_APPROVAL_POLICIES: dict[str, ApprovalPolicy] = {
    "sales.invoice.post": ApprovalPolicy("sales.invoice.post", True, 1),
    "sales.credit_note.post": ApprovalPolicy("sales.credit_note.post", True, 1),
    "purchases.bill.post": ApprovalPolicy("purchases.bill.post", True, 1),
    "purchases.supplier_payment.create": ApprovalPolicy("purchases.supplier_payment.create", True, 1),
    "accounting.journal_entry.post": ApprovalPolicy("accounting.journal_entry.post", True, 1),
    "accounting.journal_entry.reverse": ApprovalPolicy("accounting.journal_entry.reverse", True, 1),
    "accounting.period.reopen": ApprovalPolicy("accounting.period.reopen", True, 1),
    # Dormant until view + execute_approved_action branch exist (requires_approval=True without
    # wiring is a control-design failure: policy implies dual control that cannot run).
    "sales.invoice.void": ApprovalPolicy("sales.invoice.void", False, 1),
    "accounting.coa.modify": ApprovalPolicy("accounting.coa.modify", False, 1),
    "accounting.tax_config.change": ApprovalPolicy("accounting.tax_config.change", False, 1),
    "sales.zatca.config.change": ApprovalPolicy("sales.zatca.config.change", False, 1),
    "sales.payment.writeoff": ApprovalPolicy("sales.payment.writeoff", False, 1),
    "sales.customer_payment.create": ApprovalPolicy("sales.customer_payment.create", True, 1),
    "sales.customer_payment.update": ApprovalPolicy("sales.customer_payment.update", True, 1),
    "sales.customer_refund.create": ApprovalPolicy("sales.customer_refund.create", True, 1),
    "sales.customer_refund.update": ApprovalPolicy("sales.customer_refund.update", True, 1),
    # Statutory submission — dual control when MAKER_CHECKER_ENABLED=true
    "sales.invoice.zatca.submit": ApprovalPolicy("sales.invoice.zatca.submit", True, 1),
    "sales.credit_note.zatca.submit": ApprovalPolicy("sales.credit_note.zatca.submit", True, 1),
    "products.inventory_adjustment.post": ApprovalPolicy("products.inventory_adjustment.post", True, 1),
}


def get_approval_policy(scope: str) -> ApprovalPolicy:
    return DEFAULT_APPROVAL_POLICIES.get(scope, ApprovalPolicy(scope, False, 1))

