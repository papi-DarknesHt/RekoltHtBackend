# ── IMPORTS ───────────────────────────────────────────────────────────────────
"""
Intégration Google Drive pour la destination "distante" des sauvegardes.

Volontairement SANS google-api-python-client : cette bibliothèque tire
google-api-core (protobuf récent), incompatible avec paddlepaddle==2.6.2
(protobuf<=3.20.2, voir requirements.txt et la note historique sur
requirements-face.txt) — vérifié à l'installation, import paddle échouait dès
que google-api-python-client était installé dans le même environnement.

Ce module appelle donc l'API REST Drive v3 directement via `requests` (déjà
une dépendance du projet), et n'utilise google-auth / google-auth-oauthlib que
pour le flux OAuth2 (construction de l'URL de consentement, échange du code,
rafraîchissement du token) — ces deux paquets ne dépendent pas de protobuf.
"""
import json
import os
import secrets

import requests
from django.conf import settings
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from .chiffrement_service import chiffrer_texte, dechiffrer_texte

# Ce projet utilise le MÊME client OAuth Google pour deux usages : la
# connexion "Continuer avec Google" (social_core.backends.google.GoogleOAuth2,
# scopes email/profile/openid) et la connexion Drive ci-dessous (scope
# drive.file uniquement). Un même compte Google mémorise les scopes déjà
# accordés à un client OAuth : au consentement Drive, Google renvoie donc
# souvent email/profile/openid EN PLUS de drive.file (constaté en conditions
# réelles, voir le paramètre `scope` de la redirection vers google_callback).
# oauthlib refuse par défaut cet écart entre scopes demandés et obtenus
# ("Scope has changed" — échoue flow.fetch_token() dans
# echanger_code_contre_refresh_token ci-dessous), avant même que Google Drive
# n'entre en jeu. OAUTHLIB_RELAX_TOKEN_SCOPE=1 désactive cette vérification
# stricte — c'est le correctif officiel documenté par google-auth-oauthlib
# pour ce cas précis. Doit être positionné avant le premier appel à Flow.
os.environ.setdefault('OAUTHLIB_RELAX_TOKEN_SCOPE', '1')

# scope minimal : l'app ne voit QUE les fichiers qu'elle crée elle-même, pas
# le reste du Drive de l'admin connecté
SCOPES = ['https://www.googleapis.com/auth/drive.file']
NOM_DOSSIER_DRIVE = 'RekoltHt — Sauvegardes'
URL_UPLOAD = 'https://www.googleapis.com/upload/drive/v3/files'
URL_FICHIERS = 'https://www.googleapis.com/drive/v3/files'


class ErreurGoogleDrive(Exception):
    """Identifiants Drive absents, token invalide, ou appel API en échec."""


def _verifier_identifiants_configures():
    if not (settings.GOOGLE_DRIVE_CLIENT_ID and settings.GOOGLE_DRIVE_CLIENT_SECRET):
        raise ErreurGoogleDrive(
            "GOOGLE_DRIVE_CLIENT_ID/GOOGLE_DRIVE_CLIENT_SECRET ne sont pas configurés "
            "(voir .env/.env.dev) — la destination Google Drive est indisponible."
        )


def _config_client_oauth():
    return {
        'web': {
            'client_id':     settings.GOOGLE_DRIVE_CLIENT_ID,
            'client_secret': settings.GOOGLE_DRIVE_CLIENT_SECRET,
            'auth_uri':      'https://accounts.google.com/o/oauth2/auth',
            'token_uri':     'https://oauth2.googleapis.com/token',
            'redirect_uris': [settings.GOOGLE_DRIVE_REDIRECT_URI],
        }
    }


