"""
ZATCA adapter layer (domain boundary).

Golden rule:
- This package MUST NOT import or depend on accounting domain logic.
- Callers (invoicing/accounting orchestration) must enforce period locks, approvals, and posting governance.
"""

