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

FONCTIONNALITÉS SUPPLÉMENTAIRES : SYSTÈME DE VENDEURS ET ENTREPRISES
================================================================================

1. INSCRIPTION EN TANT QU'ENTREPRISE (ACHETEUR)
────────────────────────────────────────────────

Un utilisateur peut s'inscrire directement comme "entreprise" lors de
l'inscription initiale. Son compte reste `role = "acheteur"` (une entreprise-
acheteuse n'est pas automatiquement vendeuse), mais `type_vendeur = "entreprise"`
et un objet `Entreprise` est créé immédiatement avec le même processus de
vérification que celui utilisé pour les vendeurs.

Avantages:
- Un seul système de vérification d'entreprise pour achat ET vente
- Si cette entreprise décide plus tard de devenir vendeur via `devenirVendeur`,
  le système reconnaît l'`Entreprise` déjà existante et ne redemande pas de
  document si aucun nouveau document n'est fourni.

Implémentation (backend)
------------------------
- Nouvelle méthode `Profil.creer_compte_entreprise(nom_entreprise, nouveau_fichier)`
  qui crée l'objet `Entreprise` au moment de l'inscription sans modifier `role`.
  Contrairement à `soumettre_demande_vendeur`, elle ne touche jamais au champ
  `role` — le rôle reste "acheteur" jusqu'à un appel explicite à
  `devenirVendeur`.

- Endpoint `sinscrire` [POST /Registration/inscription/] accepte optionnellement
  les champs:
  - `type_vendeur` : "individu" | "entreprise"
  - `nom_entreprise` : obligatoire si `type_vendeur == "entreprise"`
  - `piece_justificative` : obligatoire si `type_vendeur == "entreprise"`

- Pré-validation stricte: la pièce justificative est validée AVANT création
  de l'Utilisateur pour éviter les comptes orphelins en cas d'erreur.

- La réponse d'inscription inclut désormais la clé `profil` avec les détails
  `type_vendeur`, `nom_entreprise`, `statut_verification` immédiatement après
  l'inscription, sans appel API supplémentaire.

Exemple de requête:
  POST /Registration/inscription/
  {
    "nom": "Dupont",
    "prenom": "Jean",
    "email": "jean@example.com",
    "mot_de_passe": "SecurePassword123",
    "telephone": "+509xxxxxxxx",
    "type_vendeur": "entreprise",
    "nom_entreprise": "Ferme Lakou Vert",
    "piece_justificative": {
      "content": "data:application/pdf;base64,JVBERi0xLjQK...",
      "filename": "patente.pdf"
    }
  }

Réponse (201):
  {
    "message": "Utilisateur inscrit avec succès",
    "token": "abc123def456...",
    "utilisateur": { ... },
    "profil": {
      "id": 1,
      "role": "acheteur",
      "type_vendeur": "entreprise",
      "nom_entreprise": "Ferme Lakou Vert",
      "statut_verification": "en_attente",
      "piece_justificative": "http://localhost:8000/media/pieces_justificatives/patente.pdf",
      ...
    }
  }

Règles de validation:
- Si `type_vendeur` absent du corps: comportement standard (compte individuel)
- Si `type_vendeur = "individu"`: fixe `profil.type_vendeur`, pas de création
  d'objet `Entreprise`
- Si `type_vendeur = "entreprise"`:
  - `nom_entreprise` obligatoire → 400 sinon
  - `piece_justificative` obligatoire → 400 sinon
  - Validation d'extension (.pdf, .jpg, .jpeg, .png) → 400 sinon
  - Validation de taille (≤ 5 Mo après décodage) → 400 sinon
  - Aucun utilisateur n'est créé si la validation échoue

Tests ajoutés:
- Inscription sans `type_vendeur` → compte individuel normal, aucun `Entreprise` créé
- Inscription avec `type_vendeur = "individu"` → fixe `profil.type_vendeur`
- Inscription avec `type_vendeur = "entreprise"` + tous les champs → crée
  l'objet `Entreprise` avec `statut_verification = "en_attente"`
- Inscription avec `type_vendeur = "entreprise"` sans `nom_entreprise` → 400,
  aucun utilisateur créé
- Inscription avec `type_vendeur = "entreprise"` sans pièce justificative → 400,
  aucun utilisateur créé
- Inscription avec pièce justificative à extension invalide → 400, aucun
  utilisateur créé
