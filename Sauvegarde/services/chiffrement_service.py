# ── IMPORTS ───────────────────────────────────────────────────────────────────
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

# identifie le format avant même de tenter le déchiffrement (ex. rejeter
# immédiatement un fichier qui n'est pas un .rhtbackup, message d'erreur plus
# clair qu'une InvalidToken brute de Fernet) — le "1" est la version du format,
# à incrémenter si la structure de l'archive change un jour de façon incompatible
EN_TETE_FORMAT = b'RHTBK1'


class ErreurChiffrementSauvegarde(Exception):
    """Clé maître absente, fichier corrompu, ou format .rhtbackup non reconnu."""


def _obtenir_fernet():
    cle = settings.BACKUP_MASTER_KEY
    if not cle:
        raise ErreurChiffrementSauvegarde(
            "BACKUP_MASTER_KEY n'est pas configurée (voir .env/.env.dev) — "
            "impossible de chiffrer ou déchiffrer une sauvegarde."
        )
    try:
        return Fernet(cle.encode() if isinstance(cle, str) else cle)
    except (ValueError, TypeError) as e:
        raise ErreurChiffrementSauvegarde(f"BACKUP_MASTER_KEY invalide : {e}")


def chiffrer_archive(donnees: bytes) -> bytes:
    """Chiffre le contenu d'une archive de sauvegarde (voir export_service.py)
    — format propriétaire .rhtbackup, illisible sans la clé maître du serveur."""
    jeton = _obtenir_fernet().encrypt(donnees)
    return EN_TETE_FORMAT + jeton


def dechiffrer_archive(contenu: bytes) -> bytes:
    """Inverse de chiffrer_archive — lève ErreurChiffrementSauvegarde si le
    fichier n'est pas un .rhtbackup valide ou si la clé ne correspond pas."""
    if not contenu.startswith(EN_TETE_FORMAT):
        raise ErreurChiffrementSauvegarde(
            "Ce fichier n'est pas une sauvegarde RekoltHt valide (.rhtbackup)."
        )
    jeton = contenu[len(EN_TETE_FORMAT):]
    try:
        return _obtenir_fernet().decrypt(jeton)
    except InvalidToken:
        raise ErreurChiffrementSauvegarde(
            "Impossible de déchiffrer ce fichier : il est corrompu, ou provient "
            "d'un serveur avec une clé de sauvegarde différente."
        )


def chiffrer_texte(texte: str) -> str:
    """Chiffre une chaîne courte (ex. refresh token Google Drive, voir
    ConfigurationSauvegarde.google_drive_refresh_token_chiffre) — même clé
    maître, sans l'en-tête de format (usage interne, pas un fichier exporté)."""
    return _obtenir_fernet().encrypt(texte.encode()).decode()


def dechiffrer_texte(texte_chiffre: str) -> str:
    try:
        return _obtenir_fernet().decrypt(texte_chiffre.encode()).decode()
    except InvalidToken:
        raise ErreurChiffrementSauvegarde("Jeton chiffré invalide ou clé de sauvegarde changée.")
