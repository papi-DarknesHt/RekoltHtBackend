from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from Api.broadcast import broadcast
from .models import Utilisateur, Entreprise, Profil, DemandeVerification


# ── CRÉER LE PROFIL AUTOMATIQUEMENT ──────────────────────────────────────────
# Déclenché après chaque création d'un compte : Utilisateur (ou Entreprise, qui
# en hérite via une table distincte et envoie donc son propre signal post_save).
# Vendeur/Acheteur sont des proxys de Utilisateur : ils partagent son signal.
@receiver(post_save, sender=Utilisateur)
@receiver(post_save, sender=Entreprise)
def creer_profil(sender, instance, created, **kwargs):
    """
    Crée un Profil vide automatiquement quand un compte est créé.
    Garantit que chaque utilisateur a toujours un profil.
    Une Entreprise démarre 'acheteur' comme n'importe quel compte — elle peut
    ensuite devenir 'vendeur' via Profil.convertir_en_vendeur().
    """
    if created:
        Profil.objects.create(utilisateur=instance)


# ── BROADCAST WEBSOCKET — NOUVEL UTILISATEUR ─────────────────────────────────
# Notifie React en temps réel quand un utilisateur est créé ou modifié
@receiver(post_save, sender=Utilisateur)
@receiver(post_save, sender=Entreprise)
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


# ── BROADCAST WEBSOCKET — DEMANDE DE VÉRIFICATION MISE À JOUR ────────────────
@receiver(post_save, sender=DemandeVerification)
def broadcast_verification(sender, instance, created, **kwargs):
    """
    Notifie React en temps réel à chaque changement sur une demande de
    vérification KYC (étape 08) — en plus de l'email envoyé par
    marquer_verifie()/marquer_echoue() (Registration/models.py). On ne
    diffuse pas la création initiale (en_attente, rien de nouveau à afficher),
    même logique que broadcast_profil ci-dessus.
    """
    if created:
        return
    broadcast("verification.updated", {
        "utilisateur_id": str(instance.utilisateur_id),
        "type_demandeur": instance.type_demandeur,
        "statut":         instance.statut,
        "motif_echec":    instance.motif_echec,
    })