- Scénario end-to-end: s'inscrire en tant qu'entreprise, puis appeler
  `devenirVendeur` sans nouveau document → role devient "vendeur", le même
  objet `Entreprise` est réutilisé (pas de duplication)


2. DEVENIR VENDEUR (APRÈS L'INSCRIPTION)
────────────────────────────────────────

Cette fonctionnalité permet à un utilisateur connecté de demander le passage
au rôle "vendeur" en choisissant un type : "individu" ou "entreprise".

- Type "individu" : aucun document requis — le profil devient vendeur et le
  statut de vérification est marqué "valide" immédiatement.
- Type "entreprise" : requiert le nom de l'entreprise et une pièce justificative
  (patente, certificat d'immatriculation, etc.). Lors de la première soumission
  la pièce est obligatoire et le statut passe à "en_attente" jusqu'à
  vérification manuelle par un administrateur. Lors d'une mise à jour, si un
  nouveau document est envoyé l'ancien est supprimé et le statut repasse à
  "en_attente" ; si seul le nom de l'entreprise change, le document et le
  statut existants restent inchangés.

Implémentation (backend)
------------------------
- Champs ajoutés au modèle `Profil` :
  - `type_vendeur` (CharField : "individu" | "entreprise")
  - `nom_entreprise` (CharField)
  - `piece_justificative` (FileField upload_to='pieces_justificatives/')
  - `statut_verification` (CharField : 'non_requis' | 'en_attente' | 'valide' | 'rejete')

- Nouvelle méthode serveur `Profil.soumettre_demande_vendeur(...)` qui applique
  la logique métier (suppression d'ancien fichier lors du remplacement,
  mise à jour du statut, assignation du rôle côté serveur).

- Endpoint HTTP ajouté : `POST /Registration/devenir-vendeur/` — authentification
  via header `Authorization: Token xxx` (mécanisme de token existant).

Validation des pièces justificatives
------------------------------------
- Formats autorisés : `.pdf`, `.jpg`, `.jpeg`, `.png` (vérification insensible
  à la casse sur le nom du fichier fourni).
- Taille maximale : 5 Mo après décodage base64 (attention : chaîne base64 plus
  volumineuse dans le JSON). Le projet définit désormais
  `DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024` dans `settings.py` pour
  permettre l'envoi de fichiers encodés en base64.

Confidentialité et accès aux documents
-------------------------------------
- Les pièces justificatives sont des données sensibles. Le sérialiseur de
  profil n'inclut l'URL de `piece_justificative` QUE si la requête est faite par
  le propriétaire du profil ou par un compte admin. Cela évite qu'un futur
  endpoint public n'expose accidentellement ces documents.
- Remarque : servir les fichiers via `MEDIA_URL` avec accès direct expose
  l'URL si elle est connue. Pour une sécurité renforcée, prévoir une vue
  dédiée qui vérifie l'authentification avant de streamer le fichier (tâche
  recommandée mais non implémentée ici).

Administration
--------------
- L'interface d'administration (`Registration/admin.py`) affiche désormais
  `type_vendeur` et `statut_verification` et permet le filtrage par ces champs
  pour faciliter la validation manuelle des demandes "entreprise".

Tests
-----
- Des tests unitaires ont été ajoutés (`Registration/tests.py`) couvrant :
  - soumission individu et entreprise
  - validations d'extension et de taille
  - remplacement de document (ancienne pièce supprimée)
  - protection contre la tentative de définition directe de `statut_verification`

Migration
---------
Après pull, exécuter :

```powershell
python manage.py makemigrations Registration
python manage.py migrate
```

Ces commandes ajoutent les nouveaux champs au modèle `Profil`.

Fichiers modifiés / ajoutés
---------------------------
- `Registration/models.py` :
  - Nouveaux champs + méthode `soumettre_demande_vendeur` (pour devenirVendeur)
  - Nouvelle méthode `creer_compte_entreprise` (pour inscription entreprise)
  - Propriétés proxy pour compatibilité (nom_entreprise, piece_justificative, statut_verification)
- `Registration/entreprise.py` : nouveau modèle `Entreprise` (OneToOne vers Profil)
- `Registration/views.py`  :
  - Endpoint `devenirVendeur`, validation base64
  - Modification de `sinscrire` : pré-validation `type_vendeur`, appel à `creer_compte_entreprise`
  - Ajout du champ `profil` dans la réponse d'inscription
  - Fonction `_valider_et_preparer_piece` pour validation des fichiers base64
  - Fonction `_serialiseProfil` mise à jour pour gérer la confidentialité des documents
- `Registration/urls.py`   : route `devenir-vendeur/`
- `Registration/admin.py`   : affichage et filtres admin pour Entreprise et Profil
- `Registration/tests.py`   :
  - Tests unitaires pour `devenirVendeur` (individu, entreprise, validations)
  - Tests pour l'inscription entreprise (success, erreurs de validation, reuse scenario)
- `BackendRekoltHt/settings.py` : `DATA_UPLOAD_MAX_MEMORY_SIZE` augmenté à 10 MB
- Migrations : création et application des migrations pour Entreprise et modifications Profil

Notes de sécurité supplémentaires
--------------------------------
- Ne pas accepter `role` ni `statut_verification` depuis le client ; ces
  valeurs sont recalculées côté serveur.
- Considérer à terme une stratégie de stockage sécurisé (S3 privé + URL
  signées) et l'utilisation d'un système d'authentification persistant pour
  les tokens (actuellement `TOKENS` est en mémoire).


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
├── .env                         # Variables d'environnement (secrets, non commité)
├── .env.example                 # Modèle des variables attendues dans .env
├── .gitignore                   # Fichiers/dossiers exclus de Git (.env, db.sqlite3, ...)
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

.env
  Description: Variables d'environnement sensibles (SECRET_KEY, identifiants
  OAuth2 Google, ...)
  Note: Fichier non commité (voir .gitignore) — chargé au démarrage par
  settings.py via python-dotenv (load_dotenv)

