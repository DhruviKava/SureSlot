from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect


def login_view(request):
    """Custom login form: business owner logs in with email + password.

    Deliberately custom (not Django's built-in LoginView) for full control
    over the form markup and error messaging, consistent with the rest of
    this project's hand-built templates rather than Django's default forms.
    """
    if request.user.is_authenticated:
        return redirect('tenant-dashboard')

    error = None
    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')

        user = authenticate(request, username=email, password=password)
        if user is not None:
            login(request, user)
            return redirect('tenant-dashboard')
        error = "Incorrect email or password. Please try again."

    return render(request, 'accounts/login.html', {'error': error})


@login_required(login_url='login')
def logout_view(request):
    logout(request)
    return redirect('login')