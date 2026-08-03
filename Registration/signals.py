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

    Le tout premier compte créé sur la plateforme (aucun autre Utilisateur en
    base avant celui-ci) devient automatiquement 'admin' — sans ça, personne
    n'aurait accès au dashboard admin/à la gestion des catégories sur une
    instance fraîchement déployée, faute d'un moyen de promouvoir un compte
    autrement que par un admin déjà existant.
    """
    if not created:
        return
    role = 'admin' if Utilisateur.objects.count() == 1 else 'acheteur'
    Profil.objects.create(utilisateur=instance, role=role)


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

    # même champs que _serialiseUtilisateur (Registration/views.py) + role,
    # pour que le dashboard admin (AdminDashboard.jsx) affiche un utilisateur
    # complet dès l'évènement temps réel, sans attendre un rechargement de
    # page pour récupérer telephone/est_bloquer/role. instance.profil existe
    # déjà à ce point : creer_profil (même signal post_save, déclaré avant
    # dans ce fichier donc appelé en premier) l'a créé juste avant.
    broadcast(event_type, {
        # id en entier (comme _serialiseUtilisateur, Registration/views.py) —
        # pas de str() : Utilisateur.id est un AutoField, pas un UUID, et le
        # frontend (applyListEvent.js) compare les id avec ===, donc un
        # mismatch de type casserait le rapprochement avec la liste REST
        "id":               instance.id,
        "nom":              instance.nom,
        "prenom":           instance.prenom,
        "email":            instance.email,
        "telephone":        instance.telephone,
        "est_actif":        instance.est_actif,
        "est_bloquer":      instance.est_bloquer,
        "desactive_par_signalements": instance.desactive_par_signalements,
        "date_inscription": instance.date_inscription.isoformat(),
        "role":             instance.profil.role,
    })


# ── BROADCAST WEBSOCKET — UTILISATEUR SUPPRIMÉ ───────────────────────────────
@receiver(post_delete, sender=Utilisateur)
def broadcast_utilisateur_supprime(sender, instance, **kwargs):
    """
    Notifie React quand un utilisateur est supprimé.
    """
    broadcast("utilisateur.deleted", {
        "id": instance.id,
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
            "user_id":   instance.utilisateur.id,
            "role":      instance.role,  # ex: acheteur -> vendeur via convertir_en_vendeur()/KYC (marquer_verifie)
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
        "user_id": instance.utilisateur.id,
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