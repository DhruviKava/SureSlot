from django.db import models


class Tenant(models.Model):
    """A business using the platform (a salon, clinic, or consultant practice).

    Every other domain model either belongs to a Tenant directly or belongs
    to something that belongs to a Tenant. We use a shared schema with a
    tenant_id foreign key on each table rather than schema-per-tenant —
    simpler to build correctly, and sufficient to demonstrate the multi-
    tenant concept. Schema-per-tenant isolation is a documented scaling
    consideration, not something this project needs to implement.
    """

    class BusinessType(models.TextChoices):
        SALON = "salon", "Salon"
        CLINIC = "clinic", "Clinic"
        CONSULTANT = "consultant", "Consultant"

    name = models.CharField(max_length=255)
    business_type = models.CharField(
        max_length=20, choices=BusinessType.choices, default=BusinessType.SALON
    )
    timezone = models.CharField(
        max_length=50,
        default="UTC",
        help_text="IANA timezone name, e.g. 'Asia/Kolkata'.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def encoded_id(self):
        from core.obfuscator import encode_id
        return encode_id(self.pk)

    def __str__(self):
        return self.name


class StaffMember(models.Model):
    """A person who performs services for a tenant (stylist, doctor, consultant)."""

    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name="staff_members"
    )
    name = models.CharField(max_length=255)
    role = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.tenant.name})"


class Service(models.Model):
    """A bookable service offered by a tenant (e.g. Haircut, Consultation)."""

    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name="services"
    )
    name = models.CharField(max_length=255)
    duration_minutes = models.PositiveIntegerField(default=30)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    requires_deposit = models.BooleanField(
        default=False,
        help_text="Tenant-level default. Individual bookings snapshot this "
        "value at creation time so historical records stay meaningful "
        "even if this setting changes later.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Inactive services are hidden from the public booking "
        "form but never deleted — existing bookings continue to "
        "reference them correctly.",
    )
    image = models.ImageField(
        upload_to="services/",
        blank=True,
        null=True,
        help_text="Shown on the public booking page's service selection "
        "card. Optional — a service without a photo falls back to a "
        "placeholder graphic rather than showing nothing.",
    )

    def __str__(self):
        return f"{self.name} ({self.tenant.name})"


class Customer(models.Model):
    """An end customer of a tenant's business.

    Aggregate history (total bookings, no-show rate) is deliberately NOT
    stored here as columns. Those are derived at query/feature-extraction
    time from the Booking table — storing them would require triggers or
    signals to keep them in sync, which adds complexity with no real
    benefit at this scale, and risks silently going stale.
    """

    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name="customers"
    )
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.tenant.name})"

