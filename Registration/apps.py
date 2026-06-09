from django.apps import AppConfig


class RegistrationConfig(AppConfig):
    name = 'Registration'

    def ready(self):
        import Registration.signals 
