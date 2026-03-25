from django.core.management.base import BaseCommand
from accounting.models import Account

# ──────────────────────────────────────────────────────────────────────────────
# Tuple fields:
#   code, name, name_ar,
#   account_type   (asset / liability / equity / revenue / expense  or "" for groups)
#   cash_flow_type (cash / operating / investing / financing         or "")
#   parent_code    (None for root accounts)
#   is_locked      (bool — system accounts cannot be deleted/structurally changed)
#   enable_payment (bool)
#   show_in_expense_claim (bool)
#   account_sub_type      (detailed label for "Account Type" column, "" for groups)
#   zatca_mapping  (ZATCA e-invoicing category, "" if not mapped)
#                  choices: vat_output | vat_input | sales_revenue |
#                           accounts_receivable | accounts_payable |
#                           retained_earnings   | cash_and_bank
# ──────────────────────────────────────────────────────────────────────────────

DEFAULT_ACCOUNTS = [

    # ══════════════════════════════════════════════════════════════════════════
    # 1 — ASSETS
    # ══════════════════════════════════════════════════════════════════════════
    #  code    name (EN)                        name (AR)                             type       cft          parent  lock   pay    exp    sub_type                           zatca
    ("1",    "Assets",                        "الأصول",                              "",        "",           None,   True,  False, False, "",                                ""),

    # Current Assets
    ("11",   "Current Assets",               "الأصول المتداولة",                    "asset",   "operating",  "1",    True,  False, False, "",                                ""),
    ("111",  "Cash and Cash Equivalents",    "النقد وما يعادله",                    "asset",   "cash",       "11",   True,  False, False, "Cash and Cash Equivalents",        "cash_and_bank"),
    ("1111", "Undeposited Funds",            "الأموال غير المودعة",                 "asset",   "cash",       "111",  False, True,  False, "Cash and Cash Equivalents",        ""),
    ("1112", "Petty Cash",                   "الصندوق",                             "asset",   "cash",       "111",  False, True,  False, "Cash and Cash Equivalents",        ""),
    ("1113", "Bank Accounts",               "الحسابات البنكية",                    "asset",   "cash",       "111",  True,  True,  False, "Cash and Cash Equivalents",        ""),
    ("112",  "Accounts Receivable",         "حسابات القبض",                        "asset",   "operating",  "11",   False, False, False, "Account Receivable",               "accounts_receivable"),
    ("113",  "Employee Advance",            "سلف الموظفين",                        "asset",   "operating",  "11",   False, True,  False, "Employee Advance",                 ""),
    ("114",  "Prepaid Expenses",            "مصروفات مدفوعة مقدماً",               "asset",   "operating",  "11",   False, False, False, "Prepaid Expenses",                 ""),
    ("115",  "Inventory",                   "المخزون",                             "asset",   "investing",  "11",   True,  False, False, "Inventory",                        ""),
    ("1151", "Primary Warehouse",           "المستودع الرئيسي",                    "asset",   "operating",  "115",  False, False, False, "Inventory",                        ""),
    ("116",  "VAT Receivable",              "ضريبة القيمة المضافة المدخلات",       "asset",   "operating",  "11",   True,  False, False, "Tax Receivable",                   "vat_input"),

    # Non-Current Assets
    ("12",   "Non-Current Assets",          "الأصول غير المتداولة",                "asset",   "operating",  "1",    True,  False, False, "",                                ""),
    ("121",  "Fixed Assets",               "الأصول الثابتة",                      "asset",   "investing",  "12",   True,  False, False, "Property, Plant and Equipment",    ""),
    ("1211", "Furniture and Equipment",    "الأثاث والمعدات",                     "asset",   "investing",  "121",  False, True,  False, "Property, Plant and Equipment",    ""),
    ("1212", "Accumulated Depreciation",   "مجمع الإهلاك",                        "asset",   "investing",  "121",  False, False, False, "Accumulated Depreciation",         ""),

    # ══════════════════════════════════════════════════════════════════════════
    # 2 — LIABILITIES
    # ══════════════════════════════════════════════════════════════════════════
    ("2",    "Liabilities",                  "الالتزامات",                          "",        "",           None,   True,  False, False, "",                                ""),

    # Current Liabilities
    ("21",   "Current Liabilities",         "الالتزامات المتداولة",                "liability", "operating", "2",   True,  False, False, "",                                ""),
    ("211",  "Accounts Payable",            "حسابات الدفع",                        "liability", "operating", "21",  False, False, False, "Accounts Payable",                "accounts_payable"),
    ("2110", "VAT Payable",                 "ضريبة القيمة المضافة المستحقة",       "liability", "operating", "211", False, True,  False, "Tax Payable",                     "vat_output"),
    ("2111", "Zakat Payable",               "الزكاة المستحقة",                     "liability", "operating", "211", False, False, False, "Tax Payable",                     ""),
    ("212",  "Unearned Revenue",            "إيرادات مؤجلة",                       "liability", "operating", "21",  False, False, False, "Unearned Revenue",                ""),
    ("213",  "Opening Balance Adjustments", "تسويات رصيد الافتتاح",               "liability", "financing", "21",  False, True,  False, "Other Current Liabilities",        ""),
    ("214",  "Payroll Payable",             "مستحقات الرواتب",                     "liability", "operating", "21",  True,  False, False, "Payroll Payable",                 ""),
    ("215",  "Loan from Owner",             "قرض من المالك",                       "liability", "financing", "21",  False, True,  False, "Short-Term Loans Payable",        ""),
    ("216",  "Employee Reimbursements",     "تسديدات الموظفين",                    "liability", "operating", "21",  True,  True,  False, "Other Current Liabilities",        ""),
    ("2161", "Anil Reimbursements",         "تسديدات أنيل",                        "liability", "operating", "216", True,  True,  False, "Other Current Liabilities",        ""),
    ("217",  "VAT",                         "ضريبة القيمة المضافة",                "liability", "operating", "21",  False, False, False, "VAT",                             ""),
    ("218",  "Excise Tax Payable",          "ضريبة الاستهلاك المستحقة",            "liability", "operating", "21",  False, False, False, "Tax Payable",                     ""),

    # Non-current Liabilities
    ("22",   "Non-current Liabilities",    "الالتزامات غير المتداولة",             "liability", "operating", "2",   True,  False, False, "",                                ""),

    # ══════════════════════════════════════════════════════════════════════════
    # 3 — EQUITY
    # ══════════════════════════════════════════════════════════════════════════
    ("3",    "Equity",                       "حقوق الملكية",                        "",        "",           None,   True,  False, False, "",                                ""),

    # Opening Balance Equity
    ("31",   "Opening Balance Equity",      "حقوق الملكية الافتتاحية",             "equity",  "investing",  "3",    True,  False, False, "",                                ""),
    ("311",  "Opening Balance Offset",      "تسوية رصيد الافتتاح",                 "equity",  "financing",  "31",   False, False, False, "Opening Balance Equity",           ""),

    # Owner's Equity
    ("32",   "Owner's Equity",             "حقوق المالك",                         "equity",  "investing",  "3",    True,  False, False, "",                                ""),
    ("321",  "Owner's Equity",             "حقوق المالك",                         "equity",  "financing",  "32",   False, True,  False, "Share Capital",                    ""),
    ("322",  "Drawings",                   "المسحوبات",                            "equity",  "financing",  "32",   False, True,  False, "Share Capital",                    ""),

    # Retained Earnings
    ("33",   "Retained Earnings",          "الأرباح المحتجزة",                    "equity",  "operating",  "3",    True,  False, False, "",                                ""),
    ("334",  "Retained Earnings",          "الأرباح المحتجزة",                    "equity",  "operating",  "33",   True,  False, False, "Retained Earnings",                "retained_earnings"),

    # Acc. Other Comprehensive Income
    ("34",   "Acc. Other Comprehensive Income", "الدخل الشامل الآخر المتراكم",    "equity",  "operating",  "3",    True,  False, False, "",                                ""),
    ("341",  "Accumulated Unrealized Gain and Loss", "الأرباح والخسائر غير المحققة المتراكمة", "equity", "operating", "34", True, False, False, "Other Comprehensive Income", ""),

    # Paid-in Capital
    ("35",   "Paid-in Capital",            "رأس المال المدفوع",                   "equity",  "investing",  "3",    True,  False, False, "",                                ""),

    # ══════════════════════════════════════════════════════════════════════════
    # 4 — REVENUE
    # ══════════════════════════════════════════════════════════════════════════
    ("4",    "Revenue",                      "الإيرادات",                           "",        "",           None,   True,  False, False, "",                                ""),

    # Income
    ("41",   "Income",                      "الدخل",                               "revenue", "operating",  "4",    True,  False, False, "",                                ""),
    ("411",  "Sales",                       "المبيعات",                            "revenue", "operating",  "41",   False, False, False, "",                                "sales_revenue"),
    ("412",  "Interest Income",            "دخل الفوائد",                         "revenue", "operating",  "41",   False, False, False, "",                                ""),
    ("413",  "Late Fee Income",            "دخل رسوم التأخير",                    "revenue", "operating",  "41",   False, False, False, "",                                ""),
    ("414",  "Shipping Charge",            "رسوم الشحن",                          "revenue", "operating",  "41",   False, False, False, "",                                ""),
    ("415",  "Other Charges",              "رسوم أخرى",                           "revenue", "operating",  "41",   False, False, False, "",                                ""),
    ("416",  "Discount",                   "الخصم",                               "revenue", "operating",  "41",   False, False, False, "",                                ""),

    # Other Income
    ("42",   "Other Income",               "إيرادات أخرى",                        "revenue", "operating",  "4",    True,  False, False, "",                                ""),

    # ══════════════════════════════════════════════════════════════════════════
    # 5 — EXPENSES
    # ══════════════════════════════════════════════════════════════════════════
    ("5",    "Expenses",                     "المصروفات",                           "",        "",           None,   True,  False, False, "",                                ""),

    # Cost of Sales
    ("51",   "Cost of Sales",              "تكلفة المبيعات",                      "expense", "operating",  "5",    True,  False, False, "",                                ""),
    ("511",  "Cost of Goods Sold",         "تكلفة البضاعة المباعة",               "expense", "operating",  "51",   False, False, False, "",                                ""),

    # Operating Expenses
    ("52",   "Operating Expenses",         "المصروفات التشغيلية",                 "expense", "operating",  "5",    True,  False, False, "",                                ""),
    ("521",  "Office Supplies",            "مستلزمات المكتب",                     "expense", "operating",  "52",   False, False, False, "General and Administrative Expenses", ""),
    ("5210", "Rent Expense",               "مصروف الإيجار",                       "expense", "operating",  "52",   False, False, False, "General and Administrative Expenses", ""),
    ("5211", "Janitorial Expense",         "مصروف النظافة",                       "expense", "operating",  "52",   False, False, False, "General and Administrative Expenses", ""),
    ("5212", "Postage",                    "البريد",                              "expense", "operating",  "52",   False, False, False, "General and Administrative Expenses", ""),
    ("5213", "Bad Debt",                   "الديون المعدومة",                     "expense", "operating",  "52",   False, False, False, "General and Administrative Expenses", ""),
    ("5214", "Printing and Stationery",    "الطباعة والقرطاسية",                  "expense", "operating",  "52",   False, False, False, "General and Administrative Expenses", ""),
    ("5215", "Salaries and Employee Wages","رواتب وأجور الموظفين",                "expense", "operating",  "52",   False, False, False, "General and Administrative Expenses", ""),
    ("5216", "Consultant Expense",         "مصروف الاستشارات",                    "expense", "operating",  "52",   False, False, False, "General and Administrative Expenses", ""),
    ("5217", "Repairs and Maintenance",    "الإصلاح والصيانة",                    "expense", "operating",  "52",   False, False, False, "General and Administrative Expenses", ""),
    ("522",  "Lodging",                    "الإقامة",                             "expense", "operating",  "52",   False, False, False, "General and Administrative Expenses", ""),
    ("523",  "Advertising and Marketing",  "الإعلان والتسويق",                    "expense", "operating",  "52",   False, False, False, "Marketing Expenses",                  ""),
    ("524",  "Bank Fees and Charges",      "رسوم ومصاريف البنك",                  "expense", "operating",  "52",   False, False, False, "General and Administrative Expenses", ""),
    ("525",  "Credit Card Charges",        "رسوم بطاقة الائتمان",                 "expense", "operating",  "52",   False, False, False, "General and Administrative Expenses", ""),
    ("526",  "Travel Expense",             "مصروف السفر",                         "expense", "operating",  "52",   False, False, True,  "General and Administrative Expenses", ""),
    ("527",  "Telephone Expense",          "مصروف الهاتف",                        "expense", "operating",  "52",   False, False, True,  "General and Administrative Expenses", ""),
    ("528",  "Vehicle Expense",            "مصروف المركبة",                       "expense", "operating",  "52",   False, False, True,  "General and Administrative Expenses", ""),
    ("529",  "Software and Tools",         "البرمجيات والأدوات",                  "expense", "operating",  "52",   False, False, False, "General and Administrative Expenses", ""),

    # Non-Operating Expenses
    ("53",   "Non-Operating Expenses",     "المصروفات غير التشغيلية",             "expense", "operating",  "5",    True,  False, False, "",                                ""),
    ("531",  "Exchange Gain or Loss",      "أرباح أو خسائر العملة",               "expense", "operating",  "53",   True,  False, False, "Realized Exchange Gain or Loss",    ""),
    ("532",  "Unrealized Gain and Losses", "الأرباح والخسائر غير المحققة",        "expense", "operating",  "53",   False, False, False, "Unrealized Exchange Gain or Loss",  ""),
    ("533",  "Uncategorized",              "غير مصنف",                            "expense", "operating",  "53",   False, False, False, "Other Non-Operating Expenses",      ""),
    ("534",  "Meals and Entertainment",    "وجبات الطعام والترفيه",               "expense", "operating",  "53",   False, False, True,  "Other Non-Operating Expenses",      ""),
    ("535",  "Depreciation Expense",       "مصروف الإهلاك",                       "expense", "operating",  "53",   False, False, False, "Depreciation and Amortization",     ""),
]


