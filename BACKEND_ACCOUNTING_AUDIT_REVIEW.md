# Backend Accounting + ZATCA — Deep Audit (Pre–Go-Live)

*Review snapshot: March 2026. Single-tenant monolithic Django backend. Audience: finance control, external audit, ZATCA operations.*

This document combines **(A)** a full-domain review (architecture, IFRS/VAT posture, ZATCA Phase 1/2, security, performance, tests) and **(B)** **implemented** go-live hardening already in the codebase (referenced by path).

---

## 1. Monolithic architecture review

### Strengths

- **App boundaries** are sensible for a monolith: **`accounting`** (CoA, JE, periods, reports), **`sales`** / **`purchases`** / **`products`** (commercial documents), **`zatca_adapter`** (UBL/XML/signing/QR/hash), **`main`** (cross-cutting: approvals, idempotency, company settings).
- **Posting logic** is concentrated in **`accounting/services/posting.py`**, **`sales/customer_cash_posting.py`**, **`purchases/supplier_payment_posting.py`**, **`products/inventory_posting.py`** — better than fat models alone.
- **Validators** (`accounting/validators.py`, `main/allocation_validator.py`) centralize rules (CoA locks, period checks, allocation invariants).
- **Journal posting gate** (`accounting/journal_post_gate.py`, `AccountingEngine.post_journal_entry`) reduces accidental **`JournalEntry.post()`** bypass.

### Risks / smells

- **`zatca_adapter/services.py`** is a **very large** module (XML, signing, QR, submission, evidence) — high maintenance and regression risk; candidates to split: UBL build, signing, HTTP transport, evidence persistence.
- **`main/approvals.py`** — **`execute_approved_action`** is a **large switch**; acceptable for now but will not scale past ~20 scopes — prefer a **registry** (`scope → callable`) when you add dormant scopes (void, writeoff, CoA).
- **`sales/views.py`** (1000+ lines) mixes orchestration and policy — consider thin views + application services per bounded context (`sales/application/invoice.py`) without splitting into microservices.

### Suggestions (clean modular monolith)

1. Introduce **application services** per domain (invoice lifecycle, ZATCA submit, payment allocation) that views call; keep DRF serializers for I/O only.
2. Keep **`zatca_adapter`** free of **`accounting.*`** imports (already mostly true); keep period/approval gates in **sales** runners before calling adapter.
3. Document **bounded contexts** in one internal `ARCHITECTURE.md` only if the team agrees — optional.

---

## 2. Accounting engine validation (critical)

### Double-entry

- **Balanced posting:** `JournalEntryValidator.validate_can_post` enforces **Σ debit = Σ credit**, ≥2 lines, non-archived accounts. **`JournalEntryLine`** DB **`CheckConstraint`** enforces one-sided lines and non-negative amounts.
- **Posting:** `JournalEntry.post()` assigns sequential **JE-** reference under **`select_for_update`**; idempotent if already posted.
- **Bypass risk (mitigated):** `ENFORCE_JOURNAL_ENTRY_POST_GATE` (default on when `DEBUG=False`) blocks raw **`post()`** outside **`permit_journal_post`** / **`AccountingEngine.post_journal_entry`**.

### Sub-ledger vs GL

- **AR invoice balance:** `Invoice.paid_amount` is **denormalized** but **reconciled** against allocation rows (`DRIFT_DETECTED` checks in **`customer_cash_posting`**), and **DB `CheckConstraint`s** enforce **`0 ≤ paid_amount ≤ total_amount`** (same pattern on **`CustomerCreditNote.refunded_amount`**, **`Bill.paid_amount`**). Raw SQL can still corrupt data, but the ORM/API path cannot persist impossible paid/refunded totals.
- **Supplier side:** analogous drift checks in **`apply_supplier_payment_allocations`** / rollback paths, plus **`bill_paid_lte_total_nonneg`** on **`purchases.Bill`**.

### Chart of accounts

- Hierarchy via **`Account.parent`**; **ZATCA mapping** and **system accounts** (`accounting/system_accounts.py`, strict mode **`accounting.E001`**) support controlled statutory mapping.
- **Activity detection:** `TRANSACTION_SOURCES` + **`accounting.E002`** (non-exempt Account FK models) — extend registry when new posted document types reference accounts.

### Reversals vs deletion

- Posted JEs are **immutable**; corrections via **`create_reversal`** + optional post. Soft-delete patterns on allocations use **`is_deleted`** — ensure **reports** exclude `is_deleted=True` (they generally filter).

### Residual accounting risks

