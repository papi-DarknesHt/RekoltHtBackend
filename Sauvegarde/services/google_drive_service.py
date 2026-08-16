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
import secrets

import requests
from django.conf import settings
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from .chiffrement_service import chiffrer_texte, dechiffrer_texte

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
    Retourne (url_autorisation, state). `state` est un jeton aléatoire à faire
    voyager côté frontend (ex. sessionStorage) et à revérifier dans le callback
    (voir Sauvegarde/views.py::google_callback) — protection CSRF standard du
    flux OAuth2, indépendante du token d'authentification de l'API (le
    callback est appelé directement par Google, sans header Authorization).
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
    return url, state


def echanger_code_contre_refresh_token(code: str) -> str:
    """Étape callback : échange le `code` reçu de Google contre un refresh
    token. Lève ErreurGoogleDrive si l'échange échoue (code expiré/invalide)."""
    _verifier_identifiants_configures()
    flow = Flow.from_client_config(_config_client_oauth(), scopes=SCOPES)
    flow.redirect_uri = settings.GOOGLE_DRIVE_REDIRECT_URI
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
        raise ErreurGoogleDrive(f"Impossible de rafraîchir la connexion Google Drive : {e}")
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


def uploader_sauvegarde(contenu_chiffre: bytes, nom_fichier: str) -> str:
    """Envoie l'archive .rhtbackup (déjà chiffrée) dans le dossier Drive dédié.
    Retourne l'id du fichier créé (stocké dans HistoriqueSauvegarde.google_drive_file_id)."""
    from ..models import obtenir_configuration
    config = obtenir_configuration()
    credentials = _obtenir_credentials(config)
    dossier_id = _assurer_dossier(config, credentials)

    metadonnees = json.dumps({'name': nom_fichier, 'parents': [dossier_id]})
    frontieres = b'RekoltHtBackupBoundary'
    corps = (
        b'--' + frontieres + b'\r\n'
        b'Content-Type: application/json; charset=UTF-8\r\n\r\n'
        + metadonnees.encode('utf-8') + b'\r\n'
        b'--' + frontieres + b'\r\n'
        b'Content-Type: application/octet-stream\r\n\r\n'
        + contenu_chiffre + b'\r\n'
        b'--' + frontieres + b'--'
    )
    reponse = requests.post(
        f"{URL_UPLOAD}?uploadType=multipart",
        headers={
            'Authorization': f'Bearer {credentials.token}',
            'Content-Type': f'multipart/related; boundary={frontieres.decode()}',
        },
        data=corps,
    )
    if reponse.status_code >= 400:
        raise ErreurGoogleDrive(f"Échec de l'envoi vers Google Drive : {reponse.text}")
    return reponse.json()['id']


def telecharger_sauvegarde(fichier_id: str) -> bytes:
    """Récupère le contenu binaire (toujours chiffré) d'une sauvegarde stockée
    sur Drive — voir Sauvegarde/views.py::historique_telecharger."""
    from ..models import obtenir_configuration
    config = obtenir_configuration()
    credentials = _obtenir_credentials(config)

    reponse = requests.get(
        f"{URL_FICHIERS}/{fichier_id}",
        headers={'Authorization': f'Bearer {credentials.token}'},
        params={'alt': 'media'},
    )
    if reponse.status_code >= 400:
        raise ErreurGoogleDrive(f"Échec du téléchargement depuis Google Drive : {reponse.text}")
    return reponse.content


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
