# HostelHub — second pass: additional issues

Found by probing paths the first pass did not cover. Each was **reproduced against a
running app**, not inferred from reading source. Fresh DB for the repros:
`python3.13 -m venv && pip install -r requirements.txt && manage.py migrate && manage.py seed_rooms`.

Verified: no `|safe`, no `mark_safe`, no `autoescape off` anywhere in `portal/` → **no XSS surface** in these templates.

---

## P0-1 · `room_number` has no UNIQUE constraint in the actual schema

The model says `unique=True`. The **initial migration never did** — so the deployed table
has no constraint. This is real schema/model drift, and Django reports it:

```
$ python manage.py makemigrations --check
Migrations for 'portal':
  portal/migrations/0004_alter_room_room_number.py
    ~ Alter field room_number on room

$ python manage.py makemigrations portal --dry-run -v 3
    field=models.CharField(max_length=20, unique=True),   # ← the ONLY change: unique=True

$ # actual table DDL on a migrated DB:
  CREATE TABLE "portal_room" ("id" integer NOT NULL PRIMARY KEY AUTOINCREMENT,
    "room_number" varchar(20) NOT NULL, "floor" varchar(20) NOT NULL, ...
                                              ^^^^^^^^^^^^^^^^^^^^^^^ no UNIQUE
```

Consequence, reproduced:

```
Room.objects.create(room_number='A-101', ...)   → ACCEPTED. count == 2
GET /hostels/ZZ-DUP/ with two rows              → 500
  MultipleObjectsReturned: get() returned more than one Room -- it returned 2!
    portal/views.py:206  room = get_object_or_404(Room..., room_number=room_number)
```

`detail()` and `booking()` both look rooms up **by `room_number`, not by pk**, and both use
`.get()`. So one duplicate row takes down that room's page for everyone — a 500, not a 404.

