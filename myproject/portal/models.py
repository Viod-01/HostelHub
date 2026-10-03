from django.db import models
from django.db.models import Q
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

    class Meta:
        # The database itself enforces "one live application per student":
        # a partial unique index — at most ONE row per student whose status is
        # pending or approved. Rejected rows don't block a new application.
        # The views check this in Python too, but a check-then-insert race
        # (double-click, two tabs) can only be caught by the database itself.
        constraints = [
            models.UniqueConstraint(
                fields=["student"],
                condition=Q(status__in=["pending", "approved"]),
                name="one_active_booking_per_student",
            ),
        ]


class Complaint(models.Model):
    """A maintenance or service issue reported by a student, e.g. a burst
    pipe. Students file these from their dashboard; the hostel office
    walks each one through the pipeline and every step is fed back to the
    student as an update trail: Reported → In Review → Assigned →
    In Progress → Resolved → Closed."""
    CATEGORY_CHOICES = [
        ("plumbing", "Plumbing"),
        ("electrical", "Electrical"),
        ("water", "Water Supply"),
        ("security", "Security"),
        ("maintenance", "General Maintenance"),
        ("other", "Other"),
    ]
    STATUS_CHOICES = [
        ("open", "Reported"),
        ("review", "In Review"),
        ("assigned", "Assigned"),
        ("in_progress", "In Progress"),
        ("resolved", "Resolved"),
        ("closed", "Closed"),
    ]

    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="complaints")
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    description = models.TextField()
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="open")
    # the block supervisor the office handed this complaint to (if any)
    assigned_to = models.ForeignKey(
        "BlockSupervisor",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="complaints",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.get_category_display()} — {self.student.username} ({self.status})"


class ComplaintUpdate(models.Model):
    """One entry in a complaint's timeline: a status change, an assignment,
    or a note from the hostel office. The student sees these on their
    complaints page — this IS the feedback loop."""
    complaint = models.ForeignKey(
        Complaint, on_delete=models.CASCADE, related_name="updates"
    )
    author = models.ForeignKey(User, on_delete=models.CASCADE)
    status = models.CharField(max_length=12, choices=Complaint.STATUS_CHOICES)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"#{self.complaint_id} → {self.get_status_display()} by {self.author}"


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

class BlockSupervisor(models.Model):
    """The member of staff responsible for one hostel block. Shown to
    students on that block's room pages, and managed by wardens through
    the Django admin (/admin/) rather than a custom form."""
    hostel = models.OneToOneField(
        Hostel, on_delete=models.CASCADE, related_name="supervisor"
    )
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=30)
    email = models.EmailField(blank=True)
    office_hours = models.CharField(max_length=100, blank=True)

    @property
    def initials(self):
        # "Mrs. Margaret Okoye" -> "MO"; titles (Mr./Mrs./Dr.) are skipped
        parts = [p.strip(". ") for p in self.name.split() if p.strip(". ")]
        if len(parts) >= 2:
            return (parts[-2][0] + parts[-1][0]).upper()
        return self.name[:2].upper()

    def __str__(self):
        return f"{self.name} ({self.hostel.name})"


class BlockAssistant(models.Model):
    """An assistant to a block supervisor, shown alongside them."""
    supervisor = models.ForeignKey(
        BlockSupervisor, on_delete=models.CASCADE, related_name="assistants"
    )
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=30)

    def __str__(self):
        return f"{self.name} (assistant, {self.supervisor.hostel.name})"
