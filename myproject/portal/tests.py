"""
HostelHub's test suite.

The decision path (warden approves/rejects) is where correctness matters
most: it writes bed counts, and it is exactly the code that concurrency
bugs bite. These tests run on any database (sqlite in dev, Postgres in
prod). The threaded concurrency tests live in test_concurrency.py and
only run when the database is PostgreSQL, because sqlite ignores row locks.

Run with:  python manage.py test portal
"""
from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.db import IntegrityError
from django.test import RequestFactory, TestCase
from django.utils import timezone

from .models import Hostel, Room, StudentProfile, Booking, Complaint, BlockSupervisor, BlockAssistant
from .views import booking_decision


# ---------------------------------------------------------------- helpers

def make_hostel(name="Test Block", capacity=4):
    return Hostel.objects.create(
        name=name, room_type="shared", capacity_per_room=capacity,
        price_per_session=60000,
    )


def make_room(hostel, number="T-101", occupied=0):
    return Room.objects.create(
        hostel=hostel, room_number=number, occupied_beds=occupied
    )


def make_student(username="student1", matric="MAT/001"):
    user = User.objects.create_user(username=username, password="pw-12345!")
    StudentProfile.objects.create(
        user=user, matric_number=matric, department="CS", level=200
    )
    return user


def decide(booking_pk, decision, staff):
    """Call the booking_decision view the way a warden's browser would."""
    request = RequestFactory().post(f"/dashboard/admin/bookings/{booking_pk}/{decision}/")
    request.user = staff
    # The messages framework needs storage on the request; the test client
    # normally wires this up, but we are calling the view function directly.
    setattr(request, "session", "session")
    setattr(request, "_messages", FallbackStorage(request))
    return booking_decision(request, booking_pk, decision)


# ------------------------------------------------- deciding a booking

class BookingDecisionTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("warden", password="pw", is_staff=True)
        self.hostel = make_hostel(capacity=2)
        self.room = make_room(self.hostel, occupied=1)      # exactly one bed free
        self.student = make_student()
        self.booking = Booking.objects.create(
            student=self.student, room=self.room, session="2026/2027"
        )

    def test_approve_takes_a_bed_and_stamps_the_decision(self):
        response = decide(self.booking.pk, "approved", self.staff)
        self.assertEqual(response.status_code, 302)
        self.room.refresh_from_db()
        self.booking.refresh_from_db()
        self.assertEqual(self.room.occupied_beds, 2)
        self.assertEqual(self.booking.status, "approved")
        self.assertIsNotNone(self.booking.decided_at)

    def test_double_decide_is_a_noop(self):
        # A double-click (or two wardens) must not count the bed twice.
        decide(self.booking.pk, "approved", self.staff)
        first_at = Booking.objects.get(pk=self.booking.pk).decided_at
        decide(self.booking.pk, "approved", self.staff)
        self.room.refresh_from_db()
        self.booking.refresh_from_db()
        self.assertEqual(self.room.occupied_beds, 2)        # not 3
        self.assertEqual(self.booking.decided_at, first_at) # not re-stamped

    def test_stale_tab_cannot_flip_a_final_decision(self):
        decide(self.booking.pk, "rejected", self.staff)
        decide(self.booking.pk, "approved", self.staff)     # late approve
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, "rejected")   # stays rejected

    def test_cannot_approve_into_a_full_room(self):
        self.room.occupied_beds = 2                         # now full
        self.room.save()
        decide(self.booking.pk, "approved", self.staff)
        self.room.refresh_from_db()
        self.booking.refresh_from_db()
        self.assertEqual(self.room.occupied_beds, 2)        # unchanged
        self.assertEqual(self.booking.status, "pending")    # still needs a decision

    def test_reject_takes_no_bed_and_stamps_the_decision(self):
        decide(self.booking.pk, "rejected", self.staff)
        self.room.refresh_from_db()
        self.booking.refresh_from_db()
        self.assertEqual(self.room.occupied_beds, 1)
        self.assertEqual(self.booking.status, "rejected")
        self.assertIsNotNone(self.booking.decided_at)


# --------------------------------------- one live application per student

