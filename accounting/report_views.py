from decimal import Decimal

from django.db.models import Q, Sum
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from main.pagination import CustomPagination
from main.management.commands.create_groups_and_permissions import IsAdmin
from accounting.models import Account, JournalEntryLine


class StatementOfAccountAPI(APIView):
    """
    GET /accounting/reports/statement-of-account/

    Query params:
      - account: uuid (preferred) OR account_code: string
      - date_from, date_to: YYYY-MM-DD
      - search: matches JE reference, description, line description
      - source: optional free-text filter (matches derived source label)
      - export: csv (optional)
    """

    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        account_id = request.query_params.get("account")
        account_code = request.query_params.get("account_code")
        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")
        search = (request.query_params.get("search") or "").strip()
        source_filter = (request.query_params.get("source") or "").strip()
        export = (request.query_params.get("export") or "").strip().lower()

        account = None
        if account_id:
            account = Account.objects.filter(pk=account_id, is_deleted=False).first()
        elif account_code:
            account = Account.objects.filter(code=str(account_code).strip(), is_deleted=False).first()

        if not account:
            return Response(
                {"error": "ACCOUNT_REQUIRED", "message": "Provide 'account' (uuid) or 'account_code'."},
                status=400,
            )

        lines = (
            JournalEntryLine.objects.filter(
                is_deleted=False,
                account_id=account.id,
                journal_entry__is_deleted=False,
                journal_entry__status="posted",
            )
            .select_related("journal_entry", "account")
            .order_by("journal_entry__date", "journal_entry__reference", "line_order", "created_at")
        )

        if date_from:
            lines = lines.filter(journal_entry__date__gte=date_from)
        if date_to:
            lines = lines.filter(journal_entry__date__lte=date_to)

        if search:
            lines = lines.filter(
                Q(journal_entry__reference__icontains=search)
                | Q(journal_entry__description__icontains=search)
                | Q(description__icontains=search)
            )

        # Opening balance (sum of all posted lines before date_from)
        opening_balance = Decimal("0")
        if date_from:
            agg = JournalEntryLine.objects.filter(
                is_deleted=False,
                account_id=account.id,
                journal_entry__is_deleted=False,
                journal_entry__status="posted",
                journal_entry__date__lt=date_from,
            ).aggregate(
                debit=Sum("debit"),
                credit=Sum("credit"),
            )
            opening_balance = (agg.get("debit") or Decimal("0")) - (agg.get("credit") or Decimal("0"))

        # Build rows (running balance requires sequential processing),
        # then paginate the computed rows for consistent output.
        running = opening_balance
        rows = []
        for line in lines:
            source_label = self._derive_source(line)
            if source_filter and source_filter.lower() not in source_label.lower():
                continue

            debit = line.debit or Decimal("0")
            credit = line.credit or Decimal("0")
            running = running + debit - credit
            rows.append(
                {
                    "date": str(line.journal_entry.date),
                    "account_id": str(account.id),
                    "account": f"{account.code} - {account.name}",
                    "serial_number": line.journal_entry.reference or "",
                    "source": source_label,
                    "activity": line.description or line.journal_entry.description or "",
                    "debit": str(debit),
                    "credit": str(credit),
                    "balance": str(running),
                }
            )

        paginator = CustomPagination()
        page = paginator.paginate_queryset(rows, request)
        results = list(page)

        payload = {
            "account": {"id": str(account.id), "code": account.code, "name": account.name},
            "opening_balance": str(opening_balance),
            "results": results,
        }

        if export == "csv":
            # Simple CSV export (frontend can convert to PDF if needed)
            import csv
            from io import StringIO
            from django.http import HttpResponse

            buf = StringIO()
            writer = csv.writer(buf)
            writer.writerow(["Date", "Account", "Serial Number", "Source", "Activity", "Debit", "Credit", "Balance"])
            for row in results:
                writer.writerow(
                    [
                        row["date"],
                        row["account"],
                        row["serial_number"],
                        row["source"],
                        row["activity"],
                        row["debit"],
                        row["credit"],
                        row["balance"],
                    ]
                )
            resp = HttpResponse(buf.getvalue(), content_type="text/csv")
            resp["Content-Disposition"] = f'attachment; filename="statement_of_account_{account.code}.csv"'
            return resp

        return paginator.get_paginated_response(payload)

    def _derive_source(self, line: JournalEntryLine) -> str:
        je = line.journal_entry
        # Try to infer source from known related objects
        if hasattr(je, "inventory_adjustment"):
            return "Inventory Adjustment"
        if hasattr(je, "purchase_bill"):
            return "Bill"
        if hasattr(je, "sales_invoice"):
            return "Invoice"
        if hasattr(je, "sales_credit_note"):
            return "Credit Note"
        return "Journal Entry"


