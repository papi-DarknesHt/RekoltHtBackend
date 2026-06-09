================================================================================
                    PROJET BACKEND REKOLTHT
================================================================================

DESCRIPTION DU PROJET
================================================================================
BackendRekoltHt est un backend Django qui gère une plateforme de mise en relation 
entre acheteurs et vendeurs en Haïti. 
Le projet utilise Django REST Framework pour fournir une API REST et Django 
Channels pour la communication en temps réel via WebSocket.


FONCTIONNALITÉS PRINCIPALES
================================================================================

1. GESTION DES UTILISATEURS
   - Inscription et création de compte
   - Authentification et connexion/déconnexion
   - Système de tokens d'authentification
   - Gestion des mots de passe avec hashage sécurisé (SHA256 + salt)
   - Profils utilisateur avec informations personnelles et géolocalisation
   - Rôles d'utilisateurs : acheteur, vendeur, admin

2. SYSTÈME DE PROFILS
   - Profils détaillés pour chaque utilisateur
   - Informations personnelles (nom, prénom, email, téléphone)
   - Adresse, commune, ville et pays
   - Géolocalisation avec longitude et latitude
   - Support des photos de profil
   - Conversion entre rôles (acheteur ↔ vendeur)
   - Calcul de distance entre deux profils en km


3. COMMUNICATION EN TEMPS RÉEL
   - WebSocket via Django Channels
   - Système de notifications globales
   - Broadcasting de messages en temps réel

4. API REST
   - Django REST Framework pour les endpoints API
   - Authentification par tokens
   - CORS configuré pour communiquer avec React (localhost:5173)
   - Endpoints pour Registration et RekoltHt


STACK TECHNOLOGIQUE
================================================================================

Framework Principal:
  - Django 6.0.4 - Framework web Python
  - Django REST Framework 3.17.1 - Framework API REST
  - Django Channels 4.3.2 - Support WebSocket

Base de Données:
  - SQLite3 (dev/test)

Bibliothèques Utilitaires:
  - django-cors-headers 4.9.0 - Gestion CORS
  - Uvicorn 0.48.0 - Serveur ASGI
  - Geopy - Calcul de distances géographiques
  - python-dateutil 2.9.0 - Manipulation de dates
  - python-dotenv 1.2.2 - Gestion des variables d'environnement
  - PyYAML 6.0.3 - Traitement YAML
  - Pandas 3.0.3 - Analyse de données
  - NumPy 2.4.4 - Calculs numériques

Frontend (communiquant avec ce backend):
  - React (sur localhost:5173)


STRUCTURE DU PROJET
================================================================================

BackendRekoltHt/
├── db.sqlite3                  # Base de données SQLite (développement)
├── manage.py                   # Utilitaire CLI Django
├── requirements.txt            # Dépendances Python du projet
├── README.txt                  # Ce fichier
│
├── BackendRekoltHt/           # Configuration principale du projet Django
│   ├── __init__.py
│   ├── settings.py            # Configuration et paramètres globaux
│   ├── urls.py                # Routes URL principales
│   ├── asgi.py                # Configuration ASGI pour Channels/WebSocket
│   └── wsgi.py                # Configuration WSGI (déploiement)
│
├── Api/                       # App pour l'API WebSocket et communications
│   ├── __init__.py
│   ├── apps.py                # Configuration de l'app Api
│   ├── consumers.py           # Consommateurs WebSocket (GlobalConsumer)
│   ├── routing.py             # Routes WebSocket
│   ├── broadcast.py           # Logique de broadcast des messages
│   └── migrations/
│       └── __init__.py
│
├── Registration/              # App pour l'inscription/authentification
│   ├── __init__.py
│   ├── apps.py                # Configuration de l'app Registration
│   ├── models.py              # Modèles Utilisateur et Profil
│   ├── views.py               # Vues API (sinscrire, seConnecter, etc.)
│   ├── admin.py               # Configuration admin Django
│   ├── signals.py             # Signaux Django (creation auto du profil)
│   ├── tests.py               # Tests unitaires
│   ├── urls.py                # Routes de Registration
│   └── migrations/
│       ├── __init__.py
│       └── 0001_initial.py    # Migration initiale
│
├── RekoltHt/                  # App pour la gestion des annonces/ventes
│   ├── __init__.py
│   ├── apps.py                # Configuration de l'app RekoltHt
│   ├── models.py              # Modèles pour les annonces (vide actuellement)
│   ├── views.py               # Vues API pour RekoltHt
│   ├── admin.py               # Configuration admin Django
│   ├── signals.py             # Signaux Django
│   ├── tests.py               # Tests unitaires
│   ├── urls.py                # Routes de RekoltHt
│   └── migrations/
│       └── __init__.py
│
├── Produits/                  # App pour la gestion des produits
│   ├── __init__.py
│   ├── apps.py                # Configuration de l'app Produits
│   ├── models.py              # Modèles Produit (vide actuellement)
│   ├── views.py               # Vues API pour Produits
│   ├── admin.py               # Configuration admin Django
│   ├── tests.py               # Tests unitaires
│   └── migrations/
│       └── __init__.py
│
└── htmlcov/                   # Rapports de couverture de code (à ignorer)
    └── [fichiers HTML de couverture de tests]


