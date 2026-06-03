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