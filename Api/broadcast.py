from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

channel_layer = get_channel_layer()

def broadcast(event_type, data):
    async_to_sync(channel_layer.group_send)(
        "global",
        {
            "type": "global_update",
            "message": {
                "type": event_type,
                "data": data
            }
        }
    )


def broadcast_to_user(user_id, event_type, data):
    """Comme broadcast(), mais envoie uniquement au groupe personnel de cet
    utilisateur (voir Api/consumers.py::GlobalConsumer.connect) — pour les
    évènements privés (nouveaux messages de messagerie) qui ne doivent pas
    être diffusés à "global"."""
    async_to_sync(channel_layer.group_send)(
        f"user_{user_id}",
        {
            "type": "global_update",
            "message": {
                "type": event_type,
                "data": data
            }
        }
    )


def broadcast_to_admins(event_type, data):
    """Comme broadcast(), mais envoie uniquement au groupe "admins" (voir
    Api/consumers.py::GlobalConsumer.connect) — pour les messages vendeur->admin
    (voir Messagerie/models.py::MessageAdmin), qui ne doivent être visibles que
    des comptes admin, pas de tout visiteur connecté comme "global"."""
    async_to_sync(channel_layer.group_send)(
        "admins",
        {
            "type": "global_update",
            "message": {
                "type": event_type,
                "data": data
            }
        }
    )