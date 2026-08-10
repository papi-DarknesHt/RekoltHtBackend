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
    True si une exécution réussie couvre déjà l'occurrence de déclenchement
    courante — évite de relancer une sauvegarde déjà faite pour cette
    occurrence si le job de vérification tourne plusieurs fois après l'heure
    de déclenchement (toutes les 15 min, voir apps.py).

    Comparaison basée sur `maintenant` recalé à l'heure de déclenchement
    configurée (PAS seulement "même date/semaine/mois") : un déclenchement
    MANUEL plus tôt dans la journée, AVANT heure_declenchement, ne doit PAS
    empêcher le déclenchement automatique planifié plus tard le même jour.
    Bug constaté en conditions réelles : sauvegarde manuelle à 17h11,
    heure_declenchement planifiée à 17h17 — l'ancienne logique (uniquement
    `derniere_locale.date() == maintenant.date()`) considérait la journée
    déjà couverte et le déclenchement automatique n'était JAMAIS tenté, pas
    même comme échec dans l'historique (verifier_et_executer_sauvegarde_
    planifiee() retourne avant d'appeler executer_sauvegarde(), voir
    ci-dessous). Pour hebdomadaire/mensuelle, le jour exact est déjà filtré
    par _heure_declenchement_depassee : recaler seulement sur l'heure (en
    gardant la date du jour) suffit, une exécution d'une occurrence
    précédente (semaine/mois passés) est alors toujours antérieure au seuil
    du jour courant.
    """
    derniere = config.derniere_execution_reussie
    if not derniere:
        return False

    heure = config.heure_declenchement
    seuil = maintenant.replace(hour=heure.hour, minute=heure.minute, second=heure.second, microsecond=0)
    return timezone.localtime(derniere) >= seuil


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