.env.example
  Description: Modèle listant les variables attendues dans .env, avec des
  valeurs vides/placeholder
  Utilisation: Copier en .env puis renseigner les vraies valeurs

.gitignore
  Description: Liste des fichiers/dossiers exclus du suivi Git
  Contenu: .env, db.sqlite3, media/, __pycache__/, htmlcov/, .idea/, ...


DOSSIER BackendRekoltHt/ (Configuration Principale)
────────────────────────────────────────────────────

__init__.py
  Description: Marqueur de package Python (peut être vide)
  Utilité: Indique à Python que le dossier est un package

settings.py
  Description: Configuration centralisée du projet Django
  Contient:
    - Chargement des variables d'environnement depuis .env (python-dotenv)
    - Configuration de la base de données (db.sqlite3)
    - Applications installées (Api, Registration, RekoltHt, Produits)
    - Middleware pour sécurité et traitements HTTP
    - Configuration CORS pour communiquer avec React (localhost:5173)
    - Paramètres REST Framework (authentification par tokens)
    - Configuration ASGI/Channels pour WebSocket
    - Configuration des templates et contextes
    - SECRET_KEY et identifiants Google OAuth2 lus depuis .env
  Note critique: NE PAS MODIFIER en production sans mesures de sécurité

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
      Crée un nouvel utilisateur avec option d'inscription en tant qu'entreprise
      Paramètres obligatoires: nom, prenom, email, mot_de_passe, telephone
      Paramètres optionnels: bio, photo_profil, adresse, commune, ville,
                             pays, role, latitude, longitude
      Paramètres optionnels pour entreprise:
                             type_vendeur (individu|entreprise),
                             nom_entreprise, piece_justificative
      Retour: Token, données utilisateur, et profil sérialisé (incluant type_vendeur,
              nom_entreprise, statut_verification si applicable)

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
  - social_django: Authentification sociale (Google OAuth2)

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

FICHIERS MÉDIAS (photos de profil):
  - MEDIA_URL: /media/
  - MEDIA_ROOT: BASE_DIR / media
  - Servis automatiquement par Django uniquement si DEBUG = True

WEBSOCKET:
  - Backend: InMemoryChannelLayer (à remplacer par Redis en production)
  - Type: Asynchrone (async)

BASE DE DONNÉES:
  - Engine: sqlite3
  - Name: db.sqlite3

UPLOAD DE FICHIERS (Pièces justificatives et médias):
  - DATA_UPLOAD_MAX_MEMORY_SIZE: 10 * 1024 * 1024 (10 MB)
  - Permet l'envoi de fichiers encodés en base64 jusqu'à ~7.5 Mo via JSON
  - Extensions acceptées: .pdf, .jpg, .jpeg, .png
  - Taille max après décodage: 5 MB


FLUX D'AUTHENTIFICATION
================================================================================

