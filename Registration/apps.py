from django.apps import AppConfig
from django.db.models.signals import post_migrate


class RegistrationConfig(AppConfig):
    name = 'Registration'

    def ready(self):
        import Registration.signals
        from .signals import creer_compte_proprietaire_si_base_vide
        # post_migrate (pas ready() directement) : garantit que les tables
        # existent déjà — voir la docstring de creer_compte_proprietaire_si_base_vide
        post_migrate.connect(creer_compte_proprietaire_si_base_vide, sender=self)