def construire_url_autorisation():
    """
    Retourne (url_autorisation, state, code_verifier). `state` est un jeton
    aléatoire à faire voyager côté frontend (ex. sessionStorage) et à
    revérifier dans le callback (voir Sauvegarde/views.py::google_callback) —
    protection CSRF standard du flux OAuth2, indépendante du token
    d'authentification de l'API (le callback est appelé directement par
    Google, sans header Authorization).

    `code_verifier` : google-auth-oauthlib active PKCE par défaut
    (Flow(autogenerate_code_verifier=True)) — un `code_verifier` aléatoire est
    généré sur CETTE instance de Flow dès l'appel à authorization_url() ci-
    dessous, et Google exige le MÊME `code_verifier` à l'échange du code
    (echanger_code_contre_refresh_token). Comme cette instance de Flow ne
    survit pas à la requête HTTP courante (nouvelle instance recréée dans le
    callback, potentiellement une tout autre requête/process), l'appelant
    (Sauvegarde/views.py::google_autoriser) doit le faire voyager lui-même —
    même mécanisme que `state` (cache serveur, associé à l'admin). Sans ça,
    Google répond "(invalid_grant) Missing code verifier" à l'échange —
    constaté en conditions réelles.
    """
    _verifier_identifiants_configures()
    flow = Flow.from_client_config(_config_client_oauth(), scopes=SCOPES)
    flow.redirect_uri = settings.GOOGLE_DRIVE_REDIRECT_URI
    state = secrets.token_urlsafe(32)
    url, _ = flow.authorization_url(
        access_type='offline',   # nécessaire pour obtenir un refresh_token
        prompt='consent',        # force la réémission d'un refresh_token même en reconnexion
        state=state,
        include_granted_scopes='true',
    )
    return url, state, flow.code_verifier


def echanger_code_contre_refresh_token(code: str, code_verifier: str) -> str:
    """Étape callback : échange le `code` reçu de Google contre un refresh
    token. `code_verifier` : voir construire_url_autorisation ci-dessus —
    OBLIGATOIRE, doit être celui généré pour la même demande d'autorisation
    (récupéré du cache par l'appelant). Lève ErreurGoogleDrive si l'échange
    échoue (code expiré/invalide, code_verifier absent/incorrect)."""
    _verifier_identifiants_configures()
    flow = Flow.from_client_config(_config_client_oauth(), scopes=SCOPES)
    flow.redirect_uri = settings.GOOGLE_DRIVE_REDIRECT_URI
    flow.code_verifier = code_verifier
    try:
        flow.fetch_token(code=code)
    except Exception as e:
        raise ErreurGoogleDrive(f"Échec de l'échange du code d'autorisation Google : {e}")

    refresh_token = flow.credentials.refresh_token
    if not refresh_token:
        raise ErreurGoogleDrive(
            "Google n'a renvoyé aucun refresh token — reconnecte Google Drive "
            "(assure-toi de bien accepter l'écran de consentement)."
        )
    return refresh_token


def _obtenir_credentials(config):
    """Construit des Credentials à partir du refresh token stocké (chiffré) et
    rafraîchit l'access token si besoin — un access token Google expire au
    bout d'environ 1h, alors qu'un refresh token reste valable indéfiniment
    (tant que l'accès n'est pas révoqué côté compte Google)."""
    if not config.google_drive_connecte or not config.google_drive_refresh_token_chiffre:
        raise ErreurGoogleDrive("Google Drive n'est pas connecté — connecte-le d'abord depuis la configuration.")
    _verifier_identifiants_configures()

    refresh_token = dechiffrer_texte(config.google_drive_refresh_token_chiffre)
    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri='https://oauth2.googleapis.com/token',
        client_id=settings.GOOGLE_DRIVE_CLIENT_ID,
        client_secret=settings.GOOGLE_DRIVE_CLIENT_SECRET,
        scopes=SCOPES,
    )
    try:
        credentials.refresh(GoogleAuthRequest())
    except Exception as e:
        # 'unauthorized_client'/'invalid_grant' : le refresh token stocké est
        # DÉFINITIVEMENT inutilisable — le cas le plus fréquent est un
        # changement de GOOGLE_DRIVE_CLIENT_ID/SECRET après la connexion
        # initiale (constaté en conditions réelles) : un refresh token Google
        # est lié au client OAuth qui l'a émis, il ne peut jamais être
        # rafraîchi par un AUTRE client_id, même valide par ailleurs. Aucune
        # nouvelle tentative ne réussira sans reconnexion complète (nouvel
        # écran de consentement) — on efface donc l'état "connecté" ici
        # plutôt que de laisser le dashboard admin afficher à tort "connecté"
        # alors que chaque sauvegarde (manuelle ET planifiée) échouera. Une
        # erreur réseau/timeout transitoire (pas de ces deux mots dans le
        # message) ne déclenche PAS cette déconnexion : elle peut réussir au
        # prochain essai sans reconnexion.
        message = str(e)
        if 'unauthorized_client' in message or 'invalid_grant' in message:
            deconnecter()
            raise ErreurGoogleDrive(
                "La connexion Google Drive n'est plus valide (identifiants OAuth changés ou accès "
                "révoqué côté Google) et a été réinitialisée — reconnecte Google Drive depuis la "
                f"configuration des sauvegardes. Détail : {message}"
            )
        raise ErreurGoogleDrive(f"Impossible de rafraîchir la connexion Google Drive : {message}")
    return credentials


