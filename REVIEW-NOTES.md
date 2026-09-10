# HostelHub — review notes & fixes

Everything below was **verified by running the project**, not read off the source.
Setup used: Python 3.13, `pip install -r requirements.txt` (Django 6.1), SQLite,
`migrate` + `seed_rooms`, then the flows were driven over HTTP with a real client
(CSRF included) and checked against the database.

```
manage.py check          → System check identified no issues (0 silenced)
migrate                  → 3 portal migrations applied OK
seed_rooms               → 20 blocks × 51 rooms = 1,020 rooms, all vacant
GET / /hostels/ /hostels/A-101/ /login/ /admin-login/     → 200
GET /dashboard/ /hostels/A-101/apply/ /dashboard/admin/   → 302 (auth required, correct)
```

---

## P0 — fix before the repo is public-facing

### 1. Hardcoded `SECRET_KEY`

`myproject/settings.py:27` ships a real key as a literal fallback. It is already in
git history, so treat it as burned — anyone who cloned the repo can forge signed
cookies and session tokens.

```python
# now
SECRET_KEY = 'django-insecure-)5&0qem19#nuk*-abfx9&k%r^94mvfm3r-qb--n+pw_)o5kff)'

# instead — mirror what DEBUG/ALLOWED_HOSTS already do
SECRET_KEY = config('SECRET_KEY')
```

`DEBUG` and `ALLOWED_HOSTS` already use `python-decouple`; `SECRET_KEY` is the one
that was left behind while the rest was moved to env vars.

Then rotate it on the deployed host, and assume the old key leaked.

### 2. Static files 404 in production

```
/static/admin/css/base.css  →  404     (DEBUG=False, after collectstatic ran)
collectstatic               →  130 static files copied to 'staticfiles'
whitenoise in MIDDLEWARE    →  False
STORAGES["staticfiles"]     →  django.contrib.staticfiles.storage.StaticFilesStorage
```

Two separate causes:

- `whitenoise` is in `requirements.txt` but `whitenoise.middleware.WhiteNoiseMiddleware`
  was never added to `MIDDLEWARE`. Nothing is serving `/static/`.
- `STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'`
  **is a no-op on Django 6.1** — that setting was deprecated in 4.2 and *removed in
  5.1* (see the [5.1 release notes](https://docs.djangoproject.com/en/6.0/releases/5.1/),
  "Features removed in 5.1"). Confirmed on the installed version:
  `hasattr(django.conf.global_settings, 'STATICFILES_STORAGE')` → `False`, and
  `settings.STORAGES['staticfiles']['BACKEND']` is still the plain default
  `StaticFilesStorage`, i.e. Django ignored the line entirely rather than erroring.
  `manage.py check` doesn't warn about an unrecognized setting, which is why it looks fine.

```python
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',   # add, right after Security
    ...
]

STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}
```

Low blast radius today — all eight portal pages inline their CSS — which is exactly
why it hasn't been noticed. It breaks Django's own `/admin/`.

---

## P1 — correctness

### 0. The listing page ships 1 MB of HTML to display 100 cards

```
GET /hostels/ (guest, DEBUG=True)  → 200, ~1,017,000 bytes of HTML
rooms sent into the template        → 1,019 of 1,020 (only fully-booked rooms excluded)
cards actually visible on screen    → 100
```

The view has no pagination or limit; `listing.html:264` renders `{% for room in rooms %}`
over everything, and the client script at `listing.html:329-336` then keeps only 5 rooms
per block and sets `display:none` on the rest. So the "100 rooms shown" counter is a
deliberate 20-blocks × 5 preview — but it is achieved by downloading and laying out all
1,019 cards first, plus 1,019 `select_related("hostel")` rows.

Both the count and the preview are display logic; neither needs 1 MB of markup. Move the
limit to the query (`rooms[:5]` per block, or real pagination) and let the block filter
re-query the server instead of filtering hidden DOM. This is the cheapest big win in the
project: a hostel with 5,000 rooms would ship a 5 MB page.

### 3. Overbooking race on approval

`views.booking_decision` does a read-modify-write with no lock:

```python
if booking_obj.room.is_full:            # read
    ...
else:
    booking_obj.room.occupied_beds += 1  # modify
    booking_obj.room.save()              # write
```

Two wardens approving into the last bed at the same moment both pass `is_full`,
and the room ends up over capacity. Fix:

```python
from django.db import transaction

@transaction.atomic
def booking_decision(request, booking_id, decision):
    ...
    room = Room.objects.select_for_update().get(pk=booking_obj.room_id)
    if room.vacant_beds <= 0:
        ...
    else:
        room.occupied_beds = F('occupied_beds') + 1   # or += 1 after the lock
        room.save(update_fields=['occupied_beds'])
```

Same class of bug in the reverse direction: `listings` and `detail` read
`occupied_beds` without a lock, so a student can reach the apply form for a room
that filled a moment earlier — the `room.is_full` check in `booking()` covers that
one already, so it's just a UX seam, not a hole.

### 4. No DB constraint on "one active booking per student"

The rule lives only in Python (`status__in=["pending", "approved"]`), so it isn't
enforced against concurrent requests or direct writes. A partial unique index makes
it impossible to violate:

```python
class Meta:
    constraints = [
        models.UniqueConstraint(
            fields=['student'],
            condition=models.Q(status__in=['pending', 'approved']),
            name='one_active_booking_per_student',
        ),
    ]
```

### 5. Landing page advertises numbers that aren't real

`landing()` renders a template and performs no query — the view says so in its own
docstring. "1020+ Rooms Listed", "20 Hostel Blocks", "24h Avg. Approval" and the
"Block A · 48 vacant" chips are all hardcoded HTML (`landing.html:403-405`). The
admin dashboard computes the same figures from the DB and agrees on 20 blocks —
but if a block is added or a bed taken, the landing page will lie about it.

Either wire it to the same query, or label them as illustrative. The vacancy chips
are the ones most likely to mislead, since they look like live inventory.

### 6. Two nav bugs on the portal chrome

**a) `Complaints` links to a feature that doesn't exist.** `landing.html:385` and
`listing.html:225` both render `<a href="#">Complaints</a>`. There is no complaint model,
view, or URL anywhere in `portal/`:

