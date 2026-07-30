from django.apps import AppConfig


class MessagerieConfig(AppConfig):
    name = 'Messagerie'

    def ready(self):
        import Messagerie.signals
