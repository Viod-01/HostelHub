from django.db import models
from django.contrib.auth.models import User


class Hostel(models.Model):
    """A hostel block, e.g. Block A."""
    ROOM_TYPE_CHOICES = [
        ("single", "Single"),
        ("shared", "Shared"),
        ("self_contain", "Self-Contain"),
        ("two_bedroom", "2-Bedroom Apartment"),
    ]

    name = models.CharField(max_length=50)          # e.g. "Block A"
    room_type = models.CharField(max_length=15, choices=ROOM_TYPE_CHOICES)
    capacity_per_room = models.PositiveIntegerField()  # e.g. 4 for shared, 1 for single
    price_per_session = models.PositiveIntegerField()  # in naira
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name

class Room(models.Model):
    """An individual room inside a hostel block."""
    hostel = models.ForeignKey(Hostel, on_delete=models.CASCADE, related_name="rooms")
    room_number = models.CharField(max_length=20, unique=True)    # e.g. "A-101"
    floor = models.CharField(max_length=20, blank=True)
    occupied_beds = models.PositiveIntegerField(default=0)

    @property
    def capacity(self):
        return self.hostel.capacity_per_room

    @property
    def vacant_beds(self):
        return self.capacity - self.occupied_beds

    @property
    def is_full(self):
        return self.vacant_beds <= 0

    def __str__(self):
        return self.room_number


class StudentProfile(models.Model):
    """Extra fields on top of Django's built-in User."""
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    matric_number = models.CharField(max_length=30, unique=True)
    department = models.CharField(max_length=100)
    level = models.PositiveIntegerField()
    phone = models.CharField(max_length=20, blank=True)

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} ({self.matric_number})"


class Booking(models.Model):
    """A student's application for a room."""
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]

    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="bookings")
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name="bookings")
    session = models.CharField(max_length=20)        # e.g. "2026/2027"
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    special_requests = models.TextField(blank=True)
    applied_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.student} -> {self.room} ({self.status})"


class StaffAccessRequest(models.Model):
    """A request for admin/staff access, submitted from the admin login
    page's 'Request Access' tab. The linked User is created immediately
    but stays inactive (is_active=False) until an existing admin approves
    it — so authenticate() already refuses login for anyone still pending,
    with no extra code needed for that."""
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    staff_id = models.CharField(max_length=30, unique=True)
    department = models.CharField(max_length=100)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="pending")
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} ({self.status})"