from django.db.models import F
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.models import User
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.utils import timezone
from .models import Hostel, Room, StudentProfile, Booking, StaffAccessRequest
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.contrib.auth.password_validation import validate_password


def landing(request):
    """Simplest possible view — just renders the template, no database query yet.
    Confirm this works before writing anything else."""
    return render(request, "hostel/landing.html")


def login_register(request):
    """Renders the login/register page. Actual auth happens in login_view
    and register_view below, which this page's two forms POST to."""
    return render(request, "hostel/login.html")


def login_view(request):
    if request.method != "POST":
        return redirect("login_register")

    identifier = request.POST.get("login_id", "").strip()
    password = request.POST.get("login_pw", "")

    # identifier can be a matric number or an email — this form is for
    # students only, staff use the separate admin login page.
    username = None
    profile = StudentProfile.objects.filter(matric_number=identifier).first()
    if profile:
        username = profile.user.username
    else:
        user_by_email = User.objects.filter(email=identifier).first()
        username = user_by_email.username if user_by_email else identifier

    user = authenticate(request, username=username, password=password) if username else None

    if user is not None:
        if user.is_staff:
            messages.error(request, "Staff accounts sign in from the admin login page.")
            return render(request, "hostel/login.html", {"login_error": True})
        auth_login(request, user)
        return redirect("dashboard")

    messages.error(request, "Incorrect matric number/email or password.")
    return render(request, "hostel/login.html", {"login_error": True})


def register_view(request):
    if request.method != "POST":
        return redirect("login_register")

    full_name = request.POST.get("reg_name", "").strip()
    matric = request.POST.get("reg_matric", "").strip()
    level = request.POST.get("reg_level", "").strip()
    email = request.POST.get("reg_email", "").strip()
    password = request.POST.get("reg_pw", "")

    if not all([full_name, matric, level, email, password]):
        messages.error(request, "Please fill in every field.")
        return render(request, "hostel/login.html", {"register_error": True})

    if User.objects.filter(username=matric).exists():
        messages.error(request, "An account with that matric number already exists.")
        return render(request, "hostel/login.html", {"register_error": True})

    try:
        validate_email(email)
        validate_password(password)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
        return render(request, "hostel/login.html", {"register_error": True})

    if User.objects.filter(email__iexact=email).exists():
        messages.error(request, "An account with that email already exists.")
        return render(request, "hostel/login.html", {"register_error": True})
    
    user = User.objects.create_user(username=matric, email=email, password=password)
    name_parts = full_name.split(" ", 1)
    user.first_name = name_parts[0]
    user.last_name = name_parts[1] if len(name_parts) > 1 else ""
    user.save()

    StudentProfile.objects.create(
        user=user,
        matric_number=matric,
        department="",
        level=level,
    )

    auth_login(request, user)
    return redirect("dashboard")


def logout_view(request):
    auth_logout(request)
    return redirect("landing")


def admin_login_page(request):
    """Renders the separate admin login page. Actual auth happens in
    admin_login_submit below, which its form POSTs to."""
    rooms = Room.objects.select_related("hostel").all()
    total_capacity = sum(r.capacity for r in rooms)
    total_occupied = sum(r.occupied_beds for r in rooms)
    total_vacant = total_capacity - total_occupied
    context = {"total_capacity": total_capacity, "total_vacant": total_vacant}
    return render(request, "hostel/admin_login.html", context)


def admin_login_submit(request):
    if request.method != "POST":
        return redirect("admin_login")

    identifier = request.POST.get("admin_id", "").strip()
    password = request.POST.get("admin_pw", "")

    # staff accounts have no matric number — identifier is either their
    # Django username or their email, never looked up via StudentProfile
    user_by_email = User.objects.filter(email=identifier).first()
    username = user_by_email.username if user_by_email else identifier

    user = authenticate(request, username=username, password=password)

    if user is not None and user.is_staff:
        auth_login(request, user)
        return redirect("admin_dashboard")

    # deliberately the same generic message whether the account doesn't
    # exist, the password's wrong, or it's a real account that just
    # isn't staff — don't reveal which, to avoid helping someone probe
    messages.error(request, "Incorrect staff ID/email or password.")
    return render(request, "hostel/admin_login.html", {"login_error": True})


