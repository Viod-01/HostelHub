from django.contrib import admin
from .models import Hostel, Room, StudentProfile, Booking, StaffAccessRequest


@admin.register(Hostel)
class HostelAdmin(admin.ModelAdmin):
    list_display = ("name", "room_type", "capacity_per_room", "price_per_session")


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("room_number", "hostel", "occupied_beds", "vacant_beds", "is_full")
    list_filter = ("hostel",)


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display = ("matric_number", "user", "department", "level")


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("student", "room", "session", "status", "applied_at")
    list_filter = ("status", "session")


@admin.register(StaffAccessRequest)
class StaffAccessRequestAdmin(admin.ModelAdmin):
    list_display = ("user", "staff_id", "department", "status", "requested_at")
    list_filter = ("status", "department")