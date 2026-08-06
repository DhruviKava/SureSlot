from django.urls import path
from . import views

urlpatterns = [
    path('manage/services/', views.manage_services, name='manage-services'),
    path('manage/staff/', views.manage_staff, name='manage-staff'),
]