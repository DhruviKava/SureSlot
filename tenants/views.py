from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponseForbidden

from .models import Service, StaffMember


def _require_tenant(request):
    """Shared guard: every view in this module operates on the logged-in
    user's own tenant only, never a tenant chosen via URL or form input.
    Returns the tenant, or None if this account has no tenant (e.g. the
    platform superuser) — callers render a clear message in that case
    rather than crashing, same pattern as bookings/views.py's dashboard.
    """
    return request.user.tenant


@login_required(login_url='login')
def manage_services(request):
    """List the logged-in owner's services, with an inline 'add new'
    form and per-row inline edit/deactivate — no separate pages, since
    each record is small enough that a full page per action would be
    needless navigation for a business owner doing routine upkeep.
    """
    tenant = _require_tenant(request)
    if tenant is None:
        return render(request, 'bookings/no_tenant.html', {})

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'create':
            name = request.POST.get('name', '').strip()
            duration = request.POST.get('duration_minutes', '').strip()
            price = request.POST.get('price', '').strip()
            requires_deposit = request.POST.get('requires_deposit') == 'on'

            if not name or not duration or not price:
                messages.error(request, "Please fill in name, duration, and price.")
            else:
                service = Service(
                    tenant=tenant, name=name, duration_minutes=duration,
                    price=price, requires_deposit=requires_deposit,
                    image=request.FILES.get('image'),
                )
                try:
                    service.full_clean()
                    service.save()
                    messages.success(request, f"Added '{service.name}'.")
                except ValidationError as e:
                    messages.error(request, " ".join(e.messages))

        elif action == 'update':
            service = get_object_or_404(Service, pk=request.POST.get('service_id'), tenant=tenant)
            service.name = request.POST.get('name', '').strip()
            service.duration_minutes = request.POST.get('duration_minutes', '').strip()
            service.price = request.POST.get('price', '').strip()
            service.requires_deposit = request.POST.get('requires_deposit') == 'on'
            if request.FILES.get('image'):
                service.image = request.FILES.get('image')
            if request.POST.get('remove_image') == 'on':
                service.image = None
            try:
                service.full_clean()
                service.save()
                messages.success(request, f"Updated '{service.name}'.")
            except ValidationError as e:
                messages.error(request, " ".join(e.messages))

        elif action == 'deactivate':
            # Soft-delete only — never a real .delete(). A hard delete
            # would orphan any historical booking that references this
            # service (Booking.service uses on_delete=SET_NULL), silently
            # corrupting past records. Deactivating hides it from the
            # public booking form while keeping history intact.
            service = get_object_or_404(Service, pk=request.POST.get('service_id'), tenant=tenant)
            service.is_active = False
            service.save()
            messages.success(request, f"'{service.name}' is now hidden from your booking page.")

        elif action == 'reactivate':
            service = get_object_or_404(Service, pk=request.POST.get('service_id'), tenant=tenant)
            service.is_active = True
            service.save()
            messages.success(request, f"'{service.name}' is visible on your booking page again.")

        return redirect('manage-services')

    services = Service.objects.filter(tenant=tenant).order_by('-is_active', 'name')
    return render(request, 'tenants/manage_services.html', {'tenant': tenant, 'services': services})


@login_required(login_url='login')
def manage_staff(request):
    """List the logged-in owner's staff members, same inline list/add/
    edit/deactivate pattern as manage_services."""
    tenant = _require_tenant(request)
    if tenant is None:
        return render(request, 'bookings/no_tenant.html', {})

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'create':
            name = request.POST.get('name', '').strip()
            role = request.POST.get('role', '').strip()

            if not name:
                messages.error(request, "Please enter a name.")
            else:
                staff = StaffMember(tenant=tenant, name=name, role=role)
                try:
                    staff.full_clean()
                    staff.save()
                    messages.success(request, f"Added '{staff.name}'.")
                except ValidationError as e:
                    messages.error(request, " ".join(e.messages))

        elif action == 'update':
            staff = get_object_or_404(StaffMember, pk=request.POST.get('staff_id'), tenant=tenant)
            staff.name = request.POST.get('name', '').strip()
            staff.role = request.POST.get('role', '').strip()
            try:
                staff.full_clean()
                staff.save()
                messages.success(request, f"Updated '{staff.name}'.")
            except ValidationError as e:
                messages.error(request, " ".join(e.messages))

        elif action == 'deactivate':
            staff = get_object_or_404(StaffMember, pk=request.POST.get('staff_id'), tenant=tenant)
            staff.is_active = False
            staff.save()
            messages.success(request, f"'{staff.name}' is now hidden from booking availability.")

        elif action == 'reactivate':
            staff = get_object_or_404(StaffMember, pk=request.POST.get('staff_id'), tenant=tenant)
            staff.is_active = True
            staff.save()
            messages.success(request, f"'{staff.name}' is available for booking again.")

        return redirect('manage-staff')

    staff_members = StaffMember.objects.filter(tenant=tenant).order_by('-is_active', 'name')
    return render(request, 'tenants/manage_staff.html', {'tenant': tenant, 'staff_members': staff_members})