"""
Global Decimal defaults: ROUND_HALF_UP and configurable precision for GL / ZATCA consistency.

Call configure_decimal_context() from AccountingConfig.ready().
"""

from __future__ import annotations

import decimal
import os
from decimal import ROUND_HALF_UP


def configure_decimal_context() -> None:
    prec_raw = (os.getenv("DECIMAL_CONTEXT_PREC", "") or "").strip()
    prec = int(prec_raw) if prec_raw.isdigit() else 28
    prec = max(9, min(prec, 999999))
    ctx = decimal.getcontext()
    ctx.prec = prec
    ctx.rounding = ROUND_HALF_UP
