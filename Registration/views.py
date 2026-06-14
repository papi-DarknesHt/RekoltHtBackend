import base64
import json
import secrets
import requests
from django.core.files.base import ContentFile
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.db import IntegrityError
from .models import Utilisateur, Profil,  haser_password, verifier_password


# Dictionnaire en mémoire pour stocker les tokens
# clé = token, valeur = id de l'utilisateur
TOKENS = {}

# -----------------------------------Google Authntification connection -------------------
@csrf_exempt
def google_connection(request):
    """Connexion via Google — l'utilisateur doit déjà exister"""
    if request.method != "POST":
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    try:
        data         = json.loads(request.body)
        google_token = data.get('token')

        if not google_token:
            return JsonResponse({'error': 'Token Google manquant'}, status=400)
         # vérifie le token auprès de Google
        google_response = requests.get(
            'https://www.googleapis.com/oauth2/v3/userinfo',
            headers={'Authorization': f'Bearer {google_token}'}
        )

        if google_response.status_code != 200:
            return JsonResponse({'error': 'Token Google invalide'}, status=401)

        google_data = google_response.json()
        email       = google_data.get('email')

        if not email:
            return JsonResponse({'error': 'Email Google non disponible'}, status=400)

        # vérifie si l'utilisateur existe
        try:
            utilisateur = Utilisateur.objects.get(email=email)
        except Utilisateur.DoesNotExist:
            # ← message clair si le compte n'existe pas
            return JsonResponse({
                'error': 'Kont sa a pa egziste. Tanpri enskri dabò.'
            }, status=404)

        # active l'utilisateur
        if not utilisateur.est_actif:
            utilisateur.modifier_est_actif()

        token         = secrets.token_hex(32)
        TOKENS[token] = utilisateur.id

        return JsonResponse({
            'message':     'Koneksyon reyisi via Google',
            'token':       token,
            'utilisateur': _serialiseUtilisateur(utilisateur),
        })
    except Exception as e:
        print("ERREUR google_connexion :", str(e))
        return JsonResponse({'error': str(e)}, status=500)

# ------------------------------------Google auth inscription-------------------
@csrf_exempt
def google_inscription(request):
    """Inscription via Google — crée un nouveau compte"""
    if request.method != "POST":
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    try:
        data         = json.loads(request.body)
        google_token = data.get('token')

        if not google_token:
            return JsonResponse({'error': 'Token Google manquant'}, status=400)

        # vérifie le token auprès de Google
        google_response = requests.get(
            'https://www.googleapis.com/oauth2/v3/userinfo',
            headers={'Authorization': f'Bearer {google_token}'}
        )
        if google_response.status_code != 200:
            return JsonResponse({'error': 'Token Google invalide'}, status=401)

        google_data = google_response.json()
        email       = google_data.get('email')
        nom         = google_data.get('family_name',  'Inconnu')
        prenom      = google_data.get('given_name',   'Inconnu')

        if not email:
            return JsonResponse({'error': 'Email Google non disponible'}, status=400)
        if Utilisateur.objects.filter(email=email).exists():
            return JsonResponse({
                'error': 'Kont sa a deja egziste. Tanpri konekte.'
            }, status=400)
        utilisateur = Utilisateur.objects.create(
            nom          = nom,
            prenom       = prenom,
            email        = email,
            mot_de_passe = haser_password(secrets.token_hex(16)),
            telephone    = '',
            est_actif    = False,
        )

        # le profil est créé par le signal
        # on met juste à jour les champs supplémentaires
        profil      = utilisateur.profil
        profil.pays = 'Haiti'
        profil.role = data.get('role', 'acheteur')
        profil.latitude  = data.get('latitude', None)
        profil.longitude = data.get('longitude', None)
        profil.save()

        token         = secrets.token_hex(32)
        TOKENS[token] = utilisateur.id

        return JsonResponse({
            'message':     'Enskripsyon reyisi via Google',
            'token':       token,
            'utilisateur': _serialiseUtilisateur(utilisateur),
        }, status=201)

    except Exception as e:
        print("ERREUR google_inscription :", str(e))
        return JsonResponse({'error': str(e)}, status=500)

