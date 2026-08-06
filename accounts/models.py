from django.contrib.auth.models import AbstractUser
from django.db import models

from tenants.models import Tenant


class User(AbstractUser):
    """Custom user, tied to a Tenant, authenticating by email.

    Tying the user to a tenant at the auth layer (rather than relying on
    every view to remember to filter by tenant) means tenant-scoping can be
    enforced once, centrally, in a permission/queryset mixin — rather than
    repeated, and potentially forgotten, in every view.

    Login is by email, not username. `username` is kept (not removed) since
    Django admin and createsuperuser still expect it to exist meaningfully —
    it's just no longer what a person types in to log in.
    """

    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        STAFF = "staff", "Staff"

    email = models.EmailField("email address", unique=True)

    role = models.CharField(
        max_length=10, choices=Role.choices, default=Role.OWNER,
        help_text="Owner: full control of their tenant. Staff: sees only "
        "their own assigned bookings. Ignored for platform superusers.",
    )

    staff_member = models.ForeignKey(
        "tenants.StaffMember",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="user_account",
        help_text="Only set when role=staff — links this login to the "
        "StaffMember record so their dashboard can be filtered to "
        "only the bookings assigned to them.",
    )

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="users",
        null=True,
        blank=True,
        help_text="The business this user manages. Null for platform-level "
        "superusers only.",
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    def __str__(self):
        return self.email