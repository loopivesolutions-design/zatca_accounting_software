"""
Deterministic ZATCA submission status transitions (document-level).

Terminal states are immutable: no backward transitions, no overwrites.
"""

from __future__ import annotations

TERMINAL_ZATCA_SUBMISSION_STATUSES = frozenset({"cleared", "reported", "rejected", "failed_final"})

# Allowed transitions: from_status -> set(new_status)
_ALLOWED: dict[str, frozenset[str]] = {
    "not_submitted": frozenset({"signed", "submitted"}),
    "signed": frozenset({"submitted", "cleared", "reported", "rejected", "failed_final"}),
    "submitted": frozenset({"cleared", "reported", "rejected", "retrying", "failed_final"}),
    "retrying": frozenset({"submitted", "cleared", "reported", "rejected", "failed_final"}),
    "cleared": frozenset(),
    "reported": frozenset(),
    "rejected": frozenset(),
    "failed_final": frozenset(),
}


def normalize_zatca_submission_status(status: str) -> str:
    s = (status or "").strip()
    if s == "failed":
        return "failed_final"
    if s == "pending":
        return "submitted"
    return s


def assert_zatca_submission_transition(*, old_status: str, new_status: str, allow_retry_from_terminal: bool = False) -> None:
    """
    Raises ValueError if transition is illegal.
    """
    old = normalize_zatca_submission_status(old_status)
    new = normalize_zatca_submission_status(new_status)
    if old == new:
        return
    if old in TERMINAL_ZATCA_SUBMISSION_STATUSES and not allow_retry_from_terminal:
        raise ValueError(f"ZATCA submission status is terminal ('{old}') and cannot transition to '{new}'.")
    allowed = _ALLOWED.get(old, frozenset())
    if new not in allowed:
        raise ValueError(f"Illegal ZATCA submission transition: '{old}' -> '{new}'.")
