import json
from urllib.parse import parse_qs

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async


class GlobalConsumer(AsyncWebsocketConsumer):
    # Connection websocket
    async def connect(self):
        await self.channel_layer.group_add("global", self.channel_name)

        # rejoint aussi un groupe personnel "user_<id>" si un token valide est
        # fourni en query string (?token=...) — permet d'envoyer des
        # évènements privés (nouveaux messages de messagerie, voir
        # Messagerie/signals.py) à CE seul utilisateur, sans les diffuser à
        # "global" que tout visiteur connecté reçoit (produit.*, categorie.*...)
        self.groupe_utilisateur = None
        # rejoint aussi "admins" si le compte connecté a le rôle admin — permet
        # de diffuser les messages vendeur->admin (voir Messagerie/models.py
        # ::MessageAdmin) à tous les admins connectés, sans les envoyer à "global"
        self.est_admin = False
        utilisateur = await self._utilisateur_depuis_token()
        if utilisateur:
            self.groupe_utilisateur = f"user_{utilisateur.id}"
            await self.channel_layer.group_add(self.groupe_utilisateur, self.channel_name)
            self.est_admin = await self._est_admin(utilisateur)
            if self.est_admin:
                await self.channel_layer.group_add("admins", self.channel_name)

        await self.accept()

    # deconnection websocket
    async def disconnect(self, close_code):
        await self.channel_layer.group_discard("global", self.channel_name)
        if self.groupe_utilisateur:
            await self.channel_layer.group_discard(self.groupe_utilisateur, self.channel_name)
        if self.est_admin:
            await self.channel_layer.group_discard("admins", self.channel_name)

    async def global_update(self, event):
        await self.send(text_data = json.dumps(event["message"]))

    @database_sync_to_async
    def _utilisateur_depuis_token(self):
        from Registration.models import Token
        qs = parse_qs(self.scope["query_string"].decode())
        cle = qs.get("token", [None])[0]
        if not cle:
            return None
        try:
            return Token.objects.select_related("utilisateur").get(cle=cle).utilisateur
        except Token.DoesNotExist:
            return None

    @database_sync_to_async
    def _est_admin(self, utilisateur):
        return getattr(getattr(utilisateur, 'profil', None), 'role', None) == 'admin'
