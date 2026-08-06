from django.shortcuts import render, redirect
from tenants.models import Tenant


def home_view(request):
    """Public-facing homepage showing a directory of businesses grouped by business type.
    
    Supports filtering by category via the 'category' query parameter.
    """
    selected_category = request.GET.get('category', '').strip().lower()
    
    # Get all tenants
    tenants = Tenant.objects.all()
    
    # Filter by category if a valid one is specified
    valid_categories = [choice[0] for choice in Tenant.BusinessType.choices]
    if selected_category in valid_categories:
        tenants = tenants.filter(business_type=selected_category)
    else:
        selected_category = ''
        
    # Group tenants by business type label
    grouped_tenants = {}
    for code, label in Tenant.BusinessType.choices:
        # If filtering is active, only include the filtered group if it has items
        if selected_category and code != selected_category:
            continue
            
        type_tenants = tenants.filter(business_type=code)
        if type_tenants.exists():
            grouped_tenants[label] = type_tenants

    return render(request, 'core/home.html', {
        'grouped_tenants': grouped_tenants,
        'categories': Tenant.BusinessType.choices,
        'selected_category': selected_category,
    })