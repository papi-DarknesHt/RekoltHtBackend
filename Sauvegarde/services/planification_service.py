# ── IMPORTS ───────────────────────────────────────────────────────────────────
from datetime import timedelta

from django.db import DatabaseError, transaction
from django.utils import timezone

from ..models import ConfigurationSauvegarde, obtenir_configuration
from .export_service import executer_sauvegarde

# fréquence d'appel de verifier_et_executer_sauvegarde_planifiee (voir
# Sauvegarde/apps.py) — la granularité de l'heure de déclenchement configurée
# n'a donc de sens qu'à ce nombre de minutes près. Abaissé de 15 à 2 (retard
# constaté en conditions réelles : jusqu'à 15 min entre l'heure configurée et
# le prochain passage du job, perçu à tort comme "ça ne marche pas" sur un
# test de quelques minutes).
INTERVALLE_VERIFICATION_MINUTES = 2

# au-delà de ce délai, un verrou marqué "en cours" (voir
# verification_planifiee_en_cours_depuis, Sauvegarde/models.py) est considéré
# abandonné (process tué en cours d'exécution : redéploiement, autoreload en
# dev, crash...) et n'empêche plus une nouvelle tentative — largement au-delà
# de la durée d'un envoi Google Drive normal, sans bloquer indéfiniment
DELAI_EXPIRATION_VERROU_SECONDES = 30 * 60


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

    Le projet tourne via uvicorn (voir README.md/STRUCTURE.md), pas
    `manage.py runserver` — la garde dans apps.py (RUN_MAIN) ne protège que
    contre le double appel de ready() par l'auto-reloader de runserver, elle
    ne dit rien du nombre de process/workers uvicorn réellement en cours. Si
    plusieurs process tournent, CHACUN démarre son propre scheduler
    APScheduler en mémoire (voir apps.py) — sans verrou, chacun peut décider
    indépendamment que la sauvegarde planifiée est due et la déclencher,
    donnant l'apparence d'un "double déclenchement" pour la même occurrence
    (constaté en conditions réelles : sauvegarde comptée deux fois dans
    l'historique ET sur Google Drive). Le verrou ci-dessous (voir
    verification_planifiee_en_cours_depuis, Sauvegarde/models.py) rend cette
    fonction sûre quel que soit le nombre de process qui l'appellent en
    parallèle : seul celui qui pose le verrou en premier exécute la
    sauvegarde, les autres se retirent immédiatement.
    """
    try:
        with transaction.atomic():
            # select_for_update(nowait=True) : verrou ligne PostgreSQL — si un
            # AUTRE process tient déjà ce verrou (vérification concurrente en
            # cours), lève DatabaseError immédiatement plutôt que d'attendre
            # (pas de file d'attente de process qui se déclenchent en cascade)
            config = ConfigurationSauvegarde.objects.select_for_update(nowait=True).get(pk=obtenir_configuration().pk)

            if config.verification_planifiee_en_cours_depuis is not None:
                age = timezone.now() - config.verification_planifiee_en_cours_depuis
                if age < timedelta(seconds=DELAI_EXPIRATION_VERROU_SECONDES):
                    return   # une autre exécution est déjà en cours (ou verrou récent)
                # sinon : verrou abandonné (process tué en cours de route), on continue

            if not config.active:
                return

            maintenant = timezone.localtime()
            if _periode_courante_couverte(config, maintenant):
                return
            if not _heure_declenchement_depassee(config, maintenant):
                return

            type_sauvegarde = config.type_sauvegarde
            destination     = config.destination

            # posé AVANT de sortir de la transaction (donc avant de libérer le
            # verrou de ligne) : un autre process qui tenterait d'acquérir le
            # verrou juste après verra immédiatement cette valeur récente et
            # se retirera lui aussi, même une fois la ligne déverrouillée
            ConfigurationSauvegarde.objects.filter(pk=config.pk).update(
                verification_planifiee_en_cours_depuis=timezone.now()
            )
    except DatabaseError:
        return   # verrou déjà tenu par un autre process — rien à faire ici

    try:
        # declenche_par=None : distingue une exécution automatique d'une
        # exécution manuelle dans l'historique (voir _serialiseHistorique,
        # Sauvegarde/views.py)
        executer_sauvegarde(
            type_sauvegarde=type_sauvegarde,
            destination=destination,
            declenche_par=None,
        )
    finally:
        # libère le verrou que la sauvegarde ait réussi ou échoué — sinon un
        # échec ponctuel (réseau, quota Drive...) bloquerait tout nouveau
        # déclenchement automatique jusqu'à DELAI_EXPIRATION_VERROU_SECONDES
        ConfigurationSauvegarde.objects.filter(pk=1).update(verification_planifiee_en_cours_depuis=None)
