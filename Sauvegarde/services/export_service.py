# ── IMPORTS ───────────────────────────────────────────────────────────────────
import hashlib
import io
import json
import shutil
import tarfile
import time
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core import serializers
from django.core.management import call_command
from django.utils import timezone

from .chiffrement_service import chiffrer_archive

# marge de sécurité ajoutée à la taille strictement nécessaire lors d'une
# vérification d'espace disque (voir verifier_espace_disque ci-dessous) — le
# système d'exploitation et d'autres processus continuent de consommer de
# l'espace pendant l'écriture, une marge nulle exposerait à un disque plein
# EN COURS d'écriture (pire qu'un refus propre avant de commencer)
MARGE_SECURITE_ESPACE_DISQUE = 500 * 1024 * 1024   # 500 Mio


class EspaceDisqueInsuffisant(Exception):
    """Espace disque restant insuffisant pour l'opération demandée (sauvegarde
    locale, sauvegarde de sécurité avant restauration, ou restauration
    elle-même) — voir verifier_espace_disque ci-dessous. Levée AVANT toute
    écriture, jamais après coup : une IOError en cours d'écriture (disque
    plein à moitié du fichier) est bien plus dangereuse qu'un refus propre."""


def verifier_espace_disque(dossier, taille_necessaire_octets):
    """
    Vérifie qu'il reste au moins `taille_necessaire_octets` + une marge de
    sécurité (MARGE_SECURITE_ESPACE_DISQUE) sur le disque contenant `dossier`
    — lève EspaceDisqueInsuffisant sinon, avec un message explicite (espace
    disponible vs nécessaire) destiné à être affiché tel quel à l'admin (voir
    Sauvegarde/views.py::declencher/restaurer_confirmer).
    """
    dossier = Path(dossier)
    dossier.mkdir(parents=True, exist_ok=True)   # shutil.disk_usage exige un chemin existant
    usage = shutil.disk_usage(dossier)
    necessaire = taille_necessaire_octets + MARGE_SECURITE_ESPACE_DISQUE
    if usage.free < necessaire:
        raise EspaceDisqueInsuffisant(
            f"Espace disque insuffisant sur le serveur : "
            f"{usage.free / (1024 ** 3):.2f} Go disponibles, "
            f"{necessaire / (1024 ** 3):.2f} Go nécessaires "
            f"(dont {MARGE_SECURITE_ESPACE_DISQUE / (1024 ** 3):.2f} Go de marge de sécurité)."
        )

# modèles internes Django (régénérés automatiquement, pas des données
# applicatives) et modèles de cette app elle-même (pas de sauvegarde imbriquée
# de son propre historique) — exclus de toute sauvegarde. ATTENTION : le
# app_label doit avoir EXACTEMENT la casse de _meta.app_label (constaté :
# 'Sauvegarde' avec un S majuscule, pas 'sauvegarde' — un mismatch de casse
# ici ne lève aucune erreur, il désactive silencieusement l'exclusion, ce
# qui s'est produit jusqu'ici : chaque sauvegarde complète embarquait sans
# le vouloir son propre historique et sa propre configuration).
MODELES_EXCLUS = {
    ('contenttypes', 'contenttype'), ('sessions', 'session'),
    ('admin', 'logentry'), ('auth', 'permission'), ('auth', 'group'), ('auth', 'user'),
    ('Sauvegarde', 'configurationsauvegarde'), ('Sauvegarde', 'historiquesauvegarde'),
}

# champs candidats pour détecter "dernière modification" d'un modèle, dans
# l'ordre de préférence — un modèle sans aucun de ces champs est TOUJOURS
# inclus en entier lors d'une sauvegarde incrémentale (jamais de perte
# silencieuse, seulement un incrémental moins minimal pour ces modèles-là)
CHAMPS_MODIFICATION_CANDIDATS = ['date_maj', 'date_modification']


