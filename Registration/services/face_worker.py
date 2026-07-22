"""
Script autonome, exécuté par un interpréteur Python DÉDIÉ à DeepFace (voir
settings.FACE_VENV_PYTHON) — jamais importé directement par Django. N'importe
volontairement rien de Django ni du reste du projet : c'est le seul point de
contact entre le processus Django (paddleocr/paddlepaddle, protobuf<=3.20.2)
et l'environnement DeepFace/TensorFlow (protobuf>=6.31.1), qui ne peuvent pas
cohabiter dans le même processus (vérifié : `import paddle` échoue dès que
deepface/tensorflow sont installés dans le même environnement).

Appelé via subprocess par Registration/services/face_service.py.

Usage : python face_worker.py <chemin_selfie> <chemin_document>
Sortie (stdout, une seule ligne JSON) :
  {"correspond": bool, "score_confiance": float, "erreur": str|None}
"""
import json
import sys


def main():
    chemin_selfie, chemin_document = sys.argv[1], sys.argv[2]

    from deepface import DeepFace

    try:
        resultat = DeepFace.verify(
            img1_path=chemin_selfie,
            img2_path=chemin_document,
            # Facenet confondait authentique et usurpation sur des paires
            # selfie/permis reelles (la photo de piece, une fois recadree sur
            # le visage, ne fait que ~70-140px de large - vignette imprimee
            # photographiee au telephone) : la distance d'une paire authentique
            # depassait parfois celle d'une paire usurpee. ArcFace separe
            # correctement les deux sur le meme echantillon - verifie en
            # conditions reelles avant ce changement.
            model_name="ArcFace",
            # le detector par defaut ("opencv") est trop faible et rate des
            # visages pourtant nets/bien eclaires (constate en conditions
            # reelles) ; "retinaface" est nettement plus fiable, verifie sur
            # de vraies photos (selfie + page de passeport)
            detector_backend="retinaface",
            enforce_detection=True,
        )
    except ValueError as e:
        # DeepFace lève ValueError si aucun visage n'est détecté dans une des images
        print(json.dumps({'correspond': False, 'score_confiance': 0.0, 'erreur': str(e)}))
        return

    distance  = resultat['distance']
    seuil     = resultat['threshold']
    confiance = max(0.0, 1 - (distance / seuil)) if seuil else 0.0

    # seuil metier assoupli a 35% de confiance (score normalise, independant
    # du modele) plutot que le "verified" natif de DeepFace, trop strict en
    # conditions reelles sur des paires selfie/piece d'identite (photo de
    # piece souvent degradee : reflet, faible resolution, angle) — compense
    # par le cross-check nom/prenom desormais actif meme pour le permis (voir
    # _lancer_pipeline_ocr, Registration/views.py)
    SEUIL_CONFIANCE_VISAGE = 0.35

    print(json.dumps({
        'correspond':      confiance >= SEUIL_CONFIANCE_VISAGE,
        'score_confiance': round(confiance, 4),
        'erreur':          None,
    }))


if __name__ == '__main__':
    main()
