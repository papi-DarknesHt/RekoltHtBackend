"""
ASGI config for BackendRekoltHt project.

ASGI (Asynchronous Server Gateway Interface) remplace WSGI pour gérer :
  - les requêtes HTTP classiques (comme WSGI)
  - les connexions WebSocket en temps réel (notifications, chat…)

Le serveur ASGI utilisé est uvicorn :
  uvicorn BackendRekoltHt.asgi:application --reload
"""

import os

from django.core.asgi import get_asgi_application     # application Django standard pour les requêtes HTTP
from channels.routing import ProtocolTypeRouter, URLRouter  # routage selon le protocole (http / websocket)
from channels.auth import AuthMiddlewareStack          # middleware qui injecte l'utilisateur Django dans le scope WebSocket
from Api.routing import websocket_urlpatterns          # liste des routes WebSocket définies dans Api/routing.py

# pointer vers le module de configuration Django (dev par défaut, voir
# BackendRekoltHt/settings/ — surchargeable via la variable d'environnement
# DJANGO_SETTINGS_MODULE, ex: BackendRekoltHt.settings.prod sur Render)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'BackendRekoltHt.settings.dev')

# ProtocolTypeRouter dirige chaque connexion vers le bon gestionnaire selon son type
application = ProtocolTypeRouter({

    # requêtes HTTP classiques → Django gère comme d'habitude
    'http': get_asgi_application(),

    # connexions WebSocket → authentification Django + routage vers Api/routing.py
    # AuthMiddlewareStack permet de lire le token de session pour identifier l'utilisateur WebSocket
    # AllowedHostsOriginValidator est commenté pour faciliter le développement local
    'websocket': AuthMiddlewareStack(
        URLRouter(websocket_urlpatterns)
    ),
})
