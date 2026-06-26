import json
from  channels.generic.websocket import AsyncWebsocketConsumer

class GlobalConsumer(AsyncWebsocketConsumer):
    # Connection websocket
    async def connect(self):
        await self.channel_layer.group_add("global", self.channel_name)
        await self.accept()
    # deconnection websocket
    async def disconnect(self, close_code):
        await self.channel_layer.group_discard("global", self.channel_name)

    async def global_update(self, event):
        await self.send(text_data = json.dumps(event["message"]))