# ──-------- INSCRIPTION ───────────────────────────────────────────────────────────────
@csrf_exempt
def sinscrire(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    data = json.loads(request.body)

    # vérifier les champs obligatoires
    for field in ['nom', 'prenom', 'email', 'mot_de_passe', 'telephone']:
        if field not in data:
            return JsonResponse({'error': f'Le champ {field} est requis'}, status=400)

    # vérifier si l'email existe déjà
    if Utilisateur.objects.filter(email=data['email']).exists():
        return JsonResponse({'error': "L'email existe déjà"}, status=400)

    # créer l'utilisateur
    utilisateur = Utilisateur.objects.create(
        nom          = data['nom'],
        prenom       = data['prenom'],
        email        = data['email'],
        mot_de_passe = haser_password(data['mot_de_passe']),
        telephone    = data['telephone'],
        est_actif    = False,
    )


    # le profil est créé automatiquement par le signal
    # on met à jour les champs supplémentaires
    profil          = utilisateur.profil
    profil.bio      = data.get('bio', '')
    _enregistrer_photo_profil(profil, data.get('photo_profil'))
    profil.adresse  = data.get('adresse', '')
    profil.commune  = data.get('commune', '')
    profil.ville    = data.get('ville',   '')
    profil.pays     = data.get('pays',    'Haiti')
    profil.role     = data.get('role',    'acheteur')
    profil.latitude  = data.get('latitude', None)
    profil.longitude = data.get('longitude', None)
    profil.save()

    # créer le token
    token         = secrets.token_hex(32)
    TOKENS[token] = utilisateur.id

    return JsonResponse({
        'message':     'Utilisateur inscrit avec succès',
        'token':       token,
        'utilisateur': _serialiseUtilisateur(utilisateur),
    }, status=201)


# ── CONNEXION ─────────────────────────────────────────────────────────────────
@csrf_exempt
def seConnecter(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    data = json.loads(request.body)

    # vérifier les champs obligatoires
    for field in ['email', 'mot_de_passe']:
        if field not in data:
            return JsonResponse({'error': f'Le champ {field} est requis'}, status=400)

    # vérifier si l'utilisateur existe
    try:
        utilisateur = Utilisateur.objects.get(email=data['email'])
    except Utilisateur.DoesNotExist:
        return JsonResponse({'error': 'Email n\'existe pas ou est incorrect'}, status=401)

    # vérifier le mot de passe
    if not verifier_password(data['mot_de_passe'], utilisateur.mot_de_passe):
        return JsonResponse({'error': 'Le mot de passe n\'existe pas ou incorrect'}, status=401)

    # activer l'utilisateur via la méthode du modèle
    if not utilisateur.est_actif:
        utilisateur.modifier_est_actif()

    # créer le token
    token         = secrets.token_hex(32)
    TOKENS[token] = utilisateur.id

    return JsonResponse({
        'message':     'Utilisateur connecté avec succès',
        'token':       token,
        'utilisateur': _serialiseUtilisateur(utilisateur),
    }, status=200)


# ── DÉCONNEXION ───────────────────────────────────────────────────────────────
@csrf_exempt
def seDeconnecter(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification invalide"}, status=401)

    # désactiver l'utilisateur via la méthode du modèle
    if utilisateur.est_actif:
        utilisateur.modifier_est_actif()

    # supprimer le token
    token_key = request.headers.get('Authorization', '').replace('Token ', '')
    TOKENS.pop(token_key, None)

    return JsonResponse({'message': 'Utilisateur déconnecté avec succès'}, status=200)


# ── PROFIL ────────────────────────────────────────────────────────────────────
@csrf_exempt
def profilAfficher(request):
    if request.method != 'GET':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    profil = utilisateur.profil

    return JsonResponse({
        'utilisateur': _serialiseUtilisateur(utilisateur),
        'profil':      _serialiseProfil(profil, request),
    }, status=200)


# ── MODIFIER UTILISATEUR ──────────────────────────────────────────────────────
@csrf_exempt
def modifierUtilisateur(request):
    if request.method != 'PUT':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide'}, status=400)

    # mettre à jour les champs de l'utilisateur
    for champ in ['nom', 'prenom', 'email', 'telephone']:
        if champ in data:
            setattr(utilisateur, champ, data[champ])

    try:
        utilisateur.save()
    except IntegrityError:
        return JsonResponse({'error': "L'email existe déjà"}, status=400)

    return JsonResponse({
        'message':     'Utilisateur mis à jour avec succès',
        'utilisateur': _serialiseUtilisateur(utilisateur),
    }, status=200)


# ── MODIFIER PROFIL ───────────────────────────────────────────────────────────
@csrf_exempt
def modifierProfil(request):
    if request.method != 'PUT':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    profil = utilisateur.profil

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Corps de requête JSON invalide'}, status=400)

    # mettre à jour les champs du profil
    for champ in ['bio', 'adresse', 'commune', 'ville', 'pays', 'role', 'latitude', 'longitude']:
        if champ in data:
            setattr(profil, champ, data[champ])

    _enregistrer_photo_profil(profil, data.get('photo_profil'))

    profil.save()

    return JsonResponse({
        'message': 'Profil mis à jour avec succès',
        'profil':  _serialiseProfil(profil, request),
    }, status=200)


# ── MODIFIER MOT DE PASSE ─────────────────────────────────────────────────────
@csrf_exempt
def modifierMotDePasse(request):
    if request.method != 'PUT':
        return JsonResponse({'error': 'Méthode non autorisée'}, status=405)

    utilisateur = _get_user_from_token(request)
    if not utilisateur:
        return JsonResponse({'error': "Token d'authentification requis"}, status=401)

    data = json.loads(request.body)

    # vérifier les champs obligatoires
    for field in ['ancien_mot_de_passe', 'nouveau_mot_de_passe']:
        if field not in data:
            return JsonResponse({'error': f'Le champ {field} est requis'}, status=400)

    # vérifier l'ancien mot de passe
    if not verifier_password(data['ancien_mot_de_passe'], utilisateur.mot_de_passe):
        return JsonResponse({'error': 'Ancien mot de passe incorrect'}, status=401)

    # modifier via la méthode du modèle
    utilisateur.modifier_mot_de_passe(data['nouveau_mot_de_passe'])

    return JsonResponse({'message': 'Mot de passe modifié avec succès'}, status=200)


# ── FONCTIONS UTILITAIRES ─────────────────────────────────────────────────────
def _get_user_from_token(request):
    """Récupère l'utilisateur depuis le header Authorization: Token xxx"""
    auth = request.headers.get('Authorization', '')
    if not auth:
        return None

    token_key = auth.replace('Token ', '')
    user_id   = TOKENS.get(token_key)

    if not user_id:
        return None

    try:
        return Utilisateur.objects.get(id=user_id)
    except Utilisateur.DoesNotExist:
        return None


def _enregistrer_photo_profil(profil, photo_data):
    """Décode une image envoyée en base64 (JSON) et la sauvegarde sur le profil"""
    if not photo_data:
        return

    contenu = photo_data['content']
    # retirer le préfixe data URL si présent (ex: "data:image/png;base64,...")
    if contenu.startswith('data:'):
        contenu = contenu.split(',', 1)[1]

    fichier_binaire = base64.b64decode(contenu)
    profil.photo_profil.save(photo_data['filename'], ContentFile(fichier_binaire), save=False)

# -----------Serialiser pour utilisateur-----------
def _serialiseUtilisateur(utilisateur):
    """Sérialise un utilisateur en dict JSON"""
    return {
        'id':               utilisateur.id,
        'nom':              utilisateur.nom,
        'prenom':           utilisateur.prenom,
        'email':            utilisateur.email,
        'telephone':        utilisateur.telephone,
        'est_actif':        utilisateur.est_actif,
        'date_inscription': utilisateur.date_inscription.isoformat(),
    }


def _serialiseProfil(profil, request=None):
    """Sérialise un profil en dict JSON"""
    photo_url = None
    if profil.photo_profil:
        photo_url = profil.photo_profil.url
        if request is not None:
            photo_url = request.build_absolute_uri(photo_url)

    return {
        'id':          profil.id,
        'bio':         profil.bio,
        'photo_profil': photo_url,
        'adresse':     profil.adresse,
        'commune':     profil.commune,
        'ville':       profil.ville,
        'pays':        profil.pays,
        'longitude':   profil.longitude,
        'latitude':    profil.latitude,
        'date_maj':    profil.date_maj.isoformat(),
        'role':        profil.role,
    }