How duplicates appear: warden adds a room in Django admin (admin form validates uniqueness
against the *model*, but bulk import, a hand-written script, or a second `createsuperuser`-era
seed won't); or two concurrent `seed_rooms` runs, which do `get_or_create(room_number=...)`
— a TOCTOU race that creates two rows without a DB constraint to catch it.

`matric_number` and `staff_id` are declared `unique=True` in `0001_initial`, so those two
*are* enforced. Only `room_number` drifted.

**Fix.** Generate and commit the missing migration, dedupe first so it can apply on the
live DB:

```bash
python manage.py makemigrations portal        # writes 0004_alter_room_room_number.py
```

Before migrating on a deployed database, find and resolve any existing duplicates:

```sql
SELECT room_number, COUNT(*) FROM portal_room GROUP BY room_number HAVING COUNT(*) > 1;
```

---

## P0-2 · A bad `level` value creates an account that can never use the app, permanently

`register_view` has no `transaction.atomic()`. It creates the `User` first, then the
`StudentProfile`. Anything that throws in between leaves a half-made account, and the two
uniqueness guards then lock the person out of trying again.

Reproduced on a clean DB:

```
POST /register/submit/  reg_matric=CS/22/0002  reg_level=9999x
  → ValueError: Field 'level' expected a number but got '9999x'  (500, views.py:93)
  → User row exists:        True
  → StudentProfile exists:  False
  → can log in:             True
  → GET /dashboard/:        404     # get_object_or_404(StudentProfile, user=...)
  → can re-register:        blocked ("An account with that matric number already exists.")
```

The user is *authenticated, active, and unusable forever*. They cannot re-register (matric
taken), cannot reach the dashboard (404), and there is no password-reset flow to recover through.

**Reachability, stated honestly:** `reg_level` is a `<select>` with literal options
(`100`…`500`, plus `<option value="">Select</option>`), and the empty option is caught by the
`if not all([...])` check. So **normal browser use cannot trigger this** — it needs a crafted POST,
an extension, a scraper, or any future client that isn't the template. The fix is still
required because the write is unguarded, not because it is easy to hit by accident.

Two more fields on the same missing-validation path:

- `reg_matric` — model max 30. An 80-char value **stores fine on SQLite** (verified:
  `len == 80`, no truncation) and raises `DataError` → 500 on **Postgres**, which is what
  `build.sh`/Render deploy uses. Same input, two different behaviours per backend.
- `session` on the apply form is free text into `CharField(max_length=20)`, though the form
  presents radio buttons (`2026/2027`, `2027/2028`). A POST with `session=''` or a 40-char
  value is stored as-is (Postgres → 500 again).

**Fix** — validate on a whitelist, and make the write atomic so it cannot half-succeed:

```python
from django.db import transaction

LEVELS = {100, 200, 300, 400, 500}          # mirrors the <select> options

if level.isdigit() and int(level) in LEVELS:
    level = int(level)
else:
    messages.error(request, "Select a valid level.")
    return render(request, "hostel/login.html", {"register_error": True})

with transaction.atomic():
    user = User.objects.create_user(username=matric, email=email, password=password)
    user.first_name, user.last_name = ...
    user.save()
    StudentProfile.objects.create(user=user, matric_number=matric, department="", level=level)
```

The durable fix is Django forms: `portal/forms.py` does not exist, and there is **zero**
`django.forms` usage in `views.py` — every rule is hand-rolled, so every one of the above has
to be remembered by hand. A `ModelForm` with `fields = [...]` would enforce max-length,
choices and integer coercion for free, and `StudentProfile.full_clean()` would cover the rest.

---

## P1 · `seed_rooms` overwrites warden edits on every redeploy

`build.sh` calls `python manage.py seed_rooms` on **every build**, and the command does not
just insert — it force-overwrites existing rows:

```python
hostel, created = Hostel.objects.get_or_create(name=name, defaults={...})
if not created:
    hostel.room_type = room_type
    hostel.capacity_per_room = capacity
    hostel.price_per_session = price
    hostel.save()          # ← unconditional overwrite of live config
```

Reproduced: a warden sets Block A to `price=99999, capacity=6`, then one redeploy runs:

```
before seed: price=99999 capacity=6   →   after one build.sh: price=60000 capacity=4
```

Their edits are gone, silently, on the next deploy — as is anything renamed. `Room` rows are
touched the same way (`room.hostel = hostel; room.floor = floor`) though `occupied_beds`
survives, which is the only reason bookings are not destroyed.

**Fix:** make seeding insert-only, or gate it:

```python
if created:                      # only ever set config on first insert
    Hostel.objects.filter(pk=hostel.pk).update(
        room_type=room_type, capacity_per_room=capacity, price_per_session=price)
```

or drop `seed_rooms` from `build.sh` and document it as a dev/demo command.

---

## P1 · No password reset, and no way to edit a profile

```
$ grep -rniE "password_reset|PasswordChange|set_password" portal/ myproject/urls.py
  (no matches)
```

Combined with P0-2's "can't re-register" lockout and matric-based login, a forgotten password
has **no self-service path at all** — the only recovery is someone with Django-admin access
editing the `auth_user` row by hand. Worth calling out because "no queues at the porter's
lodge" is the pitch; a support-desk bypass contradicts it.

---

## P2 · Smaller things, each verified

- **The landing page's vacancy numbers are invented.** `landing()` passes no context, and
  `landing.html:416-420` hardcodes `<div class="stat">46 vacant</div>` per block — these read
  as live inventory, and 46/44/47/48/45 do not correspond to anything in the DB (all 1,020
  rooms are vacant after seeding). The hero's `1020+ / 20 / 24h` (`landing.html:403-405`) are
  likewise literals. Fix: feed the same annotation the admin dashboard uses.
- **`request_access` does not check email uniqueness** (the student path does). Two staff
  requests can share an email, and since `admin_login_submit` resolves
  `User.objects.filter(email=...).first()`, the second person can be silently authenticated
  against the *first* account's username. Add the `User.objects.filter(email__iexact=email).exists()`
  guard the student path already has, and use `.get()` semantics rather than `.first()`.
- **Admin dashboard is one huge page.** `Room.objects.all()` (1,020 rows) into a loop, plus a
  per-student `Booking...first()` — a textbook N+1 that would be fixed by
  `StudentProfile.objects.prefetch_related('user__bookings')` or an annotation. `admin.html` is
  44 KB of template; the rendered page was ~685 KB in my run.
- **No `Forms`, no `serializers`, no API.** Nothing here is consumable by a mobile client, so
  the "students check status on their phone" story depends on the responsive HTML only.
- **`SECURE_*` coverage is thin.** `check --deploy` reports `security.W004` (no
  `SECURE_HSTS_SECONDS`). Also unset: `SECURE_CONTENT_TYPE_NOSNIFF`, `SECURE_REFERRER_POLICY`,
  `X_FRAME_OPTIONS`, `SESSION_COOKIE_HTTPONLY` (Django defaults are safe for the last three, so
  this is hardening, not a hole — but HSTS is worth adding given `SECURE_SSL_REDIRECT` is on).
- **`LOGGING` is not configured**, so under gunicorn application errors only reach stderr/access
  logs; there is no way to see a 500 traceback like the two above after deploy.
- **Images are `picsum.photos` seeds** (`landing.html`, `details.html`, `listing.html`, plus a
  `` `https://picsum.photos/seed/${seed}/800/600` `` template literal in JS). Fine for a demo,
  but it means every room card shows a random photo of a mountain or a road, and the site is a
  runtime dependency on a third-party host.
- **The favicon is a `.jpg`** declared as `type="image/png"`, hosted on Cloudinary — mismatched
  MIME in all 8 templates.
- **`db.sqlite3` and `staticfiles/` are ignored, but the local `myproject/.env` pattern is not
  matched by the root `.gitignore`** — the root one is a stock Python template that does not
  list `.env`; `myproject/.gitignore` does. Only `myproject/` is protected, so a second `.env`
  added at the repo root would be committable.

---

## Not issues — checked and cleared

So this list is not just alarming:

- **No XSS.** No `|safe` / `autoescape off` / `mark_safe`. `room_number` is URL-derived and
  `get_object_or_404`-constrained, so template output is escaped and bounded.
- **Occupancy percentages cannot fail to sum to 100** — `occupied_pct = 100 - vacant_pct` by
  construction, so rounding cannot desynchronise them (checked at cap=1000/2295/3).
- **The admin dashboard does offer Reject** — as `<form method="post">` icon buttons hitting
  `booking_decision`/`staff_request_decision` (`admin.html:345-357`), both `csrf_token`-ed.
  (My first-pass grep for a literal `rejected/` string missed it; the `approved/` count of 0 was
  the same bad pattern. There is no missing decision path.)
- **CSRF is consistently applied** — all 9 state-changing forms carry `{% csrf_token %}`;
  the GET-only pages legitimately have none.
- **`is_staff` is never settable from a public form** — `register_view` and `request_access`
  both create plain users, and only `staff_request_decision` grants it.