class OneActiveBookingRuleTests(TestCase):
    """The partial unique index declared in Booking.Meta, exercised directly
    against the database — the views' Python checks are only advisory."""

    def setUp(self):
        self.hostel = make_hostel()
        self.room = make_room(self.hostel)
        self.student = make_student()

    def test_second_active_booking_is_refused_by_the_database(self):
        Booking.objects.create(student=self.student, room=self.room, session="2026/2027")
        with self.assertRaises(IntegrityError):
            Booking.objects.create(student=self.student, room=self.room, session="2027/2028")

    def test_rejected_application_does_not_block_reapplying(self):
        Booking.objects.create(
            student=self.student, room=self.room, session="2026/2027", status="rejected"
        )
        Booking.objects.create(student=self.student, room=self.room, session="2027/2028")

    def test_bogus_decision_string_touches_nothing(self):
        # A crafted URL like /decide/5/delete/ must redirect and change nothing.
        booking = Booking.objects.create(student=self.student, room=self.room, session="2026/2027")
        staff = User.objects.create_user("w", password="pw", is_staff=True)
        response = decide(booking.pk, "delete", staff)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Booking.objects.get(pk=booking.pk).status, "pending")


# ----------------------------------------------------------- complaints

class ComplaintTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("warden", password="pw", is_staff=True)
        self.student = make_student()
        self.client.login(username="student1", password="pw-12345!")

    def test_student_submits_and_sees_their_complaint(self):
        response = self.client.post("/dashboard/complaints/submit/", {
            "category": "plumbing",
            "description": "The tap in the corner bathroom is dripping.",
        }, follow=True)
        self.assertContains(response, "Complaint submitted")
        self.assertContains(response, "The tap in the corner bathroom is dripping.")
        complaint = Complaint.objects.get(student=self.student)
        self.assertEqual(complaint.status, "open")
        self.assertIsNone(complaint.resolved_at)

    def test_crafted_category_is_refused(self):
        # the <select> is advisory; a hand-built POST can send anything
        self.client.post("/dashboard/complaints/submit/", {
            "category": "free_wifi", "description": "please",
        })
        self.assertEqual(Complaint.objects.count(), 0)

    def test_empty_description_is_refused(self):
        self.client.post("/dashboard/complaints/submit/", {"category": "water"})
        self.assertEqual(Complaint.objects.count(), 0)

    def test_warden_resolves_and_it_shows_on_their_dashboard(self):
        complaint = Complaint.objects.create(
            student=self.student, category="water", description="No water on floor 2"
        )
        self.client.logout()
        self.client.login(username="warden", password="pw")
        response = self.client.post(
            f"/dashboard/admin/complaints/{complaint.pk}/resolve/", follow=True
        )
        self.assertContains(response, "No water on floor 2")      # on the warden list
        complaint.refresh_from_db()
        self.assertEqual(complaint.status, "resolved")
        self.assertIsNotNone(complaint.resolved_at)

    def test_student_sees_the_resolved_status(self):
        complaint = Complaint.objects.create(
            student=self.student, category="security", description="Broken window lock"
        )
        complaint.status = "resolved"
        complaint.resolved_at = timezone.now()
        complaint.save()
        response = self.client.get("/dashboard/")
        self.assertContains(response, "Broken window lock")
        self.assertContains(response, "Resolved")

    def test_students_cannot_resolve_complaints(self):
        complaint = Complaint.objects.create(
            student=self.student, category="other", description="x"
        )
        response = self.client.post(f"/dashboard/admin/complaints/{complaint.pk}/resolve/")
        self.assertEqual(response.status_code, 302)   # bounced to admin login
        complaint.refresh_from_db()
        self.assertEqual(complaint.status, "open")

    def test_double_resolve_is_a_noop(self):
        complaint = Complaint.objects.create(
            student=self.student, category="other", description="x"
        )
        self.client.logout()
        self.client.login(username="warden", password="pw")
        self.client.post(f"/dashboard/admin/complaints/{complaint.pk}/resolve/")
        first_at = Complaint.objects.get(pk=complaint.pk).resolved_at
        self.client.post(f"/dashboard/admin/complaints/{complaint.pk}/resolve/")
        complaint.refresh_from_db()
        self.assertEqual(complaint.resolved_at, first_at)


# ---------------------------------------------- block supervisor display

