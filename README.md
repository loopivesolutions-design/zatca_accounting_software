# ZATCA Accounting Software — Backend

Django REST Framework backend for ZATCA-integrated accounting software.
Covers User & Role Management, Chart of Accounts, Tax Rates, Journal Entries, and JWT authentication.

---

## Table of Contents

1. [Tech Stack](#1-tech-stack)
2. [Project Structure](#2-project-structure)
3. [Environment Setup](#3-environment-setup)
4. [Running the Project](#4-running-the-project)
5. [Management Commands](#5-management-commands)
6. [Migration Reset (Clean Start)](#6-migration-reset-clean-start)
7. [API Overview](#7-api-overview)

---

## 1. Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.10 |
| Framework | Django 5.x + Django REST Framework |
| Auth | JWT (`djangorestframework-simplejwt`) |
| Database | SQLite (dev) / PostgreSQL (prod) |
| Email | Django SMTP (`python-dotenv` for config) |
| CORS | `django-cors-headers` |

---

## 2. Project Structure

```
zatca_accounting_software/
│
├── accounting/                  # Chart of Accounts, Tax Rates, Journal Entries
│   ├── management/
│   │   └── commands/
│   │       ├── seed_chart_of_accounts.py
│   │       └── seed_tax_rates.py
│   ├── migrations/
│   ├── models.py                # Account, TaxRate, JournalEntry, JournalEntryLine
│   ├── serializers.py           # Account serializers
│   ├── tax_serializers.py       # TaxRate serializers
│   ├── journal_serializers.py   # JournalEntry serializers
│   ├── views.py                 # Account views
│   ├── tax_views.py             # TaxRate views
│   ├── journal_views.py         # JournalEntry views
│   ├── validators.py            # AccountValidator, JournalEntryValidator
│   ├── exceptions.py            # Structured ZATCA error classes
│   └── urls.py
│
├── main/                        # Shared utilities (BaseModel, pagination, etc.)
│   ├── management/
│   │   └── commands/
│   │       └── create_groups_and_permissions.py
│   ├── migrations/
│   ├── fixtures/                # countries.json, states.json
│   ├── models.py                # BaseModel, Country, State, CompanySettings
│   └── pagination.py
│
├── purchases/                   # Purchase module (Suppliers/Vendors)
│   ├── migrations/
│   ├── models.py                # Supplier
│   ├── serializers.py
│   ├── urls.py
│   └── views.py
│
├── user/                        # Auth, Users, Roles, Invitations
│   ├── migrations/
│   ├── models.py                # CustomUser, Role, RolePermission, UserInvitation
│   ├── serializers.py
│   ├── urls.py
│   └── views.py
│
├── zatca_accounting_software/   # Project config
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
│
├── .env                         # Local environment variables (not committed)
├── .env.example                 # Template for .env
├── API_DOCUMENTATION.md         # Full API reference with request/response examples
└── manage.py
```

---

## 3. Environment Setup

### 3.1 Create and activate virtual environment

```bash
python -m venv venv
source venv/bin/activate        # Linux / macOS
venv\Scripts\activate           # Windows
```

### 3.2 Install dependencies

```bash
pip install django djangorestframework djangorestframework-simplejwt \
            django-cors-headers python-dotenv
```

### 3.3 Configure `.env`

Copy the example file and fill in your values:

```bash
cp .env.example .env
```

`.env` contents:

```env
# ── Django ────────────────────────────────────────────────────────────────────
SECRET_KEY=your-secret-key-here
DEBUG=True
ALLOWED_HOSTS=

# ── Email (SMTP) ──────────────────────────────────────────────────────────────
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=your-email@gmail.com
EMAIL_HOST_PASSWORD=your-app-password
DEFAULT_FROM_EMAIL=ZATCA Accounting <your-email@gmail.com>

# ── Backend URL (used in invitation email links) ──────────────────────────────
BACKEND_URL=http://127.0.0.1:8000

# ── CORS (comma-separated list of allowed frontend origins) ───────────────────
CORS_ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

> **Gmail:** Use an [App Password](https://myaccount.google.com/apppasswords) — not your regular Gmail password.

---

## 4. Running the Project

```bash
# Apply all migrations
python manage.py migrate

# Create a superuser (first-time setup)
python manage.py createsuperuser

# Seed default Chart of Accounts (76 accounts)
python manage.py seed_chart_of_accounts

# Seed default KSA/ZATCA tax rates (10 rates)
python manage.py seed_tax_rates

# Seed default Primary Warehouse (linked to CoA 1151)
python manage.py seed_primary_warehouse

# Load Countries/States reference data (used for dropdowns)
python manage.py loaddata countries states

# Seed default module permissions for roles
# If no roles exist yet, this can create a minimal 'Admin' role automatically:
python manage.py create_groups_and_permissions --create-roles

# Start the development server
python manage.py runserver
```

API is available at: `http://127.0.0.1:8000/api/v1/`

---

## 5. Management Commands

### 5.1 `seed_chart_of_accounts`

Seeds the full ZATCA Chart of Accounts (76 accounts) with English and Arabic names, cash flow types, enable_payment, show_in_expense_claim, and detailed account sub-types.

```bash
python manage.py seed_chart_of_accounts
```

> If accounts already exist, the command exits safely with a warning:
> ```
> Chart of Accounts already has data.
> Use --force to re-seed (existing codes will be skipped, missing ones added).
> ```

To add any missing accounts without touching existing ones:

```bash
python manage.py seed_chart_of_accounts --force
```

**Flags:**

| Flag | Description |
|---|---|
| *(none)* | Aborts if any accounts already exist — safe for first-time setup |

### 5.x `seed_primary_warehouse`

Creates (or ensures) the default **Primary Warehouse** (`WH-001`) and links it to the **Chart of Accounts** account code **`1151`**.

- If the warehouse is renamed later, the linked CoA account name is automatically updated to match.
- Deletion is blocked once the warehouse has any **posted** inventory transactions (e.g., posted inventory adjustments).

```bash
python manage.py seed_primary_warehouse
```

**Flags:**

| Flag | Description |
|---|---|
| `--force-link` | Force linking `WH-001` to CoA `1151` even if it was linked differently |
| `--force` | Skips existing codes, creates only missing ones — safe to re-run |

**What it creates (76 accounts):**

```
1   Assets
├── 11  Current Assets
│   ├── 111   Cash and Cash Equivalents
│   │   ├── 1111  Undeposited Funds
│   │   ├── 1112  Petty Cash
│   │   └── 1113  Bank Accounts
│   ├── 112   Accounts Receivable
│   ├── 113   Employee Advance
│   ├── 114   Prepaid Expenses
│   └── 115   Inventory
│       └── 1151  Primary Warehouse
└── 12  Non-Current Assets
    └── 121   Fixed Assets
        ├── 1211  Furniture and Equipment
        └── 1212  Accumulated Depreciation

2   Liabilities
├── 21  Current Liabilities
│   ├── 211   Accounts Payable
│   │   ├── 2110  VAT Payable
│   │   └── 2111  Zakat Payable
│   ├── 212   Unearned Revenue
│   ├── 213   Opening Balance Adjustments
│   ├── 214   Payroll Payable
│   ├── 215   Loan from Owner
│   ├── 216   Employee Reimbursements
│   │   └── 2161  Anil Reimbursements
│   ├── 217   VAT
│   └── 218   Excise Tax Payable
└── 22  Non-current Liabilities

3   Equity
├── 31  Opening Balance Equity
│   └── 311   Opening Balance Offset
├── 32  Owner's Equity
│   ├── 321   Owner's Equity
│   └── 322   Drawings
├── 33  Retained Earnings
│   └── 334   Retained Earnings
├── 34  Acc. Other Comprehensive Income
│   └── 341   Accumulated Unrealized Gain and Loss
└── 35  Paid-in Capital

4   Revenue
├── 41  Income
│   ├── 411   Sales
│   ├── 412   Interest Income
│   ├── 413   Late Fee Income
│   ├── 414   Shipping Charge
│   ├── 415   Other Charges
│   └── 416   Discount
└── 42  Other Income

5   Expenses
├── 51  Cost of Sales
│   └── 511   Cost of Goods Sold
├── 52  Operating Expenses
│   ├── 521   Office Supplies
│   ├── 5210  Rent Expense
│   ├── 5211  Janitorial Expense
│   ├── 5212  Postage
│   ├── 5213  Bad Debt
│   ├── 5214  Printing and Stationery
│   ├── 5215  Salaries and Employee Wages
│   ├── 5216  Consultant Expense
│   ├── 5217  Repairs and Maintenance
│   ├── 522   Lodging
│   ├── 523   Advertising and Marketing
│   ├── 524   Bank Fees and Charges
│   ├── 525   Credit Card Charges
│   ├── 526   Travel Expense
│   ├── 527   Telephone Expense
│   ├── 528   Vehicle Expense
│   └── 529   Software and Tools
└── 53  Non-Operating Expenses
    ├── 531   Exchange Gain or Loss
    ├── 532   Unrealized Gain and Losses
    ├── 533   Uncategorized
    ├── 534   Meals and Entertainment
    └── 535   Depreciation Expense
```

**Locked accounts:** Root and group accounts are marked `is_locked=True` and cannot be modified or deleted via the API.

**Fields seeded per account:**

| Field | Description |
|---|---|
| `code` | Unique account code (e.g. `111`, `5215`) |
| `name` | English name |
| `name_ar` | Arabic name |
| `account_type` | Basic category: `asset` / `liability` / `equity` / `revenue` / `expense` |
| `account_sub_type` | Detailed label shown in the Account Type column (e.g. `Cash and Cash Equivalents`) |
| `cash_flow_type` | `cash` / `operating` / `investing` / `financing` |
| `enable_payment` | Whether this account can be used for payments |
| `show_in_expense_claim` | Whether this account appears in expense claims |
| `is_locked` | System accounts cannot be edited or deleted |

---

### 5.2 `seed_tax_rates`

Seeds the 10 default KSA / ZATCA-compliant tax rates matching FATOORAH e-invoicing standards.
All seeded rates are marked `is_default=True` and **cannot be deleted** via the API (deactivate instead).

```bash
python manage.py seed_tax_rates
```

> If default rates already exist, the command exits safely:
> ```
> Tax rates already seeded (10 default records). Use --force to re-seed.
> ```

To update all existing default rates in place:

```bash
python manage.py seed_tax_rates --force
```

**Flags:**

| Flag | Description |
|---|---|
| *(none)* | Skips if any default rates already exist — safe for first-time setup |
| `--force` | Updates all existing default rates — safe to re-run |

**Default rates seeded:**

| Tax Name (EN) | Tax Name (AR) | Type | Rate | ZATCA Code |
|---|---|---|---|---|
| Out of Scope | غير خاضع للضريبة | Out of Scope | 0% | O |
| Reverse Charge | إحتساب عكسي | Reverse Charge | 15% | S |
| Exempt Purchases | مشتريات معفاة من الضريبة | Purchases | 0% | E |
| Zero-rated Purchases | مشتريات نسبة صفر | Purchases | 0% | Z |
| VAT at Customs | الضريبة القيمة المضافة في الجمارك | Purchases | 15% | S |
| VAT on Purchases | الضريبة القيمة المضافة على مشتريات | Purchases | 15% | S |
| Exempt | معفى | Sales | 0% | E |
| Zero-Rated Exports | صادرات | Sales | 0% | Z |
| Zero-Rated Domestic Sales | مبيعات داخل المملكة خاضعة للضريبة بنسبة صفر | Sales | 0% | Z |
| VAT on Sales | الضريبة القيمة المضافة على الإيرادات | Sales | 15% | S |

**ZATCA Category Codes (FATOORAH XML):**

| Code | Meaning |
|---|---|
| `S` | Standard Rate — 15% |
| `Z` | Zero Rate — 0% |
| `E` | Exempt — 0% |
| `O` | Out of Scope — 0% |

**Fields seeded per tax rate:**

| Field | Description |
|---|---|
| `name` | English name |
| `name_ar` | Arabic name |
| `tax_type` | `sales` / `purchases` / `reverse_charge` / `out_of_scope` |
| `rate` | Percentage value (e.g. `15.00`) |
| `description` | Standard KSA description |
| `zatca_category` | FATOORAH XML VAT category code (`S` / `Z` / `E` / `O`) |
| `is_default` | `True` — cannot be deleted |
| `is_active` | `True` |

---

### 5.3 `create_groups_and_permissions`

Seeds default module permission rows (all permissions set to `False`) for every existing Role in the database.

```bash
python manage.py create_groups_and_permissions
```

If your database has no roles yet, you can also run:

```bash
python manage.py create_groups_and_permissions --create-roles
```

This will create a minimal `Admin` role (only if none exist) and then seed its permission matrix.

**Modules seeded per role:**
`sales`, `purchase`, `vat_zatca`, `customers`, `products`, `banking`, `accounting`, `reports`, `settings`

---

## 6. Migration Reset (Clean Start)

Use this when you need a completely fresh database:

```bash
# Step 1: Delete all migration files (keep __init__.py)
find . -path "*/migrations/*.py" -not -name "__init__.py" -not -path "*/venv/*" -delete

# Step 2: Remove compiled Python cache
find . -type d -name "__pycache__" -not -path "*/venv/*" -exec rm -rf {} + 2>/dev/null; true
find . -type f -name "*.pyc" -not -path "*/venv/*" -delete

# Step 3: Delete the database
rm -f db.sqlite3

# Step 4: Regenerate migrations and apply
python manage.py makemigrations
python manage.py migrate

# Step 5: Re-create superuser and seed data
python manage.py createsuperuser
python manage.py seed_chart_of_accounts
python manage.py seed_tax_rates
python manage.py loaddata countries states
python manage.py create_groups_and_permissions --create-roles
```

---

## 7. API Overview

Full documentation with request bodies and response examples is in [`API_DOCUMENTATION.md`](./API_DOCUMENTATION.md).

| Module | Base Path | Description |
|---|---|---|
| Main | `/api/v1/main/` | Countries/States dropdowns, Company Settings |
| Auth | `/api/v1/user/` | Admin login, user login, token refresh |
| Admin | `/api/v1/user/admin/` | Create admin (superuser only) |
| Users | `/api/v1/user/management/users/` | List, retrieve, update, delete users |
| Roles | `/api/v1/user/management/roles/` | CRUD roles + permission matrix |
| Invitations | `/api/v1/user/management/invitations/` | Send, resend, list invitations |
| Accept Invite | `/api/v1/user/accept-invitation/` | Verify token, activate account |
| Chart of Accounts | `/api/v1/accounting/chart-of-accounts/` | Full CRUD, tree view, archive, export CSV |
| Tax Rates | `/api/v1/accounting/tax-rates/` | CRUD + dropdown choices |
| Journal Entries | `/api/v1/accounting/journal-entries/` | Draft, post (immutable), reverse |
| Products | `/api/v1/products/` | Categories, Items, UoM, Warehouses, Inventory adjustments |
| Purchases | `/api/v1/purchases/` | Suppliers/Vendors |

### Authentication Header

```
Authorization: Bearer <access_token>
```


Pro Tip for Cursor Development

When building modules, instruct Cursor like this:

Build this accounting module using
double entry accounting,
immutable ledger design,
ZATCA compliant e-invoicing,
and full audit logging.

This dramatically improves the generated architecture.# zatca_accounting_software
