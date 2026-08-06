import zoneinfo
from django.utils import timezone
from django.urls import resolve
from tenants.models import Tenant

class TenantTimezoneMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tz_name = None

        # 1. Try logged in user's tenant timezone
        if request.user.is_authenticated and request.user.tenant:
            tz_name = request.user.tenant.timezone

        # 2. Otherwise try to get it from the URL kwargs
        if not tz_name:
            try:
                match = resolve(request.path_info)
                if 'tenant_id' in match.kwargs:
                    tenant_id = match.kwargs['tenant_id']
                    # Get timezone for this tenant
                    tenant = Tenant.objects.only('timezone').get(pk=tenant_id)
                    tz_name = tenant.timezone
            except Exception:
                pass

        if tz_name:
            try:
                timezone.activate(zoneinfo.ZoneInfo(tz_name))
            except Exception:
                timezone.deactivate()
        else:
            timezone.deactivate()

        response = self.get_response(request)
        return response
