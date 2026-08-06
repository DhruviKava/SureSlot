from django.contrib import admin
from .models import Tenant, StaffMember, Service, Customer


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "business_type", "timezone", "created_at")
    list_filter = ("business_type",)
    search_fields = ("name",)


@admin.register(StaffMember)
class StaffMemberAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "role", "is_active")
    list_filter = ("tenant", "is_active")
    search_fields = ("name",)


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "duration_minutes", "price", "requires_deposit")
    list_filter = ("tenant", "requires_deposit")
    search_fields = ("name",)


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "phone", "email", "created_at")
    list_filter = ("tenant",)
    search_fields = ("name", "phone", "email")

