# Structure du projet — RekoltHtBackend

RekoltHt est une plateforme qui met en relation acheteurs et vendeurs de produits agricoles en Haïti. Ce dépôt contient le **backend Django**, exposant une API HTTP (endpoints JSON « faits main », pas de DRF viewsets/serializers malgré DRF installé) ainsi qu'un canal **WebSocket** (Django Channels) consommé par le frontend React (`RekoltHtFront`).

## 1. Vue d'ensemble

```
BackendRekoltHt/    Package de configuration du projet (settings, urls racine, asgi/wsgi)
Api/                 App WebSocket (pas de vues HTTP, pas de modèles)
Registration/        Auth, utilisateurs, profils, entreprises, vérification KYC, admin
Produits/            Catalogue produits (catégories, sous-catégories, produits, photos)
Messagerie/          Messagerie privée + messages vendeur → admin (support)
RekoltHt/            App quasi vide / historique (un seul endpoint de test)
media/               Fichiers uploadés (dev uniquement, stockage disque)
db.sqlite3           Base de données de dev
manage.py, requirements.txt, requirements-face.txt
README.md / README.txt
```

### Settings

Le dossier `BackendRekoltHt/settings/` est un **package** (pas un simple `settings.py`) :

- **`base.py`** — réglages communs : `INSTALLED_APPS`, `MIDDLEWARE`, CORS, DRF, Channels/ASGI, backend Google OAuth, email SMTP, `FACE_VENV_PYTHON`, `MEDIA_URL`, `DATA_UPLOAD_MAX_MEMORY_SIZE = 10MB` (car les images arrivent en base64 dans le JSON).
- **`dev.py`** — `DEBUG=True`, SQLite (`db.sqlite3`), médias sur disque local, charge `.env.dev`. C'est le module par défaut (`DJANGO_SETTINGS_MODULE=BackendRekoltHt.settings.dev`).
- **`prod.py`** — `DEBUG=False`, PostgreSQL via variables d'env `DB_*`, stockage médias sur Cloudinary, `ALLOWED_HOSTS` depuis l'env, charge `.env.prod`. Contient des avertissements sur les pièges SQLite↔PostgreSQL (éviter `ArrayField`, précision GPS, système de fichiers éphémère sur Render).

### URLs racine (`BackendRekoltHt/urls.py`)

| Préfixe | App |
|---|---|
| `/admin/` | Django admin |
| `/api/` | `RekoltHt.urls` (actuellement juste `/api/test/`) |
| `/Registration/` | `Registration.urls` |
| `/auth/` | `social_django.urls` (flow OAuth2 Google) |
| `/produits/` | `Produits.urls` |
| `/messagerie/` | `Messagerie.urls` |

Les médias ne sont servis directement par Django qu'en `DEBUG=True`.

### ASGI / WebSocket

`BackendRekoltHt/asgi.py` route `http` vers Django classique et `websocket` vers `AuthMiddlewareStack(URLRouter(Api.routing.websocket_urlpatterns))`. **Doit être lancé via `uvicorn BackendRekoltHt.asgi:application`** (pas `manage.py runserver`) pour que le WebSocket fonctionne.

## 2. Base de données

- **Dev** : SQLite3 (`db.sqlite3`).
- **Prod** : PostgreSQL (Render), configuré via variables d'env (`DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`), driver `psycopg2-binary`.
- Avertissements dev/prod documentés dans `prod.py` : pas d'`ArrayField` (préférer `JSONField`, déjà utilisé pour `DemandeVerification.donnees_ocr_brutes`), précision GPS potentiellement différente, `MEDIA_ROOT` jamais utilisable en prod (fichiers perdus au redeploy → Cloudinary).

## 3. Authentification

**Système entièrement fait maison**, malgré la présence de DRF (`TokenAuthentication`/`IsAuthenticated` déclarés dans `REST_FRAMEWORK` mais inutilisés en pratique — toutes les vues sont des fonctions Django classiques `@csrf_exempt`).

