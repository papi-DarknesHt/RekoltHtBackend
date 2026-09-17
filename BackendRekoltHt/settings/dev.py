"""
Réglages de DÉVELOPPEMENT (poste local) — seul environnement du projet,
celui-ci n'étant pas déployé en production (pas de settings/prod.py, pas de
Docker/Render).

Charge .env.dev puis importe les réglages communs de base.py, avant de
définir DEBUG=True, la base PostgreSQL partagée (Supabase — voir DATABASES
ci-dessous) et le stockage local des fichiers médias.

Utilisé par défaut par manage.py / asgi.py tant que la variable d'environnement
DJANGO_SETTINGS_MODULE n'est pas explicitement définie.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# charge les variables du fichier .env.dev (clés secrètes, identifiants OAuth, ...)
# AVANT d'importer base.py, pour que ses os.getenv() lisent déjà les bonnes valeurs
load_dotenv(BASE_DIR / '.env.dev')

from .base import *  # noqa: F401,F403 — réglages communs (INSTALLED_APPS, MIDDLEWARE, CORS, ...)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = ['localhost', '127.0.0.1','192.168.1.4','crudely-bounce-universal.ngrok-free.dev']

# adresse publique du backend lui-même — sert UNIQUEMENT à construire une URL
# absolue de média (photo produit, ...) hors d'une requête HTTP (diffusion
# WebSocket depuis un signal, voir Messagerie/views.py::_url_absolue_media) :
# request.build_absolute_uri() n'existe que dans une vue. Redéfinissable via
# la variable d'environnement du même nom si le backend est testé depuis un
# autre appareil du réseau local (voir ALLOWED_HOSTS ci-dessus, ex. 192.168.1.4)
BACKEND_BASE_URL = os.getenv('BACKEND_BASE_URL', 'http://127.0.0.1:8000')
# autoriser la communication entre react et django
CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "https://rekolthtfront.onrender.com",
    "http://192.168.1.4:5173",
    "https://crudely-bounce-universal.ngrok-free.dev"
]
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_METHODS = [
    "GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"
]


# Database — PostgreSQL partagé (Supabase), voir DB_* dans .env.dev.
# Partagée entre toutes les machines de l'équipe : plus de db.sqlite3 local,
# les mêmes données (comptes, produits, ...) sont vues par tout poste
# possédant ce .env.dev. sslmode=require et CONN_MAX_AGE=0 : mêmes raisons
# que documentées auparavant pour l'environnement de production (Supabase
# exige une connexion chiffrée, et son pooler gère seul la réutilisation des
# connexions — CONN_MAX_AGE>0 double le pooling et épuise le nombre de
# connexions autorisées côté Supabase sous charge).
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE':   'django.db.backends.postgresql',
        'NAME':     os.getenv('DB_NAME'),
        'USER':     os.getenv('DB_USER'),
        'PASSWORD': os.getenv('DB_PASSWORD'),
        'HOST':     os.getenv('DB_HOST'),
        'PORT':     os.getenv('DB_PORT', '5432'),
        'OPTIONS': {'sslmode': 'require'},
        'CONN_MAX_AGE': int(os.getenv('DB_CONN_MAX_AGE', '0')),
    }
}

# Fichiers médias stockés directement sur le disque local en développement,
# servis par Django lui-même (voir BackendRekoltHt/urls.py, actif si DEBUG=True)
MEDIA_ROOT = BASE_DIR / 'media'
