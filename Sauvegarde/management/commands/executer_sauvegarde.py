from django.core.management.base import BaseCommand

from Sauvegarde.services.export_service import executer_sauvegarde


class Command(BaseCommand):
    help = (
        "Déclenche une sauvegarde manuellement en ligne de commande — utile en "
        "secours (ex. juste avant une opération de maintenance risquée, ou si "
        "le process Django/le scheduler intégré est arrêté). Réutilise le même "
        "service que le déclenchement manuel depuis le tableau de bord admin "
        "(voir Sauvegarde/services/export_service.py)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--type', dest='type_sauvegarde', default='complete',
            choices=['complete', 'incrementale'],
        )
        parser.add_argument(
            '--destination', default='locale',
            choices=['locale', 'google_drive'],
        )

    def handle(self, *args, **options):
        # declenche_par=None : cette commande n'est pas exécutée dans le
        # contexte d'un compte admin authentifié — même convention que les
        # exécutions planifiées automatiques (voir planification_service.py)
        historique = executer_sauvegarde(
            type_sauvegarde=options['type_sauvegarde'],
            destination=options['destination'],
            declenche_par=None,
        )

        if historique.statut == 'succes':
            self.stdout.write(self.style.SUCCESS(
                f"Sauvegarde {historique.type_sauvegarde} vers {historique.destination} réussie — "
                f"{historique.nombre_enregistrements} enregistrement(s), {historique.taille_octets} octet(s), "
                f"id {historique.id}"
            ))
        else:
            self.stderr.write(self.style.ERROR(f"Échec de la sauvegarde : {historique.message_erreur}"))