def _modeles_sauvegardables():
    """Tous les modèles concrets à inclure dans une sauvegarde, dans l'ordre de
    apps.get_models() (suit l'ordre d'INSTALLED_APPS — cohérent avec les
    dépendances FK du projet : Registration avant Produits/Messagerie)."""
    modeles = []
    for model in apps.get_models():
        if model._meta.proxy:   # Vendeur/Acheteur (Registration/models.py) — mêmes lignes que Utilisateur
            continue
        cle = (model._meta.app_label, model._meta.model_name)
        if cle in MODELES_EXCLUS:
            continue
        modeles.append(model)
    return modeles


def _champ_modification(model):
    noms_champs = {f.name for f in model._meta.get_fields()}
    for candidat in CHAMPS_MODIFICATION_CANDIDATS:
        if candidat in noms_champs:
            return candidat
    return None


def _dump_complet(modeles) -> bytes:
    """Sauvegarde complète : réutilise directement call_command('dumpdata', ...)
    — le sérialiseur standard de Django, le plus éprouvé, pour la totalité
    des enregistrements."""
    labels = [f"{m._meta.app_label}.{m._meta.model_name}" for m in modeles]
    tampon = io.StringIO()
    call_command('dumpdata', *labels, indent=None, stdout=tampon)
    return tampon.getvalue().encode('utf-8')


def _dump_incremental(modeles, depuis: datetime) -> bytes:
    """Sauvegarde incrémentale : sérialise, modèle par modèle, uniquement les
    enregistrements modifiés depuis `depuis` (détecté via _champ_modification) —
    les modèles sans champ de modification connu sont inclus en entier."""
    objets_json = []
    for model in modeles:
        champ = _champ_modification(model)
        queryset = model.objects.filter(**{f"{champ}__gt": depuis}) if champ else model.objects.all()
        if not queryset.exists():
            continue
        objets_json.extend(json.loads(serializers.serialize('json', queryset)))
    return json.dumps(objets_json).encode('utf-8')


def _fichiers_media_a_inclure(depuis: datetime | None):
    """Liste des fichiers media locaux à inclure — None si MEDIA_ROOT ne pointe
    pas vers un dossier local existant (ex. Cloudinary en production, voir
    settings/prod.py) : ces fichiers ont alors leur propre redondance côté
    Cloudinary, hors périmètre de cette sauvegarde."""
    media_root = getattr(settings, 'MEDIA_ROOT', None)
    if not media_root:
        return None
    media_root = Path(media_root)
    if not media_root.is_dir():
        return None

    fichiers = []
    for chemin in media_root.rglob('*'):
        if not chemin.is_file():
            continue
        if depuis is not None:
            mtime = datetime.fromtimestamp(chemin.stat().st_mtime, tz=dt_timezone.utc)
            if mtime <= depuis:
                continue
        fichiers.append(chemin)
    return fichiers


def construire_archive(type_sauvegarde: str, depuis: datetime | None = None):
    """
    Construit l'archive .rhtbackup (déjà chiffrée) pour cette exécution.
    Retourne (contenu_chiffre: bytes, nombre_enregistrements: int, manifest: dict).
    `depuis` n'a d'effet que si type_sauvegarde == 'incrementale' (ignoré sinon,
    et traité comme une sauvegarde complète si aucune exécution précédente
    n'existe encore — voir executer_sauvegarde ci-dessous).
    """
    modeles = _modeles_sauvegardables()

    if type_sauvegarde == 'incrementale' and depuis is not None:
        data_bytes = _dump_incremental(modeles, depuis)
        fichiers_media = _fichiers_media_a_inclure(depuis)
    else:
        type_sauvegarde = 'complete'
        data_bytes = _dump_complet(modeles)
        fichiers_media = _fichiers_media_a_inclure(None)

    nombre_enregistrements = len(json.loads(data_bytes))
    media_inclus = fichiers_media is not None

    manifest = {
        'version_format':          1,
        'date_generation':         timezone.now().isoformat(),
        'type_sauvegarde':         type_sauvegarde,
        'nombre_enregistrements':  nombre_enregistrements,
        'media_inclus':            media_inclus,
        'nombre_fichiers_media':   len(fichiers_media) if fichiers_media else 0,
    }

    tampon = io.BytesIO()
    with tarfile.open(fileobj=tampon, mode='w:gz') as tar:
        _ajouter_bytes_au_tar(tar, 'manifest.json', json.dumps(manifest, indent=2).encode('utf-8'))
        _ajouter_bytes_au_tar(tar, 'data.json', data_bytes)
        if fichiers_media:
            media_root = Path(settings.MEDIA_ROOT)
            for chemin in fichiers_media:
                tar.add(chemin, arcname=f"media/{chemin.relative_to(media_root)}")

    archive_brute = tampon.getvalue()
    contenu_chiffre = chiffrer_archive(archive_brute)
    return contenu_chiffre, nombre_enregistrements, manifest


