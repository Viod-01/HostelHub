"""
Supabase exposes every table in the public schema through its auto-
generated REST API (the anon key is designed to be public). With Row-Level
Security off and no policies, that API can read, edit, and delete rows in
every table — including Django's auth_user with password hashes and emails.

This migration flips RLS on for every table in the public schema and
creates NO policies, which is deny-by-default: the REST API gets nothing.

Django is unaffected. It connects as the role that owns these tables (the
migrations were run by it), and Postgres table owners bypass RLS unless
FORCE ROW LEVEL SECURITY is set — which this deliberately does not do.

On non-PostgreSQL databases (sqlite in dev/tests) this is a no-op.
"""
from django.db import migrations, connection


ENABLE_RLS = """
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN
        SELECT c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind = 'r'
          AND c.relrowsecurity = false
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', r.relname);
    END LOOP;
END $$;
"""


def enable(apps, schema_editor):
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(ENABLE_RLS)


def disable(apps, schema_editor):
    # reversing is a no-op on purpose: turning RLS back off re-opens the
    # API hole this migration closes. Migrating backwards past this point
    # is a deliberate act, not something we automate.
    return


class Migration(migrations.Migration):

    # Hard dependencies on every built-in app's table-creating migration,
    # so the DO-block below sees ALL tables on a fresh database. (Note:
    # Django documents run_after but its loader never reads it — only
    # run_before is implemented — so dependencies are the reliable tool.)
    dependencies = [
        ("portal", "0008_complaint_assigned_to_alter_complaint_status_and_more"),
        ("contenttypes", "0001_initial"),
        ("auth", "0001_initial"),
        ("admin", "0001_initial"),
        ("sessions", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(enable, disable, elidable=False),
    ]