DESCRIPTION DÉTAILLÉE DES FICHIERS
================================================================================

FICHIERS DE CONFIGURATION RACINE
──────────────────────────────────

db.sqlite3
  Description: Base de données SQLite utilisée en développement
  Contenu: Toutes les données du projet (utilisateurs, profils, produits)
  Utilisation: Stockage persistant des données
  Note: À ne pas committer en production (utiliser PostgreSQL ou MySQL)

manage.py
  Description: Script utilitaire en ligne de commande de Django
  Utilisation: Exécute les commandes Django (runserver, migrate, createsuperuser)
  Exemple: python manage.py runserver

requirements.txt
  Description: Liste de toutes les dépendances Python du projet
  Contenu: Noms et versions des packages nécessaires
  Installation: pip install -r requirements.txt


DOSSIER BackendRekoltHt/ (Configuration Principale)
────────────────────────────────────────────────────

__init__.py
  Description: Marqueur de package Python (peut être vide)
  Utilité: Indique à Python que le dossier est un package

settings.py
  Description: Configuration centralisée du projet Django
  Contient:
    - Configuration de la base de données (db.sqlite3)
    - Applications installées (Api, Registration, RekoltHt, Produits)
    - Middleware pour sécurité et traitements HTTP
    - Configuration CORS pour communiquer avec React (localhost:5173)
    - Paramètres REST Framework (authentification par tokens)
    - Configuration ASGI/Channels pour WebSocket
    - Configuration des templates et contextes
  Note critique: NE PAS MODIFIER en production sans mesures de sécurité

urls.py
  Description: Routeur principal des URLs du projet
  Routes principales:
    - /admin/ → Interface administrateur Django
    - /api/ → Endpoints API RekoltHt
    - /Registration/ → Endpoints d'authentification et inscription
  Utilité: Point d'entrée pour toutes les requêtes HTTP

asgi.py
  Description: Point d'entrée ASGI (Asynchronous Server Gateway Interface)
  Fonction: Configure le routage entre protocoles HTTP et WebSocket
  Contient:
    - Configuration de get_asgi_application() pour HTTP
    - Configuration de URLRouter pour WebSocket via Channels
    - AuthMiddlewareStack pour authentifier les connexions WebSocket
  Utilité: Utilisé avec Uvicorn pour le déploiement

wsgi.py
  Description: Point d'entrée WSGI (Web Server Gateway Interface)
  Fonction: Configure l'application pour déploiement traditionnel
  Utilité: Utilisé avec Gunicorn, Apache, Nginx, etc.
  Note: asgi.py est préféré pour ce projet en raison des WebSockets


DOSSIER Api/ (WebSocket et Communication Temps Réel)
──────────────────────────────────────────────────────

__init__.py
  Description: Marqueur de package

apps.py
  Description: Configuration de l'application Api
  Contient: Métadonnées et configuration de l'app

consumers.py
  Description: Consommateurs WebSocket (comme les views pour WebSocket)
  Classe: GlobalConsumer
    - connect(): Accepte la connexion WebSocket et ajoute au groupe "global"
    - disconnect(): Nettoie la déconnexion
    - global_update(): Reçoit et envoie les messages globaux
  Utilité: Gère la communication en temps réel avec les clients

routing.py
  Description: Routes WebSocket du projet
  Routes:
    - ws/global/ → GlobalConsumer pour les notifications globales
  Utilité: Mappage des WebSocket URLs aux consommateurs

broadcast.py
  Description: Logique de broadcast (diffusion) des messages
  Utilité: Envoie des messages à tous les clients connectés
  Utilisé par: Api pour les notifications en temps réel

migrations/
  Description: Dossier de migrations de base de données
  Contenu: Scripts de création/modification de la structure DB


DOSSIER Registration/ (Authentification et Profils)
───────────────────────────────────────────────────

__init__.py
  Description: Marqueur de package

apps.py
  Description: Configuration de l'application Registration

