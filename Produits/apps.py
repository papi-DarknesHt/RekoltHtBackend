from django.apps import AppConfig


class ProduitsConfig(AppConfig):
    name = 'Produits'

    def ready(self):
        import Produits.signals