def request_access(request):
    if request.method != "POST":
        return redirect("admin_login")

    full_name = request.POST.get("full_name", "").strip()
    staff_id = request.POST.get("staff_id", "").strip()
    department = request.POST.get("department", "").strip()
    email = request.POST.get("email", "").strip()
    password = request.POST.get("password", "")

    if not all([full_name, staff_id, department, email, password]):
        messages.error(request, "Please fill in every field.")
        return render(request, "hostel/admin_login.html", {
            "request_access_error": True, "active_tab": "request",
        })

    if User.objects.filter(username=staff_id).exists() or StaffAccessRequest.objects.filter(staff_id=staff_id).exists():
        messages.error(request, "An access request with that staff ID already exists.")
        return render(request, "hostel/admin_login.html", {
            "request_access_error": True, "active_tab": "request",
        })

    # Created inactive and not staff — authenticate() already refuses login
    # for inactive users, so there's no separate check needed to keep this
    # account locked out until an existing admin approves the request.
    user = User.objects.create_user(
        username=staff_id, email=email, password=password, is_active=False,
    )
    name_parts = full_name.split(" ", 1)
    user.first_name = name_parts[0]
    user.last_name = name_parts[1] if len(name_parts) > 1 else ""
    user.save()

    StaffAccessRequest.objects.create(user=user, staff_id=staff_id, department=department)

    return render(request, "hostel/admin_login.html", {
        "request_submitted": True, "active_tab": "request",
    })


def listing(request):
    # Only rooms with at least one free bed — mirrors the r.vacant > 0
    # filter your frontend JS already does, but now enforced at the DB level.
    rooms = (
        Room.objects
        .select_related("hostel")
        .filter(occupied_beds__lt=F("hostel__capacity_per_room"))
    )
    for room in rooms:
        room.bed_dots = ["vacant"] * room.vacant_beds + ["taken"] * room.occupied_beds

    hostels = Hostel.objects.all()  # populates the block filter dropdown
    return render(request, "hostel/listing.html", {"rooms": rooms, "hostels": hostels})


# def detail(request, room_number):
#     room = get_object_or_404(Room.objects.select_related("hostel"), room_number=room_number)
#     room.bed_dots = ["vacant"] * room.vacant_beds + ["taken"] * room.occupied_beds
#     return render(request, "hostel/details.html", {"room": room})

def detail(request, room_number):
    room = get_object_or_404(
        Room.objects.select_related("hostel"),
        room_number=room_number,
    )
    room.bed_dots = ["vacant"] * room.vacant_beds + ["taken"] * room.occupied_beds

    approved_booking = None
    if request.user.is_authenticated:
        approved_booking = (
            Booking.objects
            .filter(student=request.user, status="approved")
            .select_related("room", "room__hostel")
            .order_by("-decided_at")
            .first()
        )

    return render(request, "hostel/details.html", {
        "room": room,
        "approved_booking": approved_booking,
    })


@login_required
def dashboard(request):
    student = get_object_or_404(StudentProfile, user=request.user)
    # latest booking regardless of status — the template branches on booking.status
    booking = (
        Booking.objects
        .filter(student=request.user)
        .select_related("room", "room__hostel")
        .order_by("-applied_at")
        .first()
    )
    full_name = request.user.get_full_name()
    initials = "".join(p[0].upper() for p in full_name.split()[:2]) if full_name else request.user.username[:2].upper()

    context = {
        "student": student,
        "booking": booking,
        "full_name": full_name,
        "initials": initials,
    }
    return render(request, "hostel/dashboard.html", context)