models.py
  Description: Modèles de données pour users et profils
  
  Classes:
  
  Fonctions de hachage:
    - haser_password(password): Hash un mot de passe avec SHA256 + salt aléatoire
    - verifier_password(password, hashed): Vérifie un mot de passe

  Modèle Utilisateur:
    Champs:
      - id: Identifiant unique (clé primaire)
      - nom: Nom de famille
      - prenom: Prénom
      - email: Adresse email (unique)
      - mot_de_passe: Hash du mot de passe SHA256+salt
      - telephone: Numéro de téléphone
      - date_inscription: Date/heure d'inscription (auto)
      - est_actif: État du compte (actif/inactif)
    Méthodes:
      - modifier_est_actif(): Active/désactive le compte
      - modifier_mot_de_passe(nouveau): Change le mot de passe
    Table DB: utilisateur
    
  Modèle Profil:
    Champs:
      - id: Identifiant unique
      - utilisateur: Relation 1-à-1 vers Utilisateur (CASCADE delete)
      - bio: Biographie facultative
      - photo_profil: Image de profil (upload à photos_profil/)
      - adresse: Adresse postale
      - commune: Commune de résidence
      - ville: Ville de résidence
      - pays: Pays (défaut: Haiti)
      - longitude, latitude: Coordonnées GPS
      - date_maj: Date de dernière modification (auto)
      - role: Rôle (acheteur/vendeur/admin)
    Méthodes:
      - mettre_a_jour(**kwargs): Met à jour plusieurs champs
      - convertir_en_vendeur(): Change le rôle en vendeur
      - convertir_en_acheteur(): Change le rôle en acheteur
      - obtenir_coordonnees(): Retourne {longitude, latitude}
      - calculer_distance(autre_profil): Distance en km avec un autre profil
    Table DB: profil

views.py
  Description: Vues API pour Registration (endpoints HTTP)
  
  Endpoints:
    - sinscrire() [POST]: Crée un nouvel utilisateur
      Paramètres: nom, prenom, email, mot_de_passe, telephone (+ optionnels)
      Retour: Token et données utilisateur
    
    - seConnecter() [POST]: Authentifie l'utilisateur
      Paramètres: email, mot_de_passe
      Retour: Token et données utilisateur
      Effet: Active l'utilisateur si inactif
    
    - [Autres endpoints déconnexion, etc.]
  
  Fonction interne:
    - _serialiseUtilisateur(utilisateur): Convertit l'utilisateur en dict JSON

admin.py
  Description: Configuration de l'interface administrateur Django
  Utilité: Permet de gérer les utilisateurs et profils via /admin/

signals.py
  Description: Signaux Django (triggers automatiques)
  Fonction probable: Crée automatiquement un Profil à la création d'Utilisateur
  Utilité: Maintient la cohérence des relations 1-à-1

tests.py
  Description: Tests unitaires pour Registration
  Utilité: Valide le fonctionnement du module

urls.py
  Description: Routes des endpoints Registration
  Routes:
    - /Registration/[sinscrire/seConnecter/etc./]

migrations/
  Description: Migrations de base de données
  0001_initial.py: Crée les tables utilisateur et profil


DOSSIER RekoltHt/ (Gestion des Annonces/Ventes)
─────────────────────────────────────────────────

__init__.py
  Description: Marqueur de package

apps.py
  Description: Configuration de l'application RekoltHt

models.py
  Description: Modèles de données (actuellement vide)
  À développer: Modèles pour les annonces, listings, categories

views.py
  Description: Vues API pour RekoltHt
  
  Endpoint exemple:
    - test_conn() [GET]: Retourne un message de test
      {
        "message": "Bonjour",
        "status": "ok",
        "app": "myapp"
      }

admin.py
  Description: Configuration admin pour RekoltHt

signals.py
  Description: Signaux Django pour RekoltHt

tests.py
  Description: Tests unitaires

urls.py
  Description: Routes API RekoltHt
  Routes:
    - /api/test/ → test_conn()

migrations/
  Description: Migrations de base de données


DOSSIER Produits/ (Gestion des Produits)
──────────────────────────────────────────

__init__.py
  Description: Marqueur de package

apps.py
  Description: Configuration de l'application Produits

models.py
  Description: Modèles pour les produits (actuellement vide)
  À développer: Modèle Produit avec champs (nom, prix, description, etc.)

views.py
  Description: Vues API pour Produits

admin.py
  Description: Configuration admin pour Produits

tests.py
  Description: Tests unitaires pour Produits

migrations/
  Description: Migrations de base de données


CONFIGURATION IMPORTANTE (settings.py)
================================================================================

