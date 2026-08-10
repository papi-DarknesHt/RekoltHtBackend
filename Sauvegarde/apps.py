import sys

from django.apps import AppConfig


class SauvegardeConfig(AppConfig):
    name = 'Sauvegarde'

    def ready(self):
        """
        Démarre le scheduler des sauvegardes planifiées (APScheduler, voir
        Sauvegarde/services/planification_service.py) — un seul process, pas
        de dépendance d'infrastructure supplémentaire (pas de Celery/Redis).

        ready() s'exécute pour CHAQUE commande manage.py (migrate,
        makemigrations, shell, test...), pas seulement runserver — on ne veut
        démarrer le scheduler que pour un vrai process serveur :
        - sous `manage.py runserver`, l'auto-reloader exécute ready() deux
          fois (le process de surveillance, puis le process rechargé qui sert
          réellement les requêtes) ; seul ce second process porte la variable
          d'environnement RUN_MAIN=true (voir doc Django sur StatReloader) ;
        - en production (gunicorn/uwsgi, pas de manage.py runserver), il n'y
          a pas d'auto-reloader : on démarre alors toujours le scheduler.
        """
        via_manage_py = len(sys.argv) > 0 and 'manage.py' in sys.argv[0]
        if via_manage_py:
            est_runserver = len(sys.argv) > 1 and sys.argv[1] == 'runserver'
            if not est_runserver:
                return   # migrate/makemigrations/shell/test/... : jamais de scheduler
            import os
            if os.environ.get('RUN_MAIN') != 'true':
                return   # process de surveillance du reloader, pas celui qui sert les requêtes

        import datetime
        from apscheduler.schedulers.background import BackgroundScheduler
        from .services.planification_service import (
            verifier_et_executer_sauvegarde_planifiee, INTERVALLE_VERIFICATION_MINUTES,
        )

        scheduler = BackgroundScheduler()
        scheduler.add_job(
            verifier_et_executer_sauvegarde_planifiee,
            'interval', minutes=INTERVALLE_VERIFICATION_MINUTES,
            id='verifier_sauvegarde_planifiee', replace_existing=True,
            # sans next_run_time explicite, un trigger 'interval' n'exécute sa
            # PREMIÈRE vérification qu'après le premier intervalle complet
            # (comportement par défaut d'APScheduler) — donc jusqu'à 15 min
            # après CHAQUE démarrage du process. En dev, `uvicorn --reload`
            # (voir README.md > Lancer le projet) redémarre le worker à
            # chaque sauvegarde de fichier : si ça arrive plus souvent que
            # toutes les 15 min (quasi systématique en session de dev active),
            # la planification automatique n'obtient jamais sa toute première
            # chance de s'exécuter — constaté en conditions réelles, c'est ce
            # qui donnait l'impression qu'elle "ne fait même pas l'action".
            # En production (pas de --reload), ça garantit aussi qu'une
            # sauvegarde due n'attend pas jusqu'à 15 min après un redéploiement.
            # +10s (pas immédiat) : laisse le registre des apps Django finir
            # de démarrer avant la première requête ORM du job, sur le thread
            # séparé du scheduler — sinon RuntimeWarning "Accessing the
            # database during app initialization" constaté en conditions réelles.
            next_run_time=datetime.datetime.now() + datetime.timedelta(seconds=10),
        )
        scheduler.start()
