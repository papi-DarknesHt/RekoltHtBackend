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
   - Connexion et inscription via Google (OAuth2)
   - Système de tokens d'authentification
   - Gestion des mots de passe avec hashage sécurisé (SHA256 + salt)
   - Modification du mot de passe (avec vérification de l'ancien mot de passe)
   - Modification des informations du compte (nom, prénom, email, téléphone)
   - Profils utilisateur avec informations personnelles et géolocalisation
   - Rôles d'utilisateurs : acheteur, vendeur, admin

2. SYSTÈME DE PROFILS
   - Profils détaillés pour chaque utilisateur
   - Informations personnelles (nom, prénom, email, téléphone)
   - Adresse, commune, ville et pays
   - Géolocalisation avec longitude et latitude
   - Support des photos de profil (upload encodé en base64, exposé en URL absolue)
   - Modification du profil (bio, adresse, commune, ville, pays, rôle, géolocalisation, photo)
   - Conversion entre rôles (acheteur ↔ vendeur)
   - Calcul de distance entre deux profils en km


3. COMMUNICATION EN TEMPS RÉEL
   - WebSocket via Django Channels
   - Système de notifications globales
   - Broadcasting de messages en temps réel
   - Notifications automatiques (signaux) à la création/modification/suppression
     d'un utilisateur ou d'un profil

4. API REST
   - Django REST Framework pour les endpoints API
   - Authentification par tokens
   - CORS configuré pour communiquer avec React (localhost:5173)
   - Endpoints pour Registration et RekoltHt

5. AUTHENTIFICATION GOOGLE (OAuth2)
   - Connexion Google pour un compte existant (google/connexion/)
   - Inscription Google pour créer un nouveau compte (google/inscription/)
   - Vérification du token Google auprès de l'API userinfo de Google
   - Création automatique du profil associé (rôle par défaut: acheteur)

6. VÉRIFICATION VENDEUR / KYC — NOUVEAU SUR CETTE BRANCHE (Features/become-seller)
   ─────────────────────────────────────────────────────────────────────────────
   Permet à un compte (individuel ou entreprise) de soumettre un dossier de
   vérification d'identité et de devenir vendeur automatiquement si tout
   concorde — sans étape de "revue manuelle" indéfinie : le pipeline conclut
   toujours vérifié ou échoué (avec la cause précise).

   Modèle (Registration/models.py) :
     - DemandeVerification (OneToOne avec Utilisateur) — couvre à la fois le
       flux individuel (pièce d'identité + selfie) et entreprise (certificat
       de patente), pour éviter de dupliquer un statut de vérification séparé
       dans Profil et dans Entreprise.
     - Champs clés : type_demandeur, type_document, document_recto/verso,
       selfie, numero_piece_saisi (saisi par l'utilisateur), certificat_patente,
       numero_patente_extrait, nom_extrait/prenom_extrait/numero_piece_extrait
       (par OCR), donnees_ocr_brutes (JSON), score_correspondance_visage,
       statut (en_attente / verifie / echoue), motif_echec, contrat_pdf.
     - DemandeVerification.marquer_verifie() : promeut automatiquement le
       compte au rôle 'vendeur' (Profil.convertir_en_vendeur), génère le
       contrat PDF signé électroniquement et l'envoie par email.
     - DemandeVerification.marquer_echoue(motif) : horodate l'échec et envoie
       un email expliquant le motif exact.

   Pipeline automatique (Registration/views.py, soumettre_verification) :
     1. Unicité inter-comptes : un document déjà utilisé par un AUTRE compte
        (dont la demande n'a pas échoué) est rejeté avant même l'OCR.
     2. OCR (Registration/services/ocr_service.py, PaddleOCR) : extrait
        nom/prénom/numéro de pièce (individuel) ou nom d'entreprise/numéro de
        patente (entreprise) — extraction par POSITION des boîtes détectées
        (pas seulement l'ordre séquentiel du texte), avec repli sur une
        correspondance de sous-chaîne tolérante aux fusions/pluriels
        introduits par l'OCR (ex: "Siyati/Nom" lu "SiyatilNom").
     3. Cross-vérification : nom/prénom (ou nom d'entreprise) et numéro saisi
        comparés aux valeurs extraites — normalisés (accents/espaces/casse
        ignorés) mais comparaison EXACTE sur le numéro (pas de tolérance
        floue, décision produit assumée).
     4. Vérification faciale (individuel uniquement, Registration/services/
        face_service.py + face_worker.py) : compare le selfie à la photo de
        la pièce via DeepFace (modèle Facenet, detector_backend="retinaface").
     5. Vérification patente (entreprise uniquement, Registration/services/
        patente_service.py, Playwright) : croise le nom d'entreprise avec le
        registre public guichet.mci.ht (recherche par NOM uniquement, pas par
        numéro — vérifié empiriquement) et confirme le numéro de patente.
     6. Aucun état de "revue manuelle" automatique : toute indisponibilité
        d'infrastructure (DeepFace non configuré, guichet.mci.ht injoignable)
        conclut désormais en 'echoue' avec la cause exacte, jamais en attente
        indéfinie.

   Point d'architecture critique — deux environnements Python séparés :
     DeepFace (TensorFlow, protobuf>=6.31.1) est incompatible avec paddleocr/
     paddlepaddle (protobuf<=3.20.2) dans le même environnement — vérifié :
     les deux plantent installés côte à côte. DeepFace tourne donc dans un
     second venv dédié (voir requirements-face.txt, non inclus dans
     requirements.txt), appelé via subprocess (face_worker.py) et jamais
     importé directement dans le processus Django. Chemin renseigné via la
     variable d'environnement FACE_VENV_PYTHON (.env.dev/.env.prod) — si
     absente, la vérification faciale automatique échoue proprement avec un
     motif clair plutôt que de planter.

   Endpoints (Registration/urls.py) :
     - POST /Registration/verification/soumettre/     → soumettre/mettre à jour le dossier KYC (multipart)
     - GET  /Registration/verification/statut/         → statut courant + motif d'échec + lien du contrat
     - POST /Registration/verification/previsualiser/  → génère un aperçu du contrat PDF sans rien persister
     - GET  /Registration/admin/verifications-entreprise/ → demandes entreprise en attente (rôle admin)

   Contrat PDF (Registration/services/contrat_service.py, reportlab) :
     photo (selfie ou logo entreprise), identité, type de pièce fourni et son
     numéro, photo du document lui-même, conditions générales, mention de
     signature électronique.

   Admin Django (Registration/admin.py) : DemandeVerificationAdmin avec
   actions groupées valider_selectionnees / rejeter_selectionnees (appellent
   marquer_verifie()/marquer_echoue() manuellement).

   Notifications temps réel : signal broadcast_verification (Registration/
   signals.py) émet "verification.updated" sur chaque changement de statut,
   en plus de l'email — voir Api/broadcast.py (réutilisé tel quel).

   Dépendances ajoutées à requirements.txt : paddleocr, paddlepaddle,
   opencv-python-headless, playwright, reportlab, whitenoise (middleware déjà
   utilisé mais absent du fichier — gap pré-existant corrigé). DeepFace/
   TensorFlow sont dans requirements-face.txt séparément (voir plus haut).


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
  - Pillow 12.2.0 - Traitement des images (photos de profil)
  - psycopg2-binary 2.9.12 - Driver PostgreSQL (prêt pour la production)
  - whitenoise - Sert les fichiers statiques directement depuis Django

Vérification KYC (nouveau sur cette branche — voir section 6 plus haut):
  - paddleocr / paddlepaddle - OCR des pièces d'identité et certificats
  - opencv-python-headless - Traitement d'image (dépendance de paddleocr)
  - playwright - Vérification du registre du Ministère du Commerce (MCI)
  - reportlab - Génération du contrat vendeur (PDF)
  - deepface / tensorflow (requirements-face.txt, venv séparé) - Vérification
    faciale (selfie vs pièce d'identité), incompatible avec paddleocr dans le
    même environnement (conflit protobuf)

Authentification Google (OAuth2):
  - social-auth-app-django 5.9.0 - Intégration Django de l'authentification sociale
  - social-auth-core 4.9.1 - Coeur de l'authentification OAuth2
  - requests 2.34.2 / requests-oauthlib 2.0.0 - Vérification des tokens auprès de Google
  - PyJWT 2.13.0 - Décodage/validation de jetons JWT
  - python3-openid 3.2.0, oauthlib 3.3.1, defusedxml 0.7.1 - Dépendances OAuth/OpenID

Frontend (communiquant avec ce backend):
  - React (sur localhost:5173)


STRUCTURE DU PROJET
================================================================================

BackendRekoltHt/
├── db.sqlite3                  # Base de données SQLite (développement)
├── manage.py                   # Utilitaire CLI Django
├── requirements.txt            # Dépendances Python du projet
├── .env.dev                     # Variables d'environnement dev (secrets, non commité)
├── .env.prod                    # Variables d'environnement prod (secrets, non commité, local uniquement)
├── .env.dev.example              # Modèle des variables attendues dans .env.dev
├── .env.prod.example             # Modèle des variables attendues dans .env.prod
├── .gitignore                   # Fichiers/dossiers exclus de Git (.env*, db.sqlite3, ...)
├── README.txt                  # Ce fichier
├── requirements-face.txt       # NOUVEAU — DeepFace/TensorFlow, venv séparé (voir section 6)
│                                 (venv/, venv_face/ : environnements virtuels, non commités)
│
├── BackendRekoltHt/           # Configuration principale du projet Django
│   ├── __init__.py
│   ├── settings/               # Package de configuration (dev / prod)
│   │   ├── __init__.py         # vide
│   │   ├── base.py             # Réglages communs aux deux environnements
│   │   ├── dev.py              # DEBUG=True, SQLite, médias en local
│   │   └── prod.py             # DEBUG=False, PostgreSQL, médias sur Cloudinary
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
│   ├── models.py              # Utilisateur, Profil, Entreprise, DemandeVerification (KYC)...
│   ├── views.py               # Vues API (sinscrire, seConnecter, soumettre_verification, ...)
│   ├── admin.py               # Configuration admin Django (+ DemandeVerificationAdmin)
│   ├── signals.py             # Signaux Django (création auto du profil, broadcast_verification)
│   ├── tests.py               # Tests unitaires
│   ├── urls.py                # Routes de Registration
│   ├── services/              # NOUVEAU sur cette branche — logique KYC isolée des vues
│   │   ├── __init__.py
│   │   ├── ocr_service.py     # Extraction OCR (PaddleOCR) des pièces/certificats
│   │   ├── face_service.py    # Appelle face_worker.py en sous-processus (venv dédié)
│   │   ├── face_worker.py     # Script autonome DeepFace — jamais importé par Django
│   │   ├── patente_service.py # Vérification patente via guichet.mci.ht (Playwright)
│   │   └── contrat_service.py # Génération du contrat vendeur (PDF, reportlab)
│   └── migrations/
│       ├── __init__.py
│       ├── 0001_initial.py               # Migration initiale
│       ├── 0012_demandeverification.py   # NOUVEAU — modèle DemandeVerification
│       └── 0013_alter_demandeverification_statut.py  # NOUVEAU — ajout numero_piece_saisi, etc.
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
├── media/                      # Fichiers médias uploadés (servis en DEBUG)
│   └── photos_profil/          # Photos de profil des utilisateurs
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

.env.dev / .env.prod
  Description: Variables d'environnement sensibles par environnement
  (SECRET_KEY, identifiants OAuth2 Google, SMTP, base PostgreSQL, Cloudinary...)
  Note: Fichiers non commités (voir .gitignore) — chargés au démarrage par
  BackendRekoltHt/settings/dev.py ou prod.py via python-dotenv (load_dotenv).
  Sur Render, .env.prod n'existe pas : les vraies variables d'environnement du
  tableau de bord Render sont utilisées directement.

.env.dev.example / .env.prod.example
  Description: Modèles listant les variables attendues dans .env.dev/.env.prod,
  avec des valeurs vides/placeholder
  Utilisation: Copier en .env.dev (ou .env.prod) puis renseigner les vraies valeurs

.gitignore
  Description: Liste des fichiers/dossiers exclus du suivi Git
  Contenu: .env, .env.dev, .env.prod, db.sqlite3, media/, __pycache__/, htmlcov/, .idea/, ...


DOSSIER BackendRekoltHt/ (Configuration Principale)
────────────────────────────────────────────────────

__init__.py
  Description: Marqueur de package Python (peut être vide)
  Utilité: Indique à Python que le dossier est un package

settings/ (package)
  Description: Configuration du projet Django, scindée en dev/prod pour éviter
  de dupliquer le code commun (voir "CONFIGURATION IMPORTANTE" plus bas)
  - base.py: INSTALLED_APPS, MIDDLEWARE, CORS, REST_FRAMEWORK, ASGI/Channels,
    AUTHENTICATION_BACKENDS, TEMPLATES, AUTH_PASSWORD_VALIDATORS, email SMTP
  - dev.py: DEBUG=True, base SQLite (db.sqlite3), médias sur disque local
  - prod.py: DEBUG=False, base PostgreSQL (variables DB_*), médias sur
    Cloudinary, ALLOWED_HOSTS depuis l'environnement
  Note critique: prod.py documente les pièges SQLite/PostgreSQL (ArrayField,
  précision GPS, stockage médias) — à lire avant tout déploiement

urls.py
  Description: Routeur principal des URLs du projet
  Routes principales:
    - /admin/ → Interface administrateur Django
    - /api/ → Endpoints API RekoltHt
    - /Registration/ → Endpoints d'authentification et inscription
    - /auth/ → Routes d'authentification sociale (social_django / Google OAuth2)
  Médias:
    - En mode DEBUG, les fichiers de MEDIA_ROOT (ex: photos de profil) sont
      servis directement via MEDIA_URL (/media/...)
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
    - sinscrire() [POST] → /Registration/inscription/
      Crée un nouvel utilisateur
      Paramètres: nom, prenom, email, mot_de_passe, telephone
                  (+ optionnels: bio, photo_profil, adresse, commune, ville,
                   pays, role, latitude, longitude)
      Retour: Token et données utilisateur

    - seConnecter() [POST] → /Registration/connexion/
      Authentifie l'utilisateur
      Paramètres: email, mot_de_passe
      Retour: Token et données utilisateur
      Effet: Active l'utilisateur si inactif

    - seDeconnecter() [POST] → /Registration/deconnexion/
      Déconnecte l'utilisateur (nécessite le token)
      Effet: Désactive l'utilisateur et supprime son token

    - profilAfficher() [GET] → /Registration/profil/
      Retourne les informations de l'utilisateur connecté et son profil
      Retour: { utilisateur: {...}, profil: {...} }
      Note: photo_profil est renvoyée en URL absolue (build_absolute_uri)

    - modifierUtilisateur() [PUT] → /Registration/modifier-utilisateur/
      Met à jour les informations du compte (nom, prenom, email, telephone)
      Retour: utilisateur mis à jour
      Erreur: 400 si le nouvel email existe déjà

    - modifierProfil() [PUT] → /Registration/modifier-profil/
      Met à jour le profil (bio, adresse, commune, ville, pays, role,
      latitude, longitude, photo_profil)
      Retour: profil mis à jour (photo_profil en URL absolue)

    - modifierMotDePasse() [PUT] → /Registration/modifier-mdp/
      Change le mot de passe après vérification de l'ancien
      Paramètres: ancien_mot_de_passe, nouveau_mot_de_passe
      Erreur: 401 si l'ancien mot de passe est incorrect

    - google_connection() [POST] → /Registration/google/connexion/
      Connecte un utilisateur existant via un token Google
      Erreur: 404 si aucun compte n'est associé à l'email Google

    - google_inscription() [POST] → /Registration/google/inscription/
      Crée un nouveau compte à partir des informations Google
      (email, nom, prénom) ; mot de passe aléatoire généré côté serveur

  Fonctions internes:
    - _get_user_from_token(request): Récupère l'utilisateur à partir du
      header "Authorization: Token xxx" (basé sur le dict TOKENS en mémoire)
    - _enregistrer_photo_profil(profil, photo_data): Décode une photo envoyée
      en base64 (data URL) et l'enregistre sur le champ photo_profil
    - _serialiseUtilisateur(utilisateur): Convertit l'utilisateur en dict JSON
    - _serialiseProfil(profil, request=None): Convertit le profil en dict JSON
      (photo_profil en URL absolue si "request" est fourni)

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
    - /Registration/inscription/          [POST] → sinscrire
    - /Registration/connexion/             [POST] → seConnecter
    - /Registration/deconnexion/           [POST] → seDeconnecter
    - /Registration/profil/                [GET]  → profilAfficher
    - /Registration/modifier-utilisateur/  [PUT]  → modifierUtilisateur
    - /Registration/modifier-profil/       [PUT]  → modifierProfil
    - /Registration/modifier-mdp/          [PUT]  → modifierMotDePasse
    - /Registration/google/connexion/      [POST] → google_connection
    - /Registration/google/inscription/    [POST] → google_inscription

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


CONFIGURATION IMPORTANTE (settings/)
================================================================================
Depuis la restructuration en package, la configuration est scindée en
BackendRekoltHt/settings/ (base.py commun + dev.py + prod.py) — voir la
section dédiée plus haut. La variable d'environnement DJANGO_SETTINGS_MODULE
détermine l'environnement actif (défaut: BackendRekoltHt.settings.dev, voir
manage.py/asgi.py/wsgi.py).

APPLICATIONS INSTALLÉES (base.py, + Cloudinary en prod uniquement):
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
  - social_django: Authentification sociale (Google OAuth2)
  - cloudinary_storage, cloudinary: stockage des médias (prod.py uniquement)

CORS (Cross-Origin Resource Sharing):
  - Origin autorisée: http://localhost:5173 (React)
  - Credentials: Autorisé
  - Méthodes: GET, POST, PUT, PATCH, DELETE, OPTIONS
  - Headers: accept, authorization, content-type, origin, x-csrftoken,
    x-requested-with
  - CSRF trusted origins: http://localhost:5173

AUTHENTIFICATION API:
  - Type: Token Authentication (table TOKENS en mémoire, header
    "Authorization: Token <token>")
  - Permission par défaut: IsAuthenticated (endpoints protégés)

AUTHENTIFICATION GOOGLE (OAuth2):
  - AUTHENTICATION_BACKENDS: social_core.backends.google.GoogleOAuth2
  - SOCIAL_AUTH_GOOGLE_OAUTH2_KEY / SECRET: identifiants client OAuth2 Google
  - SOCIAL_AUTH_GOOGLE_OAUTH2_SCOPE: openid, email, profile
  - SECURE_CROSS_ORIGIN_OPENER_POLICY: None (nécessaire pour la popup Google)
  - Route: /auth/ (social_django.urls)

FICHIERS MÉDIAS (photos de profil, logos):
  - MEDIA_URL: /media/ (commun, défini dans base.py)
  - Dev (dev.py): MEDIA_ROOT = BASE_DIR / media, servis par Django (DEBUG=True)
  - Prod (prod.py): DEFAULT_FILE_STORAGE = Cloudinary (variable CLOUDINARY_URL)
    — MEDIA_ROOT n'est PAS utilisé car le filesystem de Render est éphémère
    (tout fichier écrit sur disque est perdu à chaque redéploiement)

WEBSOCKET:
  - Backend: InMemoryChannelLayer (à remplacer par Redis en production)
  - Type: Asynchrone (async)

BASE DE DONNÉES:
  - Dev (dev.py): sqlite3 (db.sqlite3)
  - Prod (prod.py): PostgreSQL, via DB_NAME/DB_USER/DB_PASSWORD/DB_HOST/DB_PORT
  - Attention: ArrayField n'existe pas sous SQLite — utiliser JSONField pour
    tout futur champ "liste" (ex: categories_produits). Détail complet dans
    BackendRekoltHt/settings/prod.py

ALLOWED_HOSTS:
  - Dev (dev.py): localhost, 127.0.0.1 (codé en dur)
  - Prod (prod.py): lu depuis la variable d'environnement ALLOWED_HOSTS (liste
    séparée par des virgules), défaut: rekolthtbackend.onrender.com


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

4. AFFICHER LE PROFIL:
   GET /Registration/profil/
   Header: Authorization: Token abc123def456...

   Réponse (200):
   {
     "utilisateur": { "id": 1, "nom": "Dupont", "prenom": "Jean", ... },
     "profil": {
       "id": 1, "bio": "...", "photo_profil": "http://localhost:8000/media/photos_profil/xxx.jpg",
       "adresse": "...", "commune": "...", "ville": "...", "pays": "Haiti",
       "longitude": null, "latitude": null, "date_maj": "...", "role": "acheteur"
     }
   }

5. MODIFIER LES INFORMATIONS DU COMPTE:
   PUT /Registration/modifier-utilisateur/
   Header: Authorization: Token abc123def456...
   {
     "nom": "Dupont",
     "prenom": "Jean",
     "email": "jean.nouveau@example.com",
     "telephone": "+509xxxxxxxx"
   }

   Réponse (200):
   {
     "message": "Utilisateur mis à jour avec succès",
     "utilisateur": { ... }
   }
   Erreur (400): si le nouvel email existe déjà chez un autre utilisateur

6. MODIFIER LE PROFIL:
   PUT /Registration/modifier-profil/
   Header: Authorization: Token abc123def456...
   {
     "bio": "Producteur de riz",
     "adresse": "Rue de la Paix",
     "commune": "...",
     "ville": "Port-au-Prince",
     "pays": "Haiti",
     "role": "vendeur",
     "latitude": 19.45,
     "longitude": -72.68,
     "photo_profil": {
       "filename": "avatar.jpg",
       "content": "data:image/jpeg;base64,/9j/4AAQSk..."
     }
   }

   Réponse (200):
   {
     "message": "Profil mis à jour avec succès",
     "profil": { ..., "photo_profil": "http://localhost:8000/media/photos_profil/avatar.jpg" }
   }
   Note: tous les champs sont optionnels, seuls ceux fournis sont mis à jour.
   La photo est décodée depuis le base64 et enregistrée dans media/photos_profil/.

7. MODIFIER LE MOT DE PASSE:
   PUT /Registration/modifier-mdp/
   Header: Authorization: Token abc123def456...
   {
     "ancien_mot_de_passe": "AncienPass123",
     "nouveau_mot_de_passe": "NouveauPass456"
   }

   Réponse (200):
   { "message": "Mot de passe modifié avec succès" }
   Erreur (401): si l'ancien mot de passe est incorrect

8. CONNEXION / INSCRIPTION VIA GOOGLE:
   POST /Registration/google/connexion/
   { "token": "<id_token Google>" }
   - Recherche un utilisateur existant avec l'email Google
   - Erreur (404) si aucun compte n'existe pour cet email

   POST /Registration/google/inscription/
   { "token": "<id_token Google>", "role": "acheteur" }
   - Crée un nouvel utilisateur (nom/prénom/email depuis Google)
   - Erreur (400) si un compte existe déjà pour cet email

   Réponse (200/201) pour les deux:
   {
     "message": "...",
     "token": "abc123def456...",
     "utilisateur": { ... }
   }


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

2. VARIABLES D'ENVIRONNEMENT (développement):
   Copier .env.dev.example vers .env.dev et renseigner les valeurs réelles
   (SECRET_KEY, SOCIAL_AUTH_GOOGLE_OAUTH2_KEY, SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET, ...)
   cp .env.dev.example .env.dev

3. MIGRATIONS:
   python manage.py migrate
   (utilise BackendRekoltHt.settings.dev par défaut, voir manage.py)

4. CRÉER UN SUPER-UTILISATEUR (optionnel):
   python manage.py createsuperuser

5. SERVEUR DÉVELOPPEMENT:
   avec support WebSocket (asynchrone):
   uvicorn BackendRekoltHt.asgi:application --port 8000 --reload
   Serveur ASGI: http://localhost:8000/

6. ACCÈS ADMIN:
   http://localhost:8000/admin/

6bis. VÉRIFICATION KYC (nouveau — optionnel, voir section 6 des fonctionnalités):
   Nécessite Python 3.12 (paddlepaddle n'a pas de wheel 3.13+) :
     python -m playwright install chromium
   Pour activer la vérification faciale automatique (sinon échoue proprement
   avec un motif clair, revue manuelle via l'admin possible) :
     py -3.12 -m venv venv_face
     venv_face\Scripts\pip install -r requirements-face.txt
     (renseigner FACE_VENV_PYTHON=<chemin>\venv_face\Scripts\python.exe dans .env.dev)

7. DÉPLOIEMENT EN PRODUCTION (Render):
   Renseigner SECRET_KEY, SOCIAL_AUTH_GOOGLE_OAUTH2_KEY/SECRET, EMAIL_*, DB_*
   et CLOUDINARY_URL dans le tableau de bord Render (voir .env.prod.example),
   puis définir DJANGO_SETTINGS_MODULE=BackendRekoltHt.settings.prod.
   Avant tout déploiement, vérifier qu'aucune migration n'est manquante :
     python manage.py migrate --check --settings=BackendRekoltHt.settings.prod


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
Authentification sociale: https://python-social-auth.readthedocs.io/


================================================================================
                            FIN DU README
================================================================================
Créé pour: Projet BackendRekoltHt
Version: 1.2
Date: 2026
Mise à jour: Branche Features/become-seller — vérification KYC vendeur
(DemandeVerification, OCR, vérification faciale via DeepFace en venv séparé,
vérification patente via guichet.mci.ht, génération de contrat PDF, promotion
automatique acheteur→vendeur). Voir section 6 des fonctionnalités.
Historique: Ajout des endpoints de gestion du profil (affichage, modification
des informations utilisateur/profil, changement de mot de passe, upload de
photo de profil) et de l'authentification Google OAuth2 (v1.1).
