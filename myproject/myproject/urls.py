"""
URL configuration for myproject project.
"""
from django.contrib import admin
from django.urls import path
from portal import views

urlpatterns = [
    path('admin/', admin.site.urls),

    # Public pages
    path("", views.landing, name="landing"),
    path("hostels/", views.listing, name="listing"),
    path("hostels/<str:room_number>/", views.detail, name="detail"),

    # Auth
    path("login/", views.login_register, name="login_register"),
    path("login/submit/", views.login_view, name="login_submit"),
    path("register/submit/", views.register_view, name="register_submit"),
    path("logout/", views.logout_view, name="logout"),
    path("admin-login/", views.admin_login_page, name="admin_login"),
    path("admin-login/submit/", views.admin_login_submit, name="admin_login_submit"),
    path("admin-login/request/", views.request_access, name="request_access"),

    # Logged-in student area
    path("dashboard/", views.dashboard, name="dashboard"),
    path("hostels/<str:room_number>/apply/", views.booking, name="booking"),

    # Admin dashboard (staff only — enforced in the view, not here)
    path("dashboard/admin/", views.admin_dashboard, name="admin_dashboard"),
    path("dashboard/admin/bookings/<int:booking_id>/<str:decision>/", views.booking_decision, name="booking_decision"),
    path("dashboard/admin/staff-requests/<int:request_id>/<str:decision>/", views.staff_request_decision, name="staff_request_decision"),
]