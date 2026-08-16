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

        from apscheduler.schedulers.background import BackgroundScheduler
        from .services.planification_service import (
            verifier_et_executer_sauvegarde_planifiee, INTERVALLE_VERIFICATION_MINUTES,
        )

        scheduler = BackgroundScheduler()
        scheduler.add_job(
            verifier_et_executer_sauvegarde_planifiee,
            'interval', minutes=INTERVALLE_VERIFICATION_MINUTES,
            id='verifier_sauvegarde_planifiee', replace_existing=True,
        )
        scheduler.start()