def _assurer_dossier(config, credentials) -> str:
    """Retourne l'id du dossier Drive dédié aux sauvegardes, le créant s'il
    n'existe pas encore (une seule fois, réutilisé ensuite via
    ConfigurationSauvegarde.google_drive_dossier_id)."""
    if config.google_drive_dossier_id:
        return config.google_drive_dossier_id

    reponse = requests.post(
        URL_FICHIERS,
        headers={'Authorization': f'Bearer {credentials.token}', 'Content-Type': 'application/json'},
        data=json.dumps({'name': NOM_DOSSIER_DRIVE, 'mimeType': 'application/vnd.google-apps.folder'}),
    )
    if reponse.status_code >= 400:
        raise ErreurGoogleDrive(f"Impossible de créer le dossier Drive de sauvegardes : {reponse.text}")

    dossier_id = reponse.json()['id']
    config.google_drive_dossier_id = dossier_id
    config.save(update_fields=['google_drive_dossier_id'])
    return dossier_id


# taille d'un bloc d'envoi resumable — DOIT être un multiple de 256 Kio (sauf
# le tout dernier bloc), c'est une exigence de l'API Drive resumable upload
TAILLE_BLOC_UPLOAD = 8 * 1024 * 1024   # 8 Mio


def uploader_sauvegarde(contenu_chiffre: bytes, nom_fichier: str, on_progress=None) -> str:
    """
    Envoie l'archive .rhtbackup (déjà chiffrée) dans le dossier Drive dédié,
    via l'upload "resumable" de l'API Drive (initiation + envoi par blocs de
    TAILLE_BLOC_UPLOAD, avec reprise automatique après une coupure réseau) —
    PAS l'upload "multipart" en un seul bloc utilisé avant (contenu entier en
    mémoire, aucune reprise possible). Une sauvegarde "complète" inclut tous
    les médias uploadés et peut facilement dépasser 300 Mo (constaté en
    conditions réelles), largement au-delà des ~5 Mo au-delà desquels Google
    déconseille lui-même le mode "multipart" — un simple aléa réseau en cours
    de route (SSLEOFError constaté en conditions réelles) faisait alors tout
    échouer sans aucun moyen de reprendre, qu'il fallait relancer depuis zéro.

    `on_progress(octets_envoyes, octets_total)`, si fourni, est appelé après
    chaque bloc CONFIRMÉ par Google (pas juste tenté — voir
    _envoyer_par_blocs_avec_reprise) : sert à alimenter la barre de
    progression du dashboard admin (voir Sauvegarde/services/export_service.py).

    Retourne l'id du fichier créé (stocké dans HistoriqueSauvegarde.google_drive_file_id).
    """
    from ..models import obtenir_configuration
    config = obtenir_configuration()
    credentials = _obtenir_credentials(config)
    dossier_id = _assurer_dossier(config, credentials)
    session_uri = _initier_upload_resumable(dossier_id, nom_fichier, credentials)
    return _envoyer_par_blocs_avec_reprise(session_uri, contenu_chiffre, on_progress=on_progress)


def _initier_upload_resumable(dossier_id: str, nom_fichier: str, credentials) -> str:
    """Ouvre une session d'envoi resumable auprès de Drive, retourne son URI
    (en-tête Location de la réponse) — voir uploader_sauvegarde ci-dessus."""
    metadonnees = json.dumps({'name': nom_fichier, 'parents': [dossier_id]})
    reponse = requests.post(
        f"{URL_UPLOAD}?uploadType=resumable",
        headers={
            'Authorization': f'Bearer {credentials.token}',
            'Content-Type': 'application/json; charset=UTF-8',
        },
        data=metadonnees,
        timeout=30,
    )
    if reponse.status_code >= 400:
        raise ErreurGoogleDrive(f"Impossible d'initier l'envoi vers Google Drive : {reponse.text}")
    session_uri = reponse.headers.get('Location')
    if not session_uri:
        raise ErreurGoogleDrive("Google Drive n'a renvoyé aucune URI de session d'envoi (en-tête Location manquant).")
    return session_uri


