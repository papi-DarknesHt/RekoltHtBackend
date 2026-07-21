"""
Réglages spécifiques à l'environnement de DÉVELOPPEMENT (poste local).

Charge .env.dev puis importe les réglages communs de base.py, avant de
définir DEBUG=True, la base SQLite (comportement actuel du projet) et le
stockage local des fichiers médias.

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

ALLOWED_HOSTS = ['localhost', '127.0.0.1']
# autoriser la communication entre react et django
CORS_ALLOWED_ORIGINS = ["http://localhost:5173","https://rekolthtfront.onrender.com"]
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_METHODS = [
    "GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"
]


# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# Fichiers médias stockés directement sur le disque local en développement,
# servis par Django lui-même (voir BackendRekoltHt/urls.py, actif si DEBUG=True)
MEDIA_ROOT = BASE_DIR / 'media'
