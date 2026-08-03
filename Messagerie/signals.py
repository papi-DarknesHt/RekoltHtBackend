from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from Api.broadcast import broadcast_to_user
from .models import Conversation, Message, MessageSupport


# ── TOUCHER LA CONVERSATION PARENTE ───────────────────────────────────────────
@receiver(post_save, sender=Message)
def toucher_conversation(sender, instance, created, **kwargs):
    """Met à jour Conversation.date_maj à chaque nouveau message, pour que la
    liste des conversations (voir mesConversations, Messagerie/views.py) reste
    triée par activité récente."""
    if created:
        instance.conversation.save()


# ── BROADCAST WEBSOCKET — NOUVEAU MESSAGE ─────────────────────────────────────
@receiver(post_save, sender=Message)
def broadcast_message(sender, instance, created, **kwargs):
    """
    Notifie les DEUX participants en temps réel (groupe WebSocket personnel
    "user_<id>", voir Api/consumers.py) — contrairement au reste de ce projet
    (broadcast_produit, broadcast_utilisateur...), un message privé ne doit
    PAS être diffusé au groupe "global" que tout visiteur connecté reçoit.
    """
    if not created:
        return

    from .views import _serialiseMessage  # import différé : évite un cycle views <-> signals

    conversation = instance.conversation
    payload = _serialiseMessage(instance)
    for participant_id in (conversation.participant_a_id, conversation.participant_b_id):
        broadcast_to_user(participant_id, 'message.created', payload)


# ── BROADCAST WEBSOCKET — MESSAGE SUPPRIMÉ (par un admin, voir supprimerMessageAdmin) ──
@receiver(post_delete, sender=Message)
def broadcast_message_supprime(sender, instance, **kwargs):
    """
    Notifie les deux participants en temps réel qu'un message a été supprimé
    (seul un admin peut supprimer un message, voir Messagerie/views.py::
    supprimerMessageAdmin) — même diffusion personnelle que broadcast_message
    ci-dessus, jamais au groupe "global".

    La conversation peut avoir été supprimée dans le même CASCADE (ex: un
    admin supprime le compte d'un des deux participants, voir
    Registration/views.py::supprimerUtilisateurAdmin) — auquel cas la
    diffusion n'a plus de sens, même garde que broadcast_avis_supprime
    (Produits/signals.py).
    """
    conversation = Conversation.objects.filter(id=instance.conversation_id).first()
    if not conversation:
        return
    payload = {'id': instance.id, 'conversation_id': instance.conversation_id}
    for participant_id in (conversation.participant_a_id, conversation.participant_b_id):
        broadcast_to_user(participant_id, 'message.deleted', payload)


# ── BROADCAST WEBSOCKET — NOUVEAU MESSAGE VENDEUR->ADMINS ────────────────────
@receiver(post_save, sender=MessageSupport)
def broadcast_message_admin_cree(sender, instance, created, **kwargs):
    """
    Notifie tous les admins dès qu'un vendeur envoie un nouveau message — la
    réponse (qui retire le message de la file des autres admins) est diffusée
    séparément dans repondreMessageAdmin (Messagerie/views.py), pas ici : ce
    signal ne concerne que la CRÉATION.

    Diffusion individuelle (broadcast_to_user par admin) et non
    broadcast_to_admins group-wide : le message est chiffré en enveloppe
    (voir MessageSupport dans Messagerie/models.py), chaque admin a donc sa
    propre copie chiffrée de la clé du message, jamais la même — un seul
    payload partagé ne conviendrait à personne.
    """
    if not created:
        return

    from Registration.models import Utilisateur  # import différé : évite un cycle avec Registration
    from .views import _serialiseMessageAdmin     # import différé : évite un cycle views <-> signals
    for admin in Utilisateur.objects.filter(profil__role='admin'):
        broadcast_to_user(admin.id, 'message_admin.created', _serialiseMessageAdmin(instance, admin))
