import uuid
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class BaseModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    creator = models.ForeignKey(
        "user.CustomUser",
        blank=True,
        null=True,
        related_name="creator_%(class)s_objects",
        on_delete=models.SET_NULL,
    )
    updator = models.ForeignKey(
        "user.CustomUser",
        blank=True,
        null=True,
        related_name="updator_%(class)s_objects",
        on_delete=models.SET_NULL,
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    @property
    def created_at_local(self):
        return timezone.localtime(self.created_at).strftime("%Y-%m-%d %H:%M:%S")

    @property
    def updated_at_local(self):
        return timezone.localtime(self.updated_at).strftime("%Y-%m-%d %H:%M:%S")

    class Meta:
        abstract = True


class Country(BaseModel):
    name = models.CharField(max_length=128)
    iso3 = models.CharField(max_length=128)
    iso2 = models.CharField(max_length=128)
    numeric_code = models.CharField(max_length=128)
    phone_code = models.CharField(max_length=128)
    capital = models.CharField(max_length=128)
    currency = models.CharField(max_length=128)
    currency_symbol = models.CharField(max_length=128)
    tld = models.CharField(max_length=128)
    native = models.CharField(max_length=128)
    region = models.CharField(max_length=128)
    subregion = models.CharField(max_length=128)
    latitude = models.CharField(max_length=128)
    longitude = models.CharField(max_length=128)

    class Meta:
        db_table = "countries"
        verbose_name = _("country")
        verbose_name_plural = _("countries")

    def __str__(self):
        return self.name


class State(models.Model):
    country = models.ForeignKey("main.Country", on_delete=models.CASCADE)
    name = models.CharField(max_length=128)
    country_code = models.CharField(max_length=128)
    state_code = models.CharField(max_length=128)
    latitude = models.CharField(max_length=128)
    longitude = models.CharField(max_length=128)

    class Meta:
        db_table = "states"
        verbose_name = _("state")
        verbose_name_plural = _("states")

    def __str__(self):
        return self.name


class CompanySettings(models.Model):
    """
    Singleton organization/company settings used across the app (ZATCA profile, invoices, etc.).
    Stored as a single row; API uses get-or-create semantics.
    """

    logo = models.ImageField(_("Organization Logo"), upload_to="company-logo/", null=True, blank=True)

    company_name = models.CharField(_("Company Name (EN)"), max_length=255, blank=True)
    company_name_ar = models.CharField(_("Company Name (AR)"), max_length=255, blank=True)

    street_address = models.CharField(_("Street Address (EN)"), max_length=255, blank=True)
    street_address_ar = models.CharField(_("Street Address (AR)"), max_length=255, blank=True)
    building_number = models.CharField(_("Building Number"), max_length=50, blank=True)
    district = models.CharField(_("District (EN)"), max_length=100, blank=True)
    district_ar = models.CharField(_("District (AR)"), max_length=100, blank=True)
    city = models.CharField(_("City (EN)"), max_length=100, blank=True)
    city_ar = models.CharField(_("City (AR)"), max_length=100, blank=True)
    country = models.ForeignKey("main.Country", on_delete=models.SET_NULL, null=True, blank=True)
    postal_code = models.CharField(_("Postal Code"), max_length=20, blank=True)

    cr_number = models.CharField(_("CR Number"), max_length=50, blank=True)
    vat_registration_number = models.CharField(_("VAT Registration Number"), max_length=50, blank=True)
    industry = models.CharField(_("Industry"), max_length=100, blank=True)
    email = models.EmailField(_("Email"), blank=True)
    phone = models.CharField(_("Phone"), max_length=32, blank=True)

    class Meta:
        db_table = "company_settings"
        verbose_name = _("company settings")
        verbose_name_plural = _("company settings")

    def __str__(self):
        return self.company_name or "Company Settings"


IDEMPOTENCY_STATE_CHOICES = (
    ("processing", "Processing"),
    ("succeeded", "Succeeded"),
    ("failed", "Failed"),
)


class IdempotencyRecord(BaseModel):
    """
    Generic idempotency storage for all financial mutations.
    Ensures exactly-once semantics on network retries.
    """

    # Unique per (scope, key). Reusing the same key across different endpoints should be allowed.
    key = models.CharField(max_length=120, db_index=True)
    scope = models.CharField(max_length=120, db_index=True)  # e.g. sales.invoice.post
    method = models.CharField(max_length=10, db_index=True)
    path = models.CharField(max_length=255, db_index=True)
    request_hash = models.CharField(max_length=64, db_index=True)
    state = models.CharField(max_length=20, choices=IDEMPOTENCY_STATE_CHOICES, default="processing", db_index=True)
    response_status = models.PositiveIntegerField(null=True, blank=True)
    response_body = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        db_table = "idempotency_record"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["scope", "method", "path", "key"], name="uniq_idempotency_scope_method_path_key"),
        ]


APPROVAL_STATUS_CHOICES = (
    ("pending", "Pending"),
    ("approved", "Approved"),
    ("denied", "Denied"),
    ("executed", "Executed"),
    ("failed", "Failed"),
)


class ApprovalRequest(BaseModel):
    """
    Maker-checker approval workflow for sensitive financial actions.
    """

    scope = models.CharField(max_length=120, db_index=True)  # e.g. sales.invoice.post
    object_type = models.CharField(max_length=80, db_index=True)  # e.g. sales.Invoice
    object_id = models.UUIDField(db_index=True)
    payload = models.JSONField(default=dict, blank=True)  # request body snapshot

    status = models.CharField(max_length=20, choices=APPROVAL_STATUS_CHOICES, default="pending", db_index=True)
    requested_by = models.ForeignKey(
        "user.CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approval_requests_made",
    )
    approved_by = models.ForeignKey(
        "user.CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approval_requests_approved",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    executed_at = models.DateTimeField(null=True, blank=True)
    result_status = models.PositiveIntegerField(null=True, blank=True)
    result_body = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        db_table = "approval_request"
        ordering = ["-created_at"]


class MaintenanceAuditLog(BaseModel):
    """Audit trail for sensitive maintenance/repair operations."""
    action = models.CharField(max_length=100, db_index=True)
    reason = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "maintenance_audit_log"
        ordering = ["-created_at"]


class ScheduledJobRun(BaseModel):
    """Run-once guard for scheduled jobs (job_type + period)."""
    job_type = models.CharField(max_length=100, db_index=True)
    period_key = models.CharField(max_length=100, db_index=True)
    status = models.CharField(max_length=20, default="succeeded", db_index=True)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "scheduled_job_run"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["job_type", "period_key"], name="uniq_scheduled_job_period"),
        ]


class BulkExecutionItem(BaseModel):
    """Per-item bulk idempotency ledger to avoid duplicates on partial retries."""
    scope = models.CharField(max_length=120, db_index=True)
    batch_id = models.CharField(max_length=120, db_index=True)
    item_key = models.CharField(max_length=120, db_index=True)
    state = models.CharField(max_length=20, default="succeeded", db_index=True)
    response_body = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "bulk_execution_item"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["scope", "batch_id", "item_key"], name="uniq_bulk_scope_batch_item"),
        ]
