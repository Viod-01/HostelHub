"""Keep every table in the public schema behind Row-Level Security.

Migration 0009 enabled RLS once, but it only covered tables that existed
at that moment — any table added by a LATER migration would be exposed
through Supabase's auto-generated API again. This post_migrate hook
re-runs the same enable-statement after every migrate, so new tables are
covered from the moment they are created. Deny-by-default for the API;
Django (table owner) is unaffected by RLS.
"""
from django.db import connection

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


def enable_rls(sender, **kwargs):
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute(ENABLE_RLS)
