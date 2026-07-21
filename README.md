# RekoltHtBackend

Backend Django de **RekoltHt**, une plateforme qui met en relation acheteurs
et vendeurs de produits agricoles en Haïti. Expose une API REST (Django REST
Framework) et un canal WebSocket (Django Channels) consommés par le frontend
React (`RekoltHtFront`).

Fonctionnalités principales : comptes utilisateurs (particulier ou
entreprise) et authentification par token, connexion Google OAuth2, profils
avec géolocalisation, notifications temps réel, et un pipeline de
vérification vendeur (KYC) qui lit une pièce d'identité par OCR, compare un
selfie à la pièce par reconnaissance faciale, vérifie une entreprise auprès
du registre du MCI, puis génère un contrat PDF signé électroniquement.

> Pour la documentation technique complète (tous les modèles, endpoints,
> structure de fichiers, flux d'authentification détaillé...), voir
> [README.txt](README.txt).

## Stack technique

- Django 6 + Django REST Framework — API REST
- Django Channels + Uvicorn — WebSocket / temps réel
- SQLite (dev) / PostgreSQL (prod)
- PaddleOCR, DeepFace, Playwright, reportlab — pipeline de vérification KYC
- python-dotenv — variables d'environnement

## Prérequis

- **Python 3.12** exactement (PaddlePaddle n'a pas de build pour 3.13+)
- [uv](https://docs.astral.sh/uv/) pour gérer les environnements virtuels
  (voir étape 2 ci-dessous — `venv`/`pip` standard fonctionnent aussi si
  préféré, les commandes équivalentes sont données à chaque étape)
- Un fichier `.env.dev` renseigné (voir étape 4 ci-dessous)

## Installation étape par étape

### 1. Cloner le projet et se placer dedans

```bash
git clone <url-du-repo>
cd RekoltHtBackend
```

### 2. Installer uv (si ce n'est pas déjà fait)

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

*(macOS/Linux : `curl -LsSf https://astral.sh/uv/install.sh | sh` — ou, sur
n'importe quel OS, via pip : `pip install uv`)*

Redémarrer le terminal puis vérifier :

```bash
uv --version
```

### 3. Créer l'environnement virtuel et installer les dépendances

```bash
uv venv .venv --python 3.12
uv pip install -r requirements.txt --python .venv\Scripts\python.exe
```

*(sans uv : `py -3.12 -m venv .venv` puis, après activation,
`.venv\Scripts\pip install -r requirements.txt`)*

Puis installer le navigateur headless requis pour la vérification du
registre d'entreprise (Playwright) :

```bash
.venv\Scripts\python -m playwright install chromium
```

### 4. Configurer les variables d'environnement

```bash
cp .env.dev.example .env.dev
```

Ouvrir `.env.dev` et renseigner au minimum :

| Variable | Description |
|---|---|
| `SECRET_KEY` | clé secrète Django (n'importe quelle chaîne aléatoire en local) |
| `SOCIAL_AUTH_GOOGLE_OAUTH2_KEY` / `_SECRET` | identifiants OAuth2 Google (connexion Google) |
| `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` | compte SMTP pour l'envoi d'emails |
| `FACE_VENV_PYTHON` | chemin vers l'environnement de vérification faciale — voir étape 7, optionnel |

### 5. Appliquer les migrations

```bash
.venv\Scripts\python manage.py migrate
```

### 6. (Optionnel) Créer un compte administrateur

```bash
.venv\Scripts\python manage.py createsuperuser
```

### 7. (Optionnel) Activer la vérification faciale (KYC)

La reconnaissance faciale (DeepFace) est incompatible avec l'OCR
(PaddleOCR) dans le même environnement Python (conflit de version de
`protobuf`) — elle tourne donc dans un **second venv séparé**, appelé en
sous-processus :

```bash
uv venv venv_face --python 3.12
uv pip install -r requirements-face.txt --python venv_face\Scripts\python.exe
venv_face\Scripts\python -c "from deepface import DeepFace; DeepFace.build_model('ArcFace')"
```

Puis renseigner dans `.env.dev` :

```
FACE_VENV_PYTHON=C:\chemin\complet\vers\RekoltHtBackend\venv_face\Scripts\python.exe
```

⚠️ Le chemin doit pointer vers **`python.exe` lui-même**, pas vers le
dossier `venv_face`. Sans cette étape, l'inscription vendeur fonctionne
quand même : la vérification faciale échoue simplement proprement avec un
motif explicite au lieu d'être effectuée.

## Lancer le projet

```bash
.venv\Scripts\python -m uvicorn BackendRekoltHt.asgi:application --port 8000 --reload
```

- API disponible sur **http://localhost:8000/**
- Interface admin : **http://localhost:8000/admin/**
- WebSocket : `ws://localhost:8000/ws/global/`

> Utiliser `uvicorn` (pas `manage.py runserver`) : le WebSocket (notifications
> temps réel) nécessite le serveur ASGI.

## Tests

```bash
.venv\Scripts\python manage.py test
```

## Déploiement en production

Voir la section "DÉPLOIEMENT EN PRODUCTION" de [README.txt](README.txt) —
en résumé : renseigner les variables listées dans `.env.prod.example` sur la
plateforme d'hébergement (Render), puis définir
`DJANGO_SETTINGS_MODULE=BackendRekoltHt.settings.prod`.