def _interroger_position_reprise(session_uri: str, taille_totale: int):
    """
    Après une coupure réseau en cours d'envoi, demande à Google jusqu'où le
    contenu a bien été reçu (protocole resumable upload standard : PUT avec
    un corps vide et Content-Range "bytes */total") — répond 308 avec un
    en-tête Range ("bytes=0-X") indiquant les octets déjà confirmés, ou None
    si la position n'a pas pu être déterminée (on retente alors depuis la
    dernière position connue localement plutôt que depuis zéro).
    """
    try:
        reponse = requests.put(
            session_uri,
            headers={'Content-Range': f'bytes */{taille_totale}'},
            timeout=30,
        )
    except requests.exceptions.RequestException:
        return None
    if reponse.status_code == 308:
        plage = reponse.headers.get('Range')   # ex: "bytes=0-1048575"
        if plage and '-' in plage:
            return int(plage.rsplit('-', 1)[1]) + 1
    return None


def _envoyer_par_blocs_avec_reprise(session_uri: str, contenu: bytes, tentatives_max: int = 5, on_progress=None) -> str:
    """Envoie `contenu` à la session resumable `session_uri` par blocs de
    TAILLE_BLOC_UPLOAD, en reprenant à la bonne position (voir
    _interroger_position_reprise) après chaque coupure réseau plutôt que de
    tout recommencer — voir uploader_sauvegarde ci-dessus. `on_progress`
    n'est appelé qu'après un bloc CONFIRMÉ (308/200/201), jamais après une
    tentative ratée — la progression rapportée reflète toujours ce que Google
    a réellement reçu, pas ce qui a été envoyé sur le fil."""
    taille_totale = len(contenu)
    position = 0
    echecs_consecutifs = 0

    while position < taille_totale:
        fin = min(position + TAILLE_BLOC_UPLOAD, taille_totale)
        bloc = contenu[position:fin]
        try:
            reponse = requests.put(
                session_uri,
                headers={
                    'Content-Length': str(len(bloc)),
                    'Content-Range': f'bytes {position}-{fin - 1}/{taille_totale}',
                },
                data=bloc,
                timeout=120,
            )
        except requests.exceptions.RequestException as e:
            echecs_consecutifs += 1
            if echecs_consecutifs > tentatives_max:
                raise ErreurGoogleDrive(f"Échec de l'envoi vers Google Drive après {tentatives_max} tentatives : {e}")
            reprise = _interroger_position_reprise(session_uri, taille_totale)
            if reprise is not None:
                position = reprise
            continue   # retente (depuis `position`, éventuellement corrigée ci-dessus)

        if reponse.status_code in (200, 201):
            if on_progress:
                on_progress(taille_totale, taille_totale)
            return reponse.json()['id']
        if reponse.status_code == 308:
            position = fin
            echecs_consecutifs = 0
            if on_progress:
                on_progress(position, taille_totale)
            continue
        raise ErreurGoogleDrive(f"Échec de l'envoi vers Google Drive (code {reponse.status_code}) : {reponse.text}")

    raise ErreurGoogleDrive("Envoi vers Google Drive terminé sans confirmation de Google (aucun id de fichier reçu).")


def telecharger_sauvegarde_en_flux(fichier_id: str):
    """Récupère le contenu binaire (toujours chiffré) d'une sauvegarde stockée
    sur Drive — voir Sauvegarde/views.py::historique_telecharger.

    Renvoie un flux (fichier-like, .read() par blocs), PAS le contenu entier
    en mémoire : une sauvegarde "complète" inclut tous les médias uploadés et
    peut facilement dépasser 300 Mo (constaté en conditions réelles) — tout
    charger d'un coup avant de répondre est ce qui cassait le téléchargement
    (connexion coupée côté navigateur, "Failed to fetch")."""
    from ..models import obtenir_configuration
    config = obtenir_configuration()
    credentials = _obtenir_credentials(config)

    reponse = requests.get(
        f"{URL_FICHIERS}/{fichier_id}",
        headers={'Authorization': f'Bearer {credentials.token}'},
        params={'alt': 'media'},
        stream=True,
    )
    if reponse.status_code >= 400:
        raise ErreurGoogleDrive(f"Échec du téléchargement depuis Google Drive : {reponse.text}")
    reponse.raw.decode_content = True   # applique la décompression gzip/deflate éventuelle nous-mêmes
    return reponse.raw


def deconnecter():
    """Efface la connexion Drive stockée — n'appelle pas l'API Google pour
    révoquer le token côté serveur (l'admin peut le faire lui-même depuis
    myaccount.google.com/permissions si souhaité), se contente d'oublier le
    refresh token localement."""
    from ..models import obtenir_configuration
    config = obtenir_configuration()
    config.google_drive_connecte = False
    config.google_drive_refresh_token_chiffre = None
    config.google_drive_dossier_id = None
    config.save(update_fields=['google_drive_connecte', 'google_drive_refresh_token_chiffre', 'google_drive_dossier_id'])