| Risk | Severity | Note |
|------|-----------|------|
| Denormalized `paid_amount` / `refunded_amount` vs allocations | Low–Med | Drift checks + **DB bounds** on paid/refunded vs document total; **admin/raw DB** still a gap. |
| Manual JE API | Medium | Balanced entry enforced; **segregation of duties** relies on **`MAKER_CHECKER_ENABLED`** + roles. |
| Global ΣDr = ΣCr identity | Addressed | **`accounting/double_entry_audit.py`**, **`verify_double_entry_integrity`** command, and **`check_accounting_ready`** (includes this in **`--strict`**). |

---

## 3. ZATCA (FATOORA) compliance audit

### Implemented well

- **UUID**, **chained invoice hash** (`SHA256(prev‖canonical)`), **QR TLV** (tags 1–6 baseline; **7–9** optional via **`ZATCA_QR_INCLUDE_ECDSA`**), **XAdES / XMLDSig** pipeline, **evidence bundles** + **`ZatcaSubmissionStatusLog`**, **outbox** + retries, **hash-chain anchor** + **`verify_zatca_hash_chain`**, **clock drift** startup guard (with documented escape hatches).

### Gaps / watch items

| Topic | Risk | Action |
|-------|------|--------|
| **Simulation mode** | **Addressed in code:** `ZATCA_SIMULATION_MODE` in **`settings.py`** defaults to **`DEBUG`** when env is unset (**True** in development, **False** when **`DEBUG=False`**). **`submit_to_zatca`** and **`zatca_submit_runner`** read Django settings, not a hard-coded default. Override with **`ZATCA_SIMULATION_MODE=true`** only when intentionally simulating. |
| **QR TLV tags 7–9** | Certification-dependent. | Confirm against your **sandbox** profile before enabling **`ZATCA_QR_INCLUDE_ECDSA`**. |
| **ICV / previous-hash bootstrap** | Operational at first live invoice. | Runbook + controlled cutover. |
| **Timezone** | UBL uses **fixed UTC+3** for issue time in places; server **`TIME_ZONE`** is UTC — ensure **business acceptance** of displayed vs stored times. | Document in operator runbook. |
| **Immutability** | Posted invoices/CNs constrained by status + **`invoice_posted_requires_journal_entry`**; **API** blocks edits when posted. | Keep **only** credit notes / corrective flows for issued docs. |

---

## 4. VAT & tax logic (Saudi)

- **Rates** come from **`TaxRate`** (and line references) — **15%** is data-dependent, not hard-coded everywhere. **`check_accounting_ready --strict`** fails if there is **no** active **`TaxRate`** with **`zatca_category='S'`** (standard VAT bucket for ZATCA XML). Run **`seed_tax_rates`** (or equivalent) before go-live.
- **Rounding:** `main/money.py` — **`money()`** uses **2 dp** and **`ROUND_HALF_UP`**; **`VAT_ROUNDING_STRATEGY`** (`line` vs `invoice`) affects whether VAT is rounded per line or at document level — **must be fixed per policy** and tested on **discount / multi-line** scenarios.
- **Floating point:** Amounts use **`Decimal`** in models; avoid **`float`** in new financial code.
- **Global context:** `accounting/decimal_context.py` sets **`ROUND_HALF_UP`** and **`DECIMAL_CONTEXT_PREC`** (default 28) — align with ZATCA **display** rules (typically 2 dp for SAR) in serializers/output.

### Edge cases to regression-test

- Multi-line with **mixed tax rates**; **zero-rated / exempt** (depends on `TaxRate` + line setup); **percentage discounts** on lines; **invoice-level** vs **line-level** VAT rounding.

---

## 5. Data integrity & auditability

- **Creator/updator** on **`BaseModel`**; **ZATCA** evidence and **status logs** support **who** for submissions when **`zatca_actor_scope`** is used.
- **Atomicity:** Posting flows use **`transaction.atomic()`** where allocation + JE must be consistent.
- **Idempotency:** **`IdempotencyRecord`** + **`begin_idempotent`** on key financial POSTs; **lease reclaim** for stuck `processing` (**`reclaim_stale_idempotency`**).
- **Soft delete:** Financial rows often **`is_deleted`** — reporting queries must stay consistent (most GL queries filter `is_deleted=False`).

---

## 6. Edge case handling

| Scenario | Handling |
|----------|-----------|
| **Partial payments** | Allocations; `remaining_amount` on payment; **over-allocation** blocked vs invoice balance. |
| **Overpayments / unapplied cash** | **`advance_payment`** path reduces allocation rows — ensure **GL** matches intent (bank vs unapplied AR). |
| **Refunds** | Credit note allocations + refund journal; period checks on allocation. |
| **Failed ZATCA** | Transport errors → **retrying** / **failed_final**; **outbox** worker; **evidence** in finally (strict mode on 2xx bundles). |
| **Cancellation** | Prefer **credit note / corrective** document — **void** scope not yet wired (dormant policy). |