class ProfitAndLossAPI(APIView):
    """
    GET /accounting/reports/profit-and-loss/

    Query params:
      - date_from, date_to: YYYY-MM-DD (optional; defaults to all time)
      - group_by: "none" | "month" (default "none")
      - layout: "vertical" | "horizontal" (default "vertical")
      - export: "csv" (optional)
    """

    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")
        group_by = (request.query_params.get("group_by") or "none").strip().lower()
        layout = (request.query_params.get("layout") or "vertical").strip().lower()
        export = (request.query_params.get("export") or "").strip().lower()

        # P&L accounts: revenue + expense (exclude deleted/archived)
        accounts = (
            Account.objects.filter(is_deleted=False, is_archived=False, account_type__in=["revenue", "expense"])
            .select_related("parent")
            .order_by("code")
        )

        # Pre-calc buckets (columns)
        columns = [{"key": "total", "label": "Total"}]
        month_ranges = []
        if group_by == "month" and date_from and date_to:
            from datetime import date as date_cls
            from calendar import monthrange

            y, m, d = [int(x) for x in date_from.split("-")]
            cur = date_cls(y, m, 1)
            y2, m2, d2 = [int(x) for x in date_to.split("-")]
            end = date_cls(y2, m2, 1)
            while cur <= end:
                last_day = monthrange(cur.year, cur.month)[1]
                start = cur
                finish = date_cls(cur.year, cur.month, last_day)
                key = f"{cur.year}-{cur.month:02d}"
                columns.append({"key": key, "label": start.strftime("%b %Y")})
                month_ranges.append((key, str(start), str(finish)))
                # next month
                if cur.month == 12:
                    cur = date_cls(cur.year + 1, 1, 1)
                else:
                    cur = date_cls(cur.year, cur.month + 1, 1)

        # Sum journal lines for the period(s)
        def sum_for_range(range_from=None, range_to=None):
            qs = JournalEntryLine.objects.filter(
                is_deleted=False,
                journal_entry__is_deleted=False,
                journal_entry__status="posted",
                account__is_deleted=False,
                account__is_archived=False,
                account__account_type__in=["revenue", "expense"],
            )
            if range_from:
                qs = qs.filter(journal_entry__date__gte=range_from)
            if range_to:
                qs = qs.filter(journal_entry__date__lte=range_to)
            return {
                row["account_id"]: (row["credit"] or Decimal("0")) - (row["debit"] or Decimal("0"))
                for row in qs.values("account_id").annotate(debit=Sum("debit"), credit=Sum("credit"))
            }

        totals_map = sum_for_range(date_from, date_to)
        month_maps = {}
        for key, start, finish in month_ranges:
            month_maps[key] = sum_for_range(start, finish)

        # Group helpers: use 2-digit CoA section if present (41/51/52/53)
        def section_key(acc: Account) -> str:
            code2 = (acc.code or "")[:2]
            if code2 in {"41", "42", "51", "52", "53"}:
                return code2
            # fallback: top-level by first digit
            return (acc.code or "")[:1]

        section_titles = {
            "41": "Income",
            "42": "Other Income",
            "51": "Cost of Sales",
            "52": "Operating Expenses",
            "53": "Non-Operating Expenses",
        }

        # Build rows similar to UI (section headers, account rows, section totals, net)
        sections = {}
        for acc in accounts:
            key = section_key(acc)
            sections.setdefault(key, []).append(acc)

        def value_for(acc_id, col_key):
            if col_key == "total":
                return totals_map.get(acc_id, Decimal("0"))
            return month_maps.get(col_key, {}).get(acc_id, Decimal("0"))

        rows = []
        total_income = {c["key"]: Decimal("0") for c in columns}
        total_expense = {c["key"]: Decimal("0") for c in columns}

        for sec_code in ["41", "42", "51", "52", "53"]:
            if sec_code not in sections:
                continue
            sec_accounts = sections[sec_code]
            rows.append({"type": "section", "code": sec_code, "label": f"{sec_code} {section_titles.get(sec_code, '')}".strip()})
            sec_totals = {c["key"]: Decimal("0") for c in columns}
            for acc in sec_accounts:
                acc_vals = {c["key"]: value_for(acc.id, c["key"]) for c in columns}
                for k, v in acc_vals.items():
                    sec_totals[k] += v
                    if acc.account_type == "revenue":
                        total_income[k] += v
                    else:
                        total_expense[k] += v
                rows.append(
                    {
                        "type": "account",
                        "account_id": str(acc.id),
                        "code": acc.code,
                        "name": acc.name,
                        "values": {k: str(acc_vals[k]) for k in acc_vals},
                    }
                )
            rows.append(
                {
                    "type": "section_total",
                    "code": sec_code,
                    "label": f"Total {section_titles.get(sec_code, sec_code)}",
                    "values": {k: str(sec_totals[k]) for k in sec_totals},
                }
            )

        net = {c["key"]: total_income[c["key"]] + total_expense[c["key"]] for c in columns}  # expenses negative already
        rows.append({"type": "net", "label": "Net Profit/Loss", "values": {k: str(net[k]) for k in net}})

        payload = {
            "meta": {
                "date_from": date_from,
                "date_to": date_to,
                "group_by": group_by,
                "layout": layout,
            },
            "columns": columns,
            "rows": rows,
        }

        if export == "csv":
            import csv
            from io import StringIO
            from django.http import HttpResponse

            buf = StringIO()
            writer = csv.writer(buf)
            header = ["Section/Account"] + [c["label"] for c in columns]
            writer.writerow(header)
            for r in rows:
                if r["type"] == "account":
                    label = f'{r["code"]} {r["name"]}'
                    writer.writerow([label] + [r["values"][c["key"]] for c in columns])
                elif r["type"] in {"section", "section_total", "net"}:
                    vals = r.get("values")
                    if vals:
                        writer.writerow([r["label"]] + [vals[c["key"]] for c in columns])
                    else:
                        writer.writerow([r["label"]])
            resp = HttpResponse(buf.getvalue(), content_type="text/csv")
            resp["Content-Disposition"] = 'attachment; filename="profit_and_loss.csv"'
            return resp

        # For horizontal layout, frontend can render from same rows/columns.
        return Response(payload)


