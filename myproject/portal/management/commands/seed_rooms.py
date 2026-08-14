from django.core.management.base import BaseCommand
from portal.models import Hostel, Room

ROOMS_PER_BLOCK = 51

# (block name, room_number prefix, room type, capacity per unit, price per session)
# Blocks A-F: the original locked spec.
# Blocks G-O: same 6-pattern cycled again, since no separate spec was given for them.
# Special blocks: self-contain (sitting room + bedroom + toilet, 1 occupant)
#                 and 2-bedroom apartment (2 occupants) — 3:2 ratio.
BLOCK_DATA = [
    ("Block A", "A", "shared", 4, 60000),
    ("Block B", "B", "single", 1, 75000),
    ("Block C", "C", "shared", 3, 65000),
    ("Block D", "D", "single", 1, 80000),
    ("Block E", "E", "shared", 2, 90000),
    ("Block F", "F", "shared", 4, 70000),
    ("Block G", "G", "shared", 4, 60000),
    ("Block H", "H", "single", 1, 75000),
    ("Block I", "I", "shared", 3, 65000),
    ("Block J", "J", "single", 1, 80000),
    ("Block K", "K", "shared", 2, 90000),
    ("Block L", "L", "shared", 4, 70000),
    ("Block M", "M", "shared", 4, 60000),
    ("Block N", "N", "single", 1, 75000),
    ("Block O", "O", "shared", 3, 65000),
    ("Self-Contain 1", "SC1", "self_contain", 1, 120000),
    ("Self-Contain 2", "SC2", "self_contain", 1, 120000),
    ("Self-Contain 3", "SC3", "self_contain", 1, 120000),
    ("2-Bedroom Apartment 1", "TB1", "two_bedroom", 2, 150000),
    ("2-Bedroom Apartment 2", "TB2", "two_bedroom", 2, 150000),
]


class Command(BaseCommand):
    help = "Seeds all 20 hostel blocks (15 standard + 5 special) with 51 rooms each — 1,020 total, all vacant."

    def handle(self, *args, **options):
        for name, code, room_type, capacity, price in BLOCK_DATA:
            hostel, created = Hostel.objects.get_or_create(
                name=name,
                defaults={
                    "room_type": room_type,
                    "capacity_per_room": capacity,
                    "price_per_session": price,
                },
            )
            if not created:
                hostel.room_type = room_type
                hostel.capacity_per_room = capacity
                hostel.price_per_session = price
                hostel.save()

            new_count = 0
            for i in range(1, ROOMS_PER_BLOCK + 1):
                room_number = f"{code}-{100 + i}"           # A-101 ... A-151, SC1-101 ...
                floor = str(((i - 1) // 10) + 1)              # 10 rooms per floor

                room, was_created = Room.objects.get_or_create(
                    room_number=room_number,
                    defaults={"hostel": hostel, "floor": floor, "occupied_beds": 0},
                )
                if was_created:
                    new_count += 1
                else:
                    room.hostel = hostel
                    room.floor = floor
                    room.save(update_fields=["hostel", "floor"])

            self.stdout.write(self.style.SUCCESS(
                f"{name}: {new_count} new rooms created (target {ROOMS_PER_BLOCK}, all vacant)"
            ))

        self.stdout.write(self.style.SUCCESS(
            f"Done — {len(BLOCK_DATA)} blocks x {ROOMS_PER_BLOCK} rooms = {len(BLOCK_DATA) * ROOMS_PER_BLOCK} rooms total, all vacant."
        ))