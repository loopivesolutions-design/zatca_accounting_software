from decimal import Decimal, ROUND_HALF_UP


class BaseXMLValidator:
    def __init__(self, root):
        self.root = root
        self.errors: list[dict] = []
        raw_ns = dict(getattr(root, "nsmap", {}) or {})
        self.ns = {k: v for k, v in raw_ns.items() if k}
        if None in raw_ns and raw_ns[None]:
            self.ns["ubl"] = raw_ns[None]

    def add_error(self, code: str, message: str, xpath: str | None = None) -> None:
        self.errors.append(
            {
                "code": code,
                "message": message,
                "xpath": xpath or "",
            }
        )

    @staticmethod
    def money(value) -> Decimal:
        return Decimal(str(value or "0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