APPLICATIONS INSTALLÉES:
  - channels: Support WebSocket
  - django.contrib.admin: Interface admin
  - django.contrib.auth: Authentification Django
  - django.contrib.contenttypes: Types de contenu
  - django.contrib.sessions: Gestion des sessions
  - django.contrib.messages: Système de messages
  - django.contrib.staticfiles: Fichiers statiques
  - rest_framework: API REST
  - corsheaders: CORS
  - Api: WebSocket
  - RekoltHt: Annonces
  - Produits: Produits
  - Registration: Authentification

CORS (Cross-Origin Resource Sharing):
  - Origin autorisée: http://localhost:5173 (React)
  - Credentials: Autorisé
  - Headers: accept, authorization, content-type, x-csrftoken
  - CSRF trusted origins: http://localhost:5173

AUTHENTIFICATION API:
  - Type: Token Authentication
  - Permission par défaut: IsAuthenticated (endpoints protégés)

WEBSOCKET:
  - Backend: InMemoryChannelLayer (à remplacer par Redis en production)
  - Type: Asynchrone (async)

BASE DE DONNÉES:
  - Engine: sqlite3
  - Name: db.sqlite3


FLUX D'AUTHENTIFICATION
================================================================================

1. INSCRIPTION:
   POST /Registration/sinscrire/
   {
     "nom": "Dupont",
     "prenom": "Jean",
     "email": "jean@example.com",
     "mot_de_passe": "SecurePassword123",
     "telephone": "+509xxxxxxxx",
     "adresse": "Rue de la Paix",
     "ville": "Port-au-Prince"
   }
   
   Réponse (201):
   {
     "message": "Utilisateur inscrit avec succès",
     "token": "abc123def456...",
     "utilisateur": { ... }
   }
   
   Côté système:
   - Hash du mot de passe avec salt aléatoire
   - Création de Utilisateur
   - Signal crée Profil automatiquement
   - Génération d'un token d'authentification

2. CONNEXION:
   POST /Registration/seConnecter/
   {
     "email": "jean@example.com",
     "mot_de_passe": "SecurePassword123"
   }
   
   Réponse (200):
   {
     "message": "Utilisateur connecté avec succès",
     "token": "abc123def456...",
     "utilisateur": { ... }
   }
   
   Côté système:
   - Vérification de l'email
   - Vérification du mot de passe (hash)
   - Activation de l'utilisateur
   - Génération d'un nouveau token

3. AUTHENTIFICATION REQUÊTES:
   Header: Authorization: Token abc123def456...
   
   Les endpoints protégés vérifieront ce token via le middleware


TECHNOLOGIE WEBSOCKET
================================================================================

WebSocket URL: ws://localhost:8000/ws/global/

Flux:
1. Client se connecte au WebSocket /ws/global/
2. GlobalConsumer accepte la connexion
3. Le client est ajouté au groupe "global"
4. Chaque message reçu est broadcasté à tous les clients du groupe
5. À la déconnexion, le client est retiré du groupe

Utilité: Notifications en temps réel, updates de statut, etc.

Note: Configuration d'authentification partiellement commentée dans asgi.py


DÉMARRAGE DU PROJET
================================================================================

1. INSTALLATION:
   pip install -r requirements.txt

2. MIGRATIONS:
   python manage.py migrate

3. CRÉER UN SUPER-UTILISATEUR (optionnel):
   python manage.py createsuperuser

4. SERVEUR DÉVELOPPEMENT:
   avec support WebSocket (asynchrone):
   uvicorn BackendRekoltHt.asgi:application --reload
   Serveur ASGI: http://localhost:8000/

5. ACCÈS ADMIN:
   http://localhost:8000/admin/


STRUCTURE DES RELATIONS DE DONNÉES
================================================================================

Utilisateur (1) ────────────→ (1) Profil
  - relation OneToOneField
  - delete Utilisateur = delete Profil (CASCADE)
  - accès via: utilisateur.profil ou profil.utilisateur

TESTS
================================================================================

Exécuter les tests:
  python manage.py test

Fichiers de test:
  - Registration/tests.py
  - RekoltHt/tests.py
  - Produits/tests.py


COUVERTURE DE CODE
================================================================================

Rapports de couverture générés dans htmlcov/
Consulter htmlcov/index.html pour voir la couverture détaillée


RESSOURCES
================================================================================

Documentation Django: https://docs.djangoproject.com/
Django REST Framework: https://www.django-rest-framework.org/
Django Channels: https://channels.readthedocs.io/
Géographie/Distance: https://geopy.readthedocs.io/


================================================================================
                            FIN DU README
================================================================================
Créé pour: Projet BackendRekoltHt
Version: 1.0
Date: 2026