---

## 7. Security (high level)

- **DRF:** Default **`IsAuthenticated`**; some **reports** use **`IsAdmin`** — review **role matrix** for **AR/AP clerks** vs **posting** vs **ZATCA submit**.
- **Secrets:** `SECRET_KEY`, ZATCA tokens, cert paths — **never** in repo; **`.env`** in production.
- **User enumeration:** Not deeply reviewed here — ensure **login/reset** responses do not leak account existence if that is a requirement.

---

## 8. Performance

- **`StatementOfAccountAPI` / GL-style reports:** May **load all JEL rows** for an account into memory, build **running balance**, then paginate — acceptable for SME; for **large history**, switch to **DB-side running balance** (window functions) or **pre-aggregated balances** by period.
- **Indexes:** JE lines filtered by **`journal_entry__status`**, **`account_id`**, **`date`** — verify **`db_index=True`** on hot paths (many already present).
- **N+1:** Prefer **`select_related` / `prefetch_related`** on list endpoints (spot-check **`sales`** list APIs).

---

## 9. API design

- **REST** patterns with **machine-readable errors** (`AccountError.to_dict()`).
- **Idempotency-Key** on sensitive POSTs — extend coverage as you add money-moving endpoints.
- **422** for business rule violations — consistent with DRF usage.

---

## 10. Testing (recommended)

Current automated tests are **minimal** in-repo — for go-live:

1. **Accounting:** balanced JE post, unbalanced rejection, **period closed**, reversal twice blocked.
2. **VAT:** line vs invoice rounding, discount lines, **15%** on known fixtures.
3. **ZATCA:** mock HTTP transport; **hash chain** verification command; **QR** decode smoke tests.
4. **Allocations:** concurrent allocation tests (two workers **same invoice**) — stress **`select_for_update`** paths.
5. **Regression:** `manage.py check`, `check_accounting_ready --strict` (includes **global ΣDr=ΣCr** + **standard VAT TaxRate**), `check_zatca_ready --strict`, `verify_zatca_hash_chain --fail-on-error`, `verify_double_entry_integrity --fail-on-error`.

---

## 11. Refactoring suggestions (prioritized)

1. **Split `zatca_adapter/services.py`** into submodules by responsibility (UBL, sign, submit, evidence).
2. **Approval executor registry** instead of a single growing `if` chain.
3. **Optional** domain events table for invoice/JE/ZATCA transitions (beyond current logs) if auditors require **full** event sourcing narrative.
4. **Single “posting orchestrator”** per document type** to avoid duplicate paths between **views** and **approval replay**.

---

# Audit output (summary tables)

## 1. Critical issues (financial / compliance risks)

| # | Issue | Mitigation in code / ops |
|---|--------|---------------------------|
| C1 | **Dormant maker–checker** (policy without executor) | **`main.E002`**, dormant scopes **`requires_approval=False`**, **`APPROVAL_SCOPES_WITH_EXECUTORS`**. |
| C2 | **CoA activity under-count** (missing `TRANSACTION_SOURCES`) | **`accounting.E002`** + FK introspection vs registry. |
| C3 | **Stuck idempotency / async jobs** | **Lease reclaim**, **`reclaim_stale_idempotency`**, **`IdempotentJob`** reclaim. |
| C4 | **Broken hash chain** after restore/manual fix | **`ZatcaHashChainAnchor`**, **`ZATCA_BLOCK_POST_ON_CHAIN_FAILURE`**, **`verify_zatca_hash_chain`**. |
| C5 | **Clock skew** vs signing / timestamps | **`main/clock_sync.py`** (with documented **SKIP** flags). |
| C6 | **Simulation left on in production** | **Mitigated:** unset env → **`ZATCA_SIMULATION_MODE = DEBUG`** in **`settings.py`**; **`submit_to_zatca`** uses settings. Explicit **`ZATCA_SIMULATION_MODE=true`** only for intentional simulation. |
| C7 | **Denormalized AR/AP balances** | **Drift checks** + **DB `CheckConstraint`** on paid/refunded vs document total; **direct DB** still out of band. |

## 2. ZATCA compliance gaps

- **Profile-specific** TLV 7–9 / QR strictness — **feature-flagged**; confirm in **sandbox**.
- **Automated regression** against authority **sample XML** and **live/sandbox** responses as a **release gate** (process, not only code).
- **Legacy hash** (`ZATCA_HASH_CHAIN_LEGACY`) — keep **off** in prod unless under a **controlled migration**.

## 3. Important improvements

- **Expand automated tests** (§10) — highest ROI before external audit.
- **GL report scalability** for large tenants (§8).
- **Role-based access** review for financial and ZATCA endpoints (§7).
- **Double-entry global check** — use **`verify_double_entry_integrity --fail-on-error`** in CI (already bundled into **`check_accounting_ready --strict`**).

