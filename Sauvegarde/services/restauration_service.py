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


# La clé privée de chiffrement E2E de la messagerie n'est JAMAIS restaurée
# depuis une archive : elle reste mise en cache localement (IndexedDB, voir
# src/api/e2eKeyStore.js) dans le navigateur de chaque admin, indépendamment
# de tout backup. Si loaddata réécrivait cette table avec un ancien
# instantané, le matériel de clé publié côté serveur redeviendrait
# incohérent avec la clé privée déjà en cache sur les appareils déjà
# synchronisés (restaurée par cette même fonction plus tôt dans la session,
# ou jamais rotée depuis) ⇒ tous les messages déjà lisibles redeviennent
# "Message illisible sur cet appareil" après CHAQUE restauration, même si
# rien n'a changé côté utilisateur. Exclure cette table du chargement rend
# une restauration totalement transparente pour la messagerie chiffrée :
# les clés actuellement actives (et donc les messages en cours) continuent
# de fonctionner sans interruption. Elle reste bien présente dans l'archive
# elle-même (voir export_service.py — aucune exclusion à l'export), au cas
# où une restauration manuelle complète de la base serait un jour
# nécessaire hors de cette fonction.
MODELES_EXCLUS_DE_LA_RESTAURATION = {
    ('Registration', 'clechiffrementutilisateur'),
}


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
    from django.conf import settings
    from .export_service import executer_sauvegarde, verifier_espace_disque, EspaceDisqueInsuffisant

    manifest, data, tar = _decoder_archive(contenu_chiffre)

    # vérifié AVANT toute écriture (sauvegarde de sécurité + extraction des
    # media ci-dessous) — voir verifier_espace_disque, Sauvegarde/services/
    # export_service.py. Estimation volontairement large (×2 la taille de
    # l'archive chiffrée reçue) : le contenu déchiffré/décompressé peut être
    # plus volumineux que l'archive compressée, et la sauvegarde de sécurité
    # prise juste après occupe elle-même un espace comparable.
    if getattr(settings, 'MEDIA_ROOT', None):
        try:
            verifier_espace_disque(settings.MEDIA_ROOT, len(contenu_chiffre) * 2)
        except EspaceDisqueInsuffisant as e:
            tar.close()
            raise ErreurRestauration(str(e))

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

    # voir MODELES_EXCLUS_DE_LA_RESTAURATION ci-dessus — filtré ici (pas à
    # l'export) : l'archive garde ces enregistrements, seule leur réécriture
    # en base lors d'une restauration est évitée.
    donnees_a_charger = [
        obj for obj in data if tuple(obj['model'].split('.', 1)) not in MODELES_EXCLUS_DE_LA_RESTAURATION
    ]

    fichier_temp = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8')
    try:
        json.dump(donnees_a_charger, fichier_temp)
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
        'nombre_enregistrements_restaures': len(donnees_a_charger),
        'nombre_fichiers_media_restaures':  nombre_fichiers_media_restaures,
        'sauvegarde_securite_id':           sauvegarde_securite.id,
    }
