# ZATCA Accounting Software — API Documentation

**Base URL:** `http://127.0.0.1:8000/api/v1`

**Authentication:** Bearer Token (JWT)
Add to every protected request header:
```
Authorization: Bearer <access_token>
```

---

## Table of Contents

1. [Authentication](#1-authentication)
2. [User Management](#2-user-management)
3. [Role Management](#3-role-management)
4. [Invitations](#4-invitations)
5. [Chart of Accounts](#5-chart-of-accounts)
6. [Journal Entries](#6-journal-entries)
7. [Tax Rates](#7-tax-rates)
8. [Product Categories](#8-product-categories)
9. [ZATCA Compliance Error Codes](#9-zatca-compliance-error-codes)
10. [General Error Reference](#10-general-error-reference)
11. [Company Settings](#11-company-settings)
12. [Suppliers (Purchase)](#12-suppliers-purchase)
13. [Bills (Purchase)](#13-bills-purchase)
14. [Supplier Payments](#14-supplier-payments)
15. [Debit Notes (Purchase)](#15-debit-notes-purchase)
16. [Customers (Sales)](#16-customers-sales)
17. [Quotes (Sales)](#17-quotes-sales)
18. [Invoices (Sales)](#18-invoices-sales)
19. [Customer Payments (Sales)](#19-customer-payments-sales)
20. [Customer Refunds (Sales)](#20-customer-refunds-sales)
21. [Credit Notes (Sales)](#21-credit-notes-sales)

---

> **Last updated:** 2026-03-04  
> **Backend:** Django REST Framework · JWT Auth (`djangorestframework-simplejwt`)  
> **Base URL:** `http://127.0.0.1:8000/api/v1`  
> All list endpoints support pagination: `?page=<int>&page_size=<int>` (default 20, max 200)

---

## 1. Authentication

### 1.1 Admin Login
> Returns JWT tokens for admin/staff users.

- **URL:** `POST /user/admin/login/`
- **Auth:** None

**Request Body:**
```json
{
  "email": "admin@zatca.com",
  "password": "Admin@1234"
}
```

**Success Response `200`:**
```json
{
  "message": "Login successful.",
  "user": {
    "email": "admin@zatca.com",
    "first_name": "Owner",
    "last_name": "Admin",
    "role": "Admin"
  },
  "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

**Error Response `400`:**
```json
{
  "non_field_errors": ["Invalid email or password."]
}
```

---

### 1.2 User Login
> Returns JWT tokens for any active user (invited users after accepting invitation).

- **URL:** `POST /user/login/`
- **Auth:** None

**Request Body:**
```json
{
  "email": "john.doe@example.com",
  "password": "MySecure@123"
}
```

**Success Response `200`:**
```json
{
  "message": "Login successful.",
  "user_id": "3f1b2c4d-...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "role": "Accountant"
}
```

---

### 1.3 Refresh Access Token
> Get a new access token using a refresh token.

- **URL:** `POST /user/token/refresh/`
- **Auth:** None

**Request Body:**
```json
{
  "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

**Success Response `200`:**
```json
{
  "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

---

### 1.4 Create Admin User
> Creates a new admin (staff) user. **Superuser token required.**

- **URL:** `POST /user/admin/create/`
- **Auth:** Bearer `<superuser_access_token>`

**Request Body:**
```json
{
  "email": "manager@zatca.com",
  "first_name": "Jane",
  "last_name": "Smith",
  "password": "SecurePass@123"
}
```

**Success Response `201`:**
```json
{
  "message": "Admin user created successfully."
}
```

**Error Response `403`:**
```json
{
  "detail": "You do not have permission to perform this action."
}
```

---

## 2. User Management

> All endpoints require: `Authorization: Bearer <admin_access_token>`

### 2.1 List Users
- **URL:** `GET /user/management/users/`
- **Query Params:**
  - `search=<string>` — search by name or email
  - `ordering=<field>` — sort by `email`, `first_name`, `last_name`, `last_login`, `created_at` (prefix `-` for descending)
  - `page=<int>` — page number
  - `page_size=<int>` — items per page (default 20, max 100)

**Success Response `200`:**
```json
{
  "count": 3,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "3f1b2c4d-1234-5678-abcd-ef0123456789",
      "name": "John Doe",
      "email": "john.doe@example.com",
      "role": "e464d534-1893-4e2f-973f-f6709a8feaa6",
      "role_name": "Accountant",
      "last_login": "2026-03-04 10:30"
    }
  ]
}
```

---

### 2.2 Retrieve User
- **URL:** `GET /user/management/users/<uuid>/`

**Success Response `200`:**
```json
{
  "id": "3f1b2c4d-...",
  "name": "John Doe",
  "first_name": "John",
  "last_name": "Doe",
  "email": "john.doe@example.com",
  "phone": "+966500000000",
  "role": "e464d534-...",
  "role_name": "Accountant",
  "is_active": true,
  "last_login": "2026-03-04 10:30",
  "created_at": "2026-03-01 09:00"
}
```

---

### 2.3 Update User
- **URL:** `PATCH /user/management/users/<uuid>/`

**Request Body (any combination of fields):**
```json
{
  "first_name": "John",
  "last_name": "Doe",
  "email": "john.new@example.com",
  "phone": "+966512345678",
  "role": "e464d534-1893-4e2f-973f-f6709a8feaa6",
  "is_active": false
}
```

**Success Response `200`:** *(same as Retrieve User)*

---

### 2.4 Delete User (Soft Delete)
- **URL:** `DELETE /user/management/users/<uuid>/`

**Success Response `200`:**
```json
{
  "message": "User deleted successfully."
}
```

---

## 3. Role Management

> All endpoints require: `Authorization: Bearer <admin_access_token>`

### 3.1 List Roles
- **URL:** `GET /user/management/roles/`

**Success Response `200`:**
```json
[
  { "id": "e464d534-1893-4e2f-973f-f6709a8feaa6", "name": "Supervisor" },
  { "id": "b123c456-...", "name": "Accountant" }
]
```

---

### 3.2 Create Role
- **URL:** `POST /user/management/roles/`

**Request Body:**
```json
{
  "name": "Supervisor"
}
```

**Success Response `201`:**
```json
{
  "id": "e464d534-1893-4e2f-973f-f6709a8feaa6",
  "name": "Supervisor"
}
```

---

### 3.3 Retrieve Role
- **URL:** `GET /user/management/roles/<uuid>/`

**Success Response `200`:**
```json
{
  "id": "e464d534-...",
  "name": "Supervisor"
}
```

---

### 3.4 Update Role Name
- **URL:** `PATCH /user/management/roles/<uuid>/`

**Request Body:**
```json
{
  "name": "Senior Accountant"
}
```

**Success Response `200`:**
```json
{
  "id": "e464d534-...",
  "name": "Senior Accountant"
}
```

---

### 3.5 Delete Role
- **URL:** `DELETE /user/management/roles/<uuid>/`

**Success Response `204`:** *(no body)*

---

### 3.6 Get Role Permissions Matrix
- **URL:** `GET /user/management/roles/<uuid>/permissions/`

**Success Response `200`:**
```json
{
  "id": "e464d534-...",
  "name": "Supervisor",
  "permissions": [
    {
      "module": "sales",
      "module_display": "Sales",
      "can_view": true,
      "can_create": true,
      "can_edit": true,
      "can_delete": false,
      "can_approve": false
    },
    {
      "module": "purchase",
      "module_display": "Purchase",
      "can_view": true,
      "can_create": false,
      "can_edit": false,
      "can_delete": false,
      "can_approve": false
    },
    { "module": "vat_zatca",  "module_display": "VAT&ZATCA",  "can_view": false, "can_create": false, "can_edit": false, "can_delete": false, "can_approve": false },
    { "module": "customers",  "module_display": "Customers",  "can_view": false, "can_create": false, "can_edit": false, "can_delete": false, "can_approve": false },
    { "module": "products",   "module_display": "Products",   "can_view": false, "can_create": false, "can_edit": false, "can_delete": false, "can_approve": false },
    { "module": "banking",    "module_display": "Banking",    "can_view": false, "can_create": false, "can_edit": false, "can_delete": false, "can_approve": false },
    { "module": "accounting", "module_display": "Accounting", "can_view": false, "can_create": false, "can_edit": false, "can_delete": false, "can_approve": false },
    { "module": "reports",    "module_display": "Reports",    "can_view": false, "can_create": false, "can_edit": false, "can_delete": false, "can_approve": false },
    { "module": "settings",   "module_display": "Settings",   "can_view": false, "can_create": false, "can_edit": false, "can_delete": false, "can_approve": false }
  ]
}
```

---

### 3.7 Update Role Permissions Matrix
- **URL:** `PUT /user/management/roles/<uuid>/permissions/`

**Request Body:**
```json
{
  "permissions": [
    { "module": "sales",    "can_view": true, "can_create": true, "can_edit": true, "can_delete": false, "can_approve": false },
    { "module": "purchase", "can_view": true, "can_create": false, "can_edit": false, "can_delete": false, "can_approve": false }
  ]
}
```

**Success Response `200`:** *(same as Get Permissions Matrix)*

> **Modules:** `sales`, `purchase`, `vat_zatca`, `customers`, `products`, `banking`, `accounting`, `reports`, `settings`

---

## 4. Invitations

### 4.1 List Invitations
> Returns all invitations with status.

- **URL:** `GET /user/management/invitations/`
- **Auth:** Bearer `<admin_access_token>`
- **Query Params:**
  - `status=pending` | `status=accepted` | `status=rejected` *(omit for all)*

**Success Response `200`:**
```json
{
  "count": 2,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "edaa8bad-fd4f-4a63-b0e9-c67e8af35950",
      "first_name": "John",
      "last_name": "Doe",
      "email": "john.doe@example.com",
      "role": "e464d534-1893-4e2f-973f-f6709a8feaa6",
      "role_name": "Supervisor",
      "invited_by_name": "Owner Admin",
      "created_at": "2026-03-04T15:16:34.968305Z",
      "is_expired": false,
      "status": "pending"
    }
  ]
}
```

> **Status values:** `pending`, `accepted`, `rejected`

---

### 4.2 Send Invitation(s)
> Sends invitation emails to one or more users.

- **URL:** `POST /user/management/invitations/send/`
- **Auth:** Bearer `<admin_access_token>`

**Request Body:**
```json
{
  "users": [
    {
      "name": "John Doe",
      "email": "john.doe@example.com",
      "role": "e464d534-1893-4e2f-973f-f6709a8feaa6"
    },
    {
      "name": "Jane Smith",
      "email": "jane.smith@example.com",
      "role": "b123c456-1234-5678-abcd-ef0123456789"
    }
  ]
}
```

**Success Response `201`:**
```json
{
  "message": "Invitations sent.",
  "invitations": 2
}
```

**Error Response `400`:**
```json
{
  "users": [
    {
      "email": ["A user with this email already exists."]
    }
  ]
}
```

---

### 4.3 Resend Invitation
> Rotates the token and resends the invitation email (only for pending invitations).

- **URL:** `POST /user/management/invitations/<uuid>/resend/`
- **Auth:** Bearer `<admin_access_token>`
- **Body:** None

**Success Response `200`:**
```json
{
  "message": "Invitation resent to john.doe@example.com."
}
```

**Error Response `404`:**
```json
{
  "error": "Invitation not found or already accepted/expired."
}
```

---

### 4.4 Verify Invitation Token
> Called when invited user opens the invitation link. Returns invitation details.

- **URL:** `GET /user/accept-invitation/?token=<token>`
- **Auth:** None

**Success Response `200`:**
```json
{
  "token": "zqIRJHGzPWNzs6W7h3-uDUz76CR_9ImsENxI9S2vXtY",
  "first_name": "John",
  "last_name": "Doe",
  "email": "john.doe@example.com",
  "role": "Supervisor"
}
```

**Error Response `400`:**
```json
{
  "error": "Invalid or expired invitation token."
}
```

---

### 4.5 Accept Invitation & Activate Account
> Invited user sets their password and activates their account.

- **URL:** `POST /user/accept-invitation/`
- **Auth:** None

**Request Body:**
```json
{
  "token": "zqIRJHGzPWNzs6W7h3-uDUz76CR_9ImsENxI9S2vXtY",
  "password": "MySecure@123",
  "first_name": "John",
  "last_name": "Doe"
}
```

> `first_name` and `last_name` are optional — invitation values are used if not provided.

**Success Response `201`:**
```json
{
  "message": "Account activated successfully. You can now log in.",
  "email": "john.doe@example.com",
  "first_name": "John",
  "last_name": "Doe",
  "role": "Supervisor"
}
```

---

## 5. Chart of Accounts

> All endpoints require: `Authorization: Bearer <admin_access_token>`

### Account Object Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Unique identifier |
| `parent` | UUID \| null | Parent account ID |
| `parent_name` | string \| null | Parent account name |
| `name` | string | Account name (English) |
| `name_ar` | string | Account name (Arabic) |
| `code` | string | Unique account code |
| `cash_flow_type` | string | `cash`, `operating`, `investing`, `financing`, or `""` |
| `account_type` | string | `asset`, `liability`, `equity`, `revenue`, `expense` |
| `account_sub_type` | string | Detailed classification (e.g. `"Cash and Cash Equivalents"`) |
| `zatca_mapping` | string | ZATCA compliance category (see choices below) |
| `zatca_mapping_display` | string | Human-readable ZATCA mapping label |
| `enable_payment` | bool | Can be selected on payment transactions |
| `show_in_expense_claim` | bool | Appears in expense claim forms |
| `is_locked` | bool | System account — cannot be deleted or structurally changed |
| `is_archived` | bool | Archived — excluded from new transactions |
| `has_children` | bool | Has at least one active child account |
| `has_transactions` | bool | Has at least one posted transaction |
| `edit_metadata` | object | Field-level editability (see §5.10) |
| `created_at` | datetime | ISO 8601 |
| `updated_at` | datetime | ISO 8601 |

**ZATCA Mapping Choices:**

| Value | Label |
|-------|-------|
| `vat_output` | Output VAT (VAT Payable) |
| `vat_input` | Input VAT (VAT Receivable) |
| `sales_revenue` | Sales Revenue |
| `accounts_receivable` | Accounts Receivable |
| `accounts_payable` | Accounts Payable |
| `retained_earnings` | Retained Earnings |
| `cash_and_bank` | Cash and Bank |

---

### 5.1 List Accounts (Flat, Paginated)
- **URL:** `GET /accounting/chart-of-accounts/`
- **Query Params:**
  - `search=<string>` — search by code, name (EN or AR)
  - `include_archived=true` — include archived accounts (default: excluded)
  - `page=<int>`
  - `page_size=<int>`

**Success Response `200`:**
```json
{
  "count": 76,
  "next": "http://127.0.0.1:8000/api/v1/accounting/chart-of-accounts/?page=2",
  "previous": null,
  "results": [
    {
      "id": "a1b2c3d4-...",
      "parent": null,
      "parent_name": null,
      "name": "Assets",
      "name_ar": "الأصول",
      "code": "1",
      "cash_flow_type": "",
      "cash_flow_type_display": "",
      "account_type": "asset",
      "account_type_display": "Asset",
      "account_sub_type": "",
      "zatca_mapping": "",
      "zatca_mapping_display": "",
      "enable_payment": false,
      "show_in_expense_claim": false,
      "is_locked": true,
      "is_archived": false,
      "has_children": true,
      "has_transactions": false,
      "edit_metadata": {
        "has_transactions": false,
        "lock_reason": "SYSTEM_ACCOUNT",
        "locked_fields": ["account_sub_type", "account_type", "cash_flow_type", "code", "enable_payment", "parent", "show_in_expense_claim"],
        "editable_fields": ["name", "name_ar"],
        "balance": 0.0,
        "balance_direction": "zero",
        "account_type_locked_by_balance": false,
        "zatca_mapping": null
      },
      "created_at": "2026-03-04T10:00:00Z",
      "updated_at": "2026-03-04T10:00:00Z"
    }
  ]
}
```

---

### 5.2 Create Account
- **URL:** `POST /accounting/chart-of-accounts/`

**Request Body:**
```json
{
  "parent": "a1b2c3d4-0000-0000-0000-000000000001",
  "name": "Petty Cash",
  "name_ar": "النقد الصغير",
  "code": "1111",
  "cash_flow_type": "cash",
  "account_type": "asset",
  "account_sub_type": "Cash and Cash Equivalents",
  "enable_payment": true,
  "show_in_expense_claim": false
}
```

> `parent` can be `null` for root-level accounts. `zatca_mapping` should not be set manually — it is reserved for system accounts.

**Success Response `201`:** *(full account object as in §5.1)*

**Error Response `400`:**
```json
{
  "code": ["An account with this code already exists."]
}
```

---

### 5.3 Retrieve Account
- **URL:** `GET /accounting/chart-of-accounts/<uuid>/`

**Success Response `200`:** *(full account object as in §5.1)*

---

### 5.4 Update Account
- **URL:** `PATCH /accounting/chart-of-accounts/<uuid>/`

**Request Body (any editable fields):**
```json
{
  "name": "Petty Cash Fund",
  "enable_payment": true
}
```

**Success Response `200`:** *(updated account object)*

**ZATCA Compliance Error Responses `422`:**

| Scenario | Error Code | Message |
|----------|------------|---------|
| System account — disallowed field | `ACCOUNT_LOCKED` | Only `name` / `name_ar` can be changed |
| ZATCA-mapped account — structural field | `ZATCA_MAPPING_VIOLATION` | Field locked to protect VAT reports |
| Has transactions — structural field | `FIELD_LOCKED_AFTER_TRANSACTION` | Field locked after first transaction |
| Non-zero balance — type change | `ACCOUNT_HAS_BALANCE` | Balance must be zero before changing type |

```json
{
  "error": "FIELD_LOCKED_AFTER_TRANSACTION",
  "message": "...",
  "locked_fields": ["account_type", "code"],
  "editable_fields": ["name", "name_ar", "enable_payment", "show_in_expense_claim"]
}
```

---

### 5.5 Delete Account
- **URL:** `DELETE /accounting/chart-of-accounts/<uuid>/`

**Success Response `200`:**
```json
{
  "message": "Account deleted successfully."
}
```

**Error Responses `422`:**

| Scenario | Error Code |
|----------|------------|
| System account | `ACCOUNT_LOCKED` |
| Has child accounts | `ACCOUNT_HAS_CHILDREN` |
| Has transactions | `ACCOUNT_HAS_TRANSACTIONS` |

```json
{
  "error": "ACCOUNT_HAS_TRANSACTIONS",
  "message": "'Accounts Receivable' has 12 transaction(s) and cannot be deleted.",
  "transaction_count": 12,
  "suggestion": "Archive this account instead to preserve historical records."
}
```

---

### 5.6 Full Account Tree
> Returns all accounts in a nested recursive structure.

- **URL:** `GET /accounting/chart-of-accounts/tree/`
- **Query Params:**
  - `root_only=true` — return only root-level accounts (no recursion)
  - `include_archived=true` — include archived accounts in tree

**Success Response `200`:**
```json
[
  {
    "id": "a1b2c3d4-...",
    "name": "Assets",
    "name_ar": "الأصول",
    "code": "1",
    "account_type": "asset",
    "account_type_display": "Asset",
    "account_sub_type": "",
    "zatca_mapping": "",
    "is_locked": true,
    "is_archived": false,
    "children": [
      {
        "id": "b2c3d4e5-...",
        "name": "Current Assets",
        "code": "11",
        "account_type": "asset",
        "is_locked": true,
        "is_archived": false,
        "children": [
          {
            "id": "c3d4e5f6-...",
            "name": "Cash and Cash Equivalents",
            "name_ar": "النقد وما يعادله",
            "code": "111",
            "cash_flow_type": "cash",
            "account_type": "asset",
            "account_sub_type": "Cash and Cash Equivalents",
            "zatca_mapping": "cash_and_bank",
            "enable_payment": false,
            "is_locked": true,
            "is_archived": false,
            "children": []
          }
        ]
      }
    ]
  }
]
```

---

### 5.7 Get Direct Children
> Lazy-loads one level of children for a given account.

- **URL:** `GET /accounting/chart-of-accounts/<uuid>/children/`
- **Query Params:**
  - `include_archived=true` — include archived children

**Success Response `200`:** *(array of flat account objects)*

---

### 5.8 Dropdown Choices
> Returns all choices for the Create Account form dropdowns.

- **URL:** `GET /accounting/chart-of-accounts/choices/`

**Success Response `200`:**
```json
{
  "cash_flow_types": [
    { "value": "cash",      "label": "Cash" },
    { "value": "operating", "label": "Operating" },
    { "value": "investing", "label": "Investing" },
    { "value": "financing", "label": "Financing" }
  ],
  "account_types": [
    { "value": "asset",     "label": "Asset" },
    { "value": "liability", "label": "Liability" },
    { "value": "equity",    "label": "Equity" },
    { "value": "revenue",   "label": "Revenue" },
    { "value": "expense",   "label": "Expense" }
  ],
  "zatca_mappings": [
    { "value": "vat_output",          "label": "Output VAT (VAT Payable)" },
    { "value": "vat_input",           "label": "Input VAT (VAT Receivable)" },
    { "value": "sales_revenue",       "label": "Sales Revenue" },
    { "value": "accounts_receivable", "label": "Accounts Receivable" },
    { "value": "accounts_payable",    "label": "Accounts Payable" },
    { "value": "retained_earnings",   "label": "Retained Earnings" },
    { "value": "cash_and_bank",       "label": "Cash and Bank" }
  ],
  "parent_accounts": [
    { "id": "a1b2c3d4-...", "code": "1",  "name": "Assets" },
    { "id": "b2c3d4e5-...", "code": "11", "name": "Current Assets" }
  ]
}
```

---

### 5.9 Export Accounts as CSV
> Downloads all accounts as a CSV file.

- **URL:** `GET /accounting/chart-of-accounts/export/`
- **Query Params:**
  - `include_archived=true` — include archived accounts in export
- **Response:** File download (`chart_of_accounts.csv`)

**CSV Columns:**
```
Code, Account Name, Account Name (AR), Parent Code, Cash Flow Type, Account Type,
Account Sub-Type, ZATCA Mapping, Locked, Archived, Enable Payment, Show in Expense Claim
```

---

### 5.10 Get Edit Metadata
> Returns which fields are editable for this account and why. Use this to drive frontend form control (disable locked fields).

- **URL:** `GET /accounting/chart-of-accounts/<uuid>/edit-metadata/`

**Success Response `200`:**
```json
{
  "has_transactions": true,
  "lock_reason": "HAS_TRANSACTIONS",
  "locked_fields": ["account_type", "cash_flow_type", "code", "parent"],
  "editable_fields": ["enable_payment", "is_archived", "name", "name_ar", "show_in_expense_claim"],
  "balance": 15000.00,
  "balance_direction": "debit",
  "account_type_locked_by_balance": false,
  "zatca_mapping": null
}
```

**`lock_reason` values:**

| Value | Cause |
|-------|-------|
| `null` | No restrictions — all fields editable |
| `SYSTEM_ACCOUNT` | `is_locked=true`; only name changes allowed |
| `ZATCA_MAPPED` | Account has a `zatca_mapping`; structural fields permanently locked |
| `HAS_TRANSACTIONS` | Posted transactions exist; structural fields locked |

---

### 5.11 Archive / Unarchive Account
> Archived accounts cannot be used in new transactions but remain in historical reports.

- **Archive:** `POST /accounting/chart-of-accounts/<uuid>/archive/`
- **Unarchive:** `POST /accounting/chart-of-accounts/<uuid>/unarchive/`
- **Body:** None

**Success Response `200`:**
```json
{
  "message": "Account archived successfully."
}
```

**Error Response `422`:**
```json
{
  "error": "CANNOT_ARCHIVE_ROOT",
  "message": "'Assets' is a root system account and cannot be archived.",
  "suggestion": "Archive individual child accounts instead of the root category."
}
```

---

## 6. Journal Entries

> All endpoints require: `Authorization: Bearer <admin_access_token>`

**ZATCA Compliance Rules Enforced:**
- **Rule 1 — Ledger Immutability:** Posted entries are permanently read-only. Corrections require reversal entries.
- **Rule 2 — Sequential Integrity:** Reference numbers (`JE-000001`, `JE-000002`, …) are auto-generated at post time with no gaps. Posted entries cannot be deleted.

### Journal Entry Object Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Unique identifier |
| `reference` | string | Sequential reference (e.g. `JE-000001`). Assigned on posting; empty for drafts |
| `date` | date | Entry date (`YYYY-MM-DD`) |
| `description` | string | Memo / narrative |
| `status` | string | `draft` or `posted` |
| `is_reversal` | bool | `true` if this entry was created to reverse another entry |
| `is_reversed` | bool | `true` if a reversal entry exists for this entry |
| `reversal_reference` | string \| null | Reference of the reversal entry, if reversed |
| `reversal_of` | UUID \| null | ID of the original entry this reverses |
| `total_debit` | decimal string | Sum of all debit lines |
| `total_credit` | decimal string | Sum of all credit lines |
| `posted_at` | datetime \| null | When the entry was posted |
| `created_at` | datetime | ISO 8601 |
| `updated_at` | datetime | ISO 8601 |
| `lines` | array | Journal entry lines (see below) |

### Journal Entry Line Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Unique identifier |
| `account` | UUID | Account ID |
| `account_code` | string | Account code (read-only, on detail) |
| `account_name` | string | Account name (read-only, on detail) |
| `account_type` | string | Account type (read-only, on detail) |
| `description` | string | Line description / memo |
| `debit` | decimal | Debit amount (0 if credit line) |
| `credit` | decimal | Credit amount (0 if debit line) |
| `line_order` | integer | Display order |

> Each line must have **exactly one of** `debit` or `credit` > 0. A line cannot have both.

---

### 6.1 List Journal Entries
- **URL:** `GET /accounting/journal-entries/`
- **Query Params:**
  - `status=draft` | `status=posted` — filter by status
  - `date_from=YYYY-MM-DD` — entries on or after this date
  - `date_to=YYYY-MM-DD` — entries on or before this date
  - `search=<string>` — search reference or description
  - `include_reversals=false` — exclude reversal entries (default: included)
  - `page=<int>`
  - `page_size=<int>` (default 20, max 200)

**Success Response `200`:**
```json
{
  "count": 3,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "f1e2d3c4-...",
      "reference": "JE-000001",
      "date": "2026-03-04",
      "description": "Monthly rent payment",
      "status": "posted",
      "is_reversal": false,
      "is_reversed": false,
      "reversal_reference": null,
      "total_debit": "5000.00",
      "posted_at": "2026-03-04T11:30:00Z",
      "created_at": "2026-03-04T11:25:00Z"
    }
  ]
}
```

---

### 6.2 Create Draft Journal Entry
- **URL:** `POST /accounting/journal-entries/`

> Creates a draft entry. Drafts are editable. A minimum of 2 lines is required.

**Request Body:**
```json
{
  "date": "2026-03-04",
  "description": "Monthly rent payment",
  "lines": [
    {
      "account": "uuid-of-rent-expense-account",
      "debit": 5000,
      "credit": 0,
      "description": "Rent expense — March 2026",
      "line_order": 1
    },
    {
      "account": "uuid-of-cash-account",
      "debit": 0,
      "credit": 5000,
      "description": "Cash payment",
      "line_order": 2
    }
  ]
}
```

**Success Response `201`:** *(full journal entry object with lines)*

**Validation Error `400`:**
```json
{
  "lines": ["A journal entry requires at least 2 lines."]
}
```

---

### 6.3 Retrieve Journal Entry
- **URL:** `GET /accounting/journal-entries/<uuid>/`

**Success Response `200`:**
```json
{
  "id": "f1e2d3c4-...",
  "reference": "JE-000001",
  "date": "2026-03-04",
  "description": "Monthly rent payment",
  "status": "posted",
  "is_reversal": false,
  "is_reversed": false,
  "reversal_reference": null,
  "reversal_of": null,
  "total_debit": "5000.00",
  "total_credit": "5000.00",
  "posted_at": "2026-03-04T11:30:00Z",
  "created_at": "2026-03-04T11:25:00Z",
  "updated_at": "2026-03-04T11:30:00Z",
  "lines": [
    {
      "id": "aa11bb22-...",
      "account": "uuid-of-rent-expense-account",
      "account_code": "511",
      "account_name": "Rent Expense",
      "account_type": "expense",
      "description": "Rent expense — March 2026",
      "debit": "5000.00",
      "credit": "0.00",
      "line_order": 1
    },
    {
      "id": "cc33dd44-...",
      "account": "uuid-of-cash-account",
      "account_code": "111",
      "account_name": "Cash and Cash Equivalents",
      "account_type": "asset",
      "description": "Cash payment",
      "debit": "0.00",
      "credit": "5000.00",
      "line_order": 2
    }
  ]
}
```

---

### 6.4 Update Draft Journal Entry
- **URL:** `PATCH /accounting/journal-entries/<uuid>/`

> **Only draft entries can be updated.** Attempting to update a posted entry returns a `422` error.

**Request Body (any fields including lines):**
```json
{
  "description": "March 2026 office rent",
  "lines": [
    { "account": "uuid-rent-expense", "debit": 5000, "credit": 0, "line_order": 1 },
    { "account": "uuid-cash",         "debit": 0, "credit": 5000, "line_order": 2 }
  ]
}
```

> Providing `lines` replaces **all** existing lines.

**Success Response `200`:** *(updated journal entry object)*

**Error Response `422` (posted entry):**
```json
{
  "error": "JOURNAL_ENTRY_POSTED",
  "message": "Journal entry 'JE-000001' is posted and cannot be modified or deleted. Posted entries are immutable to preserve the audit trail.",
  "reference": "JE-000001",
  "suggestion": "Use the /reverse/ endpoint to create a correcting reversal entry, then post a corrected journal entry."
}
```

---

### 6.5 Delete Draft Journal Entry
- **URL:** `DELETE /accounting/journal-entries/<uuid>/`

> **Only draft entries can be deleted.** Posted entries are permanently retained for audit trail compliance.

**Success Response `204`:** *(no body)*

**Error Response `422` (posted entry):**
```json
{
  "error": "JOURNAL_ENTRY_POSTED",
  "message": "Journal entry 'JE-000001' is posted and cannot be modified or deleted...",
  "suggestion": "Use the /reverse/ endpoint to create a correcting reversal entry..."
}
```

---

### 6.6 Post Journal Entry
> Transitions a draft entry to **posted** (immutable). Assigns a sequential reference number.

- **URL:** `POST /accounting/journal-entries/<uuid>/post/`
- **Body:** None

**Validations performed:**
- Entry must be in `draft` status
- Must have at least 2 lines
- Total debits must equal total credits (balanced entry)
- No line may reference an archived account
- Total amounts must be > 0

**Success Response `200`:** *(full journal entry object with `status: "posted"` and assigned `reference`)*

**Error Responses `422`:**

```json
{
  "error": "JOURNAL_ENTRY_NOT_BALANCED",
  "message": "Journal entry is not balanced. Total debits (5000.00) must equal total credits (4500.00). Difference: 500.00",
  "total_debits": 5000.0,
  "total_credits": 4500.0,
  "difference": 500.0
}
```

```json
{
  "error": "JOURNAL_ENTRY_INSUFFICIENT_LINES",
  "message": "Journal entry has only 1 line(s). A minimum of 2 lines is required for a valid double-entry.",
  "line_count": 1
}
```

---

### 6.7 Reverse Journal Entry
> Creates a reversal entry — the correct mechanism for correcting a posted entry.
> All debit/credit amounts are swapped. The original entry remains unchanged.

- **URL:** `POST /accounting/journal-entries/<uuid>/reverse/`

**Request Body (all fields optional):**
```json
{
  "description": "Reversal of JE-000001 — incorrect account used",
  "date": "2026-03-10",
  "auto_post": true
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `description` | string | `"Reversal of JE-NNNNNN"` | Memo for the reversal entry |
| `date` | date | Today | Reversal date (cannot be before original entry date) |
| `auto_post` | bool | `false` | If `true`, immediately posts the reversal. If `false`, creates as draft for review. |

**Success Response `201`:**
```json
{
  "message": "Reversal entry posted successfully.",
  "original_reference": "JE-000001",
  "reversal": {
    "id": "new-uuid-...",
    "reference": "JE-000002",
    "date": "2026-03-10",
    "description": "Reversal of JE-000001 — incorrect account used",
    "status": "posted",
    "is_reversal": true,
    "reversal_of": "f1e2d3c4-...",
    "total_debit": "5000.00",
    "total_credit": "5000.00",
    "lines": [
      {
        "account": "uuid-of-rent-expense-account",
        "account_code": "511",
        "account_name": "Rent Expense",
        "debit": "0.00",
        "credit": "5000.00"
      },
      {
        "account": "uuid-of-cash-account",
        "account_code": "111",
        "account_name": "Cash and Cash Equivalents",
        "debit": "5000.00",
        "credit": "0.00"
      }
    ]
  }
}
```

**Error Response `422` (already reversed):**
```json
{
  "error": "JOURNAL_ENTRY_ALREADY_REVERSED",
  "message": "Journal entry 'JE-000001' has already been reversed.",
  "reference": "JE-000001"
}
```

**Error Response `400` (reversal date before original):**
```json
{
  "date": ["Reversal date cannot be before the original entry date."]
}
```

---

## 7. Tax Rates

> All endpoints require: `Authorization: Bearer <admin_access_token>`

Tax rates are used across sales invoices, purchase bills, and expense claims.
Default rates are seeded by `python manage.py seed_tax_rates` and match KSA FATOORAH e-invoicing standards.

### Tax Rate Object Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Unique identifier |
| `name` | string | Tax name (English) — always editable |
| `name_ar` | string | Tax name (Arabic) — always editable |
| `tax_type` | string | `sales`, `purchases`, `reverse_charge`, or `out_of_scope` — **locked after creation** |
| `tax_type_display` | string | Human-readable tax type |
| `rate` | decimal | Tax percentage (e.g. `15.00` for 15%) — **locked after creation** |
| `description` | string | Description / notes — always editable |
| `zatca_category` | string | ZATCA XML category code: `S`, `Z`, `E`, or `O` — **locked after creation** |
| `zatca_category_display` | string | Human-readable category label |
| `is_default` | bool | System default — cannot be deleted |
| `is_active` | bool | Active status |
| `has_transactions` | bool | Used in at least one posted transaction |
| `edit_metadata` | object | Field-level editability info (see below) |
| `created_at` | datetime | ISO 8601 |
| `updated_at` | datetime | ISO 8601 |

### Editing Rules

| Field | Editable | Who Sets It |
|-------|----------|-------------|
| `name` | ✅ Always editable | User |
| `name_ar` | ✅ Always editable | User |
| `description` | ✅ Always editable | User |
| `tax_type` | ❌ Locked after creation | User (on create only) |
| `rate` | ❌ Locked after creation | User (on create only) |
| `zatca_category` | ❌ System-managed — never user input | **Auto-assigned by system** |

> **Why `zatca_category` is never user-input:** ZATCA categories (`S`, `Z`, `E`, `O`) are technical XML classifications used in FATOORAH e-invoices. Exposing them to users risks incorrect selections that would fail ZATCA XML validation. The system derives the correct code automatically — this matches the behaviour of Wafeq and Zoho Books.
>
> **Why `tax_type` and `rate` are locked:** Changing them after creation would silently corrupt historical invoice calculations, VAT reports, and ZATCA XML submissions. If a different rate or type is needed, create a new tax rate.

### ZATCA Category Auto-Assignment Logic

| Condition | Assigned Code | Meaning |
|-----------|--------------|---------|
| `tax_type == out_of_scope` | `O` | Out of Scope |
| `rate > 0` (any type) | `S` | Standard Rate |
| `rate == 0` (any other type) | `Z` | Zero Rate |

> `E` (Exempt) is reserved for system-seeded rates only (e.g. "Exempt", "Exempt Purchases"). Custom 0% rates created by users always receive `Z`.

**Tax Type Choices:**

| Value | Label |
|-------|-------|
| `sales` | Sales |
| `purchases` | Purchases |
| `reverse_charge` | Reverse Charge |
| `out_of_scope` | Out of Scope |

**ZATCA Category Codes (FATOORAH XML):**

| Code | Label | Rate |
|------|-------|------|
| `S` | Standard Rate | 15% |
| `Z` | Zero Rate | 0% |
| `E` | Exempt | 0% |
| `O` | Out of Scope | 0% |

---

### 7.1 List Tax Rates
- **URL:** `GET /accounting/tax-rates/`
- **Query Params:**
  - `tax_type=sales|purchases|reverse_charge|out_of_scope`
  - `zatca_category=S|Z|E|O`
  - `active=true|false` — filter by active status (default: all)
  - `search=<string>` — match name (EN/AR) or description
  - `page=<int>`
  - `page_size=<int>` (default 50, max 200)

**Success Response `200`:**
```json
{
  "count": 10,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "a1b2c3d4-...",
      "name": "VAT on Sales",
      "name_ar": "الضريبة القيمة المضافة على الإيرادات",
      "tax_type": "sales",
      "tax_type_display": "Sales",
      "rate": "15.00",
      "description": "KSA Standard Rate Output VAT",
      "zatca_category": "S",
      "zatca_category_display": "Standard Rate (S) — 15%",
      "is_default": true,
      "is_active": true,
      "has_transactions": false,
      "created_at": "2026-03-04T10:00:00Z",
      "updated_at": "2026-03-04T10:00:00Z"
    }
  ]
}
```

---

### 7.2 Create Tax Rate
- **URL:** `POST /accounting/tax-rates/`

> `zatca_category` is **never sent by the user** — the system auto-assigns it based on `tax_type` and `rate`.
> `tax_type` and `rate` are only settable at creation; they are permanently locked afterwards.

**Request Body:**
```json
{
  "name": "Special Export Rate",
  "name_ar": "معدل التصدير الخاص",
  "tax_type": "sales",
  "rate": 0,
  "description": "Special zero-rated export for Gulf customers"
}
```

**Success Response `201`:**
```json
{
  "id": "a1b2c3d4-...",
  "name": "Special Export Rate",
  "name_ar": "معدل التصدير الخاص",
  "tax_type": "sales",
  "tax_type_display": "Sales",
  "rate": "0.00",
  "description": "Special zero-rated export for Gulf customers",
  "zatca_category": "Z",
  "zatca_category_display": "Zero Rate (Z) — 0%",
  "is_default": false,
  "is_active": true,
  "has_transactions": false,
  "edit_metadata": {
    "locked_fields": ["rate", "tax_type"],
    "editable_fields": ["description", "name", "name_ar"],
    "system_managed_fields": ["zatca_category"],
    "has_transactions": false,
    "lock_reason": "IMMUTABLE_AFTER_CREATION",
    "lock_message": "Tax Type and Tax Rate are permanently locked after creation. ZATCA Category is always auto-assigned by the system..."
  },
  "created_at": "2026-03-04T10:00:00Z",
  "updated_at": "2026-03-04T10:00:00Z"
}
```

**Error Response `400`:**
```json
{
  "rate": ["Tax rate must be between 0 and 100."]
}
```

---

### 7.3 Retrieve Tax Rate
- **URL:** `GET /accounting/tax-rates/<uuid>/`

**Success Response `200`:** *(full tax rate object as in §7.2)*

---

### 7.4 Update Tax Rate
- **URL:** `PATCH /accounting/tax-rates/<uuid>/`

> **Only `name`, `name_ar`, and `description` can be updated.**
> `tax_type`, `rate`, and `zatca_category` are **permanently locked after creation** — regardless of whether the rate has been used.
> The frontend should disable these fields in the edit form.
> If a different rate or type is needed, create a new tax rate.

**Request Body (editable fields only):**
```json
{
  "name": "KSA Output VAT — Standard",
  "name_ar": "ضريبة المخرجات - المعدل القياسي",
  "description": "KSA Standard Rate 15% Output VAT"
}
```

**Success Response `200`:** *(updated tax rate object)*

**Error Response `422` (locked field in request):**
```json
{
  "error": "TAX_RATE_FIELDS_LOCKED",
  "message": "The fields ['rate'] on 'VAT on Sales' are permanently locked after creation. They affect invoice calculations, VAT reports, and ZATCA XML — changing them would corrupt historical records.",
  "locked_fields": ["rate", "tax_type", "zatca_category"],
  "editable_fields": ["description", "name", "name_ar"],
  "suggestion": "Edit only name, name_ar, or description. If you need a different rate or type, create a new tax rate."
}
```

---

### 7.5 Delete Tax Rate
- **URL:** `DELETE /accounting/tax-rates/<uuid>/`

> System default rates and rates used in transactions cannot be deleted.
> Since tax changes are rare, the recommended approach is simply to stop using a rate in new transactions rather than deleting or deactivating it.

**Success Response `200`:**
```json
{
  "message": "Tax rate 'Special Export Rate' deleted successfully."
}
```

**Error Response `422` (system default):**
```json
{
  "error": "TAX_RATE_IS_DEFAULT",
  "message": "'VAT on Sales' is a system default tax rate and cannot be deleted.",
  "suggestion": "Create a custom tax rate if you need a different one."
}
```

**Error Response `422` (used in transactions):**
```json
{
  "error": "TAX_RATE_HAS_TRANSACTIONS",
  "message": "'VAT on Sales' has been used in transactions and cannot be deleted. It must be kept to preserve historical records.",
  "suggestion": "Stop using this tax rate in new transactions. It will remain visible in historical records."
}
```

---

### 7.6 Dropdown Choices
> Returns choices for the Create Tax Rate form. `zatca_categories` is intentionally absent — it is system-managed.

- **URL:** `GET /accounting/tax-rates/choices/`

**Success Response `200`:**
```json
{
  "tax_types": [
    { "value": "sales",          "label": "Sales" },
    { "value": "purchases",      "label": "Purchases" },
    { "value": "reverse_charge", "label": "Reverse Charge" },
    { "value": "out_of_scope",   "label": "Out of Scope" }
  ]
}
```

> `zatca_category` is not exposed in the form choices. The system assigns it automatically based on `tax_type` and `rate` at creation time.

---

### Default KSA Tax Rates (seeded by `seed_tax_rates`)

| Tax Name (EN) | Tax Name (AR) | Type | Rate | ZATCA | Description |
|---------------|---------------|------|------|-------|-------------|
| Out of Scope | غير خاضع للضريبة | Out of Scope | 0% | O | Out of Scope |
| Reverse Charge | إحتساب عكسي | Reverse Charge | 15% | S | KSA Reverse Charge VAT |
| Exempt Purchases | مشتريات معفاة من الضريبة | Purchases | 0% | E | KSA exempt purchases |
| Zero-rated Purchases | مشتريات نسبة صفر | Purchases | 0% | Z | KSA Zero-rated purchases |
| VAT at Customs | الضريبة القيمة المضافة في الجمارك | Purchases | 15% | S | KSA Standard Rate Input VAT paid at Customs |
| VAT on Purchases | الضريبة القيمة المضافة على مشتريات | Purchases | 15% | S | KSA Standard Rate Input VAT |
| Exempt | معفى | Sales | 0% | E | KSA Tax Exempt Output VAT |
| Zero-Rated Exports | صادرات | Sales | 0% | Z | KSA Zero-Rated Exports |
| Zero-Rated Domestic Sales | مبيعات داخل المملكة خاضعة للضريبة بنسبة صفر | Sales | 0% | Z | KSA Zero-Rated Domestic Sales |
| VAT on Sales | الضريبة القيمة المضافة على الإيرادات | Sales | 15% | S | KSA Standard Rate Output VAT |

---

## 8. Product Categories & Items

> All endpoints require: `Authorization: Bearer <access_token>`
>
> Base path (categories): `/api/v1/products/categories/`  
> Base path (items): `/api/v1/products/items/`  
> Base path (units of measure): `/api/v1/products/uom/`
> Base path (warehouses): `/api/v1/products/warehouses/`  
> Base path (inventory adjustments): `/api/v1/products/inventory/adjustments/`

> **Design note — Tax Rate:** Tax rates are assigned at the **individual product level**, not at the category level. A single category can contain products with different tax rates (e.g. standard 15%, zero-rated 0%, exempt). This is required for ZATCA compliance where every invoice line carries its own independent tax classification. Do **not** attempt to set or read a tax rate on categories.

### Category Object Fields

| Field | Type | Writable | Description |
|-------|------|----------|-------------|
| `id` | UUID | — | Auto-generated unique identifier |
| `name` | string | ✅ | Category name (English) |
| `name_ar` | string | ✅ | Category name (Arabic, optional) |
| `description` | string | ✅ | Free-text description |
| `parent` | UUID \| null | ✅ | Parent category ID; `null` = top-level root category |
| `parent_name` | string \| null | read-only | Resolved name of the parent (for display) |
| `product_count` | integer | read-only | **Live count** of active products directly assigned to this category |
| `has_children` | bool | read-only | `true` if this category has at least one active sub-category |
| `is_active` | bool | ✅ | Active / Inactive toggle |
| `created_at` | datetime | read-only | ISO 8601 |
| `updated_at` | datetime | read-only | ISO 8601 |

---

## 16. Customers (Sales)

> All endpoints require: `Authorization: Bearer <access_token>`  
> Base path: `/api/v1/sales/customers/`

### 16.1 Customer Choices (Dropdowns)

- **URL:** `GET /sales/customers/choices/`

**Success `200`:**
```json
{
  "payment_terms": [
    { "id": "due_on_receipt", "label": "Due on Receipt" },
    { "id": "net_30", "label": "Net 30 days" }
  ],
  "vat_treatments": [
    { "id": "vat_registered_ksa", "label": "VAT registered in KSA" },
    { "id": "not_vat_registered_ksa", "label": "Not VAT registered in KSA" },
    { "id": "outside_ksa", "label": "Outside KSA" }
  ],
  "opening_balance_types": [
    { "id": "none", "label": "No opening balance" },
    { "id": "i_owe_customer", "label": "I owe this customer" },
    { "id": "customer_owes_me", "label": "Customer owes me" }
  ]
}
```

### 16.2 List Customers

- **URL:** `GET /sales/customers/`
- **Query params:** `search`, `active`, `vat_treatment`, `country`, `page`, `page_size`

### 16.3 Create Customer

- **URL:** `POST /sales/customers/`

**Request Body:**
```json
{
  "company_name": "ABC Retail",
  "primary_contact_name": "Fahad",
  "email": "fahad@abc.sa",
  "phone": "0551234567",
  "vat_treatment": "vat_registered_ksa",
  "tax_registration_number": "3XXXXXXXXXXXXXX",
  "country": "country-uuid",
  "street_address": "King Fahd Road",
  "building_number": "12",
  "district": "Olaya",
  "city": "Riyadh",
  "postal_code": "12345",
  "payment_terms": "net_30",
  "opening_balance_type": "none"
}
```

### 16.4 Retrieve / Update / Delete Customer

- **URL:** `GET/PATCH/DELETE /sales/customers/<uuid>/`
- Delete is soft delete (`is_deleted=true`).

### 16.5 Customer Field Notes

- `opening_balance_type = none`:
  - backend force-sets `opening_balance_amount=0`, `opening_balance_as_of=null`, `opening_balance_account=null`
- `opening_balance_type != none`:
  - requires `opening_balance_amount > 0`
  - requires `opening_balance_as_of`

> **`product_count` note:** Real-time database count — not cached. Reflects the exact number of products whose `category` FK points to this category. Returns `0` until the Products module is populated.

---

### 8.1 List Categories (Flat, Paginated)

- **URL:** `GET /products/categories/`
- **Auth:** Required
- **Query Params:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `search` | string | — | Case-insensitive match on `name`, `name_ar`, or `description` |
| `active` | `true` \| `false` | all | Filter by active status |
| `parent` | UUID | — | Show only direct children of this category |
| `root_only` | `true` | — | Show only top-level categories (`parent = null`) |
| `page` | int | 1 | Page number |
| `page_size` | int | 20 | Results per page (max 200) |

**Success Response `200`:**
```json
{
  "count": 5,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "a1b2c3d4-0000-0000-0000-000000000001",
      "name": "Latest Models",
      "name_ar": "أحدث الموديلات",
      "description": "Latest Model Frames",
      "parent": null,
      "parent_name": null,
      "product_count": 20,
      "has_children": false,
      "is_active": true,
      "created_at": "2026-03-04T10:00:00Z",
      "updated_at": "2026-03-04T10:00:00Z"
    },
    {
      "id": "a1b2c3d4-0000-0000-0000-000000000002",
      "name": "Brown Color",
      "name_ar": "",
      "description": "Brown Color Frames",
      "parent": null,
      "parent_name": null,
      "product_count": 10,
      "has_children": false,
      "is_active": true,
      "created_at": "2026-03-04T10:00:00Z",
      "updated_at": "2026-03-04T10:00:00Z"
    }
  ]
}
```

---

### 8.2 Create Category

- **URL:** `POST /products/categories/`
- **Auth:** Required

**Request Body:**

| Field | Required | Notes |
|-------|----------|-------|
| `name` | ✅ | English name |
| `name_ar` | — | Arabic name (optional) |
| `description` | — | Free text |
| `parent` | — | UUID of parent; omit or `null` for root |
| `is_active` | — | Defaults to `true` |

```json
{
  "name": "Latest Models",
  "name_ar": "أحدث الموديلات",
  "description": "Latest Model Frames",
  "parent": null
}
```

**Success Response `201`:** *(full category object — `product_count` will be `0` on creation)*

**Error Response `400` — validation:**
```json
{
  "name": ["This field is required."]
}
```

**Error Response `400` — circular parent reference:**
```json
{
  "parent": ["A category cannot be its own parent or ancestor."]
}
```

---

### 8.3 Retrieve Category

- **URL:** `GET /products/categories/<uuid>/`
- **Auth:** Required

**Success Response `200`:** *(full category object with live `product_count`)*

**Error Response `404`:**
```json
{ "error": "NOT_FOUND", "message": "Category not found." }
```

---

### 8.4 Update Category

- **URL:** `PATCH /products/categories/<uuid>/`
- **Auth:** Required
- **Note:** All fields are optional (partial update). There are no locked fields on categories.

**Request Body (any writable fields):**
```json
{
  "name": "Classic Models",
  "tax_rate": 15,
  "is_active": false
}
```

**Success Response `200`:** *(updated full category object)*

**Error Response `404`:**
```json
{ "error": "NOT_FOUND", "message": "Category not found." }
```

---

### 8.5 Delete Category

- **URL:** `DELETE /products/categories/<uuid>/`
- **Auth:** Required

> **Blocked** if the category has sub-categories or products assigned to it.  
> Use PATCH `is_active: false` to deactivate instead of deleting if the category is in use.

**Success Response `200`:**
```json
{
  "message": "Category 'Latest Models' deleted successfully."
}
```

**Error Response `422` — has sub-categories:**
```json
{
  "error": "CATEGORY_HAS_CHILDREN",
  "message": "'Latest Models' has 2 sub-categories and cannot be deleted.",
  "child_count": 2,
  "suggestion": "Delete or reassign child categories first."
}
```

**Error Response `422` — has products assigned:**
```json
{
  "error": "CATEGORY_HAS_PRODUCTS",
  "message": "'Black Color' has 5 products assigned and cannot be deleted.",
  "product_count": 5,
  "suggestion": "Reassign or delete the products in this category first."
}
```

**Error Response `404`:**
```json
{ "error": "NOT_FOUND", "message": "Category not found." }
```

---

### 8.5b Bulk Actions

- **URL:** `POST /products/categories/bulk/`
- **Auth:** Required

Perform one action on multiple categories in a single request. The `action` field selects the operation; `ids` is the list of category UUIDs to act on.

**Request Body:**

```json
{
  "action": "set_status",
  "ids": ["<uuid>", "<uuid>"],
  "status": "inactive"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `action` | string | ✅ | One of: `set_status`, `delete`, `duplicate` |
| `ids` | UUID[] | ✅ | Non-empty list of category UUIDs to act on |
| `status` | string | Only for `set_status` | `"active"` or `"inactive"` |

---

#### Action: `set_status`
> Sets `is_active` on all selected categories at once.

**Request:**
```json
{
  "action": "set_status",
  "ids": ["a1b2c3d4-...", "b2c3d4e5-..."],
  "status": "inactive"
}
```

**Success Response `200`:**
```json
{
  "message": "2 categories set to INACTIVE.",
  "updated": 2
}
```

**Error Response `400` — missing or invalid status:**
```json
{
  "error": "INVALID_STATUS",
  "message": "'status' must be 'active' or 'inactive' for set_status action."
}
```

---

#### Action: `delete`
> Soft-deletes each selected category. Categories that **have sub-categories or products** are automatically **skipped** (not deleted) and reported in the `skipped` array — the rest are deleted successfully.

**Request:**
```json
{
  "action": "delete",
  "ids": ["a1b2c3d4-...", "b2c3d4e5-...", "c3d4e5f6-..."]
}
```

**Success Response `200`:**
```json
{
  "message": "2 categories deleted, 1 skipped.",
  "deleted": 2,
  "skipped": [
    {
      "id": "b2c3d4e5-...",
      "name": "Black Color",
      "reason": "CATEGORY_HAS_PRODUCTS",
      "detail": "Has 5 products assigned."
    }
  ],
  "not_found": []
}
```

> `not_found` lists any IDs from the request that did not match an existing category.

---

#### Action: `duplicate`
> Creates a copy of each selected category with the name suffixed `" (Copy)"`. The copy inherits `name_ar`, `description`, `parent`, and `is_active`. Sub-categories are **not** duplicated.

**Request:**
```json
{
  "action": "duplicate",
  "ids": ["a1b2c3d4-...", "b2c3d4e5-..."]
}
```

**Success Response `201`:**
```json
{
  "message": "2 categories duplicated.",
  "created": 2,
  "categories": [
    {
      "id": "new-uuid-1-...",
      "name": "Latest Models (Copy)",
      "copied_from": "a1b2c3d4-..."
    },
    {
      "id": "new-uuid-2-...",
      "name": "Brown Color (Copy)",
      "copied_from": "b2c3d4e5-..."
    }
  ]
}
```

---

#### Common Bulk Error Responses

**`400` — invalid action:**
```json
{
  "error": "INVALID_ACTION",
  "message": "Invalid action 'foo'. Must be one of: delete, duplicate, set_status."
}
```

**`400` — empty or missing ids:**
```json
{
  "error": "IDS_REQUIRED",
  "message": "'ids' must be a non-empty list of UUIDs."
}
```

**`400` — malformed UUID in ids:**
```json
{
  "error": "INVALID_ID",
  "message": "'not-a-uuid' is not a valid UUID."
}
```

---

### 8.6 Full Category Tree

> Returns root-level categories with all children nested recursively. Useful for sidebar trees or breadcrumb builders.

- **URL:** `GET /products/categories/tree/`
- **Auth:** Required
- **Query Params:**
  - `include_inactive=true` — include inactive categories (default: active only)

**Success Response `200`:**
```json
[
  {
    "id": "a1b2c3d4-0000-0000-0000-000000000001",
    "name": "Frames",
    "name_ar": "إطارات",
    "description": "All frame categories",
    "product_count": 0,
    "is_active": true,
    "children": [
      {
        "id": "b2c3d4e5-0000-0000-0000-000000000002",
        "name": "Black Color",
        "name_ar": "",
        "description": "Black Color Frames",
        "product_count": 2,
        "is_active": true,
        "children": []
      },
      {
        "id": "c3d4e5f6-0000-0000-0000-000000000003",
        "name": "White Color",
        "name_ar": "",
        "description": "White Color Frames",
        "product_count": 60,
        "is_active": false,
        "children": []
      }
    ]
  }
]
```

> `product_count` at each tree node counts **only products directly assigned to that node**, not the sum of its children.

---

### 8.7 Parent Category Dropdown Choices

> Returns all **active** categories as a flat list for the Parent Category selector in the Create/Edit form.

- **URL:** `GET /products/categories/choices/`
- **Auth:** Required
- **Query Params:**
  - `exclude=<uuid>` — exclude a specific category (pass the category being edited to prevent self-referencing)

**Success Response `200`:**
```json
[
  { "id": "a1b2c3d4-...", "name": "Frames",      "name_ar": "إطارات", "parent": null },
  { "id": "b2c3d4e5-...", "name": "Black Color", "name_ar": "",       "parent": "a1b2c3d4-..." },
  { "id": "c3d4e5f6-...", "name": "Gold Color",  "name_ar": "",       "parent": null }
]
```

> Prepend a `{ id: null, name: "None (Top Level)" }` option in the UI for the root-level choice.

---

### 8.8 Units of Measure (UoM)

> Simple lookup table used in the item form (`cm`, `pc`, etc.). Created inline from the UI.

- **URL (list/create):** `GET/POST /products/uom/`
- **URL (delete):** `DELETE /products/uom/<uuid>/`

#### 8.8.1 UoM Object Fields

| Field | Type | Writable | Description |
|-------|------|----------|-------------|
| `id` | UUID | — | Auto-generated identifier |
| `name` | string | ✅ | Short code such as `"cm"`, `"pc"`, `"box"`; must be unique |
| `created_at` | datetime | read-only | ISO 8601 |
| `updated_at` | datetime | read-only | ISO 8601 |

#### 8.8.2 List UoM

- **URL:** `GET /products/uom/`

**Success `200`:**
```json
[
  { "id": "u1-...", "name": "cm" },
  { "id": "u2-...", "name": "pc" }
]
```

#### 8.8.3 Create UoM

- **URL:** `POST /products/uom/`

**Request `201`:**
```json
{
  "name": "cm"
}
```

#### 8.8.4 Delete UoM

- **URL:** `DELETE /products/uom/<uuid>/`

> Blocked if any product currently uses this UoM.

**Success `204`:** Empty body.

**Error `422` (in use):**
```json
{
  "error": "UOM_IN_USE",
  "message": "This unit of measure is used by one or more products and cannot be deleted."
}
```

---

### 8.9 Products / Items

> These APIs back the **Items** list, **Add Item**, and **Edit Item** screens.

- **Base path:** `/api/v1/products/items/`

#### 8.9.1 Product Object Fields

| Field | Type | Writable | Description |
|-------|------|----------|-------------|
| `id` | UUID | — | Auto-generated identifier |
| `name` | string | ✅ | Name of item |
| `code` | string | ✅ | Unique item code shown in list (e.g. `1023`) |
| `description` | string | ✅ | Optional description |
| `category` | UUID \| null | ✅ | FK to product category |
| `category_name` | string \| null | read-only | Resolved category name |
| `unit_of_measure` | UUID \| null | ✅ | FK to UoM (e.g. `cm`, `pc`) |
| `unit_of_measure_name` | string \| null | read-only | Resolved UoM name |
| `image` | file URL | ✅ | Optional product image (upload) |
| `has_attachment` | bool | read-only | `true` if item has an image (for list paperclip icon) |
| `is_active` | bool | ✅ | Active / Disabled status |
| `selling_price` | decimal string | ✅ | Default selling price in SAR |
| `purchase_price` | decimal string | ✅ | Default purchase rate in SAR |
| `avg_unit_cost` | decimal string | read-only | Weighted average cost per unit (from purchases; used for inventory value) |
| `stock_quantity` | decimal string | ✅ | Current stock quantity (for list column) |
| `inventory_value` | decimal string | read-only | `stock_quantity × avg_unit_cost` (or × purchase_price if avg not set) |
| `is_locked` | bool | read-only | `true` if item is used in invoice/bill/credit note/quote/PO — cannot delete |
| `revenue_account` | UUID \| null | ✅ | FK to `Account` used for sales (revenue) |
| `revenue_account_name` | string \| null | read-only | Account name |
| `expense_account` | UUID \| null | ✅ | FK to `Account` used for purchases/COGS |
| `expense_account_name` | string \| null | read-only | Account name |
| `inventory_account` | UUID \| null | ✅ | FK to `Account` used for inventory asset (optional) |
| `inventory_account_name` | string \| null | read-only | Account name |
| `sales_tax_rate` | UUID \| null | ✅ | FK to `TaxRate` for revenue (Revenue Tax Rate) |
| `sales_tax_rate_name` | string \| null | read-only | Tax name |
| `purchase_tax_rate` | UUID \| null | ✅ | FK to `TaxRate` for purchases (Purchase Tax Rate) |
| `purchase_tax_rate_name` | string \| null | read-only | Tax name |
| `created_at` | datetime | read-only | ISO 8601 (Created) |
| `updated_at` | datetime | read-only | ISO 8601 (Modified) |

#### 8.9.2 List Items (Sheet View)

- **URL:** `GET /products/items/`
- **Auth:** Required

**Query params:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `search` | string | — | Match on `name`, `code`, `description` |
| `active` | `true` \| `false` | all | Filter by active status |
| `category` | UUID | — | Filter by category |
| `page` | int | 1 | Page number |
| `page_size` | int | 20 | Page size (max 200) |

**Success `200`:**
```json
{
  "count": 3,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "p-1023-...",
      "code": "1023",
      "name": "Black Color Frames",
      "description": "Black frame with width 1meter",
      "category": "cat-black-...",
      "category_name": "Black Color",
      "unit_of_measure": "u1-...",
      "unit_of_measure_name": "cm",
      "image": "http://127.0.0.1:8000/media/product-images/1023.png",
      "has_attachment": true,
      "is_active": true,
      "selling_price": "250.00",
      "purchase_price": "150.00",
      "avg_unit_cost": "150.00",
      "stock_quantity": "50.00",
      "inventory_value": "7500.00",
      "is_locked": false,
      "revenue_account": "acc-411-...",
      "revenue_account_name": "411 - Sales",
      "expense_account": "acc-511-...",
      "expense_account_name": "511 - Cost of Goods Sold",
      "inventory_account": "acc-141-...",
      "inventory_account_name": "141 - Inventory",
      "sales_tax_rate": "tax-std-...",
      "sales_tax_rate_name": "VAT on Sales (15%)",
      "purchase_tax_rate": "tax-out-...",
      "purchase_tax_rate_name": "Out of Scope (0.00%)",
      "created_at": "2026-03-04T10:00:00Z",
      "updated_at": "2026-03-04T10:00:00Z"
    }
  ]
}
```

#### 8.9.3 Create Item

- **URL:** `POST /products/items/`

**Request body (key fields):**
```json
{
  "name": "New Product",
  "code": "1026",
  "description": "Aluminum frame with width 1meter",
  "category": "cat-black-...",
  "unit_of_measure": "u1-...",
  "is_active": true,
  "selling_price": 250,
  "purchase_price": 150,
  "stock_quantity": 0,
  "revenue_account": "acc-411-...",
  "expense_account": "acc-511-...",
  "inventory_account": "acc-141-...",
  "sales_tax_rate": "tax-std-...",
  "purchase_tax_rate": "tax-out-..."
}
```

> Image upload should be sent as multipart/form-data (`image` file field). All numeric values can be passed as numbers or strings; the API serializes them as strings in responses.

**Success `201`:** *(full product object as in list item)*

#### 8.9.4 Retrieve / Edit Item

- **URL (retrieve):** `GET /products/items/<uuid>/`
- **URL (update):** `PATCH /products/items/<uuid>/`

Use the same fields as in **Create Item**; all are optional for PATCH.

**Error `404`:**
```json
{ "error": "NOT_FOUND", "message": "Product not found." }
```

#### 8.9.5 Delete Item

- **URL:** `DELETE /products/items/<uuid>/`

Soft-deletes the item (`is_deleted=true`). **Blocked** if the item is in use (invoice, bill, credit note, quote, or purchase order) — returns `422 ITEM_LOCKED`.

**Success `204`:** Empty body.

**Error `422` (item locked):**
```json
{
  "error": "ITEM_LOCKED",
  "message": "Item 'Black Color Frames' is used in an invoice, bill, credit note, quote, or purchase order and cannot be deleted.",
  "suggestion": "Remove or replace the item from those documents first."
}
```

**Error `404`:**
```json
{ "error": "NOT_FOUND", "message": "Product not found." }
```

---

### 8.10 Warehouses

- **URL (list/create):** `GET/POST /products/warehouses/`
- **URL (retrieve/update/delete):** `GET/PATCH/DELETE /products/warehouses/<uuid>/`
- **URL (bulk actions):** `POST /products/warehouses/bulk/` (duplicate, delete)

**Warehouse object:**

| Field | Type | Writable | Description |
|-------|------|----------|-------------|
| `id` | UUID | — | Auto-generated identifier |
| `code` | string | ✅ | Warehouse code shown in list (e.g. `WH-001`) |
| `name` | string | ✅ | Warehouse name (EN) |
| `name_ar` | string | ✅ | Warehouse name (AR, optional) |
| `phone` | string | ✅ | Phone number |
| `street_address` | string | ✅ | Street address (EN) |
| `street_address_ar` | string | ✅ | Street address (AR) |
| `building_number` | string | ✅ | Building number |
| `district` | string | ✅ | District (EN) |
| `district_ar` | string | ✅ | District (AR) |
| `city` | string | ✅ | City (EN) |
| `city_ar` | string | ✅ | City (AR) |
| `postal_code` | string | ✅ | Postal code |
| `address_display` | string | read-only | Combined address string for the list column |
| `is_active` | bool | ✅ | Active / inactive |
| `is_locked` | bool | read-only | `true` if the warehouse has any **posted** inventory transactions |
| `coa_account` | UUID | ✅ | Optional link to a CoA account (for Primary Warehouse this is CoA `1151`) |
| `coa_account_code` | string | read-only | Linked CoA account code |
| `coa_account_name` | string | read-only | Linked CoA account name |
| `created_at` | datetime | read-only | ISO 8601 |
| `updated_at` | datetime | read-only | ISO 8601 |

> **Locked Warehouse:** deletion is blocked (`422 WAREHOUSE_LOCKED`) if the warehouse has any **posted** inventory transactions (currently: posted inventory adjustments; later sales/purchases).

> **Primary Warehouse & CoA sync:** The system seeds a default **Primary Warehouse** (`WH-001`) linked to CoA account code **`1151`**. If you rename the warehouse, the linked CoA account name is automatically updated to match.

#### 8.10.1 Bulk Actions (Make a copy / Delete)

- **URL:** `POST /products/warehouses/bulk/`

**Request:**
```json
{ "action": "duplicate", "ids": ["<uuid>", "<uuid>"] }
```

**Actions:**
- `duplicate` → creates `"(Copy)"` warehouse rows and generates unique codes like `WH-001-COPY`, `WH-001-COPY-2`, …
- `delete` → soft-deletes warehouses; locked ones are skipped and returned in `skipped[]`

**Success `200/201`:** returns `created` or `deleted` counts, plus `skipped` and `not_found` lists.

---

### 8.11 Inventory Adjustments

> Supports **multiple adjustment lines in one entry**, posted together into **one journal entry**.

#### 8.11.1 List Inventory Adjustments (Grid = one row per line)

- **URL:** `GET /products/inventory/adjustments/`
- **Auth:** Required

**Query params:**

| Param | Type | Description |
|------|------|-------------|
| `search` | string | Match `reference`, `adjustment_id`, item name/code, account name/code |
| `status` | `draft` \| `posted` | Filter by status |
| `warehouse` | UUID | Filter by warehouse |
| `item` | UUID | Filter by item/product |
| `date_from` | `YYYY-MM-DD` | From date |
| `date_to` | `YYYY-MM-DD` | To date |
| `page` / `page_size` | int | Pagination |

**Success `200`:** (flattened list, one row per line)
```json
{
  "count": 2,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "line-uuid-...",
      "adjustment_id": "ADJ-000001",
      "reference": "11",
      "status": "posted",
      "date": "2026-02-26",
      "warehouse_id": "wh-uuid-...",
      "warehouse_name": "Primary Warehouse",
      "product": "prod-uuid-...",
      "product_code": "1024",
      "product_name": "Aluminum Frame",
      "description": "Opening Stock",
      "quantity_delta": "50.00",
      "inventory_value_delta": "7500.00",
      "account": "acc-uuid-...",
      "account_code": "511",
      "account_name": "Cost of Goods Sold",
      "total_adjustment_amount": "7500.00",
      "created_at": "2026-02-26T12:30:00Z",
      "updated_at": "2026-02-26T12:30:00Z"
    }
  ]
}
```

#### 8.11.2 Create Draft Inventory Adjustment (multiple lines)

- **URL:** `POST /products/inventory/adjustments/`

```json
{
  "reference": "11",
  "date": "2026-02-26",
  "warehouse": "wh-uuid-...",
  "lines": [
    {
      "product": "prod-uuid-...",
      "description": "Opening Stock",
      "quantity_delta": 50,
      "inventory_value_delta": 7500,
      "account": "acc-uuid-..."
    }
  ]
}
```

**Success `201`:** full header + lines.

#### 8.11.3 Retrieve / Edit Draft

- **URL (retrieve):** `GET /products/inventory/adjustments/<uuid>/`
- **URL (edit):** `PATCH /products/inventory/adjustments/<uuid>/`

> Only `draft` adjustments can be edited; posted returns `422 ADJUSTMENT_POSTED`.

#### 8.11.4 Delete Draft

- **URL:** `DELETE /products/inventory/adjustments/<uuid>/`

> Only `draft` adjustments can be deleted; posted returns `422 ADJUSTMENT_POSTED`.

#### 8.11.5 Post Draft (creates JE + updates stock)

- **URL:** `POST /products/inventory/adjustments/<uuid>/post/`

Posting rules (server-enforced):
- Validates each line has non-zero `quantity_delta` and `inventory_value_delta`
- Requires each product to have `inventory_account` set
- Creates a **balanced** journal entry (one per adjustment) and posts it (sequential JE reference)
- Updates:
  - `Product.stock_quantity += quantity_delta`
  - `Product.avg_unit_cost` weighted-average when qty/value are positive
- Sets adjustment:
  - `status = posted`
  - `posted_at`
  - `adjustment_id = ADJ-000001` (sequential)


## 9. ZATCA Compliance Error Codes

All ZATCA compliance violations return HTTP `422 Unprocessable Entity` with a structured body.

### Product Category Errors

| Error Code | Trigger | HTTP |
|------------|---------|------|
| `CATEGORY_HAS_CHILDREN` | Delete (single or bulk) a category that has sub-categories | 422 / skipped |
| `CATEGORY_HAS_PRODUCTS` | Delete (single or bulk) a category that has products assigned | 422 / skipped |
| `INVALID_ACTION` | Bulk request with unknown action | 400 |
| `INVALID_STATUS` | Bulk `set_status` without valid `status` value | 400 |
| `IDS_REQUIRED` | Bulk request with missing or empty `ids` | 400 |
| `INVALID_ID` | Bulk request with a non-UUID value in `ids` | 400 |

### Product / Item Errors

| Error Code | Trigger | HTTP |
|------------|---------|------|
| `ITEM_LOCKED` | Delete an item that is used in an invoice, bill, credit note, quote, or purchase order | 422 |

### Tax Rate Errors

| Error Code | Trigger | HTTP |
|------------|---------|------|
| `TAX_RATE_FIELDS_LOCKED` | Attempt to change `rate`, `tax_type`, or `zatca_category` on any existing tax rate | 422 |
| `TAX_RATE_IS_DEFAULT` | Attempt to delete a system default tax rate | 422 |
| `TAX_RATE_HAS_TRANSACTIONS` | Attempt to delete a tax rate that has been used in transactions | 422 |

### Account Errors

| Error Code | Trigger | HTTP |
|------------|---------|------|
| `ACCOUNT_LOCKED` | Edit/delete a system account (`is_locked=true`) | 422 |
| `ACCOUNT_HAS_CHILDREN` | Delete an account that has children | 422 |
| `ACCOUNT_HAS_TRANSACTIONS` | Delete an account with posted transactions | 422 |
| `ACCOUNT_HAS_BALANCE` | Change `account_type` when balance ≠ 0 | 422 |
| `FIELD_LOCKED_AFTER_TRANSACTION` | Change structural field after first transaction | 422 |
| `ZATCA_MAPPING_VIOLATION` | Change structural field on a ZATCA-mapped account | 422 |
| `ACCOUNT_ARCHIVED` | Use an archived account in a new transaction | 422 |
| `CANNOT_ARCHIVE_ROOT` | Archive a root-level system account | 422 |

### Journal Entry Errors

| Error Code | Trigger | HTTP |
|------------|---------|------|
| `JOURNAL_ENTRY_POSTED` | Edit or delete a posted journal entry | 422 |
| `JOURNAL_ENTRY_ALREADY_REVERSED` | Reverse an already-reversed entry | 422 |
| `JOURNAL_ENTRY_NOT_BALANCED` | Post an entry where Σ debits ≠ Σ credits | 422 |
| `JOURNAL_ENTRY_INSUFFICIENT_LINES` | Post an entry with fewer than 2 lines | 422 |

### Field-Level Lock Rules

| Scenario | Locked Fields | Always Editable |
|----------|--------------|-----------------|
| System account (`is_locked=true`) | All except `name`, `name_ar` | `name`, `name_ar` |
| ZATCA-mapped account | `code`, `account_type`, `parent`, `cash_flow_type`, `account_sub_type` | `name`, `name_ar`, `enable_payment`, `show_in_expense_claim` |
| Has transactions | `code`, `account_type`, `parent`, `cash_flow_type` | `name`, `name_ar`, `enable_payment`, `show_in_expense_claim`, `account_sub_type` |
| Non-zero balance | `account_type` (additionally) | — |

---

## 10. General Error Reference

| HTTP Code | Meaning |
|-----------|---------|
| `200` | OK |
| `201` | Created |
| `204` | No Content |
| `400` | Validation error — check response body |
| `401` | Missing or invalid token |
| `403` | Permission denied (not admin / insufficient role) |
| `404` | Record not found |
| `422` | ZATCA / business rule violation — structured error body returned |

---

### Common Auth Errors

```json
{ "detail": "Authentication credentials were not provided." }
```
→ Missing `Authorization` header.

```json
{ "detail": "Given token not valid for any token type" }
```
→ Token expired — use **Refresh Token** endpoint (`§1.3`) to get a new access token.

```json
{ "non_field_errors": ["Invalid email or password."] }
```
→ Wrong credentials on login.

---

## 11. Company Settings

> All endpoints require: `Authorization: Bearer <access_token>` (admin token)  
> Base path: `/api/v1/main/company-settings/`

Organization settings are stored as a **singleton** record. `GET` will auto-create it if missing.  
`POST` is supported as an **upsert** (create if missing, otherwise update) to simplify frontend integration.

### 11.1 Get Company Settings

- **URL:** `GET /main/company-settings/`

**Success `200`:**
```json
{
  "id": "uuid-...",
  "logo": "http://127.0.0.1:8000/media/company-logo/logo.png",
  "company_name": "Newsome",
  "company_name_ar": "نيوسوم",
  "street_address": "Prince Mohammed Bin Fahd Street",
  "street_address_ar": "",
  "building_number": "12",
  "district": "Al Khobar",
  "district_ar": "",
  "city": "Al Aziziyah",
  "city_ar": "",
  "country": "country-uuid-...",
  "country_name": "Saudi Arabia",
  "postal_code": "31952",
  "cr_number": "1010XXXXXX",
  "vat_registration_number": "3XXXXXXXXXXXXXX",
  "industry": "Retail",
  "email": "info@newsome.sa",
  "phone": "0551234567",
  "created_at": "2026-03-04T10:00:00Z",
  "updated_at": "2026-03-04T10:00:00Z"
}
```

### 11.2 Update Company Settings

- **URL:** `PATCH /main/company-settings/`
- **Content-Type:** `application/json` (no logo) OR `multipart/form-data` (with logo)

**JSON example:**
```json
{
  "company_name": "Newsome",
  "street_address": "Prince Mohammed Bin Fahd Street",
  "city": "Al Aziziyah",
  "phone": "0551234567",
  "vat_registration_number": "3XXXXXXXXXXXXXX"
}
```

**Multipart example fields:**
- `logo`: file
- plus any of the JSON fields above

---

## 12. Suppliers (Purchase)

> All endpoints require: `Authorization: Bearer <access_token>`  
> Base path: `/api/v1/purchases/suppliers/`

### 12.1 Supplier Object Fields

| Field | Type | Writable | Description |
|-------|------|----------|-------------|
| `id` | UUID | — | Supplier ID |
| `company_name` | string | ✅ | Company name (EN) |
| `company_name_ar` | string | ✅ | Company name (AR) |
| `primary_contact_name` | string | ✅ | Primary contact |
| `email` | string | ✅ | Email |
| `phone` | string | ✅ | Phone number |
| `vat_treatment` | string | ✅ | `vat_registered_ksa` \| `not_vat_registered_ksa` \| `outside_ksa` |
| `tax_registration_number` | string | ✅ | Tax registration number |
| `country` | UUID \| null | ✅ | Country ID (from `/api/v1/main/countries/`) |
| `country_name` | string \| null | read-only | Country name |
| `street_address` | string | ✅ | Street address (EN) |
| `street_address_ar` | string | ✅ | Street address (AR) |
| `building_number` | string | ✅ | Building number |
| `land_identifier` | string | ✅ | Land identifier |
| `district` | string | ✅ | District (EN) |
| `district_ar` | string | ✅ | District (AR) |
| `city` | string | ✅ | City (EN) |
| `city_ar` | string | ✅ | City (AR) |
| `postal_code` | string | ✅ | Postal code |
| `payment_terms` | string \| null | ✅ | `due_on_receipt`, `net_15`, `net_30`, `net_45`, `net_60`, `net_90` |
| `opening_balance_type` | string | ✅ | `none` \| `i_owe_vendor` \| `vendor_owes_me` |
| `opening_balance_amount` | decimal string | ✅ | Absolute amount (direction from `opening_balance_type`) |
| `opening_balance_as_of` | date \| null | ✅ | ISO date, e.g. `2026-03-04` |
| `opening_balance_account` | UUID \| null | ✅ | Optional `Account` for later posting |
| `is_active` | bool | ✅ | Active/inactive |
| `created_at` | datetime | read-only | ISO 8601 |
| `updated_at` | datetime | read-only | ISO 8601 |

---

### 12.2 Supplier Choices (Dropdowns)

- **URL:** `GET /purchases/suppliers/choices/`

**Success `200`:**
```json
{
  "payment_terms": [
    { "id": "due_on_receipt", "label": "Due on Receipt" },
    { "id": "net_15", "label": "Net 15 days" }
  ],
  "vat_treatments": [
    { "id": "vat_registered_ksa", "label": "VAT registered in KSA" },
    { "id": "not_vat_registered_ksa", "label": "Not VAT registered in KSA" },
    { "id": "outside_ksa", "label": "Outside KSA" }
  ],
  "opening_balance_types": [
    { "id": "none", "label": "No opening balance" },
    { "id": "i_owe_vendor", "label": "I owe this vendor" },
    { "id": "vendor_owes_me", "label": "Vendor owes me" }
  ]
}
```

---

### 12.3 List Suppliers (Paginated)

- **URL:** `GET /purchases/suppliers/`
- **Query Params:**
  - `search=<string>` — company/contact/email/phone/TRN
  - `active=true|false`
  - `vat_treatment=<string>`
  - `country=<uuid>`
  - `page`, `page_size`

---

### 12.4 Create Supplier

- **URL:** `POST /purchases/suppliers/`

**Request Body (example):**
```json
{
  "company_name": "ABC Trading",
  "primary_contact_name": "Ahmed",
  "email": "ahmed@abc.com",
  "phone": "0551234567",
  "vat_treatment": "vat_registered_ksa",
  "tax_registration_number": "3XXXXXXXXXXXXXX",
  "country": "country-uuid-...",
  "payment_terms": "net_30",
  "opening_balance_type": "none"
}
```

**Success `201`:** full supplier object.

---

### 12.5 Retrieve / Update / Delete Supplier

- **URL:** `GET/PATCH/DELETE /purchases/suppliers/<uuid>/`

**Delete** is currently a soft delete (`is_deleted=true`). Future versions can block deletion when used in bills/payments.

---

## 13. Bills (Purchase)

> All endpoints require: `Authorization: Bearer <access_token>`  
> Base path: `/api/v1/purchases/bills/`

### 13.1 List Bills (List page)

- **URL:** `GET /purchases/bills/`
- **Query Params:**
  - `search=<string>` (bill number, note, supplier)
  - `status=draft|posted`
  - `supplier=<uuid>`
  - `date_from=YYYY-MM-DD`
  - `date_to=YYYY-MM-DD`
  - `page`, `page_size`

**Success `200` (paginated):**
```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": "bill-uuid",
      "status": "draft",
      "status_display": "Draft",
      "bill_number": "BILL-2024-001",
      "supplier": "supplier-uuid",
      "supplier_name": "ABC Supplies",
      "bill_date": "2026-03-23",
      "due_date": "2026-04-22",
      "total_amount": "7500.00",
      "payments": "0.00",
      "balance": "7500.00",
      "line_item_description": "New Stock",
      "account_display": "511 - Cost of Goods Sold",
      "qty": "1.00",
      "rate": "7500.00",
      "tax_rate_display": "VAT on Purchases (15.00%)",
      "amount": "8625.00",
      "created_at": "2026-03-23T07:30:00Z",
      "updated_at": "2026-03-23T07:30:00Z"
    }
  ]
}
```

**List row fields (optimized for Bills grid):**

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Bill ID |
| `status` | string | `draft` or `posted` |
| `status_display` | string | Human readable status |
| `bill_number` | string | Bill reference number |
| `supplier` | UUID | Supplier ID |
| `supplier_name` | string | Supplier name |
| `bill_date` | date | Bill date |
| `due_date` | date \| null | Due date |
| `total_amount` | decimal string | Total bill amount |
| `payments` | decimal string | Paid amount (`paid_amount`) |
| `balance` | decimal string | Remaining balance |
| `line_item_description` | string | First line description (list preview) |
| `account_display` | string | First line account display (`code - name`) |
| `qty` | decimal string | First line quantity |
| `rate` | decimal string | First line unit price |
| `tax_rate_display` | string | First line tax display |
| `amount` | decimal string | First line line total |
| `created_at` | datetime | Created timestamp |
| `updated_at` | datetime | Updated timestamp |

### 13.2 Create Bill

- **URL:** `POST /purchases/bills/`

**Request Body:**
```json
{
  "bill_number": "BILL-2024-001",
  "supplier": "supplier-uuid",
  "bill_date": "2026-03-23",
  "due_date": "2026-04-22",
  "note": "Stock purchase",
  "lines": [
    {
      "description": "New Stock",
      "account": "account-uuid",
      "quantity": "1",
      "unit_price": "7500",
      "tax_rate": "tax-rate-uuid",
      "discount_percent": "0"
    }
  ]
}
```

**Success `201`:** returns full bill object (same schema as Retrieve Bill).

### 13.3 Retrieve Bill

- **URL:** `GET /purchases/bills/<uuid>/`
- Returns header + nested lines + computed totals.

**Success `200` (example):**
```json
{
  "id": "bill-uuid",
  "bill_number": "BILL-2024-001",
  "supplier": "supplier-uuid",
  "supplier_name": "ABC Supplies",
  "bill_date": "2026-03-23",
  "due_date": "2026-04-22",
  "note": "Stock purchase",
  "attachment": null,
  "status": "draft",
  "status_display": "Draft",
  "posted_at": null,
  "journal_entry": null,
  "subtotal": "7500.00",
  "total_vat": "1125.00",
  "total_amount": "8625.00",
  "paid_amount": "0.00",
  "balance_amount": "8625.00",
  "lines": [
    {
      "id": "line-uuid",
      "description": "New Stock",
      "account": "account-uuid",
      "account_code": "511",
      "account_name": "Cost of Goods Sold",
      "quantity": "1.00",
      "unit_price": "7500.00",
      "tax_rate": "tax-rate-uuid",
      "tax_rate_name": "VAT on Purchases",
      "tax_rate_percent": "15.00",
      "discount_percent": "0.00",
      "line_order": 0,
      "line_subtotal": "7500.00",
      "line_tax_amount": "1125.00",
      "line_total": "8625.00"
    }
  ],
  "created_at": "2026-03-23T07:30:00Z",
  "updated_at": "2026-03-23T07:30:00Z"
}
```

### 13.4 Update Bill

- **URL:** `PATCH /purchases/bills/<uuid>/`
- Draft bills are editable.
- Posted bills return `422 BILL_POSTED`.

**Error `422` (posted):**
```json
{
  "error": "BILL_POSTED",
  "message": "Posted bill cannot be edited."
}
```

### 13.5 Delete Bill

- **URL:** `DELETE /purchases/bills/<uuid>/`
- Soft delete for draft bills.
- Posted bills return `422 BILL_POSTED`.

**Success:** `204 No Content`

### 13.6 Confirm & Post Bill

- **URL:** `POST /purchases/bills/<uuid>/post/`
- Changes bill `status` to `posted` and sets `posted_at`.
- Optional: creates and posts a Journal Entry.

**Request Body (minimum):**
```json
{}
```

**Request Body (with JE creation):**
```json
{
  "create_journal_entry": true,
  "payable_account": "optional-account-uuid",
  "vat_account": "optional-account-uuid",
  "posting_date": "2026-03-23",
  "memo": "Post purchase bill"
}
```

**JE defaults when `create_journal_entry=true`:**
- `payable_account`: CoA code `211` (Accounts Payable) if not provided
- `vat_account`: CoA code `116` (VAT Receivable) if not provided

**Success `200`:** returns full bill object (`status=posted`, `posted_at`, `journal_entry`).

**Errors:**
- `404 NOT_FOUND` (bill not found)
- `422 BILL_ALREADY_POSTED`
- `422 NO_LINES`
- `422 POST_VALIDATION_ERROR` (invalid payable/vat account, missing required default account)

---

## 14. Supplier Payments

> All endpoints require: `Authorization: Bearer <access_token>`  
> Base path: `/api/v1/purchases/supplier-payments/`

### 14.1 Supplier Payment Object

| Field | Type | Writable | Description |
|---|---|---|---|
| `id` | UUID | — | Payment ID |
| `payment_number` | string | ✅ | Unique payment number |
| `supplier` | UUID | ✅ | Supplier ID |
| `supplier_name` | string | read-only | Supplier name |
| `paid_through` | UUID | ✅ | Cash/Bank account ID |
| `paid_through_code` | string | read-only | Paid-through account code |
| `paid_through_name` | string | read-only | Paid-through account name |
| `payment_type` | string | ✅ | `bill_payment` \| `advance_payment` |
| `payment_type_display` | string | read-only | UI display label |
| `amount_paid` | decimal string | ✅ | Total amount received for this payment |
| `payment_date` | date | ✅ | Payment date |
| `description` | string | ✅ | Optional memo/description |
| `is_posted` | bool | ✅ | Posted flag |
| `amount_applied` | decimal string | read-only | Sum of allocations applied to bills |
| `remaining_amount` | decimal string | read-only | `amount_paid - amount_applied` |
| `allocations` | array | ✅ | Bill allocations (for bill payments) |
| `created_at` | datetime | read-only | ISO 8601 |
| `updated_at` | datetime | read-only | ISO 8601 |

### 14.2 List Supplier Payments

- **URL:** `GET /purchases/supplier-payments/`
- **Query params:** `search`, `supplier`, `payment_type`, `page`, `page_size`

### 14.3 Create Supplier Payment

- **URL:** `POST /purchases/supplier-payments/`

**Request Body (Bill Payment):**
```json
{
  "payment_number": "SP-0001",
  "supplier": "supplier-uuid",
  "paid_through": "account-uuid",
  "payment_type": "bill_payment",
  "amount_paid": "2500.00",
  "payment_date": "2026-03-24",
  "description": "Bank transfer",
  "allocations": [
    { "bill": "bill-uuid-1", "amount": "1000.00" },
    { "bill": "bill-uuid-2", "amount": "1500.00" }
  ]
}
```

**Request Body (Advance Payment):**
```json
{
  "payment_number": "SP-0002",
  "supplier": "supplier-uuid",
  "paid_through": "account-uuid",
  "payment_type": "advance_payment",
  "amount_paid": "1000.00",
  "payment_date": "2026-03-24",
  "description": "Advance to supplier"
}
```

**Validation rules:**
- `amount_paid` must be > 0
- Allocation bill must belong to the same supplier
- Allocation bill must be `posted`
- Allocation amount cannot exceed current bill balance
- Total allocations cannot exceed `amount_paid`
- For `advance_payment`, allocations are ignored

### 14.4 Retrieve / Update / Delete Supplier Payment

- **URL:** `GET/PATCH/DELETE /purchases/supplier-payments/<uuid>/`
- On update/delete, previous allocations are automatically rolled back from bill `paid_amount`, then reapplied (for PATCH).

### 14.5 Outstanding Bills for Supplier (Apply Payment Grid)

- **URL:** `GET /purchases/supplier-payments/outstanding-bills/?supplier=<uuid>`
- Returns posted bills for the supplier where `balance_amount > 0`.

**Success `200`:**
```json
{
  "results": [
    {
      "id": "bill-uuid",
      "bill_number": "BILL-2024-001",
      "bill_date": "2026-03-20",
      "total_amount": "5000.00",
      "paid_amount": "1000.00",
      "balance_amount": "4000.00"
    }
  ],
  "payment_types": [
    { "id": "bill_payment", "label": "Bill Payments" },
    { "id": "advance_payment", "label": "Advance Payments" }
  ]
}
```

**Common errors:**
- `400 SUPPLIER_REQUIRED` (supplier query missing)
- `404 NOT_FOUND` (payment not found)
- `422 VALIDATION_ERROR` (allocation/business validation)

---

## 15. Debit Notes (Purchase)

> All endpoints require: `Authorization: Bearer <access_token>`  
> Base path: `/api/v1/purchases/debit-notes/`

### 15.1 List Debit Notes

- **URL:** `GET /purchases/debit-notes/`
- **Query params:** `search`, `supplier`, `status`, `date_from`, `date_to`, `page`, `page_size`

### 15.2 Create Debit Note

- **URL:** `POST /purchases/debit-notes/`

**Request Body:**
```json
{
  "debit_note_number": "DN-0001",
  "supplier": "supplier-uuid",
  "date": "2026-03-24",
  "note": "Return of damaged items",
  "lines": [
    {
      "description": "Damaged frame return",
      "account": "account-uuid",
      "quantity": "1",
      "unit_price": "1000",
      "tax_rate": "tax-rate-uuid",
      "discount_percent": "0"
    }
  ]
}
```

**Success `201`:** returns full debit note object with computed totals and nested lines.

### 15.3 Retrieve / Update / Delete Debit Note

- **URL:** `GET/PATCH/DELETE /purchases/debit-notes/<uuid>/`
- Draft debit notes are editable/deletable.
- Posted debit notes are locked:
  - `PATCH` returns `422 DEBIT_NOTE_POSTED`
  - `DELETE` returns `422 DEBIT_NOTE_POSTED`

### 15.4 Debit Note Object Fields

| Field | Type | Writable | Description |
|---|---|---|---|
| `id` | UUID | — | Debit note ID |
| `debit_note_number` | string | ✅ | Unique debit note number |
| `supplier` | UUID | ✅ | Supplier ID |
| `supplier_name` | string | read-only | Supplier name |
| `date` | date | ✅ | Debit note date |
| `note` | string | ✅ | Optional note |
| `status` | string | read-only | `draft` \| `posted` |
| `status_display` | string | read-only | Human-readable status |
| `posted_at` | datetime \| null | read-only | Posting timestamp |
| `journal_entry` | UUID \| null | read-only | Linked JE if posted in future flow |
| `subtotal` | decimal string | read-only | Sum of line subtotals |
| `total_vat` | decimal string | read-only | Sum of line VAT |
| `total_amount` | decimal string | read-only | Subtotal + VAT |
| `lines` | array | ✅ | Debit note line items |
| `created_at` | datetime | read-only | ISO 8601 |
| `updated_at` | datetime | read-only | ISO 8601 |

---

## 17. Quotes (Sales)

> All endpoints require: `Authorization: Bearer <access_token>`  
> Base path: `/api/v1/sales/quotes/`

### 17.1 Quote Choices

- **URL:** `GET /sales/quotes/choices/`
- Returns quote statuses:
  - `draft`, `sent`, `accepted`, `rejected`

### 17.2 List Quotes

- **URL:** `GET /sales/quotes/`
- **Query params:** `search`, `status`, `customer`, `date_from`, `date_to`, `page`, `page_size`

### 17.3 Create Quote

- **URL:** `POST /sales/quotes/`

**Request Body:**
```json
{
  "quote_number": "QT-100101",
  "customer": "customer-uuid",
  "date": "2026-03-24",
  "note": "Quotation for frames",
  "lines": [
    {
      "product": "product-uuid",
      "description": "Aluminum Frame",
      "quantity": "1",
      "unit_price": "1000",
      "tax_rate": "tax-rate-uuid",
      "discount_percent": "0"
    }
  ]
}
```

**Success `201`:** returns full quote object including computed totals and nested lines.

### 17.4 Retrieve / Update / Delete Quote

- **URL:** `GET/PATCH/DELETE /sales/quotes/<uuid>/`
- `PATCH` supports updating header and replacing `lines`.
- `DELETE` is soft delete (`is_deleted=true`).

### 17.5 Save & Send Quote

- **URL:** `POST /sales/quotes/<uuid>/send/`
- Sets quote `status` to `sent`.
- Returns updated quote object.

### 17.6 Quote Object Fields

| Field | Type | Writable | Description |
|---|---|---|---|
| `id` | UUID | — | Quote ID |
| `quote_number` | string | ✅ | Unique quote number |
| `customer` | UUID | ✅ | Customer ID |
| `customer_name` | string | read-only | Customer name |
| `date` | date | ✅ | Quote date |
| `note` | string | ✅ | Note/memo |
| `attachment` | file/url \| null | ✅ | Optional attachment |
| `status` | string | ✅ | `draft` \| `sent` \| `accepted` \| `rejected` |
| `status_display` | string | read-only | Human-readable status |
| `issuer_details` | object | read-only | Auto from Company Settings (name, address, VAT, logo) |
| `subtotal_before_discount` | decimal string | read-only | Sum of line gross amounts |
| `discount_total` | decimal string | read-only | Sum of line discounts |
| `total_vat` | decimal string | read-only | Sum of line VAT |
| `total_amount` | decimal string | read-only | Final quote total |
| `lines` | array | ✅ | Quote lines |
| `created_at` | datetime | read-only | ISO 8601 |
| `updated_at` | datetime | read-only | ISO 8601 |

---

## 18. Invoices (Sales)

> All endpoints require: `Authorization: Bearer <access_token>`  
> Base path: `/api/v1/sales/invoices/`

### 18.1 Invoice Choices

- **URL:** `GET /sales/invoices/choices/`
- Returns invoice statuses:
  - `draft`, `posted`, `paid`, `partially_paid`, `overdue`

### 18.2 List Invoices

- **URL:** `GET /sales/invoices/`
- **Query params:** `search`, `status`, `customer`, `date_from`, `date_to`, `page`, `page_size`

### 18.3 Create Invoice (Save as Draft)

- **URL:** `POST /sales/invoices/`

**Request Body:**
```json
{
  "invoice_number": "INV-10101",
  "customer": "customer-uuid",
  "date": "2026-03-24",
  "due_date": "2026-04-24",
  "note": "Invoice for items",
  "lines": [
    {
      "product": "product-uuid",
      "description": "Aluminum Frame",
      "account": "account-uuid",
      "quantity": "1",
      "unit_price": "1000",
      "tax_rate": "tax-rate-uuid",
      "discount_percent": "0"
    }
  ]
}
```

### 18.4 Retrieve / Update / Delete Invoice

- **URL:** `GET/PATCH/DELETE /sales/invoices/<uuid>/`
- Draft invoices are editable/deletable.
- Posted invoices are locked:
  - `PATCH` returns `422 INVOICE_POSTED`
  - `DELETE` returns `422 INVOICE_POSTED`

### 18.5 Confirm & Post Invoice

- **URL:** `POST /sales/invoices/<uuid>/post/`
- Sets:
  - `status = posted`
  - `posted_at = now`
- Optional payload:
```json
{
  "qr_code_text": "BASE64_OR_TEXT_FOR_ZATCA_QR"
}
```

### 18.6 Invoice Object Fields

| Field | Type | Writable | Description |
|---|---|---|---|
| `id` | UUID | — | Invoice ID |
| `invoice_number` | string | ✅ | Unique invoice number |
| `customer` | UUID | ✅ | Customer ID |
| `customer_name` | string | read-only | Customer name |
| `date` | date | ✅ | Invoice date |
| `due_date` | date \| null | ✅ | Due date |
| `note` | string | ✅ | Note/memo |
| `attachment` | file/url \| null | ✅ | Optional attachment |
| `status` | string | read-only | `draft` \| `posted` \| `paid` \| `partially_paid` \| `overdue` |
| `status_display` | string | read-only | Human-readable status |
| `posted_at` | datetime \| null | read-only | Posting timestamp |
| `qr_code_text` | string | set on post | ZATCA QR payload text |
| `journal_entry` | UUID \| null | read-only | Linked JE (reserved for posting workflow extension) |
| `subtotal` | decimal string | read-only | Sum of line subtotals |
| `total_vat` | decimal string | read-only | Sum of line VAT |
| `total_amount` | decimal string | read-only | Final invoice total |
| `paid_amount` | decimal string | read-only | Paid amount |
| `balance_amount` | decimal string | read-only | Remaining balance |
| `issuer_details` | object | read-only | Auto from Company Settings |
| `lines` | array | ✅ | Invoice line items |
| `created_at` | datetime | read-only | ISO 8601 |
| `updated_at` | datetime | read-only | ISO 8601 |

---

## 19. Customer Payments (Sales)

> All endpoints require: `Authorization: Bearer <access_token>`  
> Base path: `/api/v1/sales/customer-payments/`

### 19.1 List Customer Payments

- **URL:** `GET /sales/customer-payments/`
- **Query params:** `search`, `customer`, `payment_type`, `page`, `page_size`

### 19.2 Create Customer Payment

- **URL:** `POST /sales/customer-payments/`

**Request Body (Invoice Payment):**
```json
{
  "payment_number": "CP-0001",
  "customer": "customer-uuid",
  "paid_through": "account-uuid",
  "payment_type": "invoice_payment",
  "amount_received": "2500.00",
  "payment_date": "2026-03-24",
  "description": "Bank transfer",
  "allocations": [
    { "invoice": "invoice-uuid-1", "amount": "1000.00" },
    { "invoice": "invoice-uuid-2", "amount": "1500.00" }
  ]
}
```

**Request Body (Advance Payment):**
```json
{
  "payment_number": "CP-0002",
  "customer": "customer-uuid",
  "paid_through": "account-uuid",
  "payment_type": "advance_payment",
  "amount_received": "1000.00",
  "payment_date": "2026-03-24",
  "description": "Advance from customer"
}
```

### 19.3 Retrieve / Update / Delete Customer Payment

- **URL:** `GET/PATCH/DELETE /sales/customer-payments/<uuid>/`
- On update/delete, existing allocations are rolled back from invoice `paid_amount` first, then reapplied (for PATCH).

### 19.4 Outstanding Invoices (for Payment Allocation Grid)

- **URL:** `GET /sales/customer-payments/outstanding-invoices/?customer=<uuid>`
- Returns posted invoices where `balance_amount > 0`.

### 19.5 Customer Payment Object Fields

| Field | Type | Writable | Description |
|---|---|---|---|
| `id` | UUID | — | Payment ID |
| `payment_number` | string | ✅ | Unique payment number |
| `customer` | UUID | ✅ | Customer ID |
| `customer_name` | string | read-only | Customer name |
| `paid_through` | UUID | ✅ | Cash/Bank account ID |
| `paid_through_code` | string | read-only | Account code |
| `paid_through_name` | string | read-only | Account name |
| `payment_type` | string | ✅ | `invoice_payment` \| `advance_payment` |
| `payment_type_display` | string | read-only | UI label |
| `amount_received` | decimal string | ✅ | Amount received from customer |
| `payment_date` | date | ✅ | Payment date |
| `description` | string | ✅ | Optional memo |
| `is_posted` | bool | ✅ | Posted flag |
| `amount_applied` | decimal string | read-only | Sum of allocations |
| `remaining_amount` | decimal string | read-only | `amount_received - amount_applied` |
| `allocations` | array | write via request body, read in response | Applied invoice allocations |
| `created_at` | datetime | read-only | ISO 8601 |
| `updated_at` | datetime | read-only | ISO 8601 |

### 19.6 Validation Rules

- `amount_received` must be greater than `0`
- Allocation invoice must belong to the same customer
- Allocation invoice must be `posted`
- Allocation amount must not exceed invoice balance
- Total allocations must not exceed `amount_received`
- For `advance_payment`, allocations are ignored

### 19.7 Common Errors

- `400 CUSTOMER_REQUIRED` (missing customer query for outstanding invoices)
- `404 NOT_FOUND` (payment not found)
- `422 VALIDATION_ERROR` (allocation/business validation)

---

## 20. Customer Refunds (Sales)

> All endpoints require: `Authorization: Bearer <access_token>`  
> Base path: `/api/v1/sales/customer-refunds/`

### 20.1 List Customer Refunds

- **URL:** `GET /sales/customer-refunds/`
- **Query params:** `search`, `customer`, `page`, `page_size`

### 20.2 Create Customer Refund

- **URL:** `POST /sales/customer-refunds/`

**Request Body:**
```json
{
  "refund_number": "CRF-0001",
  "customer": "customer-uuid",
  "paid_through": "account-uuid",
  "amount_refunded": "1200.00",
  "refund_date": "2026-03-24",
  "description": "Customer refund transfer",
  "allocations": [
    { "credit_note": "credit-note-uuid-1", "amount": "800.00" },
    { "credit_note": "credit-note-uuid-2", "amount": "400.00" }
  ]
}
```

### 20.3 Retrieve / Update / Delete Customer Refund

- **URL:** `GET/PATCH/DELETE /sales/customer-refunds/<uuid>/`
- On update/delete, current allocations are rolled back from credit note `refunded_amount` first, then reapplied (for PATCH).

### 20.4 Outstanding Credit Notes (for Allocation Grid)

- **URL:** `GET /sales/customer-refunds/outstanding-credit-notes/?customer=<uuid>`
- Returns posted credit notes where `balance_amount > 0`.

### 20.5 Customer Refund Object Fields

| Field | Type | Writable | Description |
|---|---|---|---|
| `id` | UUID | — | Refund ID |
| `refund_number` | string | ✅ | Unique refund number |
| `customer` | UUID | ✅ | Customer ID |
| `customer_name` | string | read-only | Customer name |
| `paid_through` | UUID | ✅ | Cash/Bank account ID |
| `paid_through_code` | string | read-only | Account code |
| `paid_through_name` | string | read-only | Account name |
| `amount_refunded` | decimal string | ✅ | Amount refunded to customer |
| `refund_date` | date | ✅ | Refund date |
| `description` | string | ✅ | Optional description |
| `is_posted` | bool | ✅ | Posted flag |
| `amount_applied` | decimal string | read-only | Sum of allocations |
| `remaining_amount` | decimal string | read-only | `amount_refunded - amount_applied` |
| `allocations` | array | write via request body, read in response | Applied credit-note allocations |
| `created_at` | datetime | read-only | ISO 8601 |
| `updated_at` | datetime | read-only | ISO 8601 |

### 20.6 Validation Rules

- `amount_refunded` must be greater than `0`
- Allocation credit note must belong to the same customer
- Allocation credit note must be `posted`
- Allocation amount must not exceed credit note balance
- Total allocations must not exceed `amount_refunded`

### 20.7 Common Errors

- `400 CUSTOMER_REQUIRED` (missing customer query for outstanding credit notes)
- `404 NOT_FOUND` (refund not found)
- `422 VALIDATION_ERROR` (allocation/business validation)

---

## 21. Credit Notes (Sales)

> All endpoints require: `Authorization: Bearer <access_token>`  
> Base path: `/api/v1/sales/credit-notes/`

### 21.1 List Credit Notes

- **URL:** `GET /sales/credit-notes/`
- **Query params:** `search`, `status`, `customer`, `date_from`, `date_to`, `page`, `page_size`

### 21.2 Create Credit Note (Save as Draft)

- **URL:** `POST /sales/credit-notes/`

**Request Body:**
```json
{
  "credit_note_number": "CN-INV100101",
  "customer": "customer-uuid",
  "date": "2026-03-24",
  "note": "Return/adjustment",
  "lines": [
    {
      "product": "product-uuid",
      "description": "Returned item",
      "account": "account-uuid",
      "quantity": "1",
      "unit_price": "1000",
      "tax_rate": "tax-rate-uuid",
      "discount_percent": "0"
    }
  ]
}
```

### 21.3 Retrieve / Update / Delete Credit Note

- **URL:** `GET/PATCH/DELETE /sales/credit-notes/<uuid>/`
- Draft credit notes are editable/deletable.
- Posted credit notes are locked:
  - `PATCH` returns `422 CREDIT_NOTE_POSTED`
  - `DELETE` returns `422 CREDIT_NOTE_POSTED`

### 21.4 Confirm & Post Credit Note

- **URL:** `POST /sales/credit-notes/<uuid>/post/`
- Sets:
  - `status = posted`
  - `posted_at = now`
- Optional payload:
```json
{
  "qr_code_text": "BASE64_OR_TEXT_FOR_ZATCA_QR"
}
```

### 21.5 Credit Note Object Fields

| Field | Type | Writable | Description |
|---|---|---|---|
| `id` | UUID | — | Credit note ID |
| `credit_note_number` | string | ✅ | Unique credit note number |
| `customer` | UUID | ✅ | Customer ID |
| `customer_name` | string | read-only | Customer name |
| `date` | date | ✅ | Credit note date |
| `note` | string | ✅ | Note/memo |
| `attachment` | file/url \| null | ✅ | Optional attachment |
| `status` | string | read-only | `draft` \| `posted` |
| `posted_at` | datetime \| null | read-only | Posting timestamp |
| `qr_code_text` | string | set on post | ZATCA QR payload text |
| `journal_entry` | UUID \| null | read-only | Linked JE (reserved for posting workflow extension) |
| `subtotal` | decimal string | read-only | Sum of line subtotals |
| `total_vat` | decimal string | read-only | Sum of line VAT |
| `total_amount` | decimal string | read-only | Final credit note total |
| `refunded_amount` | decimal string | read-only | Amount already refunded |
| `balance_amount` | decimal string | read-only | Remaining refundable amount |
| `issuer_details` | object | read-only | Auto from Company Settings |
| `lines` | array | ✅ | Credit note lines |
| `created_at` | datetime | read-only | ISO 8601 |
| `updated_at` | datetime | read-only | ISO 8601 |
