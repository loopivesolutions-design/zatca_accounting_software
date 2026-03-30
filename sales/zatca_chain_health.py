"""
Block sales invoice / credit note posting when the last hash-chain verification failed.
"""

from __future__ import annotations

from django.conf import settings


def assert_zatca_hash_chain_allows_new_sales_posting() -> None:
    if not getattr(settings, "ZATCA_BLOCK_POST_ON_CHAIN_FAILURE", False):
        return
    from sales.models import ZatcaHashChainAnchor

    anchor = ZatcaHashChainAnchor.get_solo()
    if not anchor.chain_integrity_ok:
        raise ValueError(
            "ZATCA hash chain integrity check failed or has not been run successfully. "
            "Run `python manage.py verify_zatca_hash_chain` and fix mismatches before posting."
        )
