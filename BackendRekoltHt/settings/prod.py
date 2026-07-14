"""
Réglages spécifiques à l'environnement de PRODUCTION (Render).

Charge .env.prod (utile pour tester la config prod en local) puis importe les
réglages communs de base.py, avant de définir DEBUG=False, la base PostgreSQL
et le stockage externe des fichiers médias (Cloudinary).

Sur Render, aucun fichier .env.prod n'est déployé : les variables sont
renseignées directement dans le tableau de bord Render (Environment) et sont
donc déjà présentes dans les vraies variables d'environnement au démarrage.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# En local, .env.prod peut servir à tester la config prod avant déploiement.
# Sur Render, ce fichier n'existe pas : load_dotenv() ne trouve rien et ne fait
# rien (pas d'erreur) — les vraies variables d'environnement Render sont déjà
# présentes dans os.environ et donc lues normalement par les os.getenv() ci-dessous.
load_dotenv(BASE_DIR / '.env.prod')

from .base import *  # noqa: F401,F403 — réglages communs (INSTALLED_APPS, MIDDLEWARE, CORS, ...)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = False

# domaines autorisés, séparés par une virgule dans la variable d'environnement
# ex: ALLOWED_HOSTS=rekolthtbackend.onrender.com,monautredomaine.com
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'rekolthtbackend.onrender.com').split(',')


# Database — PostgreSQL via variables d'environnement (fournies par Render)
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases
DATABASES = {
    'default': {
        'ENGINE':   'django.db.backends.postgresql',
        'NAME':     os.getenv('DB_NAME'),
        'USER':     os.getenv('DB_USER'),
        'PASSWORD': os.getenv('DB_PASSWORD'),
        'HOST':     os.getenv('DB_HOST'),
        'PORT':     os.getenv('DB_PORT', '5432'),
    }
}


# ══════════════════════════════════════════════════════════════════════════════
#  POINTS CRITIQUES SQLite (dev) ↔ PostgreSQL (prod) — À LIRE AVANT DÉPLOIEMENT
# ══════════════════════════════════════════════════════════════════════════════
#
# 1. CHAMPS DE TYPE TABLEAU
#    ArrayField (django.contrib.postgres.fields) n'existe QUE sous PostgreSQL :
#    il lève une erreur dès la création des migrations sous SQLite (dev). Pour
#    un futur champ comme "categories_produits" (liste de catégories sur un
#    Produit), NE PAS utiliser ArrayField : utiliser JSONField
#    (django.db.models.JSONField), qui fonctionne identiquement sous SQLite ET
#    PostgreSQL — donc sans divergence de comportement entre dev.py et prod.py.
#    Exemple : categories_produits = models.JSONField(default=list, blank=True)
#
# 2. PRÉCISION DES COORDONNÉES GPS
#    Profil.latitude/longitude et Entreprise.latitude/longitude (voir
#    Registration/models.py) sont des FloatField. SQLite stocke un FloatField
#    en colonne REAL (flottant texte, précision variable selon la valeur)
#    tandis que PostgreSQL le stocke en DOUBLE PRECISION (IEEE 754, cohérent).
#    Les deux moteurs peuvent donc arrondir différemment des coordonnées GPS de
#    forte précision. AVANT de migrer vers DecimalField (précision garantie via
#    NUMERIC en PostgreSQL) parce qu'une précision stricte est requise en prod,
#    tester avec de VRAIES valeurs GPS dans les deux environnements (dev SQLite
#    ET prod PostgreSQL) pour constater l'écart réel plutôt que de le supposer.
#
# 3. STOCKAGE DES FICHIERS MÉDIAS (MEDIA_ROOT)
#    MEDIA_ROOT (disque local du serveur) ne doit JAMAIS être utilisé en
#    production sur Render : le système de fichiers y est éphémère — tout
#    fichier écrit sur disque (photos_profil/, logos_entreprise/) est
#    DÉFINITIVEMENT PERDU à chaque redéploiement ou redémarrage du service.
#    Solution retenue : stockage externe chez Cloudinary via les packages
#    django-cloudinary-storage + cloudinary (voir requirements.txt), plutôt que
#    boto3/S3, car Cloudinary gère aussi le redimensionnement d'images utile
#    pour les photos de profil/logos. Le SDK cloudinary lit automatiquement la
#    variable d'environnement CLOUDINARY_URL (voir .env.prod.example) : aucune
#    configuration manuelle des clés (cloud_name/api_key/api_secret) n'est
#    nécessaire ici. Important : cela ne change AUCUNE ligne de code des
#    modèles — les ImageField (Profil.photo_profil, Entreprise.logo) restent
#    inchangés dans Registration/models.py ; seul DEFAULT_FILE_STORAGE change,
#    Django route alors les fichiers vers Cloudinary de façon transparente.
INSTALLED_APPS = INSTALLED_APPS + [
    'cloudinary_storage',
    'cloudinary',
]
DEFAULT_FILE_STORAGE = 'cloudinary_storage.storage.MediaCloudinaryStorage'


# ── AVANT TOUT DÉPLOIEMENT ────────────────────────────────────────────────────
# Vérifier qu'aucune migration n'est manquante par rapport à ces réglages prod
# (nécessite les variables DB_* d'accès à la base PostgreSQL de production) :
#   python manage.py migrate --check --settings=BackendRekoltHt.settings.prod