class BlockSupervisorDisplayTests(TestCase):
    """Supervisor details are real data now (the admin page used to show
    hardcoded placeholder cards) and belong on the student's room pages."""

    def setUp(self):
        self.hostel = make_hostel()
        self.room = make_room(self.hostel, "T-101")
        self.supervisor = BlockSupervisor.objects.create(
            hostel=self.hostel, name="Mrs. Margaret Okoye", phone="0803 111 2223",
            email="m.okoye@hostelhub.edu.ng", office_hours="Mon–Fri, 9–4",
        )
        BlockAssistant.objects.create(
            supervisor=self.supervisor, name="John Okafor", phone="0812 333 4445"
        )

    def test_room_detail_shows_the_block_contacts(self):
        response = self.client.get("/hostels/T-101/")
        self.assertContains(response, "Mrs. Margaret Okoye")
        self.assertContains(response, "0803 111 2223")
        self.assertContains(response, "John Okafor")
        self.assertContains(response, "Block Contacts")

    def test_apply_page_shows_the_block_contacts(self):
        make_student()
        self.client.login(username="student1", password="pw-12345!")
        response = self.client.get("/hostels/T-101/apply/")
        self.assertContains(response, "Mrs. Margaret Okoye")
        self.assertContains(response, "John Okafor")

    def test_warden_dashboard_shows_real_supervisors_not_placeholders(self):
        staff = User.objects.create_user("warden", password="pw", is_staff=True)
        self.client.login(username="warden", password="pw")
        response = self.client.get("/dashboard/admin/")
        self.assertContains(response, "Mrs. Margaret Okoye")
        self.assertNotContains(response, "Okoye</div><div class=\"supervisor-block\">Block B")

    def test_block_without_supervisor_renders_fine(self):
        make_hostel(name="Lonely Block")
        response = self.client.get("/hostels/")  # listing unaffected
        self.assertEqual(response.status_code, 200)


# -------------------------------------------------------- public pages

class PublicPageSmokeTests(TestCase):
    """Every public page renders. Cheap insurance against template/URL drift."""

    def test_public_pages_render(self):
        for url in ["/", "/hostels/", "/login/", "/admin-login/"]:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)

    def test_password_fields_have_show_hide_toggles(self):
        for url in ["/login/", "/admin-login/"]:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, "pw-toggle")


# ------------------------------------ registration now asks for department

class RegistrationDepartmentTests(TestCase):
    def test_register_saves_department(self):
        response = self.client.post("/register/submit/", {
            "reg_name": "Test Student",
            "reg_matric": "AFIT/2026/0999",
            "reg_level": "200",
            "reg_department": "Computer Science",
            "reg_email": "test.student@example.com",
            "reg_pw": "sensible-password-1",
        })
        self.assertRedirects(response, "/dashboard/")
        profile = StudentProfile.objects.get(matric_number="AFIT/2026/0999")
        self.assertEqual(profile.department, "Computer Science")


# ------------------------- one live application, enforced in the UI as well

class ActiveApplicationUxTests(TestCase):
    def setUp(self):
        self.hostel = make_hostel()
        self.room = make_room(self.hostel, "T-101")
        make_room(self.hostel, "T-102")
        self.student = make_student()
        self.client.login(username="student1", password="pw-12345!")

    def test_pending_student_cannot_open_a_second_apply_form(self):
        Booking.objects.create(student=self.student, room=self.room, session="2026/2027")
        response = self.client.get("/hostels/T-102/apply/")
        self.assertRedirects(response, "/dashboard/")

    def test_room_page_shows_track_button_instead_of_apply(self):
        Booking.objects.create(student=self.student, room=self.room, session="2026/2027")
        response = self.client.get("/hostels/T-102/")
        self.assertNotContains(response, "Apply for This Room")
        self.assertContains(response, "Track My Application")

    def test_the_rejection_message_actually_renders(self):
        # the guard's message was set but silently dropped before the
        # dashboard learned to display messages
        Booking.objects.create(student=self.student, room=self.room, session="2026/2027")
        response = self.client.get("/hostels/T-102/apply/", follow=True)
        self.assertContains(response, "already have an active application")


# --------------------------------------------- logout reachable everywhere

class LogoutPlacementTests(TestCase):
    """Log Out used to be buried in the dashboard's quick links, absent from
    the booking page, and missing entirely from the warden dashboard."""

    def test_student_pages_have_top_right_logout(self):
        hostel = make_hostel()
        make_room(hostel, "T-101")
        make_student()
        self.client.login(username="student1", password="pw-12345!")
        for url in ["/dashboard/", "/hostels/T-101/", "/hostels/T-101/apply/"]:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Log Out")

    def test_warden_dashboard_has_logout(self):
        User.objects.create_user("warden2", password="pw", is_staff=True)
        self.client.login(username="warden2", password="pw")
        response = self.client.get("/dashboard/admin/")
        self.assertContains(response, "Log out")