class GeneralLedgerAPI(APIView):
    """
    GET /accounting/reports/general-ledger/

    Query params:
      - date_from, date_to: YYYY-MM-DD (optional)
      - account: uuid (optional)
      - account_code: string (optional)
      - source: string (optional)
      - search: string (optional; reference/account/description)
      - export: csv (optional)
    """

    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request):
        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")
        account_id = request.query_params.get("account")
        account_code = request.query_params.get("account_code")
        source_filter = (request.query_params.get("source") or "").strip()
        search = (request.query_params.get("search") or "").strip()
        export = (request.query_params.get("export") or "").strip().lower()

        lines = (
            JournalEntryLine.objects.filter(
                is_deleted=False,
                journal_entry__is_deleted=False,
                journal_entry__status="posted",
                account__is_deleted=False,
            )
            .select_related("journal_entry", "account")
            .order_by("journal_entry__date", "journal_entry__reference", "account__code", "line_order", "created_at")
        )

        if date_from:
            lines = lines.filter(journal_entry__date__gte=date_from)
        if date_to:
            lines = lines.filter(journal_entry__date__lte=date_to)
        if account_id:
            lines = lines.filter(account_id=account_id)
        if account_code:
            lines = lines.filter(account__code__icontains=account_code.strip())
        if search:
            lines = lines.filter(
                Q(journal_entry__reference__icontains=search)
                | Q(account__name__icontains=search)
                | Q(account__code__icontains=search)
                | Q(description__icontains=search)
                | Q(journal_entry__description__icontains=search)
            )

        # Running balance per account to match ledger style
        running_by_account = {}
        rows = []
        for line in lines:
            source_label = self._derive_source(line)
            if source_filter and source_filter.lower() not in source_label.lower():
                continue

            acc_id = str(line.account_id)
            current = running_by_account.get(acc_id, Decimal("0"))
            debit = line.debit or Decimal("0")
            credit = line.credit or Decimal("0")
            current = current + debit - credit
            running_by_account[acc_id] = current

            rows.append(
                {
                    "journal_id": line.journal_entry.reference or "",
                    "source": source_label,
                    "date": str(line.journal_entry.date),
                    "account_id": acc_id,
                    "account": f"{line.account.code} - {line.account.name}",
                    "description": line.description or line.journal_entry.description or "",
                    "journal_note": line.journal_entry.description or "",
                    "debit": str(debit),
                    "credit": str(credit),
                    "balance": str(current),
                }
            )

        paginator = CustomPagination()
        page = paginator.paginate_queryset(rows, request)
        paged_rows = list(page)

        if export == "csv":
            import csv
            from io import StringIO
            from django.http import HttpResponse

            buf = StringIO()
            writer = csv.writer(buf)
            writer.writerow(
                ["Journal ID", "Source", "Date", "Account", "Description", "Journal Note", "Debit SAR", "Credit SAR", "Balance SAR"]
            )
            for row in paged_rows:
                writer.writerow(
                    [
                        row["journal_id"],
                        row["source"],
                        row["date"],
                        row["account"],
                        row["description"],
                        row["journal_note"],
                        row["debit"],
                        row["credit"],
                        row["balance"],
                    ]
                )
            resp = HttpResponse(buf.getvalue(), content_type="text/csv")
            resp["Content-Disposition"] = 'attachment; filename="general_ledger.csv"'
            return resp

        return paginator.get_paginated_response({"results": paged_rows})

    def _derive_source(self, line: JournalEntryLine) -> str:
        je = line.journal_entry
        if hasattr(je, "inventory_adjustment"):
            return "Inventory Adjustment"
        if hasattr(je, "purchase_bill"):
            return "Bill Payment" if "payment" in (je.description or "").lower() else "Bill"
        if hasattr(je, "sales_invoice"):
            return "Invoice"
        if hasattr(je, "sales_credit_note"):
            return "Credit Note"
        return "Journal Entry"