```
grep -riE "complaint|payment|receipt|notif" views.py models.py urls.py admin.py
→ 0 matches
```

A dead top-nav item on the landing page is the kind of thing a reviewer clicks first.

**b) "Log In" renders twice for guests on the listing page.** `listing.html:219-243` has two
*independent* `{% if user.is_authenticated %}` blocks — one inside `.nav-links`, one for the
right-hand CTA group — instead of one `if/else`. Verified against the rendered HTML:

```
GET /hostels/  as guest        → 2 "Log In" buttons
GET /hostels/  as student      → correct: Home · Hostels · Complaints | Dashboard · Log Out
```

So only the guest view is wrong, and the listing screenshot in the README shows it.
Separately, `landing.html:387-390` hardcodes `Staff Login` + `Log In` with no auth check
at all, so a signed-in student still sees "Log In" on the landing page.

```html
{# listing.html — collapse the two blocks #}
<div class="nav-links">
  <a href="{% url 'dashboard' %}">Home</a>
  <a href="{% url 'listing' %}">Hostels</a>
</div>
{% if user.is_authenticated %}
  <div class="nav-cta-group">…Dashboard / Log Out…</div>
{% else %}
  <a href="{% url 'login_register' %}" class="nav-cta">Log In</a>
{% endif %}
```

---

## P2 — polish

- **`build.sh` can't create a superuser.** `python manage.py createsuperuser --noinput`
  needs `DJANGO_SUPERUSER_USERNAME`/`_EMAIL`/`_PASSWORD` env vars; without them it
  errors, and `|| true` swallows it silently. So a fresh deploy has *no* staff
  account, which means nobody can approve a "Request Access" submission. If the
  `|| true` is deliberate (idempotent redeploys), the vars still need setting on
  first boot.
- **`booking()` ignores `session` validation.** `session = request.POST.get("session", "").strip()`
  is stored as-is; an empty or `"garbage"` value passes. The field is
  `CharField(max_length=20)` with no validator.
- **No rejection reason field.** `Booking` records `decided_at` and `status` but no
  `rejection_reason` and no `decided_by`. A student told "rejected" has nothing to
  act on, and there's no audit trail of which warden decided.
- **`department` is always empty for students.** `register_view` creates
  `StudentProfile(department="", ...)`, so the admin "Students" table has a column
  that's blank for everyone. Either collect it at registration or drop it.
- **`admin_login_page` runs a full-table sum on every unauthenticated hit.** It
  loads all 1,020 rooms into Python and sums them in a loop — for a login page that
  renders before anyone has signed in. `Room.objects.aggregate(...)` with a
  `Sum(capacity)` annotation replaces it with one query. (Same pattern in
  `admin_dashboard`, which also loops `Booking.objects.filter(...)` once per student —
  ~N+1, 1,020 rooms and one query per registered student on one page.)
- **`tests.py` is the empty scaffold.** The lifecycles worth locking down, in order of
  value: approve-into-full-room, double-apply, pending-staff-cannot-log-in,
  inactive-user-rejected-by-authenticate. All four currently pass by hand.
- **Duplicated tab-switching and form styling** across the 8 templates. A
  `base.html` with `{% block %}` would collapse several hundred lines — though the
  "every page is self-contained" approach is defensible as a deliberate choice; if
  so, say so in a comment so it doesn't read as an oversight.

---

## What's genuinely good

Worth keeping, because these are the decisions that make it look like more than a
coursework project:

- **Locking out pending staff via `is_active=False` instead of a custom flag** —
  `authenticate()` already refuses inactive users, so the guard can't be forgotten
  in a view. That's the right instinct.
- **One generic error message** on the staff login for "no such account" / "wrong
  password" / "not staff" — actively resists account enumeration.
- **Vacancy filtered at the DB level** (`occupied_beds__lt=F('hostel__capacity_per_room')`)
  instead of fetching everything and trimming in Python.
- **`@staff_member_required(login_url='admin_login')`** pointing at the custom staff
  page rather than Django's default `/admin/login/`, which matches the rest of the design.
- **`seed_rooms` is idempotent** — `get_or_create` plus `update_fields`, so re-running
  it on a redeploy restates blocks without destroying bookings.
- **`never_cache` on the auth and dashboard views** — the right places, so a signed-out
  browser doesn't serve a stale dashboard from history.
- **Derived capacity as properties** (`vacant_beds`, `is_full`) rather than stored
  duplicated columns that can drift.


---

*`REVIEW-NOTES.md` is a private review document, not meant to be published. Delete it (and the `docs/img` extras) before you push, if you prefer to keep it to yourself:*

```
rm REVIEW-NOTES.md
git add -A && git commit -m "Add README, LICENSE, screenshots"
```
