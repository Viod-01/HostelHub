from django.apps import AppConfig


class PortalConfig(AppConfig):
    name = 'portal'

    def ready(self):
        # future-proofing for migration 0009: re-enable RLS on any table
        # that appeared after it ran (see portal/rls.py)
        from django.db.models.signals import post_migrate
        from portal import rls
        post_migrate.connect(rls.enable_rls)
