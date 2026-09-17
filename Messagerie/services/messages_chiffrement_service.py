# ── IMPORTS ───────────────────────────────────────────────────────────────────
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

# Chiffrement de la messagerie privée 1:1 (voir Message, Messagerie/models.py)
# — remplace l'ancien chiffrement de bout en bout géré par chaque navigateur
# (clé jamais lisible par le serveur). Décision explicitement inversée par le
# propriétaire : les messages doivent rester accessibles immédiatement sur
# n'importe quel appareil/navigateur dès la connexion, sans jamais redemander
# de mot de passe ni de clé côté client — impossible par nature avec un vrai
# chiffrement de bout en bout (il faut alors reconstituer la clé privée sur
# chaque nouvel appareil, ce qui a motivé le message "Clé de chiffrement non
# disponible sur cet appareil pour le moment" que l'ancien système affichait).
# Même compromis déjà en place pour la messagerie support, voir
# support_chiffrement_service.py/SUPPORT_MASTER_KEY : chiffré avec une clé que
# le serveur applicatif détient lui-même (protège contre un accès base de
# données brut — fuite/vol de la DB —, pas contre le serveur applicatif
# lui-même). Clé séparée de SUPPORT_MASTER_KEY (voir MESSAGES_MASTER_KEY,
# BackendRekoltHt/settings/base.py) : une fuite de l'une n'expose pas l'autre.


class ErreurChiffrementMessages(Exception):
    """MESSAGES_MASTER_KEY absente/invalide, ou message corrompu."""


def _obtenir_fernet():
    cle = settings.MESSAGES_MASTER_KEY
    if not cle:
        raise ErreurChiffrementMessages(
            "MESSAGES_MASTER_KEY n'est pas configurée (voir .env/.env.dev) — "
            "impossible de chiffrer ou déchiffrer un message."
        )
    try:
        return Fernet(cle.encode() if isinstance(cle, str) else cle)
    except (ValueError, TypeError) as e:
        raise ErreurChiffrementMessages(f"MESSAGES_MASTER_KEY invalide : {e}")


def chiffrer(texte: str) -> str:
    """Chiffre le contenu d'un Message avant enregistrement — voir
    envoyerMessage, Messagerie/views.py."""
    return _obtenir_fernet().encrypt(texte.encode()).decode()


def dechiffrer(texte_chiffre: str) -> str | None:
    """None (jamais d'exception brute) si le jeton est corrompu ou provient
    d'un serveur avec une clé différente — voir _serialiseMessage, qui
    traite ça comme un message illisible plutôt que de faire échouer tout
    l'affichage de la conversation."""
    try:
        return _obtenir_fernet().decrypt(texte_chiffre.encode()).decode()
    except InvalidToken:
        return None