## 4. Nice-to-have enhancements

- **ZIP audit export** (invoices XML, signatures, QR, evidence JSON).
- **Replay** from domain events; **ZATCA dry-run** CLI for pre-submit validation.

## 5. Scores (out of 10)

| Dimension | Score | Rationale |
|-----------|-------|-----------|
| **ZATCA** | **8.8** | Strong pipeline, hash/QR/sign, outbox, evidence, logs; **simulation env**, **profile quirks**, and **operational** cutover remain. |
| **Accounting** | **8.9** | Solid double-entry, constraints, posting services, allocation drift checks, period controls; **denormalized sub-ledgers** and **limited automated tests** hold the score below 9.5. |
| **Code quality** | **8.5** | Clear monolith boundaries; **large ZATCA module**, **fat sales views**, **executor switch** need refactoring headroom. |

## 6. Final audit summary — production-ready?

**Conditionally yes** for a **single-tenant** deployment **if**:

1. **`python manage.py check`** passes in **production** configuration (**`accounting.E001`**, **`main.E002`**, **`accounting.E002`** as applicable).
2. **`check_accounting_ready --strict`** and **`check_zatca_ready --strict`** pass in CI/CD.
3. **`ZATCA_SIMULATION_MODE`** unset or **`false`** in production (**defaults off** when **`DEBUG=False`** unless overridden), **`ZATCA_HASH_CHAIN_LEGACY=false`** (unless under written migration), **`MAKER_CHECKER_ENABLED`** aligned with policy.
4. Scheduled jobs: **`verify_zatca_hash_chain`**, **`reclaim_stale_idempotency`**, ZATCA **outbox** worker.
5. **Test suite** expanded beyond current minimal coverage for **VAT** and **posting**.

Residual exposure is **operational** (env, roles, monitoring) and **test coverage**, not absence of core controls.

---

## Appendix A — Enforced controls (implementation index)

| Control | Location |
|---------|----------|
| Maker–checker wiring | `main/approval_wiring.py`, `main/checks.py` (`main.E002`/`E003`) |
| Account FK registry | `accounting/validators.py`, `accounting/checks.py` (`accounting.E002`) |
| Idempotency lease | `main/idempotency_lease.py`, `main/idempotency.py`, `main/async_jobs.py` |
| Hash chain anchor | `sales.ZatcaHashChainAnchor`, `sales/management/commands/verify_zatca_hash_chain.py` |
| Clock drift | `main/clock_sync.py`, `MainConfig.ready` |
| ZATCA status audit | `sales.ZatcaSubmissionStatusLog`, `zatca_adapter/zatca_actor_context.py` |
| Evidence completeness | `sales.ZatcaEvidenceBundle.is_complete()`, `ZATCA_STRICT_EVIDENCE_COMPLETENESS` |
| Journal post gate | `accounting/journal_post_gate.py`, `AccountingEngine.post_journal_entry` |
| Period date sanity | `AccountingPeriod` `CheckConstraint` |
| Allocation periods + locking | `main/allocation_validator.py`, `sales/customer_cash_posting.py`, `purchases/supplier_payment_posting.py` |
| Decimal context | `accounting/decimal_context.py` |
| Global ΣDr=ΣCr (posted lines) | `accounting/double_entry_audit.py`, `verify_double_entry_integrity`, `check_accounting_ready` |
| Sub-ledger bounds | `invoice_paid_lte_total_nonneg`, `credit_note_refunded_lte_total_nonneg`, `bill_paid_lte_total_nonneg` (migrations **sales.0022**, **purchases.0010**) |
| ZATCA simulation default | `ZATCA_SIMULATION_MODE` in `zatca_accounting_software/settings.py` |

## Appendix B — Environment reference

See **`.env.example`** for: `STRICT_APPROVAL_POLICY_INTEGRITY`, `GO_LIVE_REQUIRED_APPROVAL_SCOPES`, `STRICT_TRANSACTION_SOURCE_REGISTRY`, `SKIP_CLOCK_DRIFT_CHECK`, `ALLOW_STARTUP_WITHOUT_NTP`, `MAX_CLOCK_DRIFT_SECONDS`, `ZATCA_BLOCK_POST_ON_CHAIN_FAILURE`, `ZATCA_QR_INCLUDE_ECDSA`, `ZATCA_SIMULATION_MODE`, `ENFORCE_JOURNAL_ENTRY_POST_GATE`, `ZATCA_STRICT_EVIDENCE_COMPLETENESS`, `DECIMAL_CONTEXT_PREC`, plus existing accounting/ZATCA variables.
