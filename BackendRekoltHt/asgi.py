"""
ASGI config for BackendRekoltHt project.

ASGI (Asynchronous Server Gateway Interface) remplace WSGI pour gérer :
  - les requêtes HTTP classiques (comme WSGI)
  - les connexions WebSocket en temps réel (notifications, chat…)

Le serveur ASGI utilisé est uvicorn :
  uvicorn BackendRekoltHt.asgi:application --reload
"""

import os
import sys

# Windows lance ce process avec un stdout/stderr en page de code cp1252 (pas
# UTF-8) : le moindre print() contenant un caractère hors cp1252 (ex. un
# séparateur "──" en Unicode, ou du texte OCR/Kreyòl imprévu) lève
# UnicodeEncodeError et FAIT ÉCHOUER tout le pipeline en cours (constaté sur
# la vérification KYC — voir Registration/views.py::_lancer_pipeline_ocr) : un
# simple log de debug ne doit jamais pouvoir planter une opération critique.
# reconfigure() (Python 3.7+) bascule stdout/stderr en UTF-8 sans toucher au
# comportement sur Linux/macOS (déjà en UTF-8 there) ; errors='replace' en
# dernier filet si un caractère venait à être malgré tout inencodable.
for _flux in (sys.stdout, sys.stderr):
    if hasattr(_flux, 'reconfigure'):
        _flux.reconfigure(encoding='utf-8', errors='replace')

from django.core.asgi import get_asgi_application     # application Django standard pour les requêtes HTTP
from channels.routing import ProtocolTypeRouter, URLRouter  # routage selon le protocole (http / websocket)
from channels.auth import AuthMiddlewareStack          # middleware qui injecte l'utilisateur Django dans le scope WebSocket
from Api.routing import websocket_urlpatterns          # liste des routes WebSocket définies dans Api/routing.py

# pointer vers le module de configuration Django (voir BackendRekoltHt/settings/
# — seul settings.dev existe, ce projet n'étant pas déployé en production)
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