@login_required
def booking(request, room_number):
    room = get_object_or_404(Room.objects.select_related("hostel"), room_number=room_number)
    student = get_object_or_404(StudentProfile, user=request.user)

    if request.method == "POST":
        if room.is_full:
            messages.error(request, "This room just filled up — please choose another.")
            return redirect("listing")

        already_active = Booking.objects.filter(
            student=request.user, status__in=["pending", "approved"]
        ).exists()
        if already_active:
            messages.error(request, "You already have an active application.")
            return redirect("dashboard")

        session = request.POST.get("session", "").strip()
        special_requests = request.POST.get("special_requests", "").strip()
        phone = request.POST.get("phone", "").strip()

        if phone:
            student.phone = phone
            student.save()

        Booking.objects.create(
            student=request.user,
            room=room,
            session=session,
            special_requests=special_requests,
        )
        return redirect("dashboard")

    return render(request, "hostel/booking.html", {"room": room, "student": student})


@staff_member_required(login_url='admin_login')
def admin_dashboard(request):
    rooms = Room.objects.select_related("hostel").all()
    hostels = Hostel.objects.all()
    students = StudentProfile.objects.select_related("user").all()
    bookings = (
        Booking.objects
        .select_related("student", "room", "room__hostel")
        .order_by("-applied_at")
    )

    # bed-level totals — computed from whatever rooms actually exist,
    # not a fixed guess, so this stays correct as you add/remove rooms
    total_capacity = sum(r.capacity for r in rooms)
    total_occupied = sum(r.occupied_beds for r in rooms)
    total_vacant = total_capacity - total_occupied
    vacant_pct = round((total_vacant / total_capacity) * 100) if total_capacity else 0
    occupied_pct = 100 - vacant_pct if total_capacity else 0

    for hostel in hostels:
        hostel_rooms = [r for r in rooms if r.hostel_id == hostel.id]
        hostel.room_count = len(hostel_rooms)
        hostel.vacant_count = sum(r.vacant_beds for r in hostel_rooms)

    for student in students:
        student.latest_booking = (
            Booking.objects.filter(student=student.user).order_by("-applied_at").first()
        )

    staff_requests = (
        StaffAccessRequest.objects
        .select_related("user")
        .order_by("-requested_at")
    )
    pending_staff_requests = staff_requests.filter(status="pending")

    context = {
        "rooms": rooms,
        "hostels": hostels,
        "students": students,
        "bookings": bookings,
        "pending_bookings": bookings.filter(status="pending"),
        "pending_count": bookings.filter(status="pending").count(),
        "staff_requests": staff_requests,
        "pending_staff_requests": pending_staff_requests,
        "pending_staff_count": pending_staff_requests.count(),
        "total_capacity": total_capacity,
        "total_occupied": total_occupied,
        "total_vacant": total_vacant,
        "vacant_pct": vacant_pct,
        "occupied_pct": occupied_pct,
    }
    return render(request, "hostel/admin.html", context)


@staff_member_required(login_url='admin_login')
def staff_request_decision(request, request_id, decision):
    if request.method != "POST" or decision not in ("approved", "rejected"):
        return redirect("admin_dashboard")

    access_request = get_object_or_404(StaffAccessRequest, pk=request_id)

    if decision == "approved":
        access_request.user.is_active = True
        access_request.user.is_staff = True
        access_request.user.save()
        access_request.status = "approved"
    else:
        # keep the account on record but leave it inactive/non-staff —
        # rejected requests can't log in anywhere, student or admin side
        access_request.status = "rejected"

    access_request.decided_at = timezone.now()
    access_request.save()

    return redirect("admin_dashboard")


@staff_member_required(login_url='admin_login')
def booking_decision(request, booking_id, decision):
    if request.method != "POST" or decision not in ("approved", "rejected"):
        return redirect("admin_dashboard")

    booking_obj = get_object_or_404(Booking, pk=booking_id)

    if decision == "approved":
        if booking_obj.room.is_full:
            messages.error(request, f"Room {booking_obj.room.room_number} is already full.")
        else:
            booking_obj.room.occupied_beds += 1
            booking_obj.room.save()
            booking_obj.status = "approved"
            booking_obj.decided_at = timezone.now()
            booking_obj.save()
    else:
        booking_obj.status = "rejected"
        booking_obj.decided_at = timezone.now()
        booking_obj.save()

    return redirect("admin_dashboard")