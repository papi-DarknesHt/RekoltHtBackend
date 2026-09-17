"""
Vérification faciale (étape 04, individuel uniquement) : compare le selfie
capturé côté client (liveness front-end) à la photo de la pièce d'identité
déjà téléversée.

IMPORTANT — architecture à deux environnements Python, découverte en testant
avant d'écrire ce module (pas supposée à l'avance) : DeepFace (via TensorFlow)
exige protobuf>=6.31.1, alors que paddleocr/paddlepaddle (Registration/
services/ocr_service.py, déjà utilisé par le même pipeline de vérification,
voir _lancer_pipeline_ocr) exige protobuf<=3.20.2. Les deux plantent si
installés dans le même environnement : `import paddle` échoue dès que
deepface/tensorflow sont présents (TypeError: Descriptors cannot be created
directly). DeepFace tourne donc dans un environnement Python SÉPARÉ (voir
settings.FACE_VENV_PYTHON, Registration/services/face_worker.py), appelé ici
via subprocess plutôt qu'importé directement dans le processus Django.

Déploiement : ce second environnement (deepface, tensorflow, tf-keras — voir
requirements-face.txt) doit être créé séparément du venv principal du projet
(qui contient paddleocr/paddlepaddle) et son python renseigné dans
FACE_VENV_PYTHON (.env). Non inclus dans requirements.txt.
"""
import json
import os
import subprocess
from pathlib import Path

from django.conf import settings

# la décision passe/échoue s'appuie sur un seuil maison de 35% appliqué à
# score_confiance (resultat['correspond'], voir face_worker.py) plutôt que sur
# le "verified" natif de DeepFace, jugé trop strict en conditions réelles sur
# des paires selfie/pièce d'identité (photo de pièce recadrée, souvent basse
# résolution). score_confiance est aussi stocké sur la demande
# (DemandeVerification.score_correspondance_visage) à des fins d'audit/débogage.

# "retinaface" (voir face_worker.py) est un détecteur à base de réseau de
# neurones, nettement plus lent qu'"opencv" à froid dans un sous-processus
# neuf à chaque appel — 60s s'est révélé insuffisant en conditions réelles
TIMEOUT_SECONDES = 150

_WORKER = Path(__file__).with_name('face_worker.py')


class VerificationFacialeIndisponible(Exception):
    """
    L'environnement Python dédié à DeepFace n'a pas pu être exécuté (venv non
    configuré sur ce serveur, timeout, sortie invalide) — voir
    _lancer_verification_faciale (Registration/views.py), qui laisse alors la
    demande en 'en_attente_manuelle' plutôt que d'échouer à tort un
    utilisateur légitime à cause d'un problème d'infrastructure.
    """


def comparer_visages(chemin_selfie, chemin_document):
    """
    Compare deux images de visage (sous-processus dédié, voir docstring du
    module) et retourne :
      {'correspond': bool, 'score_confiance': float, 'erreur': str|None}

    Ne lève VerificationFacialeIndisponible que pour un problème
    d'infrastructure (venv absent, timeout, sortie invalide) — jamais pour un
    simple "aucun visage détecté", qui est un résultat normal
    (correspond=False, voir face_worker.py).
    """
    python_dedie = getattr(settings, 'FACE_VENV_PYTHON', None)
    if not python_dedie:
        raise VerificationFacialeIndisponible(
            "FACE_VENV_PYTHON n'est pas configuré (voir BackendRekoltHt/settings/base.py et .env)"
        )

    # DeepFace imprime des emojis dans ses logs (ex: "🔗 fichier téléchargé...") —
    # sur Windows, le print() du sous-processus utilise par défaut la page de code
    # de la console (cp1252), qui plante sur ces caractères (UnicodeEncodeError,
    # constaté en conditions réelles). PYTHONIOENCODING force l'E/S du sous-
    # processus en UTF-8 indépendamment de la console qui a lancé Django.
    environnement = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}

    try:
        resultat = subprocess.run(
            [python_dedie, str(_WORKER), chemin_selfie, chemin_document],
            capture_output=True, text=True, timeout=TIMEOUT_SECONDES,
            encoding='utf-8', errors='replace',
            env=environnement,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        raise VerificationFacialeIndisponible(f"Échec d'exécution du sous-processus DeepFace : {e}") from e

    if resultat.returncode != 0:
        raise VerificationFacialeIndisponible(
            f"Le sous-processus DeepFace a échoué (code {resultat.returncode}) : {resultat.stderr[-500:]}"
        )

    try:
        return json.loads(resultat.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as e:
        raise VerificationFacialeIndisponible(f"Sortie invalide du sous-processus DeepFace : {e}") from e