class Command(BaseCommand):
    help = "Seed the default ZATCA Chart of Accounts (77 accounts across 5 root categories)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-seed even if accounts already exist (skips existing codes, adds missing ones).",
        )

    def handle(self, *args, **options):
        if Account.objects.filter(is_deleted=False).exists() and not options["force"]:
            self.stdout.write(self.style.WARNING(
                "Chart of Accounts already has data.\n"
                "Use --force to re-seed (existing codes will be skipped, missing ones added)."
            ))
            return

        created_map: dict[str, Account] = {}
        created_count = 0
        skipped_count = 0

        for (
            code, name, name_ar,
            account_type, cash_flow_type,
            parent_code, is_locked,
            enable_payment, show_in_expense_claim,
            account_sub_type, zatca_mapping,
        ) in DEFAULT_ACCOUNTS:

            if parent_code:
                parent = created_map.get(parent_code) or Account.objects.filter(
                    code=parent_code, is_deleted=False
                ).first()
            else:
                parent = None

            _, created = Account.objects.get_or_create(
                code=code,
                defaults={
                    "name": name,
                    "name_ar": name_ar,
                    "account_type": account_type,
                    "account_sub_type": account_sub_type,
                    "zatca_mapping": zatca_mapping,
                    "cash_flow_type": cash_flow_type,
                    "parent": parent,
                    "is_locked": is_locked,
                    "enable_payment": enable_payment,
                    "show_in_expense_claim": show_in_expense_claim,
                    "is_deleted": False,
                    "is_archived": False,
                },
            )

            created_map[code] = Account.objects.get(code=code)

            if created:
                created_count += 1
                indent = "  " * (len(code) - 1)
                zatca_tag = f"  [ZATCA: {zatca_mapping}]" if zatca_mapping else ""
                self.stdout.write(f"  {indent}✔ {code}  {name}{zatca_tag}")
            else:
                skipped_count += 1

        self.stdout.write(self.style.SUCCESS(
            f"\nDone.  Created: {created_count}   Skipped (already exist): {skipped_count}"
        ))
