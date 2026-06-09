from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from Api.broadcast import broadcast
from .models import Utilisateur, Profil


# ── CRÉER LE PROFIL AUTOMATIQUEMENT ──────────────────────────────────────────
# Déclenché automatiquement après chaque création d'un Utilisateur
@receiver(post_save, sender=Utilisateur)
def creer_profil(sender, instance, created, **kwargs):
    """
    Crée un Profil vide automatiquement quand un Utilisateur est créé.
    Garantit que chaque utilisateur a toujours un profil.
    """
    if created:
        Profil.objects.create(utilisateur=instance)


# ── BROADCAST WEBSOCKET — NOUVEL UTILISATEUR ─────────────────────────────────
# Notifie React en temps réel quand un utilisateur est créé ou modifié
@receiver(post_save, sender=Utilisateur)
def broadcast_utilisateur(sender, instance, created, **kwargs):
    """
    Envoie une notification WebSocket au frontend React
    à chaque création ou modification d'un utilisateur.
    """
    event_type = "utilisateur.created" if created else "utilisateur.updated"

    broadcast(event_type, {
        "id":     str(instance.id),   # UUID converti en string
        "nom":    instance.nom,
        "prenom": instance.prenom,
        "email":  instance.email,
    })


# ── BROADCAST WEBSOCKET — UTILISATEUR SUPPRIMÉ ───────────────────────────────
@receiver(post_delete, sender=Utilisateur)
def broadcast_utilisateur_supprime(sender, instance, **kwargs):
    """
    Notifie React quand un utilisateur est supprimé.
    """
    broadcast("utilisateur.deleted", {
        "id": str(instance.id),
    })


# ── BROADCAST WEBSOCKET — PROFIL MIS À JOUR ──────────────────────────────────
@receiver(post_save, sender=Profil)
def broadcast_profil(sender, instance, created, **kwargs):
    """
    Notifie React quand un profil est mis à jour.
    On ne broadcast pas la création car elle se fait
    automatiquement avec l'utilisateur.
    """
    if not created:
        broadcast("profil.updated", {
            "user_id":   str(instance.utilisateur.id),
            "commune":   instance.commune,
            "adresse":   instance.adresse,
            "ville":     instance.ville,
            "photo":     instance.photo_profil.url if instance.photo_profil else None,
            "pays":      instance.pays,
            "latitude":  instance.latitude,
            "longitude": instance.longitude,
        })


# ── BROADCAST WEBSOCKET — PROFIL SUPPRIMÉ ────────────────────────────────────
@receiver(post_delete, sender=Profil)
def broadcast_profil_supprime(sender, instance, **kwargs):
    """
    Notifie React quand un profil est supprimé.
    """
    broadcast("profil.deleted", {
        "user_id": str(instance.utilisateur.id),
    })