- **Mots de passe** : SHA-256 + sel aléatoire (`Registration.models.haser_password`/`verifier_password`), format `"sel$hash"`. Pas les hashers Django (PBKDF2/bcrypt).
- **Tokens** : modèle `Registration.models.Token` (FK `utilisateur`, `cle` = 64 caractères hex via `secrets.token_hex(32)`, `date_creation`). **Pas d'expiration**.
- **Session unique** : se connecter supprime tous les anciens tokens de l'utilisateur → une ancienne session est déconnectée silencieusement (401) à sa prochaine requête.
- Chaque vue protégée appelle un helper local `_get_user_from_token(request)`, **dupliqué 3 fois** (`Registration/views.py`, `Produits/views/_auth.py`, `Messagerie/views.py` — pas de module d'auth partagé) qui lit l'en-tête `Authorization: Token <clé>`.
- Autorisation par rôle : vérifications manuelles `utilisateur.profil.role in (...)` dans chaque vue.
- Blocage : `Utilisateur.bloquer()` met `est_bloquer=True` et supprime tous les tokens (révocation immédiate).
- **Google OAuth2** : via `social-auth-app-django` (monté sur `/auth/`) pour la redirection, mais la liaison de compte est custom : `google_connection`/`google_inscription` reçoivent un access token du frontend, appellent l'API `userinfo` de Google directement, puis émettent le même `Token` maison.
- **WebSocket** : `Api/consumers.py` lit `?token=` dans l'URL, résout l'utilisateur de la même façon, rejoint un groupe personnel `user_<id>` et, si admin, le groupe `admins`.

## 4. Gestion des fichiers / médias

- `MEDIA_URL = '/media/'`. Dev : disque local (`media/`). Prod : Cloudinary.
- Sous-dossiers : `photos_produits/`, `photos_profil/`, `verification/` (docs KYC, selfies, contrats).
- Deux styles d'upload coexistent :
  1. **Base64 dans le JSON** (photo de profil, logo d'entreprise) — décodé côté serveur.
  2. **Multipart/form-data** (documents KYC, photos produits) — via `request.FILES`.
- La suppression de fichier est toujours explicite (le CASCADE Django ne supprime jamais les fichiers physiques).

## 5. Intégrations tierces

| Intégration | Package(s) | Usage |
|---|---|---|
| Google OAuth2 | `social-auth-app-django`, `oauthlib`, `PyJWT`, etc. | Connexion/inscription Google |
| Email (SMTP) | Backend SMTP Django | Codes PIN reset mot de passe, notifications KYC (avec contrat PDF) |
| Stockage médias (prod) | `django-cloudinary-storage`, `cloudinary` | Fichiers persistants sur Render |
| OCR | `paddleocr`, `paddlepaddle`, `opencv-python-headless` | Extraction nom/n° pièce/entreprise depuis documents d'identité et patentes |
| Reconnaissance faciale | `deepface` (ArcFace), `tensorflow` — **venv séparé** (`venv_face`) via `subprocess` | Comparaison selfie / photo pièce d'identité |
| Génération PDF | `reportlab` | Contrat vendeur signé |
| Géolocalisation | `geopy`, `geographiclib` | Distance entre deux points GPS |
| Temps réel | `channels`, `uvicorn`, `websockets` | Couche WebSocket (`InMemoryChannelLayer` — mono-process, pas de Redis) |
| Fichiers statiques | `whitenoise` | Sert le statique sans serveur web dédié |
| CORS | `django-cors-headers` | Autorise le frontend React |

### `requirements.txt` vs `requirements-face.txt`

- **`requirements.txt`** (venv principal `.venv`, **Python 3.12 exactement** — PaddlePaddle n'a pas de build 3.13+) : Django 6.0.4, DRF, stack Channels/uvicorn, stack OAuth Google, psycopg2-binary, Pillow, Cloudinary, PaddleOCR/PaddlePaddle, reportlab, geopy, pandas/numpy.
- **`requirements-face.txt`** (venv séparé `venv_face`) : `deepface`, `tensorflow`, `tf-keras`. Isolé car DeepFace/TensorFlow exige `protobuf>=6.31.1` alors que PaddlePaddle exige `protobuf<=3.20.2` (conflit vérifié en pratique). `Registration/services/face_service.py` invoque ce venv via `subprocess`, jamais importé directement dans le process Django principal (variable d'env `FACE_VENV_PYTHON`).

## 6. Détail par application

### `Api` — couche WebSocket

Pas de modèles, pas de vues HTTP, pas d'admin.

- **`Api/consumers.py`** — `GlobalConsumer` : à la connexion, rejoint le groupe `"global"` (tous les clients connectés) ; si un `?token=` valide est fourni, rejoint aussi `user_<id>` et, si rôle admin, `"admins"`.
- **`Api/routing.py`** — `ws://<host>/ws/global/`.
- **`Api/broadcast.py`** — trois helpers utilisés dans tout le code : `broadcast(event_type, data)`, `broadcast_to_user(user_id, ...)`, `broadcast_to_admins(...)`.

### `Registration` — comptes, profils, entreprises, KYC, admin

App la plus riche du projet.

**Modèles principaux** (`Registration/models.py`) :

- **`Utilisateur`** — `nom`, `prenom`, `email` (unique), `mot_de_passe` (hash), `telephone`, `est_actif`, `est_bloquer`.
- **`Vendeur` / `Acheteur`** — proxy models de `Utilisateur` (pas de nouvelle table), pour des vues admin filtrées par rôle.
- **`Profil`** (OneToOne → `Utilisateur`, créé automatiquement par signal) — `bio`, `photo_profil`, `adresse`, `commune`, `ville`, `pays`, `longitude`/`latitude`, `role` (acheteur/vendeur/admin), `categories_produits` (M2M vers `Produits.Categories`).
- **`Entreprise`** (héritage multi-table de `Utilisateur` — compte de connexion à part entière) — `proprietaire`, `nom_Entreprise` (unique), `num_Enregistrement` (unique), `secteur`, `logo`, `est_verifiee`, `statut_verification`.
- **`DemandeVerification`** (OneToOne → `Utilisateur`) — le dossier KYC, individuel ou entreprise : documents (`document_recto`/`verso`, `selfie`, `certificat_patente`), champs extraits par OCR, `score_correspondance_visage`, `statut` (en_attente/en_attente_manuelle/verifie/echoue), `contrat_pdf`. Méthodes clés `marquer_verifie()` (promeut en vendeur, génère le PDF, envoie l'email) et `marquer_echoue(motif)`.
- **`CodeReinitialisation`** — PIN à 4 chiffres, expire après 15 min.
- **`Token`** — voir section Authentification.

**Services** (`Registration/services/`) — logique métier hors des vues :

- `ocr_service.py` — PaddleOCR, extraction par position spatiale des boîtes détectées (pas par ordre de lecture séquentiel), gère les particularités des pièces haïtiennes.
- `face_service.py` — appelle `venv_face` en sous-process (timeout 150s), seuil de confiance custom à 35 %.
- `contrat_service.py` — génère le contrat PDF (`reportlab`), version signée finale ou aperçu non persisté.

**Pipeline KYC** (`soumettre_verification`, synchrone) :
1. Vérification de doublon de document entre comptes.
2. OCR → recoupement avec les infos saisies/du compte.
3. Individus : comparaison faciale (DeepFace) → `marquer_verifie()` si succès.
4. Entreprises : nom d'entreprise + numéro de patente extraits par OCR comparés aux valeurs déclarées/saisies → `marquer_verifie()` si tout concorde (plus de croisement avec un registre externe — retiré, voir `_lancer_pipeline_ocr`).
5. Toute exception dans le pipeline se traduit par `marquer_echoue(...)`.

**Endpoints principaux** (préfixe `/Registration/`) : `inscription/`, `connexion/`, `deconnexion/`, `google/connexion|inscription/`, `profil/`, `modifier-utilisateur/`, `modifier-profil/`, `modifier-mdp/`, `entreprise/creer|lister|modifier|supprimer/`, `verification/soumettre|statut|previsualiser/`, `admin/utilisateurs/`, `admin/utilisateurs/bloquer/`, `admin/utilisateurs/nommer-admin/`, `admin/verifications-entreprise/`, `admin/dashboard/`, `reinitialisation/demander|verifier-code|valider/`.

### `Produits` — catalogue produits

**Modèles** (`Produits/models/`) :

- **`Categories`** — `nom`, `description`.
- **`sousCategories`** — FK vers `Categories` (CASCADE).
- **`Produits`** — `vendeur` (FK `Utilisateur`), `categorie` (CASCADE), `sous_categorie` (**SET_NULL**), `nom`, `description`, `prix`, `unitePrix` (USD/HTG), localisation (`departement`, `commune`, `section_comunale`, GPS), `est_disponible`, `nombre_contacts`.
- **`photo_produits`** — FK vers `Produits` (CASCADE, related_name `photos`).
- **`ContactProduit`** — trace qui a contacté un vendeur (`acheteur` en **SET_NULL**, contacts anonymes autorisés).

**Vues** (`Produits/views/`, 4 fichiers) : `categoriesViews.py`, `sousCategoriesViews.py`, `produitsViews.py` (CRUD produit, `contacterProduit` sans auth requise, `historiqueContactsVendeur`, `infoVendeur`), `photoProduits.py`.

**Signaux** : diffusion WebSocket `produit.*`, `categorie.*`, `sous_categorie.*` sur `"global"` ; `contact.created` uniquement vers le vendeur concerné (pas global, pour ne pas exposer l'identité de l'acheteur).

Un script de seed (`Produits/sql/seed_categories_produits.sql`) insère la catégorie « Produits Agricoles » et ~20 sous-catégories standards.

### `Messagerie` — messagerie privée + support

**Modèles** :

- **`Conversation`** — `participant_a`/`participant_b`, contrainte d'unicité sur la paire, ordre normalisé pour éviter les doublons inversés.
- **`Message`** — `conversation`, `expediteur`, `contenu`, `produit` (partage de produit optionnel, SET_NULL), `lu`.
- **`MessageSupport`** — message vendeur → admins (pas un admin spécifique), `admin_repondant` (SET_NULL jusqu'à prise en charge).

**Endpoints** (`/messagerie/`) : `mesConversations`, `demarrerConversation` (auto-partage du produit à l'ouverture), `messagesConversation`, `envoyerMessage`, `contacterAdmin`, `mesMessagesAdmin`, `listerMessagesAdminEnAttente`, `repondreMessageAdmin` (prise en charge atomique via `UPDATE ... WHERE admin_repondant IS NULL`, retourne 409 si déjà pris).

### `RekoltHt` — app quasi vide

Malgré son nom, cette app ne contient presque rien : un seul endpoint `GET /api/test/` (`test_conn`, health check). Candidat à suppression ou repurposing.

## 7. Points notables pour la suite

- **Aucun serializer/viewset DRF** n'est utilisé nulle part : toutes les vues sont des fonctions Django classiques avec sérialisation manuelle (`_serialise*`).
- `_get_user_from_token` est dupliqué dans 3 apps — piste de refactor.
- La couche temps réel (Channels) est omniprésente : quasiment chaque signal `post_save`/`post_delete` pousse un événement WebSocket correspondant, ce qui rend le frontend largement pilulé par les événements plutôt que par polling.
- Le README.txt est une bonne référence narrative mais partiellement obsolète (ex. décrit un ancien stockage de tokens en mémoire) — se fier au code source pour les détails exacts de champs/endpoints.
