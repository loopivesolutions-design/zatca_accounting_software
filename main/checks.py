import os

from django.conf import settings
from django.core import checks

from main.approval_policy import DEFAULT_APPROVAL_POLICIES
from main.approval_wiring import APPROVAL_SCOPES_WITH_EXECUTORS, is_scope_fully_wired


def _strict_approval_integrity() -> bool:
    env = (os.getenv("STRICT_APPROVAL_POLICY_INTEGRITY", "") or "").strip().lower()
    if env in {"1", "true", "yes", "on"}:
        return True
    if env in {"0", "false", "no", "off"}:
        return False
    return not settings.DEBUG


def _go_live_required_scopes() -> list[str]:
    raw = (os.getenv("GO_LIVE_REQUIRED_APPROVAL_SCOPES", "") or "").strip()
    return [s.strip() for s in raw.split(",") if s.strip()]


@checks.register()
def approval_policies_match_executors(app_configs, **kwargs):
    """
    Every scope with requires_approval=True must have an executor; otherwise maker–checker is a
    false sense of control (approvals could be created but execution returns UNKNOWN_SCOPE).
    """
    if not _strict_approval_integrity():
        return []
    errors = []
    for scope, policy in DEFAULT_APPROVAL_POLICIES.items():
        if not policy.requires_approval:
            continue
        if not is_scope_fully_wired(scope):
            errors.append(
                checks.Error(
                    f"Approval policy requires dual control for scope {scope!r} but "
                    f"execute_approved_action has no executor. Set requires_approval=False until "
                    f"wired, or implement the branch and add the scope to "
                    f"main.approval_wiring.APPROVAL_SCOPES_WITH_EXECUTORS.",
                    id="main.E002",
                )
            )
    return errors


@checks.register()
def go_live_required_approval_scopes_wired(app_configs, **kwargs):
    """Optional gate: GO_LIVE_REQUIRED_APPROVAL_SCOPES must each be fully wired."""
    required = _go_live_required_scopes()
    if not required:
        return []
    errors = []
    for scope in required:
        if scope not in APPROVAL_SCOPES_WITH_EXECUTORS:
            errors.append(
                checks.Error(
                    f"GO_LIVE_REQUIRED_APPROVAL_SCOPES includes {scope!r} but that scope is not "
                    f"in APPROVAL_SCOPES_WITH_EXECUTORS (no executor).",
                    id="main.E003",
                )
            )
    return errors