def _ajouter_bytes_au_tar(tar: tarfile.TarFile, nom: str, contenu: bytes):
    info = tarfile.TarInfo(name=nom)
    info.size = len(contenu)
    tar.addfile(info, io.BytesIO(contenu))


def executer_sauvegarde(*, type_sauvegarde, destination, declenche_par=None, est_sauvegarde_securite=False):
    """
    Point d'entrée unique pour lancer une sauvegarde (déclenchement manuel,
    planifié, ou sauvegarde de sécurité avant restauration — voir
    Sauvegarde/views.py et services/planification_service.py). Construit
    l'archive, l'écrit sur la destination choisie, journalise le résultat dans
    HistoriqueSauvegarde. Ne lève jamais d'exception vers l'appelant : un échec
    est capturé et journalisé comme tel (pour que le planificateur automatique
    ne casse jamais silencieusement sur une erreur ponctuelle).
    """
    # imports différés : évitent un cycle (models.py de cette app n'importe
    # pas ce service, mais ce service importe les modèles — cohérent avec le
    # reste du projet, voir genererRapportAudit, Registration/views.py)
    from .google_drive_service import ErreurGoogleDrive, uploader_sauvegarde
    from ..models import ConfigurationSauvegarde, HistoriqueSauvegarde, obtenir_configuration

    config = obtenir_configuration()
    depuis = config.derniere_execution_reussie if type_sauvegarde == 'incrementale' else None

    # capturé AVANT construire_archive (qui interroge la base tout de suite
    # après) plutôt qu'après l'écriture complète de l'archive : la
    # construction + l'upload peuvent prendre du temps, pendant lequel
    # d'autres enregistrements continuent d'être modifiés. Si
    # derniere_execution_reussie était l'horodatage de FIN de cette exécution,
    # tout enregistrement modifié entre le début de la requête ci-dessous et
    # cette fin serait exclu de CETTE sauvegarde (modifié après la requête)
    # ET de la PROCHAINE sauvegarde incrémentale (son "depuis" serait déjà
    # postérieur à sa date_maj) — perdu silencieusement pour toujours. En
    # capturant l'horodatage de départ, un tel enregistrement peut au pire
    # apparaître deux fois (cette sauvegarde-ci ET la suivante), jamais zéro.
    horodatage_reference = timezone.now()

    try:
        contenu_chiffre, nombre_enregistrements, manifest = construire_archive(type_sauvegarde, depuis)
    except Exception as e:
        return HistoriqueSauvegarde.objects.create(
            type_sauvegarde=type_sauvegarde, destination=destination,
            declenche_par=declenche_par, statut='echec',
            message_erreur=f"Échec de construction de l'archive : {e}",
            est_sauvegarde_securite=est_sauvegarde_securite,
        )

    checksum = hashlib.sha256(contenu_chiffre).hexdigest()
    taille_octets = len(contenu_chiffre)
    nom_fichier = f"Sauvegarde-RekoltHt-{timezone.now():%Y%m%d-%H%M%S}.rhtbackup"

    try:
        if destination == 'google_drive':
            # rien à vérifier ici : construire_archive() n'écrit jamais sur
            # disque (tout en mémoire, io.BytesIO — voir plus haut) et
            # uploader_sauvegarde() envoie ce contenu directement à Google,
            # sans fichier temporaire local

            # diffusion de la progression (voir Api/broadcast.py, groupe
            # "admins") — uniquement pour Google Drive, seule destination assez
            # lente pour justifier une barre de progression (l'écriture locale
            # ci-dessous est quasi instantanée). Import différé : cohérent avec
            # le reste du projet (voir Registration/views.py::traiter_demande_revue_manuelle)
            from Api.broadcast import broadcast_to_admins
            debut_upload = time.monotonic()
            broadcast_to_admins("sauvegarde.progression", {
                'pourcentage':        0,
                'minutes_restantes':  None,   # pas encore assez de données pour estimer
                'octets_envoyes':     0,
                'octets_total':       taille_octets,
            })

            def _rapporter_progression(octets_envoyes, octets_total):
                ecoule = time.monotonic() - debut_upload
                pourcentage = round(min(octets_envoyes, octets_total) / octets_total * 100, 1) if octets_total else 100
                minutes_restantes = None
                if octets_envoyes > 0 and ecoule > 0:
                    duree_totale_estimee = ecoule * octets_total / octets_envoyes
                    minutes_restantes = max(0, round((duree_totale_estimee - ecoule) / 60))
                broadcast_to_admins("sauvegarde.progression", {
                    'pourcentage':        pourcentage,
                    'minutes_restantes':  minutes_restantes,
                    'octets_envoyes':     octets_envoyes,
                    'octets_total':       octets_total,
                })

            fichier_id = uploader_sauvegarde(contenu_chiffre, nom_fichier, on_progress=_rapporter_progression)
            historique = HistoriqueSauvegarde.objects.create(
                type_sauvegarde=manifest['type_sauvegarde'], destination=destination,
                declenche_par=declenche_par, statut='succes',
                taille_octets=taille_octets, nombre_enregistrements=nombre_enregistrements,
                checksum_sha256=checksum, google_drive_file_id=fichier_id,
                est_sauvegarde_securite=est_sauvegarde_securite,
            )
            broadcast_to_admins("sauvegarde.terminee", {'statut': 'succes', 'historique_id': historique.id})
        else:
            dossier = Path(settings.SAUVEGARDE_ROOT)
            # vérifié AVANT d'écrire (pas après une IOError en cours
            # d'écriture) — voir verifier_espace_disque ci-dessus
            verifier_espace_disque(dossier, taille_octets)
            chemin = dossier / nom_fichier
            chemin.write_bytes(contenu_chiffre)
            historique = HistoriqueSauvegarde.objects.create(
                type_sauvegarde=manifest['type_sauvegarde'], destination=destination,
                declenche_par=declenche_par, statut='succes',
                taille_octets=taille_octets, nombre_enregistrements=nombre_enregistrements,
                checksum_sha256=checksum, chemin_fichier_local=str(chemin),
                est_sauvegarde_securite=est_sauvegarde_securite,
            )
    except (ErreurGoogleDrive, OSError, EspaceDisqueInsuffisant) as e:
        if destination == 'google_drive':
            # fait disparaître la barre de progression côté dashboard même en
            # cas d'échec en cours d'envoi (coupure réseau, token expiré...)
            from Api.broadcast import broadcast_to_admins
            broadcast_to_admins("sauvegarde.terminee", {'statut': 'echec'})
        return HistoriqueSauvegarde.objects.create(
            type_sauvegarde=manifest['type_sauvegarde'], destination=destination,
            declenche_par=declenche_par, statut='echec',
            message_erreur=f"Échec d'écriture de la sauvegarde : {e}",
            est_sauvegarde_securite=est_sauvegarde_securite,
        )

    if not est_sauvegarde_securite:
        ConfigurationSauvegarde.objects.filter(pk=config.pk).update(derniere_execution_reussie=horodatage_reference)

    return historique
