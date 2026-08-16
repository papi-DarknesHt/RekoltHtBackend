# ── IMPORTS ───────────────────────────────────────────────────────────────────
import io
import json
import tarfile
import tempfile
from collections import Counter
from pathlib import Path

from django.conf import settings
from django.core.management import call_command

from .chiffrement_service import ErreurChiffrementSauvegarde, dechiffrer_archive


class ErreurRestauration(Exception):
    """Archive illisible, corrompue, ou d'un format de sauvegarde inconnu."""


def _decoder_archive(contenu_chiffre: bytes):
    """Déchiffre puis ouvre l'archive tar — retourne (manifest, data_json,
    tarfile.TarFile encore ouvert, positionné pour lire les fichiers media).
    Lève ErreurRestauration (jamais l'exception brute de tarfile/json) si le
    contenu n'est pas un .rhtbackup exploitable."""
    try:
        archive_brute = dechiffrer_archive(contenu_chiffre)
    except ErreurChiffrementSauvegarde as e:
        raise ErreurRestauration(str(e))

    try:
        tar = tarfile.open(fileobj=io.BytesIO(archive_brute), mode='r:gz')
        manifest = json.loads(tar.extractfile('manifest.json').read())
        data = json.loads(tar.extractfile('data.json').read())
    except (tarfile.TarError, KeyError, json.JSONDecodeError, AttributeError) as e:
        raise ErreurRestauration(f"Archive corrompue ou format inattendu : {e}")

    return manifest, data, tar


def analyser(contenu_chiffre: bytes) -> dict:
    """
    Aperçu SANS AUCUNE ÉCRITURE — déchiffre et lit le contenu de l'archive,
    retourne un résumé pour confirmation par le super admin avant restauration
    réelle (voir Sauvegarde/views.py::restaurer_analyser / restaurer_confirmer).
    """
    manifest, data, tar = _decoder_archive(contenu_chiffre)
    tar.close()

    compte_par_modele = Counter(obj['model'] for obj in data)

    return {
        'manifest': manifest,
        'nombre_enregistrements': len(data),
        'enregistrements_par_modele': dict(sorted(compte_par_modele.items())),
        'media_inclus': manifest.get('media_inclus', False),
        'nombre_fichiers_media': manifest.get('nombre_fichiers_media', 0),
    }


def confirmer(contenu_chiffre: bytes, restaure_par) -> dict:
    """
    Restauration RÉELLE — prend d'abord une sauvegarde de sécurité de l'état
    courant (voir export_service.executer_sauvegarde), puis remplace les
    données applicatives par celles de l'archive via `loaddata` (déjà
    transactionnel : un échec en cours de route annule tout), et restaure les
    fichiers media inclus le cas échéant. Réservée au super admin (voir
    verifier_droit_admin(utilisateur, 'super_admin') dans la vue appelante).
    """
    # import différé : évite un cycle (export_service importe déjà ce module
    # indirectement via Sauvegarde.views, jamais l'inverse en temps normal)
    from .export_service import executer_sauvegarde

    manifest, data, tar = _decoder_archive(contenu_chiffre)

    sauvegarde_securite = executer_sauvegarde(
        type_sauvegarde='complete', destination='locale',
        declenche_par=restaure_par, est_sauvegarde_securite=True,
    )
    if sauvegarde_securite.statut != 'succes':
        tar.close()
        raise ErreurRestauration(
            "La sauvegarde de sécurité de l'état actuel a échoué "
            f"({sauvegarde_securite.message_erreur}) — restauration annulée par prudence."
        )

    fichier_temp = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8')
    try:
        json.dump(data, fichier_temp)
        fichier_temp.close()
        call_command('loaddata', fichier_temp.name, format='json')
    except Exception as e:
        tar.close()
        raise ErreurRestauration(
            f"Échec du chargement des données ({e}) — aucune donnée n'a été modifiée "
            "(loaddata annule tout en cas d'erreur), une sauvegarde de sécurité de "
            f"l'état d'avant restauration a tout de même été conservée (id {sauvegarde_securite.id})."
        )
    finally:
        Path(fichier_temp.name).unlink(missing_ok=True)

    nombre_fichiers_media_restaures = 0
    if manifest.get('media_inclus') and getattr(settings, 'MEDIA_ROOT', None):
        media_root = Path(settings.MEDIA_ROOT)
        media_root.mkdir(parents=True, exist_ok=True)
        for membre in tar.getmembers():
            if not membre.name.startswith('media/') or not membre.isfile():
                continue
            chemin_relatif = membre.name[len('media/'):]
            destination = media_root / chemin_relatif
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(membre) as source, open(destination, 'wb') as cible:
                cible.write(source.read())
            nombre_fichiers_media_restaures += 1
    tar.close()

    return {
        'nombre_enregistrements_restaures': len(data),
        'nombre_fichiers_media_restaures':  nombre_fichiers_media_restaures,
        'sauvegarde_securite_id':           sauvegarde_securite.id,
    }
