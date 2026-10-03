"""
Threaded tests for the booking decision path.

These only run when the database is PostgreSQL (they are skipped on sqlite),
because they rely on real row locks: select_for_update() is silently ignored
by sqlite. Run them against Postgres with:

    DATABASE_URL=postgres://user:pass@host:5432/dbname python manage.py test portal.test_concurrency

Each test fires two "admins" at the exact same moment (a threading.Barrier
synchronises them) and asserts the invariant that must hold no matter how
their work interleaves: a bed is counted once, a room is never overbooked,
a booking is decided once.
"""
import threading
import unittest

from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.db import connection
from django.test import RequestFactory, TransactionTestCase

from .models import Hostel, Room, StudentProfile, Booking
from .views import booking_decision


@unittest.skipUnless(
    connection.vendor == "postgresql",
    "row locks (select_for_update) need PostgreSQL; sqlite ignores them",
)
class ConcurrentDecisionTests(TransactionTestCase):
    # TransactionTestCase: real commits, no per-test transaction — threads in
    # other connections must be able to see and lock these rows.

    def setUp(self):
        self.staff = User.objects.create_user("admin", password="pw", is_staff=True)
        self.hostel = Hostel.objects.create(
            name="Test Block", room_type="shared", capacity_per_room=2,
            price_per_session=60000,
        )

    def _decide_in_thread(self, booking_pk, decision, barrier, results):
        """Call the view from inside this thread, with its own DB connection."""
        request = RequestFactory().post(f"/dashboard/admin/bookings/{booking_pk}/{decision}/")
        request.user = User.objects.get(pk=self.staff.pk)
        setattr(request, "session", "session")
        setattr(request, "_messages", FallbackStorage(request))
        try:
            barrier.wait()                      # both admins click at once
            results.append(booking_decision(request, booking_pk, decision))
        finally:
            connection.close()                  # don't leak this thread's connection

    def _run_concurrently(self, *calls):
        """calls = list of (booking_pk, decision) fired simultaneously."""
        barrier = threading.Barrier(len(calls))
        results = []
        threads = [
            threading.Thread(target=self._decide_in_thread, args=(pk, dec, barrier, results))
            for pk, dec in calls
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results

    def test_two_admins_approving_the_same_booking_count_it_once(self):
        room = Room.objects.create(hostel=self.hostel, room_number="T-101", occupied_beds=0)
        student = User.objects.create_user("student", password="pw")
        StudentProfile.objects.create(user=student, matric_number="MAT/001", department="CS", level=200)
        booking = Booking.objects.create(student=student, room=room, session="2026/2027")

        self._run_concurrently((booking.pk, "approved"), (booking.pk, "approved"))

        room.refresh_from_db()
        booking.refresh_from_db()
        self.assertEqual(booking.status, "approved")
        self.assertEqual(room.occupied_beds, 1, "the same bed was counted twice")

    def test_two_students_cannot_both_get_the_last_bed(self):
        # one bed left; two pending applications for it; approved simultaneously
        room = Room.objects.create(hostel=self.hostel, room_number="T-102", occupied_beds=1)
        bookings = []
        for i in (1, 2):
            student = User.objects.create_user(f"student{i}", password="pw")
            StudentProfile.objects.create(
                user=student, matric_number=f"MAT/00{i}", department="CS", level=200
            )
            bookings.append(Booking.objects.create(student=student, room=room, session="2026/2027"))

        self._run_concurrently((bookings[0].pk, "approved"), (bookings[1].pk, "approved"))

        room.refresh_from_db()
        statuses = sorted(Booking.objects.filter(pk__in=[b.pk for b in bookings])
                          .values_list("status", flat=True))
        self.assertEqual(room.occupied_beds, 2, "the room was overbooked past capacity")
        self.assertEqual(statuses, ["approved", "pending"],
                         "both applications were approved for one bed")
