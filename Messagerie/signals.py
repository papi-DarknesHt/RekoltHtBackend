from django.db.models.signals import post_save
from django.dispatch import receiver

from Api.broadcast import broadcast_to_user, broadcast_to_admins
from .models import Message, MessageSupport


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


# ── BROADCAST WEBSOCKET — NOUVEAU MESSAGE VENDEUR->ADMINS ────────────────────
@receiver(post_save, sender=MessageSupport)
def broadcast_message_admin_cree(sender, instance, created, **kwargs):
    """
    Notifie tous les admins connectés (groupe "admins", voir Api/consumers.py)
    dès qu'un vendeur envoie un nouveau message — la réponse (qui retire le
    message de la file des autres admins) est diffusée séparément dans
    repondreMessageAdmin (Messagerie/views.py), pas ici : ce signal ne
    concerne que la CRÉATION.
    """
    if not created:
        return

    from .views import _serialiseMessageAdmin  # import différé : évite un cycle views <-> signals
    broadcast_to_admins('message_admin.created', _serialiseMessageAdmin(instance))
