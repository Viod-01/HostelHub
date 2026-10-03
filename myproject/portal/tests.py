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

from .models import Hostel, Room, StudentProfile, Booking
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


# -------------------------------------------------------- public pages

class PublicPageSmokeTests(TestCase):
    """Every public page renders. Cheap insurance against template/URL drift."""

    def test_public_pages_render(self):
        for url in ["/", "/hostels/", "/login/", "/admin-login/"]:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
