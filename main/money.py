import os
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings


MONEY_QUANT = Decimal("0.01")


def get_vat_rounding_strategy() -> str:
    # "line" (recommended) or "invoice"
    strategy = getattr(settings, "VAT_ROUNDING_STRATEGY", None) or os.getenv("VAT_ROUNDING_STRATEGY", "line")
    strategy = str(strategy).strip().lower()
    return strategy if strategy in {"line", "invoice"} else "line"


def money(amount: Decimal | int | str | None) -> Decimal:
    value = Decimal("0") if amount is None else (amount if isinstance(amount, Decimal) else Decimal(str(amount)))
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def vat_amount(base_amount: Decimal, rate_percent: Decimal, *, strategy: str | None = None) -> Decimal:
    rate = Decimal("0") if rate_percent is None else (rate_percent if isinstance(rate_percent, Decimal) else Decimal(str(rate_percent)))
    raw = (base_amount * rate) / Decimal("100")
    strat = (strategy or get_vat_rounding_strategy()).strip().lower()
    if strat == "line":
        return money(raw)
    return raw  # invoice-level rounding happens at document totals

