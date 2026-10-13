from django.apps import AppConfig


class KeeperConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'keeper'
    verbose_name = 'Keeper'

    def ready(self):
        # Enregistrement des signals (décommente au fur et à mesure)
        # import keeper.signals
        pass