1. INSCRIPTION (COMPTE INDIVIDUEL OU ENTREPRISE):
   POST /Registration/inscription/

   Exemple A - Inscription individuelle (comportement standard):
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
     "utilisateur": { ... },
     "profil": {
       "id": 1, "role": "acheteur", "type_vendeur": null,
       "nom_entreprise": null, "statut_verification": "non_requis",
       "piece_justificative": null, ...
     }
   }

   Exemple B - Inscription en tant qu'entreprise:
   {
     "nom": "Dupont",
     "prenom": "Jean",
     "email": "jean@example.com",
     "mot_de_passe": "SecurePassword123",
     "telephone": "+509xxxxxxxx",
     "type_vendeur": "entreprise",
     "nom_entreprise": "Ferme Lakou Vert",
     "piece_justificative": {
       "content": "data:application/pdf;base64,JVBERi0xLjQK...",
       "filename": "patente.pdf"
     }
   }

   Réponse (201):
   {
     "message": "Utilisateur inscrit avec succès",
     "token": "abc123def456...",
     "utilisateur": { ... },
     "profil": {
       "id": 1, "role": "acheteur", "type_vendeur": "entreprise",
       "nom_entreprise": "Ferme Lakou Vert", "statut_verification": "en_attente",
       "piece_justificative": "http://localhost:8000/media/pieces_justificatives/patente.pdf",
       ...
     }
   }
   
   Côté système:
   - Hash du mot de passe avec salt aléatoire
   - Pré-validation du `type_vendeur` et du fichier AVANT création Utilisateur
   - Création de Utilisateur
   - Signal crée Profil automatiquement
   - Si `type_vendeur = "entreprise"`, création de l'objet Entreprise avec le fichier
   - Génération d'un token d'authentification

2. CONNEXION:
   POST /Registration/connexion/
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
       "longitude": null, "latitude": null, "date_maj": "...", "role": "acheteur",
       "type_vendeur": null, "nom_entreprise": null, "statut_verification": "non_requis"
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

7. DEVENIR VENDEUR (Après inscription):
   POST /Registration/devenir-vendeur/
   Header: Authorization: Token abc123def456...
   {
     "type_vendeur": "entreprise",
     "nom_entreprise": "Ferme Lakou Vert",
     "piece_justificative": {
       "content": "data:application/pdf;base64,JVBERi0xLjQK...",
       "filename": "patente.pdf"
     }
   }

   Réponse (200):
   {
     "message": "Demande vendeur entreprise soumise",
     "profil": {
       "id": 1, "role": "vendeur", "type_vendeur": "entreprise",
       "nom_entreprise": "Ferme Lakou Vert", "statut_verification": "en_attente", ...
     }
   }

   Note: Si l'utilisateur s'était déjà inscrit comme entreprise et appelle
   devenirVendeur sans nouveau document, le même objet Entreprise est réutilisé
   et le role passe juste à "vendeur".

8. MODIFIER LE MOT DE PASSE:
   PUT /Registration/modifier-mdp/
   Header: Authorization: Token abc123def456...
   {
     "ancien_mot_de_passe": "AncienPass123",
     "nouveau_mot_de_passe": "NouveauPass456"
   }

   Réponse (200):
   { "message": "Mot de passe modifié avec succès" }
   Erreur (401): si l'ancien mot de passe est incorrect

9. CONNEXION / INSCRIPTION VIA GOOGLE:
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

2. VARIABLES D'ENVIRONNEMENT:
   Copier .env.example vers .env et renseigner les valeurs réelles
   (SECRET_KEY, SOCIAL_AUTH_GOOGLE_OAUTH2_KEY, SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET)
   cp .env.example .env

3. MIGRATIONS:
   python manage.py migrate

4. CRÉER UN SUPER-UTILISATEUR (optionnel):
   python manage.py createsuperuser

5. SERVEUR DÉVELOPPEMENT:
   avec support WebSocket (asynchrone):
   uvicorn BackendRekoltHt.asgi:application --port 8000 --reload
   Serveur ASGI: http://localhost:8000/

6. ACCÈS ADMIN:
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
Authentification sociale: https://python-social-auth.readthedocs.io/


================================================================================
                            FIN DU README
================================================================================
Créé pour: Projet BackendRekoltHt
Version: 1.1
Date: 2026
Mise à jour: Ajout des endpoints de gestion du profil (affichage, modification
des informations utilisateur/profil, changement de mot de passe, upload de
photo de profil) et de l'authentification Google OAuth2.
