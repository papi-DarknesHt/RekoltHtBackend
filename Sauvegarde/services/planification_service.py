# ── IMPORTS ───────────────────────────────────────────────────────────────────
from django.utils import timezone

from ..models import obtenir_configuration
from .export_service import executer_sauvegarde

# fréquence d'appel de verifier_et_executer_sauvegarde_planifiee (voir
# Sauvegarde/apps.py) — la granularité de l'heure de déclenchement configurée
# n'a donc de sens qu'à 15 minutes près
INTERVALLE_VERIFICATION_MINUTES = 15


def _periode_courante_couverte(config, maintenant):
    """
    True si `derniere_execution_reussie` tombe déjà dans la période actuelle
    (jour/semaine/mois selon `frequence`) — évite de relancer une sauvegarde
    déjà faite pour cette période si le job de vérification tourne plusieurs
    fois après l'heure de déclenchement (toutes les 15 min, voir apps.py).
    """
    derniere = config.derniere_execution_reussie
    if not derniere:
        return False

    derniere_locale = timezone.localtime(derniere)

    if config.frequence == 'quotidienne':
        return derniere_locale.date() == maintenant.date()
    if config.frequence == 'hebdomadaire':
        return derniere_locale.isocalendar()[:2] == maintenant.isocalendar()[:2]
    if config.frequence == 'mensuelle':
        return (derniere_locale.year, derniere_locale.month) == (maintenant.year, maintenant.month)
    return False


def _heure_declenchement_depassee(config, maintenant):
    """True si l'heure configurée est dépassée pour aujourd'hui ET (pour
    hebdomadaire/mensuelle) que le jour configuré correspond au jour courant."""
    if maintenant.time() < config.heure_declenchement:
        return False
    if config.frequence == 'hebdomadaire':
        return maintenant.weekday() == config.jour_semaine
    if config.frequence == 'mensuelle':
        return maintenant.day == config.jour_mois
    return True   # quotidienne : seule l'heure compte


def verifier_et_executer_sauvegarde_planifiee():
    """
    Point d'entrée appelé périodiquement par le scheduler (voir Sauvegarde/
    apps.py) : déclenche une sauvegarde automatique si la planification est
    active, que l'heure/jour configurés sont dépassés, et qu'aucune exécution
    réussie ne couvre déjà la période courante.

    Limite assumée : ce mécanisme suppose un seul process serveur (cohérent
    avec le choix d'APScheduler plutôt que Celery, voir apps.py) — avec
    plusieurs workers en production, il faudrait migrer vers un vrai
    scheduler externe (Celery beat, cron système invoquant `manage.py
    executer_sauvegarde`, ...).
    """
    config = obtenir_configuration()
    if not config.active:
        return

    maintenant = timezone.localtime()
    if _periode_courante_couverte(config, maintenant):
        return
    if not _heure_declenchement_depassee(config, maintenant):
        return

    # declenche_par=None : distingue une exécution automatique d'une
    # exécution manuelle dans l'historique (voir _serialiseHistorique,
    # Sauvegarde/views.py)
    executer_sauvegarde(
        type_sauvegarde=config.type_sauvegarde,
        destination=config.destination,
        declenche_par=None,
    )
