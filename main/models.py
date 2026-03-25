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
