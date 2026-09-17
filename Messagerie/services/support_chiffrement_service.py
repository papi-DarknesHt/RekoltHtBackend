# ── IMPORTS ───────────────────────────────────────────────────────────────────
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

# Chiffrement du "coffre support" (voir MessageSupport, Messagerie/models.py)
# — décision assumée : contrairement à la messagerie privée 1:1 (chiffrement
# de bout en bout, jamais lisible par le serveur), les messages support sont
# chiffrés avec une clé que le serveur applicatif détient lui-même (voir
# SUPPORT_MASTER_KEY, BackendRekoltHt/settings/base.py). Ça protège contre un
# accès base de données brut (fuite/vol de la DB), mais pas contre le serveur
# applicatif lui-même — en échange, n'importe quel admin auquel le droit
# gestion_support est accordé, même APRÈS l'envoi d'un message, peut le lire
# et y répondre immédiatement, sans dépendre d'un autre admin encore en ligne
# pour "réparer" une enveloppe de clé E2E manquante (impossible par nature
# avec un chiffrement de bout en bout — voir l'ancien schéma, toujours
# conservé en lecture seule pour les messages antérieurs, format_chiffrement
# == 'e2e_client').


class ErreurChiffrementSupport(Exception):
    """SUPPORT_MASTER_KEY absente/invalide, ou message support corrompu."""


def _obtenir_fernet():
    cle = settings.SUPPORT_MASTER_KEY
    if not cle:
        raise ErreurChiffrementSupport(
            "SUPPORT_MASTER_KEY n'est pas configurée (voir .env/.env.dev) — "
            "impossible de chiffrer ou déchiffrer un message support."
        )
    try:
        return Fernet(cle.encode() if isinstance(cle, str) else cle)
    except (ValueError, TypeError) as e:
        raise ErreurChiffrementSupport(f"SUPPORT_MASTER_KEY invalide : {e}")


def chiffrer(texte: str) -> str:
    """Chiffre le contenu ou la réponse d'un MessageSupport — voir
    contacterAdmin/repondreMessageAdmin/migrerMessageVersCoffre,
    Messagerie/views.py."""
    return _obtenir_fernet().encrypt(texte.encode()).decode()


def dechiffrer(texte_chiffre: str) -> str | None:
    """None (jamais d'exception brute) si le jeton est corrompu ou provient
    d'un serveur avec une clé différente — voir _serialiseMessageAdmin, qui
    traite ça comme un message illisible plutôt que de faire échouer tout
    l'affichage de la liste."""
    try:
        return _obtenir_fernet().decrypt(texte_chiffre.encode()).decode()
    except InvalidToken:
        return None
